# Rooms Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add managed rooms with rich metadata, tag-based self-organization, transferable ownership, and archive lifecycle to the agentirc IRC server and agent harnesses.

**Architecture:** Server stores and federates room metadata via a new `RoomsSkill` (following the existing `HistorySkill` pattern). The `Channel` class gets room metadata fields. Agent harnesses evaluate room invitations autonomously using LLM judgment. Tags on both rooms and agents drive self-organizing membership.

**Tech Stack:** Python 3.12+, asyncio, pytest + pytest-asyncio, YAML for persistence.

---

### Task 1: Extend Channel Model with Room Metadata

**Files:**
- Modify: `agentirc/server/channel.py`
- Test: `tests/test_rooms.py` (create)

- [ ] **Step 1: Write failing test for Channel room metadata fields**

Create `tests/test_rooms.py`:

```python
# tests/test_rooms.py
"""Tests for rooms management."""
import pytest


def test_channel_has_room_metadata_fields():
    """Channel should have room metadata fields, all None/empty by default."""
    from agentirc.server.channel import Channel

    ch = Channel("#test")
    assert ch.room_id is None
    assert ch.creator is None
    assert ch.owner is None
    assert ch.purpose is None
    assert ch.instructions is None
    assert ch.tags == []
    assert ch.persistent is False
    assert ch.agent_limit is None
    assert ch.extra_meta == {}
    assert ch.archived is False
    assert ch.created_at is None


def test_channel_is_managed():
    """Channel with room_id is considered managed."""
    from agentirc.server.channel import Channel

    ch = Channel("#test")
    assert ch.is_managed is False
    ch.room_id = "R7K2M9"
    assert ch.is_managed is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py -v`
Expected: FAIL — `Channel` has no `room_id` attribute.

- [ ] **Step 3: Add room metadata fields to Channel**

Edit `agentirc/server/channel.py` — add fields after existing `__init__` assignments:

```python
from __future__ import annotations
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from agentirc.server.client import Client
    from agentirc.server.remote_client import RemoteClient

    Member = Union[Client, RemoteClient]


class Channel:
    """Represents an IRC channel with members and topic."""

    def __init__(self, name: str):
        self.name = name
        self.topic: str | None = None
        self.members: set[Client] = set()
        self.operators: set[Client] = set()
        self.voiced: set[Client] = set()
        self.restricted = False       # +R mode — never federate
        self.shared_with: set[str] = set()  # +S servers — share with these servers

        # Room metadata (populated by ROOMCREATE, None for plain channels)
        self.room_id: str | None = None
        self.creator: str | None = None
        self.owner: str | None = None
        self.purpose: str | None = None
        self.instructions: str | None = None
        self.tags: list[str] = []
        self.persistent: bool = False
        self.agent_limit: int | None = None
        self.extra_meta: dict[str, str] = {}
        self.archived: bool = False
        self.created_at: float | None = None

    @property
    def is_managed(self) -> bool:
        """True if this channel was created via ROOMCREATE."""
        return self.room_id is not None

    def _local_members(self) -> set[Client]:
        """Return only local (non-remote) members."""
        from agentirc.server.remote_client import RemoteClient
        return {m for m in self.members if not isinstance(m, RemoteClient)}

    def add(self, client: Client) -> None:
        # Only grant op to the first LOCAL joiner
        if not self._local_members():
            from agentirc.server.remote_client import RemoteClient
            if not isinstance(client, RemoteClient):
                self.operators.add(client)
        self.members.add(client)

    def remove(self, client: Client) -> None:
        self.members.discard(client)
        was_op = client in self.operators
        self.operators.discard(client)
        self.voiced.discard(client)
        if was_op and not self.operators:
            # Auto-promote only among local members
            local = self._local_members()
            if local:
                self.operators.add(min(local, key=lambda m: m.nick))

    def is_operator(self, client: Client) -> bool:
        return client in self.operators

    def is_voiced(self, client: Client) -> bool:
        return client in self.voiced

    def get_prefix(self, client: Client) -> str:
        if client in self.operators:
            return "@"
        if client in self.voiced:
            return "+"
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentirc/server/channel.py tests/test_rooms.py
git commit -m "feat(rooms): add room metadata fields to Channel"
```

---

### Task 2: Room ID Generation and Metadata Parsing

**Files:**
- Create: `agentirc/server/rooms_util.py`
- Test: `tests/test_rooms.py` (append)

- [ ] **Step 1: Write failing tests for room ID generation and metadata parsing**

Append to `tests/test_rooms.py`:

```python
def test_generate_room_id_format():
    """Room ID starts with R followed by uppercase alphanumeric."""
    from agentirc.server.rooms_util import generate_room_id
    import re

    rid = generate_room_id()
    assert rid.startswith("R")
    assert len(rid) >= 6
    assert re.match(r"^R[0-9A-Z]+$", rid)


def test_generate_room_id_uniqueness():
    """Two consecutive calls produce different IDs."""
    from agentirc.server.rooms_util import generate_room_id

    ids = {generate_room_id() for _ in range(100)}
    assert len(ids) == 100


def test_parse_room_meta_basic():
    """Parse key=value pairs separated by semicolons."""
    from agentirc.server.rooms_util import parse_room_meta

    meta = parse_room_meta("purpose=Help with Python;tags=python,code-help;persistent=true")
    assert meta["purpose"] == "Help with Python"
    assert meta["tags"] == "python,code-help"
    assert meta["persistent"] == "true"


def test_parse_room_meta_instructions_last():
    """Instructions field is always last and may contain semicolons."""
    from agentirc.server.rooms_util import parse_room_meta

    meta = parse_room_meta(
        "purpose=Help;tags=py;instructions=Do this; then that; finally done"
    )
    assert meta["purpose"] == "Help"
    assert meta["tags"] == "py"
    assert meta["instructions"] == "Do this; then that; finally done"


def test_parse_room_meta_empty():
    """Empty string returns empty dict."""
    from agentirc.server.rooms_util import parse_room_meta

    assert parse_room_meta("") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_generate_room_id_format tests/test_rooms.py::test_parse_room_meta_basic -v`
Expected: FAIL — `rooms_util` module doesn't exist.

- [ ] **Step 3: Implement room ID generation and metadata parsing**

Create `agentirc/server/rooms_util.py`:

```python
"""Utility functions for managed rooms."""
from __future__ import annotations

import time
import threading

_counter = 0
_counter_lock = threading.Lock()

_BASE36_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def generate_room_id() -> str:
    """Generate a unique room ID: R + base-36 encoded timestamp + counter."""
    global _counter
    with _counter_lock:
        _counter += 1
        counter_val = _counter

    ts_ms = int(time.time() * 1000)
    # Combine timestamp and counter for uniqueness
    combined = ts_ms * 1000 + (counter_val % 1000)

    result = []
    while combined:
        result.append(_BASE36_CHARS[combined % 36])
        combined //= 36
    return "R" + "".join(reversed(result))


def parse_room_meta(text: str) -> dict[str, str]:
    """Parse 'key=value;key=value;instructions=...' metadata format.

    The ``instructions`` field must be last — everything after
    ``instructions=`` is captured verbatim (it may contain semicolons).
    """
    if not text:
        return {}

    result: dict[str, str] = {}

    # Extract instructions first (must be last, may contain semicolons)
    if "instructions=" in text:
        before, instructions = text.split("instructions=", 1)
        result["instructions"] = instructions
        text = before.rstrip(";")

    if not text:
        return result

    for pair in text.split(";"):
        pair = pair.strip()
        if "=" in pair:
            key, value = pair.split("=", 1)
            result[key.strip()] = value.strip()

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentirc/server/rooms_util.py tests/test_rooms.py
git commit -m "feat(rooms): add room ID generation and metadata parsing"
```

---

### Task 3: Client Tags and RoomsSkill Scaffold with ROOMCREATE

**Files:**
- Modify: `agentirc/server/client.py` (add `tags` field)
- Create: `agentirc/server/skills/rooms.py`
- Modify: `agentirc/server/ircd.py` (register skill, skip persistent on cleanup)
- Modify: `tests/test_rooms.py` (append)

- [ ] **Step 1: Write failing tests for ROOMCREATE and client tags**

Append to `tests/test_rooms.py`:

```python
@pytest.mark.asyncio
async def test_roomcreate_basic(server, make_client):
    """ROOMCREATE creates a managed room and returns room ID."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python,code-help;persistent=true"
    )
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)

    # Should get a ROOMCREATED response with room ID
    assert "ROOMCREATED" in joined
    assert "#pyhelp" in joined
    assert " R" in joined  # room ID starts with R

    # Should have auto-joined the channel
    assert "JOIN" in joined
    assert "353" in joined  # RPL_NAMREPLY


@pytest.mark.asyncio
async def test_roomcreate_stores_metadata(server, make_client):
    """ROOMCREATE stores metadata on the channel."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python,code-help;persistent=true;agent_limit=5"
    )
    await alice.recv_all(timeout=1.0)

    channel = server.channels.get("#pyhelp")
    assert channel is not None
    assert channel.is_managed
    assert channel.room_id is not None
    assert channel.room_id.startswith("R")
    assert channel.creator == "testserv-alice"
    assert channel.owner == "testserv-alice"
    assert channel.purpose == "Python help"
    assert channel.tags == ["python", "code-help"]
    assert channel.persistent is True
    assert channel.agent_limit == 5
    assert channel.created_at is not None


@pytest.mark.asyncio
async def test_roomcreate_with_instructions(server, make_client):
    """ROOMCREATE handles instructions field (may contain semicolons)."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send(
        "ROOMCREATE #help :purpose=Help;tags=py;instructions=Do this; then that; done"
    )
    await alice.recv_all(timeout=1.0)

    channel = server.channels["#help"]
    assert channel.instructions == "Do this; then that; done"


@pytest.mark.asyncio
async def test_roomcreate_duplicate_name(server, make_client):
    """ROOMCREATE on existing channel fails."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE #pyhelp :purpose=first")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMCREATE #pyhelp :purpose=second")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "already exists" in joined.lower() or "403" in joined


@pytest.mark.asyncio
async def test_roomcreate_requires_hash(server, make_client):
    """ROOMCREATE requires channel name starting with #."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE badname :purpose=test")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "badname" not in server.channels


@pytest.mark.asyncio
async def test_roomcreate_no_params(server, make_client):
    """ROOMCREATE with missing params returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE")
    resp = await alice.recv()
    assert "461" in resp  # ERR_NEEDMOREPARAMS


@pytest.mark.asyncio
async def test_client_tags_default_empty(server, make_client):
    """Client tags default to empty list."""
    alice = await make_client(nick="testserv-alice", user="alice")
    client = server.clients["testserv-alice"]
    assert client.tags == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_roomcreate_basic -v`
Expected: FAIL — `ROOMCREATE` is an unknown command.

- [ ] **Step 3: Add tags field to Client**

Edit `agentirc/server/client.py` — add `self.tags` in `__init__` after `self._registered`:

```python
        self._registered = False
        self.tags: list[str] = []
```

- [ ] **Step 4: Create RoomsSkill with ROOMCREATE handler**

Create `agentirc/server/skills/rooms.py`:

```python
"""Rooms management skill — ROOMCREATE, ROOMMETA, TAGS, ROOMINVITE, ROOMKICK, ROOMARCHIVE."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from agentirc.protocol.message import Message
from agentirc.protocol import replies
from agentirc.server.rooms_util import generate_room_id, parse_room_meta
from agentirc.server.skill import Event, EventType, Skill

if TYPE_CHECKING:
    from agentirc.server.client import Client


class RoomsSkill(Skill):
    name = "rooms"
    commands = {"ROOMCREATE", "ROOMMETA", "TAGS", "ROOMINVITE", "ROOMKICK", "ROOMARCHIVE"}

    async def on_command(self, client: Client, msg: Message) -> None:
        handler = {
            "ROOMCREATE": self._handle_roomcreate,
            "ROOMMETA": self._handle_roommeta,
            "TAGS": self._handle_tags,
            "ROOMINVITE": self._handle_roominvite,
            "ROOMKICK": self._handle_roomkick,
            "ROOMARCHIVE": self._handle_roomarchive,
        }.get(msg.command)
        if handler:
            await handler(client, msg)

    async def _handle_roomcreate(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMCREATE", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        if not channel_name.startswith("#"):
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, "Channel name must start with #"],
            ))
            return

        if channel_name in self.server.channels:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, "Channel already exists"
            )
            return

        meta_text = msg.params[1]
        meta = parse_room_meta(meta_text)

        channel = self.server.get_or_create_channel(channel_name)
        channel.room_id = generate_room_id()
        channel.creator = client.nick
        channel.owner = client.nick
        channel.purpose = meta.get("purpose")
        channel.instructions = meta.get("instructions")
        channel.persistent = meta.get("persistent", "").lower() == "true"
        channel.created_at = time.time()
        channel.extra_meta = {}

        # Parse tags
        tags_str = meta.get("tags", "")
        channel.tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        # Parse agent_limit
        limit_str = meta.get("agent_limit")
        if limit_str:
            try:
                channel.agent_limit = int(limit_str)
            except ValueError:
                pass

        # Store extra metadata (anything not a known key)
        known_keys = {"purpose", "instructions", "persistent", "tags", "agent_limit"}
        for key, value in meta.items():
            if key not in known_keys:
                channel.extra_meta[key] = value

        # Auto-join creator as operator
        channel.add(client)
        client.channels.add(channel)

        # Send JOIN to the creator
        join_msg = Message(prefix=client.prefix, command="JOIN", params=[channel_name])
        await client.send(join_msg)

        # Send NAMES list
        nicks = " ".join(f"{channel.get_prefix(m)}{m.nick}" for m in channel.members)
        await client.send_numeric(replies.RPL_NAMREPLY, "=", channel_name, nicks)
        await client.send_numeric(replies.RPL_ENDOFNAMES, channel_name, "End of /NAMES list")

        # Send ROOMCREATED confirmation with room ID
        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMCREATED",
            params=[channel_name, channel.room_id, f"Room created: {channel.purpose or channel_name}"],
        ))

    async def _handle_roommeta(self, client: Client, msg: Message) -> None:
        pass  # Task 4

    async def _handle_tags(self, client: Client, msg: Message) -> None:
        pass  # Task 5

    async def _handle_roominvite(self, client: Client, msg: Message) -> None:
        pass  # Task 7

    async def _handle_roomkick(self, client: Client, msg: Message) -> None:
        pass  # Task 8

    async def _handle_roomarchive(self, client: Client, msg: Message) -> None:
        pass  # Task 9
```

- [ ] **Step 5: Register RoomsSkill and protect persistent channels from cleanup**

Edit `agentirc/server/ircd.py` — add RoomsSkill to `_register_default_skills`:

```python
    async def _register_default_skills(self) -> None:
        from agentirc.server.skills.history import HistorySkill
        from agentirc.server.skills.rooms import RoomsSkill

        await self.register_skill(HistorySkill())
        await self.register_skill(RoomsSkill())
```

Edit `agentirc/server/ircd.py` — in `_remove_client`, skip cleanup for persistent channels:

Change:
```python
            if not channel.members:
                del self.channels[channel.name]
```
To:
```python
            if not channel.members and not channel.persistent:
                del self.channels[channel.name]
```

Edit `agentirc/server/client.py` — in `_handle_join`, block joins to archived channels. After the `if not channel_name.startswith("#"): return` check, add:

```python
        # Block joins to archived rooms
        existing = self.server.channels.get(channel_name)
        if existing and existing.archived:
            await self.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[self.nick, f"{channel_name} is archived and cannot be joined"],
                )
            )
            return
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agentirc/server/client.py agentirc/server/skills/rooms.py agentirc/server/ircd.py tests/test_rooms.py
git commit -m "feat(rooms): RoomsSkill with ROOMCREATE, client tags, persistent channel protection"
```

---

### Task 4: ROOMMETA Command

**Files:**
- Modify: `agentirc/server/skills/rooms.py`
- Modify: `tests/test_rooms.py` (append)

- [ ] **Step 1: Write failing tests for ROOMMETA query and update**

Append to `tests/test_rooms.py`:

```python
@pytest.mark.asyncio
async def test_roommeta_query_all(server, make_client):
    """ROOMMETA with just channel name returns all metadata."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python,code-help;persistent=true"
    )
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #pyhelp")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)

    assert "room_id" in joined
    assert "purpose" in joined
    assert "Python help" in joined
    assert "tags" in joined
    assert "python" in joined
    assert "ROOMETAEND" in joined


@pytest.mark.asyncio
async def test_roommeta_query_single_key(server, make_client):
    """ROOMMETA with key returns just that field."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help;tags=python")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #pyhelp tags")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)

    assert "tags" in joined
    assert "python" in joined


@pytest.mark.asyncio
async def test_roommeta_update_tags(server, make_client):
    """ROOMMETA with key and value updates the field (owner can write)."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help;tags=python")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #pyhelp tags python,devops,code-help")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "updated" in joined.lower() or "ROOMETASET" in joined

    channel = server.channels["#pyhelp"]
    assert channel.tags == ["python", "devops", "code-help"]


@pytest.mark.asyncio
async def test_roommeta_update_owner(server, make_client):
    """Room owner can be transferred via ROOMMETA."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("ROOMCREATE #pyhelp :purpose=Test")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #pyhelp owner testserv-bob")
    await alice.recv_all(timeout=1.0)

    channel = server.channels["#pyhelp"]
    assert channel.owner == "testserv-bob"


@pytest.mark.asyncio
async def test_roommeta_non_owner_cannot_write(server, make_client):
    """Non-owner/non-operator cannot update room metadata."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("ROOMCREATE #pyhelp :purpose=Test;tags=python")
    await alice.recv_all(timeout=1.0)

    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    await bob.send("ROOMMETA #pyhelp tags hacked")
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "permission" in joined.lower() or "482" in joined

    channel = server.channels["#pyhelp"]
    assert channel.tags == ["python"]


@pytest.mark.asyncio
async def test_roommeta_nonexistent_channel(server, make_client):
    """ROOMMETA on nonexistent channel returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMMETA #noroom")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "403" in joined  # ERR_NOSUCHCHANNEL


@pytest.mark.asyncio
async def test_roommeta_on_plain_channel(server, make_client):
    """ROOMMETA on non-managed channel returns not-managed notice."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #plain")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #plain")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "not a managed room" in joined.lower() or "NOTICE" in joined
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_roommeta_query_all -v`
Expected: FAIL — `_handle_roommeta` is a no-op.

- [ ] **Step 3: Implement ROOMMETA handler**

Replace the `_handle_roommeta` stub in `agentirc/server/skills/rooms.py`:

```python
    async def _handle_roommeta(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMMETA", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, "No such channel"
            )
            return

        if not channel.is_managed:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"{channel_name} is not a managed room"],
            ))
            return

        if len(msg.params) == 1:
            # Query all metadata
            await self._send_all_meta(client, channel)
        elif len(msg.params) == 2:
            # Query single key
            await self._send_single_meta(client, channel, msg.params[1])
        else:
            # Update: key value
            await self._update_meta(client, channel, msg.params[1], msg.params[2])

    async def _send_all_meta(self, client: Client, channel) -> None:
        """Send all room metadata as ROOMMETA lines."""
        fields = {
            "room_id": channel.room_id,
            "creator": channel.creator,
            "owner": channel.owner,
            "purpose": channel.purpose or "",
            "instructions": channel.instructions or "",
            "tags": ",".join(channel.tags),
            "persistent": str(channel.persistent).lower(),
            "agent_limit": str(channel.agent_limit) if channel.agent_limit else "",
            "archived": str(channel.archived).lower(),
        }
        # Add extra_meta
        for key, value in channel.extra_meta.items():
            fields[key] = value

        for key, value in fields.items():
            await client.send(Message(
                prefix=self.server.config.name,
                command="ROOMMETA",
                params=[channel.name, key, value],
            ))
        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMETAEND",
            params=[channel.name, "End of room metadata"],
        ))

    async def _send_single_meta(self, client: Client, channel, key: str) -> None:
        """Send a single metadata field."""
        value = self._get_meta_value(channel, key)
        if value is None:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"Unknown metadata key: {key}"],
            ))
            return
        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMMETA",
            params=[channel.name, key, value],
        ))
        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMETAEND",
            params=[channel.name, "End of room metadata"],
        ))

    def _get_meta_value(self, channel, key: str) -> str | None:
        """Get a string value for a metadata key."""
        if key == "room_id":
            return channel.room_id
        elif key == "creator":
            return channel.creator
        elif key == "owner":
            return channel.owner
        elif key == "purpose":
            return channel.purpose or ""
        elif key == "instructions":
            return channel.instructions or ""
        elif key == "tags":
            return ",".join(channel.tags)
        elif key == "persistent":
            return str(channel.persistent).lower()
        elif key == "agent_limit":
            return str(channel.agent_limit) if channel.agent_limit else ""
        elif key == "archived":
            return str(channel.archived).lower()
        elif key in channel.extra_meta:
            return channel.extra_meta[key]
        return None

    async def _update_meta(self, client: Client, channel, key: str, value: str) -> None:
        """Update a metadata field. Requires owner or channel operator."""
        # Permission check: owner or channel operator
        is_owner = channel.owner == client.nick
        is_op = channel.is_operator(client)
        if not is_owner and not is_op:
            await client.send_numeric(
                replies.ERR_CHANOPRIVSNEEDED, channel.name, "Permission denied"
            )
            return

        if key == "owner":
            channel.owner = value
        elif key == "purpose":
            channel.purpose = value
        elif key == "instructions":
            channel.instructions = value
        elif key == "tags":
            old_tags = set(channel.tags)
            channel.tags = [t.strip() for t in value.split(",") if t.strip()]
            new_tags = set(channel.tags)
            await self._on_room_tags_changed(channel, old_tags, new_tags)
        elif key == "persistent":
            channel.persistent = value.lower() == "true"
        elif key == "agent_limit":
            try:
                channel.agent_limit = int(value) if value else None
            except ValueError:
                pass
        elif key in {"room_id", "creator", "archived", "created_at"}:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"{key} is read-only"],
            ))
            return
        else:
            channel.extra_meta[key] = value

        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMETASET",
            params=[channel.name, key, value],
        ))

    async def _on_room_tags_changed(self, channel, old_tags: set, new_tags: set) -> None:
        """Handle tag changes on a room — placeholder for Task 6."""
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentirc/server/skills/rooms.py tests/test_rooms.py
git commit -m "feat(rooms): ROOMMETA command — query and update room metadata"
```

---

### Task 5: TAGS Command

**Files:**
- Modify: `agentirc/server/skills/rooms.py`
- Modify: `tests/test_rooms.py` (append)

- [ ] **Step 1: Write failing tests for TAGS command**

Append to `tests/test_rooms.py`:

```python
@pytest.mark.asyncio
async def test_tags_set_own(server, make_client):
    """Agent can set its own tags."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("TAGS testserv-alice python,code-review,agentirc")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "TAGSSET" in joined

    client = server.clients["testserv-alice"]
    assert client.tags == ["python", "code-review", "agentirc"]


@pytest.mark.asyncio
async def test_tags_query_own(server, make_client):
    """Agent can query its own tags."""
    alice = await make_client(nick="testserv-alice", user="alice")
    client = server.clients["testserv-alice"]
    client.tags = ["python", "devops"]

    await alice.send("TAGS testserv-alice")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "python,devops" in joined


@pytest.mark.asyncio
async def test_tags_query_other(server, make_client):
    """Anyone can query another agent's tags."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    server.clients["testserv-bob"].tags = ["rust", "infra"]

    await alice.send("TAGS testserv-bob")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "rust,infra" in joined


@pytest.mark.asyncio
async def test_tags_no_params(server, make_client):
    """TAGS with no params returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("TAGS")
    resp = await alice.recv()
    assert "461" in resp


@pytest.mark.asyncio
async def test_tags_nonexistent_nick(server, make_client):
    """TAGS on nonexistent nick returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("TAGS testserv-nobody")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "401" in joined  # ERR_NOSUCHNICK


@pytest.mark.asyncio
async def test_tags_cannot_set_others(server, make_client):
    """Non-operator cannot set another agent's tags."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("TAGS testserv-bob python")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "permission" in joined.lower() or "NOTICE" in joined

    assert server.clients["testserv-bob"].tags == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_tags_set_own -v`
Expected: FAIL — `_handle_tags` is a no-op.

- [ ] **Step 3: Implement TAGS handler**

Replace the `_handle_tags` stub in `agentirc/server/skills/rooms.py`:

```python
    async def _handle_tags(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "TAGS", "Not enough parameters"
            )
            return

        target_nick = msg.params[0]
        target = self.server.get_client(target_nick)
        if not target:
            await client.send_numeric(
                replies.ERR_NOSUCHNICK, target_nick, "No such nick"
            )
            return

        if len(msg.params) == 1:
            # Query tags
            tags_str = ",".join(target.tags) if target.tags else ""
            await client.send(Message(
                prefix=self.server.config.name,
                command="TAGS",
                params=[target_nick, tags_str],
            ))
            await client.send(Message(
                prefix=self.server.config.name,
                command="TAGSEND",
                params=[target_nick, "End of tags"],
            ))
        else:
            # Set tags — must be self or channel operator in a shared channel
            if target_nick != client.nick:
                await client.send(Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, "Permission denied: can only set your own tags"],
                ))
                return

            old_tags = set(target.tags)
            target.tags = [t.strip() for t in msg.params[1].split(",") if t.strip()]
            new_tags = set(target.tags)

            await client.send(Message(
                prefix=self.server.config.name,
                command="TAGSSET",
                params=[target_nick, ",".join(target.tags)],
            ))

            await self._on_agent_tags_changed(client, old_tags, new_tags)

    async def _on_agent_tags_changed(self, client: Client, old_tags: set, new_tags: set) -> None:
        """Handle tag changes on an agent — placeholder for Task 6."""
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentirc/server/skills/rooms.py tests/test_rooms.py
git commit -m "feat(rooms): TAGS command — query and set agent tags"
```

---

### Task 6: Tag Event Engine

**Files:**
- Modify: `agentirc/server/skills/rooms.py`
- Modify: `tests/test_rooms.py` (append)

- [ ] **Step 1: Write failing tests for tag-driven notifications**

Append to `tests/test_rooms.py`:

```python
import asyncio


@pytest.mark.asyncio
async def test_room_tag_added_invites_matching_agents(server, make_client):
    """When a room gains a tag, agents with that tag get a ROOMINVITE."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    # Bob has "python" tag
    await bob.send("TAGS testserv-bob python")
    await bob.recv_all(timeout=0.5)

    # Alice creates room without python tag
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help;tags=devops")
    await alice.recv_all(timeout=1.0)
    await bob.recv_all(timeout=0.3)  # drain any messages

    # Alice adds python tag to the room
    await alice.send("ROOMMETA #pyhelp tags devops,python")
    await alice.recv_all(timeout=1.0)

    # Bob should get a ROOMINVITE
    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "#pyhelp" in joined


@pytest.mark.asyncio
async def test_room_tag_removed_notifies_matching_agents(server, make_client):
    """When a room loses a tag, in-room agents with that tag get a notice."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    # Bob has "python" tag
    await bob.send("TAGS testserv-bob python")
    await bob.recv_all(timeout=0.5)

    # Alice creates room with python tag
    await alice.send("ROOMCREATE #pyhelp :purpose=Help;tags=python")
    await alice.recv_all(timeout=1.0)

    # Bob joins the room
    await bob.recv_all(timeout=0.5)  # drain invite
    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    # Alice removes python tag
    await alice.send("ROOMMETA #pyhelp tags devops")
    await alice.recv_all(timeout=1.0)

    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMTAGNOTICE" in joined
    assert "removed" in joined.lower()


@pytest.mark.asyncio
async def test_agent_tag_added_notifies_about_rooms(server, make_client):
    """When an agent gains a tag, it gets notices about matching rooms."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    # Alice creates room with python tag
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help;tags=python")
    await alice.recv_all(timeout=1.0)

    # Bob sets python tag — should get a ROOMINVITE about #pyhelp
    await bob.send("TAGS testserv-bob python")
    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "#pyhelp" in joined


@pytest.mark.asyncio
async def test_agent_tag_removed_notifies_about_rooms(server, make_client):
    """When an agent loses a tag, it gets a notice about rooms with that tag."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await bob.send("TAGS testserv-bob python,devops")
    await bob.recv_all(timeout=0.5)

    await alice.send("ROOMCREATE #pyhelp :purpose=Help;tags=python")
    await alice.recv_all(timeout=1.0)

    # Bob joins
    await bob.recv_all(timeout=0.5)  # drain invite
    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    # Bob removes python tag (keeps devops)
    await bob.send("TAGS testserv-bob devops")
    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMTAGNOTICE" in joined
    assert "#pyhelp" in joined


@pytest.mark.asyncio
async def test_no_invite_if_already_in_room(server, make_client):
    """Tag engine doesn't invite agents already in the room."""
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("TAGS testserv-alice python")
    await alice.recv_all(timeout=0.5)

    await alice.send("ROOMCREATE #pyhelp :purpose=Help;tags=python")
    await alice.recv_all(timeout=1.0)

    # Alice is already in the room — adding matching tag to room shouldn't re-invite
    await alice.send("ROOMMETA #pyhelp tags python,code-help")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    # Should get ROOMETASET but NOT ROOMINVITE
    assert "ROOMETASET" in joined
    # Since alice is already in the room, no invite
    invite_lines = [l for l in lines if "ROOMINVITE" in l]
    assert len(invite_lines) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_room_tag_added_invites_matching_agents -v`
Expected: FAIL — tag engine methods are no-ops.

- [ ] **Step 3: Implement tag event engine**

Replace the placeholder methods in `agentirc/server/skills/rooms.py`:

```python
    async def _on_room_tags_changed(self, channel, old_tags: set, new_tags: set) -> None:
        """When room tags change, notify relevant agents."""
        from agentirc.server.remote_client import RemoteClient

        added = new_tags - old_tags
        removed = old_tags - new_tags

        # Tags added to room → invite agents with matching tags not in room
        if added:
            for client in list(self.server.clients.values()):
                if client in channel.members:
                    continue
                if not client.tags:
                    continue
                if added & set(client.tags):
                    await self._send_system_invite(client, channel)

        # Tags removed from room → notify in-room agents with those tags
        if removed:
            for member in list(channel.members):
                if isinstance(member, RemoteClient):
                    continue
                if not member.tags:
                    continue
                if removed & set(member.tags):
                    removed_str = ",".join(removed & set(member.tags))
                    await member.send(Message(
                        prefix=self.server.config.name,
                        command="ROOMTAGNOTICE",
                        params=[
                            channel.name,
                            member.nick,
                            f"Tag(s) removed from room: {removed_str}. Consider if you still belong here.",
                        ],
                    ))

    async def _on_agent_tags_changed(self, client: Client, old_tags: set, new_tags: set) -> None:
        """When agent tags change, notify about relevant rooms."""
        added = new_tags - old_tags
        removed = old_tags - new_tags

        # Tags added to agent → find rooms with matching tags
        if added:
            for channel in list(self.server.channels.values()):
                if not channel.is_managed:
                    continue
                if client in channel.members:
                    continue
                if added & set(channel.tags):
                    await self._send_system_invite(client, channel)

        # Tags removed from agent → notify about rooms they're in with those tags
        if removed:
            for channel in list(client.channels):
                if not channel.is_managed:
                    continue
                if removed & set(channel.tags):
                    removed_str = ",".join(removed & set(channel.tags))
                    await client.send(Message(
                        prefix=self.server.config.name,
                        command="ROOMTAGNOTICE",
                        params=[
                            channel.name,
                            client.nick,
                            f"Tag(s) removed from your profile: {removed_str}. Consider if you still belong in {channel.name}.",
                        ],
                    ))

    async def _send_system_invite(self, client: Client, channel) -> None:
        """Send a tag-driven ROOMINVITE (system event, no requestor)."""
        purpose = channel.purpose or ""
        instructions = channel.instructions or ""
        tags_str = ",".join(channel.tags)
        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMINVITE",
            params=[
                channel.name,
                client.nick,
                f"purpose={purpose};tags={tags_str};instructions={instructions}",
            ],
        ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentirc/server/skills/rooms.py tests/test_rooms.py
git commit -m "feat(rooms): tag event engine — notify on room/agent tag changes"
```

---

### Task 7: ROOMINVITE Command (Explicit Invitations)

**Files:**
- Modify: `agentirc/server/skills/rooms.py`
- Modify: `tests/test_rooms.py` (append)

- [ ] **Step 1: Write failing tests for explicit ROOMINVITE**

Append to `tests/test_rooms.py`:

```python
@pytest.mark.asyncio
async def test_roominvite_sends_context(server, make_client):
    """ROOMINVITE delivers room metadata to the target agent."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python;instructions=Be helpful"
    )
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMINVITE #pyhelp testserv-bob")
    await alice.recv_all(timeout=0.5)

    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "#pyhelp" in joined
    assert "Python help" in joined
    assert "testserv-alice" in joined  # requestor included


@pytest.mark.asyncio
async def test_roominvite_nonexistent_target(server, make_client):
    """ROOMINVITE to nonexistent nick returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE #pyhelp :purpose=Test")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMINVITE #pyhelp testserv-nobody")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "401" in joined  # ERR_NOSUCHNICK


@pytest.mark.asyncio
async def test_roominvite_nonexistent_room(server, make_client):
    """ROOMINVITE for nonexistent room returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMINVITE #noroom testserv-alice")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "403" in joined  # ERR_NOSUCHCHANNEL


@pytest.mark.asyncio
async def test_roominvite_missing_params(server, make_client):
    """ROOMINVITE with missing params returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMINVITE #pyhelp")
    resp = await alice.recv()
    assert "461" in resp
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_roominvite_sends_context -v`
Expected: FAIL — `_handle_roominvite` is a no-op.

- [ ] **Step 3: Implement ROOMINVITE handler**

Replace the stub in `agentirc/server/skills/rooms.py`:

```python
    async def _handle_roominvite(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMINVITE", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        target_nick = msg.params[1]

        channel = self.server.channels.get(channel_name)
        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, "No such channel"
            )
            return

        target = self.server.get_client(target_nick)
        if not target:
            await client.send_numeric(
                replies.ERR_NOSUCHNICK, target_nick, "No such nick"
            )
            return

        # Send invitation with room context and requestor info
        purpose = channel.purpose or ""
        instructions = channel.instructions or ""
        tags_str = ",".join(channel.tags)
        meta = f"purpose={purpose};tags={tags_str};requestor={client.nick};instructions={instructions}"

        from agentirc.server.remote_client import RemoteClient
        if not isinstance(target, RemoteClient):
            await target.send(Message(
                prefix=self.server.config.name,
                command="ROOMINVITE",
                params=[channel_name, target_nick, meta],
            ))

        # Confirm to the inviter
        await client.send(Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=[client.nick, f"Invitation sent to {target_nick} for {channel_name}"],
        ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentirc/server/skills/rooms.py tests/test_rooms.py
git commit -m "feat(rooms): ROOMINVITE command — explicit invitations with context"
```

---

### Task 8: ROOMKICK Command

**Files:**
- Modify: `agentirc/server/skills/rooms.py`
- Modify: `tests/test_rooms.py` (append)

- [ ] **Step 1: Write failing tests for ROOMKICK**

Append to `tests/test_rooms.py`:

```python
@pytest.mark.asyncio
async def test_roomkick_owner_removes_member(server, make_client):
    """Room owner can force-remove a member."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("ROOMCREATE #pyhelp :purpose=Test")
    await alice.recv_all(timeout=1.0)

    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    await alice.send("ROOMKICK #pyhelp testserv-bob")
    await alice.recv_all(timeout=1.0)

    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "PART" in joined or "KICK" in joined

    channel = server.channels.get("#pyhelp")
    bob_client = server.clients["testserv-bob"]
    assert bob_client not in channel.members


@pytest.mark.asyncio
async def test_roomkick_non_owner_denied(server, make_client):
    """Non-owner cannot ROOMKICK."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    charlie = await make_client(nick="testserv-charlie", user="charlie")

    await alice.send("ROOMCREATE #pyhelp :purpose=Test")
    await alice.recv_all(timeout=1.0)

    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await charlie.send("JOIN #pyhelp")
    await charlie.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.5)

    await bob.send("ROOMKICK #pyhelp testserv-charlie")
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "permission" in joined.lower() or "NOTICE" in joined

    charlie_client = server.clients["testserv-charlie"]
    channel = server.channels["#pyhelp"]
    assert charlie_client in channel.members


@pytest.mark.asyncio
async def test_roomkick_missing_params(server, make_client):
    """ROOMKICK with missing params returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMKICK #pyhelp")
    resp = await alice.recv()
    assert "461" in resp
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_roomkick_owner_removes_member -v`
Expected: FAIL — `_handle_roomkick` is a no-op.

- [ ] **Step 3: Implement ROOMKICK handler**

Replace the stub in `agentirc/server/skills/rooms.py`:

```python
    async def _handle_roomkick(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMKICK", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        target_nick = msg.params[1]

        channel = self.server.channels.get(channel_name)
        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, "No such channel"
            )
            return

        if not channel.is_managed:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"{channel_name} is not a managed room"],
            ))
            return

        # Only room owner can ROOMKICK
        if channel.owner != client.nick:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, "Permission denied: only the room owner can ROOMKICK"],
            ))
            return

        target = self.server.clients.get(target_nick)
        if not target or target not in channel.members:
            await client.send_numeric(
                replies.ERR_USERNOTINCHANNEL, target_nick, channel_name, "Not in channel"
            )
            return

        # Force-part the target
        part_msg = Message(
            prefix=target.prefix, command="PART",
            params=[channel_name, f"Removed by room owner {client.nick}"],
        )
        for member in list(channel.members):
            await member.send(part_msg)

        channel.remove(target)
        target.channels.discard(channel)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agentirc/server/skills/rooms.py tests/test_rooms.py
git commit -m "feat(rooms): ROOMKICK command — owner-only force remove"
```

---

### Task 9: ROOMARCHIVE Command

**Files:**
- Modify: `agentirc/server/skills/rooms.py`
- Modify: `agentirc/server/ircd.py` (add empty-room notification helper)
- Modify: `tests/test_rooms.py` (append)

- [ ] **Step 1: Write failing tests for ROOMARCHIVE**

Append to `tests/test_rooms.py`:

```python
@pytest.mark.asyncio
async def test_roomarchive_renames_and_preserves(server, make_client):
    """ROOMARCHIVE renames the room and preserves metadata."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("ROOMCREATE #pyhelp :purpose=Help;tags=python")
    await alice.recv_all(timeout=1.0)

    room_id = server.channels["#pyhelp"].room_id

    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    await alice.send("ROOMARCHIVE #pyhelp")
    await alice.recv_all(timeout=1.0)

    # Bob should be parted
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "archived" in joined.lower()

    # Original name freed
    assert "#pyhelp" not in server.channels

    # Archived room exists with new name
    assert "#pyhelp-archived" in server.channels
    archived = server.channels["#pyhelp-archived"]
    assert archived.archived is True
    assert archived.room_id == room_id
    assert archived.purpose == "Help"
    assert archived.tags == ["python"]


@pytest.mark.asyncio
async def test_roomarchive_increments_suffix(server, make_client):
    """Multiple archives of same-named room get incrementing suffixes."""
    alice = await make_client(nick="testserv-alice", user="alice")

    # First room + archive
    await alice.send("ROOMCREATE #pyhelp :purpose=First")
    await alice.recv_all(timeout=1.0)
    await alice.send("ROOMARCHIVE #pyhelp")
    await alice.recv_all(timeout=1.0)

    # Second room + archive
    await alice.send("ROOMCREATE #pyhelp :purpose=Second")
    await alice.recv_all(timeout=1.0)
    await alice.send("ROOMARCHIVE #pyhelp")
    await alice.recv_all(timeout=1.0)

    assert "#pyhelp-archived" in server.channels
    assert "#pyhelp-archived#2" in server.channels
    assert server.channels["#pyhelp-archived"].purpose == "First"
    assert server.channels["#pyhelp-archived#2"].purpose == "Second"


@pytest.mark.asyncio
async def test_roomarchive_non_owner_denied(server, make_client):
    """Non-owner cannot archive."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("ROOMCREATE #pyhelp :purpose=Test")
    await alice.recv_all(timeout=1.0)
    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    await bob.send("ROOMARCHIVE #pyhelp")
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "permission" in joined.lower()

    assert "#pyhelp" in server.channels
    assert server.channels["#pyhelp"].archived is False


@pytest.mark.asyncio
async def test_roomarchive_frees_name_for_reuse(server, make_client):
    """After archiving, a new room can use the same name with a new ID."""
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("ROOMCREATE #pyhelp :purpose=Original")
    await alice.recv_all(timeout=1.0)
    original_id = server.channels["#pyhelp"].room_id

    await alice.send("ROOMARCHIVE #pyhelp")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMCREATE #pyhelp :purpose=New version")
    await alice.recv_all(timeout=1.0)

    assert server.channels["#pyhelp"].room_id != original_id
    assert server.channels["#pyhelp"].purpose == "New version"


@pytest.mark.asyncio
async def test_persistent_room_notifies_owner_when_empty(server, make_client):
    """When a persistent room empties, the owner gets an archive suggestion."""
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("ROOMCREATE #pyhelp :purpose=Test;persistent=true")
    await alice.recv_all(timeout=1.0)

    # Alice leaves
    await alice.send("PART #pyhelp")
    await asyncio.sleep(0.1)
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)

    # Owner should get a notice about the empty room
    assert "empty" in joined.lower()
    assert "#pyhelp" in joined

    # Room should still exist (persistent)
    assert "#pyhelp" in server.channels
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_roomarchive_renames_and_preserves -v`
Expected: FAIL — `_handle_roomarchive` is a no-op.

- [ ] **Step 3: Implement ROOMARCHIVE handler**

Replace the stub in `agentirc/server/skills/rooms.py`:

```python
    async def _handle_roomarchive(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "ROOMARCHIVE", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel:
            await client.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, "No such channel"
            )
            return

        if not channel.is_managed:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"{channel_name} is not a managed room"],
            ))
            return

        # Only owner or operator
        if channel.owner != client.nick and not channel.is_operator(client):
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, "Permission denied: only the room owner can archive"],
            ))
            return

        # Determine archive name
        archive_name = self._next_archive_name(channel_name)

        # Notify all members
        notice = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=["*", f"Room {channel_name} has been archived by {client.nick}"],
        )
        for member in list(channel.members):
            await member.send(notice)

        # Part all members
        from agentirc.server.remote_client import RemoteClient
        for member in list(channel.members):
            part_msg = Message(
                prefix=member.prefix, command="PART",
                params=[channel_name, "Room archived"],
            )
            if not isinstance(member, RemoteClient):
                await member.send(part_msg)
            channel.members.discard(member)
            if hasattr(member, "channels"):
                member.channels.discard(channel)

        channel.operators.clear()
        channel.voiced.clear()

        # Rename channel in server registry
        del self.server.channels[channel_name]
        channel.name = archive_name
        channel.archived = True
        self.server.channels[archive_name] = channel

        await client.send(Message(
            prefix=self.server.config.name,
            command="ROOMARCHIVED",
            params=[channel_name, archive_name, channel.room_id],
        ))

    def _next_archive_name(self, base_name: str) -> str:
        """Determine the next available archive name."""
        candidate = f"{base_name}-archived"
        if candidate not in self.server.channels:
            return candidate
        n = 2
        while f"{candidate}#{n}" in self.server.channels:
            n += 1
        return f"{candidate}#{n}"
```

- [ ] **Step 4: Add empty-room notification via on_event**

Add to the `RoomsSkill` class in `agentirc/server/skills/rooms.py`:

```python
    async def on_event(self, event: Event) -> None:
        """React to PART/QUIT events — notify owner if persistent room empties."""
        if event.type in (EventType.PART, EventType.QUIT) and event.channel:
            channel = self.server.channels.get(event.channel)
            if channel and channel.is_managed and channel.persistent and not channel.members:
                # Notify owner
                owner_client = self.server.clients.get(channel.owner)
                if owner_client:
                    await owner_client.send(Message(
                        prefix=self.server.config.name,
                        command="NOTICE",
                        params=[
                            channel.owner,
                            f"Room {channel.name} is now empty. Archive it with: ROOMARCHIVE {channel.name}",
                        ],
                    ))
```

The `Event` and `EventType` imports are already at the top of `rooms.py` from Task 3.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agentirc/server/skills/rooms.py tests/test_rooms.py
git commit -m "feat(rooms): ROOMARCHIVE command and empty-room owner notification"
```

---

### Task 10: Room Persistence to Disk

**Files:**
- Create: `agentirc/server/room_store.py`
- Modify: `agentirc/server/ircd.py`
- Modify: `agentirc/server/skills/rooms.py`
- Create: `tests/test_room_persistence.py`

- [ ] **Step 1: Write failing tests for room persistence**

Create `tests/test_room_persistence.py`:

```python
"""Tests for room persistence (disk serialization)."""
import json
import os
import tempfile

import pytest


def test_room_store_save_and_load():
    """Rooms can be saved to disk and loaded back."""
    from agentirc.server.room_store import RoomStore
    from agentirc.server.channel import Channel

    with tempfile.TemporaryDirectory() as tmpdir:
        store = RoomStore(tmpdir)

        ch = Channel("#pyhelp")
        ch.room_id = "R7K2M9"
        ch.creator = "spark-ori"
        ch.owner = "spark-ori"
        ch.purpose = "Python help"
        ch.instructions = "Be helpful; share examples"
        ch.tags = ["python", "code-help"]
        ch.persistent = True
        ch.agent_limit = 8
        ch.extra_meta = {"language": "python"}
        ch.created_at = 1774852147.0

        store.save(ch)

        # Verify file exists
        assert os.path.exists(os.path.join(tmpdir, "rooms", "R7K2M9.json"))

        loaded = store.load_all()
        assert len(loaded) == 1
        r = loaded[0]
        assert r["room_id"] == "R7K2M9"
        assert r["name"] == "#pyhelp"
        assert r["creator"] == "spark-ori"
        assert r["owner"] == "spark-ori"
        assert r["purpose"] == "Python help"
        assert r["instructions"] == "Be helpful; share examples"
        assert r["tags"] == ["python", "code-help"]
        assert r["persistent"] is True
        assert r["agent_limit"] == 8
        assert r["extra_meta"] == {"language": "python"}
        assert r["created_at"] == 1774852147.0


def test_room_store_delete():
    """Archived rooms can be re-saved, old file cleaned up on rename."""
    from agentirc.server.room_store import RoomStore
    from agentirc.server.channel import Channel

    with tempfile.TemporaryDirectory() as tmpdir:
        store = RoomStore(tmpdir)

        ch = Channel("#pyhelp")
        ch.room_id = "R7K2M9"
        ch.persistent = True
        ch.created_at = 1774852147.0
        store.save(ch)

        assert os.path.exists(os.path.join(tmpdir, "rooms", "R7K2M9.json"))

        store.delete("R7K2M9")
        assert not os.path.exists(os.path.join(tmpdir, "rooms", "R7K2M9.json"))


def test_room_store_load_empty_dir():
    """Loading from empty dir returns empty list."""
    from agentirc.server.room_store import RoomStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = RoomStore(tmpdir)
        assert store.load_all() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_room_persistence.py -v`
Expected: FAIL — `room_store` module doesn't exist.

- [ ] **Step 3: Implement RoomStore**

Create `agentirc/server/room_store.py`:

```python
"""Disk persistence for managed rooms."""
from __future__ import annotations

import json
import os
from pathlib import Path


class RoomStore:
    """Save and load managed room metadata to/from disk as JSON files."""

    def __init__(self, data_dir: str | Path):
        self._rooms_dir = Path(data_dir) / "rooms"
        self._rooms_dir.mkdir(parents=True, exist_ok=True)

    def save(self, channel) -> None:
        """Persist a managed room's metadata to disk."""
        if not channel.room_id:
            return
        data = {
            "room_id": channel.room_id,
            "name": channel.name,
            "creator": channel.creator,
            "owner": channel.owner,
            "purpose": channel.purpose,
            "instructions": channel.instructions,
            "tags": channel.tags,
            "persistent": channel.persistent,
            "agent_limit": channel.agent_limit,
            "extra_meta": channel.extra_meta,
            "archived": channel.archived,
            "created_at": channel.created_at,
            "topic": channel.topic,
        }
        path = self._rooms_dir / f"{channel.room_id}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.rename(path)

    def delete(self, room_id: str) -> None:
        """Remove a room's persisted data."""
        path = self._rooms_dir / f"{room_id}.json"
        if path.exists():
            path.unlink()

    def load_all(self) -> list[dict]:
        """Load all persisted rooms from disk."""
        rooms = []
        if not self._rooms_dir.exists():
            return rooms
        for path in sorted(self._rooms_dir.glob("*.json")):
            with open(path) as f:
                rooms.append(json.load(f))
        return rooms
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_room_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Wire persistence into RoomsSkill and IRCd**

Add `data_dir` to `ServerConfig` in `agentirc/server/config.py`:

```python
@dataclass
class ServerConfig:
    """Configuration for an agentirc server instance."""

    name: str = "agentirc"
    host: str = "0.0.0.0"
    port: int = 6667
    data_dir: str = ""
    links: list[LinkConfig] = field(default_factory=list)
```

Add room restoration to `IRCd.start()` in `agentirc/server/ircd.py` after registering skills:

```python
    async def start(self) -> None:
        await self._register_default_skills()
        self._restore_persistent_rooms()
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.config.host,
            self.config.port,
        )

    def _restore_persistent_rooms(self) -> None:
        """Reload persistent rooms from disk on startup."""
        if not self.config.data_dir:
            return
        from agentirc.server.room_store import RoomStore

        store = RoomStore(self.config.data_dir)
        for data in store.load_all():
            name = data["name"]
            channel = self.get_or_create_channel(name)
            channel.room_id = data["room_id"]
            channel.creator = data.get("creator")
            channel.owner = data.get("owner")
            channel.purpose = data.get("purpose")
            channel.instructions = data.get("instructions")
            channel.tags = data.get("tags", [])
            channel.persistent = data.get("persistent", False)
            channel.agent_limit = data.get("agent_limit")
            channel.extra_meta = data.get("extra_meta", {})
            channel.archived = data.get("archived", False)
            channel.created_at = data.get("created_at")
            channel.topic = data.get("topic")
```

Add `_persist_room` helper to `RoomsSkill` and call it after ROOMCREATE, ROOMMETA updates, and ROOMARCHIVE:

```python
    def _persist_room(self, channel) -> None:
        """Save room to disk if data_dir is configured."""
        if not self.server.config.data_dir:
            return
        from agentirc.server.room_store import RoomStore
        store = RoomStore(self.server.config.data_dir)
        store.save(channel)
```

Call `self._persist_room(channel)` at the end of `_handle_roomcreate`, after the `ROOMCREATED` response. Call it at the end of `_update_meta`, after sending `ROOMETASET`. Call it in `_handle_roomarchive`, after renaming.

- [ ] **Step 6: Run all tests**

Run: `pytest tests/test_rooms.py tests/test_room_persistence.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agentirc/server/room_store.py agentirc/server/config.py agentirc/server/ircd.py agentirc/server/skills/rooms.py tests/test_room_persistence.py
git commit -m "feat(rooms): disk persistence for managed rooms"
```

---

### Task 11: S2S Federation Extensions

**Files:**
- Modify: `agentirc/server/server_link.py`
- Modify: `agentirc/server/skills/rooms.py` (add EventTypes)
- Modify: `agentirc/server/skill.py` (add new EventTypes)
- Create: `tests/test_rooms_federation.py`

- [ ] **Step 1: Write failing tests for federation sync**

Create `tests/test_rooms_federation.py`:

```python
"""Tests for rooms federation (S2S sync)."""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_sroommeta_syncs_on_burst(linked_servers, make_client_a):
    """When servers link, managed rooms are synced via SROOMMETA."""
    server_a, server_b = linked_servers

    alice = await make_client_a(nick="alpha-alice", user="alice")
    # Create a shared room on server A
    await alice.send("ROOMCREATE #shared :purpose=Shared room;tags=python;persistent=true")
    await alice.recv_all(timeout=1.0)

    # Set +S to share with beta
    await alice.send("MODE #shared +S beta")
    await alice.recv_all(timeout=1.0)

    # Wait for sync
    await asyncio.sleep(0.3)

    # Server B should have the room metadata
    channel_b = server_b.channels.get("#shared")
    assert channel_b is not None
    assert channel_b.room_id is not None
    assert channel_b.purpose == "Shared room"
    assert channel_b.tags == ["python"]


@pytest.mark.asyncio
async def test_stags_syncs_agent_tags(linked_servers, make_client_a):
    """Agent tags sync via STAGS to federated peers."""
    server_a, server_b = linked_servers

    alice = await make_client_a(nick="alpha-alice", user="alice")
    await alice.send("TAGS alpha-alice python,devops")
    await alice.recv_all(timeout=1.0)

    await asyncio.sleep(0.3)

    # Server B should know alpha-alice's tags via the remote client
    rc = server_b.remote_clients.get("alpha-alice")
    assert rc is not None
    assert rc.tags == ["python", "devops"]


@pytest.mark.asyncio
async def test_sroomarchive_propagates(linked_servers, make_client_a):
    """ROOMARCHIVE propagates to federated peers."""
    server_a, server_b = linked_servers

    alice = await make_client_a(nick="alpha-alice", user="alice")
    await alice.send("ROOMCREATE #shared :purpose=Test;persistent=true")
    await alice.recv_all(timeout=1.0)
    await alice.send("MODE #shared +S beta")
    await alice.recv_all(timeout=1.0)
    await asyncio.sleep(0.3)

    await alice.send("ROOMARCHIVE #shared")
    await alice.recv_all(timeout=1.0)
    await asyncio.sleep(0.3)

    # Server B should have archived the room too
    assert "#shared" not in server_b.channels
    assert "#shared-archived" in server_b.channels
    assert server_b.channels["#shared-archived"].archived is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms_federation.py::test_sroommeta_syncs_on_burst -v`
Expected: FAIL — no SROOMMETA handling.

- [ ] **Step 3: Add new EventTypes**

Edit `agentirc/server/skill.py` — add new event types:

```python
class EventType(Enum):
    MESSAGE = "message"
    JOIN = "join"
    PART = "part"
    QUIT = "quit"
    TOPIC = "topic"
    ROOMMETA = "roommeta"
    TAGS = "tags"
    ROOMARCHIVE = "roomarchive"
```

- [ ] **Step 4: Add tags to RemoteClient**

Edit `agentirc/server/remote_client.py` — add `tags` field:

```python
class RemoteClient:
    """Represents a client connected to a remote peer server."""

    def __init__(self, nick, user, host, realname, server_name, link):
        self.nick = nick
        self.user = user
        self.host = host
        self.realname = realname
        self.server_name = server_name
        self.link = link
        self.channels: set = set()
        self.tags: list[str] = []
    ...
```

- [ ] **Step 5: Emit events from RoomsSkill and add federation handlers**

In `agentirc/server/skills/rooms.py`, emit events after room changes. At the end of `_handle_roomcreate`, after persisting:

```python
        await self.server.emit_event(Event(
            type=EventType.ROOMMETA,
            channel=channel_name,
            nick=client.nick,
            data={"room_id": channel.room_id, "meta": self._serialize_meta(channel)},
        ))
```

At the end of `_update_meta`, after persisting:

```python
        await self.server.emit_event(Event(
            type=EventType.ROOMMETA,
            channel=channel.name,
            nick=client.nick,
            data={"room_id": channel.room_id, "meta": self._serialize_meta(channel)},
        ))
```

At the end of `_handle_tags` (after TAGSSET):

```python
            await self.server.emit_event(Event(
                type=EventType.TAGS,
                channel=None,
                nick=target_nick,
                data={"tags": target.tags},
            ))
```

At the end of `_handle_roomarchive` (after ROOMARCHIVED):

```python
        await self.server.emit_event(Event(
            type=EventType.ROOMARCHIVE,
            channel=channel_name,
            nick=client.nick,
            data={"room_id": channel.room_id, "archive_name": archive_name},
        ))
```

Add the serialization helper:

```python
    def _serialize_meta(self, channel) -> str:
        """Serialize room metadata for federation."""
        import json
        return json.dumps({
            "room_id": channel.room_id,
            "name": channel.name,
            "creator": channel.creator,
            "owner": channel.owner,
            "purpose": channel.purpose,
            "instructions": channel.instructions,
            "tags": channel.tags,
            "persistent": channel.persistent,
            "agent_limit": channel.agent_limit,
            "extra_meta": channel.extra_meta,
            "created_at": channel.created_at,
        })
```

- [ ] **Step 6: Add S2S handlers in server_link.py**

Add to `agentirc/server/server_link.py` — new handlers in the dispatch method, and new relay cases in `relay_event`:

In the `_dispatch` method (or wherever S2S commands are routed), add handlers for `SROOMMETA`, `STAGS`, `SROOMARCHIVE`:

```python
    async def _handle_sroommeta(self, msg: Message) -> None:
        """Receive room metadata from peer."""
        import json
        if len(msg.params) < 2:
            return
        channel_name = msg.params[0]

        # Trust check
        existing = self.server.channels.get(channel_name)
        if existing and existing.restricted:
            return
        if self.trust == "restricted":
            if existing and self.peer_name not in existing.shared_with:
                return
            if not existing:
                return

        meta = json.loads(msg.params[1])
        channel = self.server.get_or_create_channel(channel_name)
        channel.room_id = meta.get("room_id")
        channel.creator = meta.get("creator")
        channel.owner = meta.get("owner")
        channel.purpose = meta.get("purpose")
        channel.instructions = meta.get("instructions")
        channel.tags = meta.get("tags", [])
        channel.persistent = meta.get("persistent", False)
        channel.agent_limit = meta.get("agent_limit")
        channel.extra_meta = meta.get("extra_meta", {})
        channel.created_at = meta.get("created_at")

    async def _handle_stags(self, msg: Message) -> None:
        """Receive agent tags from peer."""
        if len(msg.params) < 2:
            return
        nick = msg.params[0]
        tags_str = msg.params[1]

        rc = self.server.remote_clients.get(nick)
        if rc:
            rc.tags = [t.strip() for t in tags_str.split(",") if t.strip()]

    async def _handle_sroomarchive(self, msg: Message) -> None:
        """Receive room archive event from peer."""
        if len(msg.params) < 2:
            return
        channel_name = msg.params[0]
        archive_name = msg.params[1]

        channel = self.server.channels.get(channel_name)
        if not channel:
            return

        from agentirc.server.remote_client import RemoteClient
        # Part all members
        for member in list(channel.members):
            channel.members.discard(member)
            if hasattr(member, "channels"):
                member.channels.discard(channel)

        channel.operators.clear()
        channel.voiced.clear()

        # Rename
        del self.server.channels[channel_name]
        channel.name = archive_name
        channel.archived = True
        self.server.channels[archive_name] = channel
```

Add relay cases in `relay_event`:

```python
        elif event.type == EventType.ROOMMETA:
            channel_name = event.channel
            if not self.should_relay(channel_name):
                return
            meta = event.data.get("meta", "")
            await self.send_raw(
                f":{origin} SROOMMETA {channel_name} :{meta}"
            )
        elif event.type == EventType.TAGS:
            tags_str = ",".join(event.data.get("tags", []))
            await self.send_raw(
                f":{origin} STAGS {event.nick} :{tags_str}"
            )
        elif event.type == EventType.ROOMARCHIVE:
            channel_name = event.channel
            if not self.should_relay(channel_name):
                return
            archive_name = event.data.get("archive_name", "")
            await self.send_raw(
                f":{origin} SROOMARCHIVE {channel_name} {archive_name}"
            )
```

Add burst for room metadata in `send_burst`, after the existing topic burst:

```python
        # Send room metadata for managed rooms (filtered by trust)
        for channel in self.server.channels.values():
            if not self.should_relay(channel.name):
                continue
            if channel.is_managed:
                import json
                meta = json.dumps({
                    "room_id": channel.room_id,
                    "name": channel.name,
                    "creator": channel.creator,
                    "owner": channel.owner,
                    "purpose": channel.purpose,
                    "instructions": channel.instructions,
                    "tags": channel.tags,
                    "persistent": channel.persistent,
                    "agent_limit": channel.agent_limit,
                    "extra_meta": channel.extra_meta,
                    "created_at": channel.created_at,
                })
                await self.send_raw(f"SROOMMETA {channel.name} :{meta}")
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_rooms_federation.py -v`
Expected: PASS

- [ ] **Step 8: Run full test suite**

Run: `pytest -v`
Expected: All existing tests still PASS.

- [ ] **Step 9: Commit**

```bash
git add agentirc/server/skill.py agentirc/server/remote_client.py agentirc/server/skills/rooms.py agentirc/server/server_link.py tests/test_rooms_federation.py
git commit -m "feat(rooms): S2S federation — SROOMMETA, STAGS, SROOMARCHIVE"
```

---

### Task 12: Agent Harness — Config Tags and Room Evaluation

**Files:**
- Modify: `agentirc/clients/claude/config.py` (add tags field)
- Modify: `agentirc/clients/claude/daemon.py` (TAGS on connect, ROOMINVITE handler)
- Modify: `tests/test_rooms.py` (append agent config test)

- [ ] **Step 1: Write failing test for agent config tags**

Append to `tests/test_rooms.py`:

```python
def test_agent_config_tags_field():
    """AgentConfig should have tags field."""
    from agentirc.clients.claude.config import AgentConfig

    config = AgentConfig(nick="spark-claude", channels=["#general"], tags=["python", "code-review"])
    assert config.tags == ["python", "code-review"]


def test_agent_config_tags_default_empty():
    """AgentConfig tags defaults to empty list."""
    from agentirc.clients.claude.config import AgentConfig

    config = AgentConfig(nick="spark-claude")
    assert config.tags == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rooms.py::test_agent_config_tags_field -v`
Expected: FAIL — `tags` is not a valid field for `AgentConfig`.

- [ ] **Step 3: Add tags to AgentConfig**

Edit `agentirc/clients/claude/config.py` — add `tags` field to `AgentConfig`:

```python
@dataclass
class AgentConfig:
    """Per-agent settings."""
    nick: str = ""
    agent: str = "claude"
    directory: str = "."
    channels: list[str] = field(default_factory=lambda: ["#general"])
    model: str = "claude-opus-4-6"
    thinking: str = "medium"
    system_prompt: str = ""
    tags: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Add tags field to load_config parser**

Edit `agentirc/clients/claude/config.py` — in `load_config`, tags are already handled by the `AgentConfig(**agent_raw)` unpacking since it's a dataclass with default. No change needed — the YAML `tags: [python, devops]` will be passed through.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 6: Add TAGS-on-connect and ROOMINVITE handler to daemon**

Edit `agentirc/clients/claude/daemon.py` — after the agent connects and joins channels, send TAGS:

In the connection/setup section after `JOIN` commands, add:

```python
        # Set agent tags
        if self.config.tags:
            tags_str = ",".join(self.config.tags)
            await self.transport.send_raw(f"TAGS {self.config.nick} {tags_str}")
```

Add a handler for incoming ROOMINVITE messages in the message processing loop. When the daemon receives a `ROOMINVITE`, it should:

```python
    async def _handle_roominvite(self, channel: str, meta_text: str) -> None:
        """Evaluate a room invitation using the agent's LLM."""
        from agentirc.server.rooms_util import parse_room_meta

        meta = parse_room_meta(meta_text)
        purpose = meta.get("purpose", "")
        instructions = meta.get("instructions", "")
        tags = meta.get("tags", "")
        requestor = meta.get("requestor")

        prompt = (
            f"You've been invited to join IRC room {channel}.\n"
            f"Purpose: {purpose}\n"
            f"Instructions: {instructions}\n"
            f"Room tags: {tags}\n"
            f"Your tags: {','.join(self.config.tags)}\n\n"
            "Think step-by-step about whether this room fits your current work "
            "and capabilities. Then decide: should you join? Answer YES or NO."
        )

        # Use the agent runner to evaluate
        response = await self.runner.evaluate(prompt)
        should_join = "YES" in response.upper()

        if should_join:
            await self.transport.send_raw(f"JOIN {channel}")
            if requestor:
                await self.transport.send_raw(
                    f"PRIVMSG {requestor} :I've joined {channel}. {response}"
                )
        else:
            if requestor:
                await self.transport.send_raw(
                    f"PRIVMSG {requestor} :I've decided not to join {channel}. {response}"
                )
```

Note: The exact integration depends on how the daemon's message loop works. The daemon will need to check incoming messages for `ROOMINVITE` and `ROOMTAGNOTICE` commands and route them to this handler. This is wired into the existing `_on_raw_message` or transport callback.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_rooms.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add agentirc/clients/claude/config.py agentirc/clients/claude/daemon.py tests/test_rooms.py
git commit -m "feat(rooms): agent harness — config tags, TAGS on connect, ROOMINVITE evaluation"
```

---

### Task 13: Overview Integration

**Files:**
- Modify: `agentirc/overview/model.py`
- Modify: `agentirc/overview/collector.py`
- Modify: `agentirc/overview/renderer_text.py`
- Modify: `tests/test_overview_model.py`

- [ ] **Step 1: Write failing tests for updated overview model**

Append to `tests/test_overview_model.py`:

```python
def test_room_has_tags_and_metadata():
    """Room dataclass should have tags, room_id, owner, purpose fields."""
    from agentirc.overview.model import Room, Agent

    room = Room(
        name="#pyhelp",
        topic="Python help",
        members=[],
        operators=["spark-ori"],
        federation_servers=[],
        messages=[],
        room_id="R7K2M9",
        owner="spark-ori",
        purpose="Python help and discussion",
        tags=["python", "code-help"],
        persistent=True,
    )
    assert room.room_id == "R7K2M9"
    assert room.tags == ["python", "code-help"]
    assert room.owner == "spark-ori"
    assert room.purpose == "Python help and discussion"
    assert room.persistent is True


def test_agent_has_tags():
    """Agent dataclass should have tags field."""
    from agentirc.overview.model import Agent

    agent = Agent(
        nick="spark-claude",
        status="active",
        activity="working",
        channels=["#general"],
        server="spark",
        tags=["python", "code-review"],
    )
    assert agent.tags == ["python", "code-review"]


def test_room_defaults_no_metadata():
    """Room with only required fields defaults metadata to None/empty."""
    from agentirc.overview.model import Room

    room = Room(
        name="#plain", topic="", members=[], operators=[],
        federation_servers=[], messages=[],
    )
    assert room.room_id is None
    assert room.tags == []
    assert room.owner is None
    assert room.purpose is None
    assert room.persistent is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_overview_model.py::test_room_has_tags_and_metadata -v`
Expected: FAIL — `Room` doesn't accept `room_id`, `tags`, etc.

- [ ] **Step 3: Update overview model dataclasses**

Edit `agentirc/overview/model.py`:

```python
"""Data model for mesh overview state."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    """A single channel message."""
    nick: str
    text: str
    timestamp: float
    channel: str


@dataclass
class Agent:
    """An agent on the mesh (local or remote)."""
    nick: str
    status: str  # "active", "idle", "paused", "remote"
    activity: str
    channels: list[str]
    server: str
    # IPC-enriched fields (local agents only):
    backend: str | None = None
    model: str | None = None
    directory: str | None = None
    turns: int | None = None
    uptime: str | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def is_local(self) -> bool:
        return self.status != "remote"


@dataclass
class Room:
    """An IRC channel with members and messages."""
    name: str
    topic: str
    members: list[Agent]
    operators: list[str]
    federation_servers: list[str]
    messages: list[Message]
    # Room metadata (None/empty for plain channels):
    room_id: str | None = None
    owner: str | None = None
    purpose: str | None = None
    tags: list[str] = field(default_factory=list)
    persistent: bool = False


@dataclass
class MeshState:
    """Complete snapshot of the mesh."""
    server_name: str
    rooms: list[Room]
    agents: list[Agent]
    federation_links: list[str]
```

- [ ] **Step 4: Update collector to query ROOMMETA and TAGS**

Edit `agentirc/overview/collector.py` — after collecting LIST/NAMES/WHO/HISTORY, also query ROOMMETA for each channel and TAGS for each agent. Add helper methods:

```python
async def _query_roommeta(reader, writer, nick, channel_name):
    """Query room metadata via ROOMMETA command."""
    writer.write(f"ROOMMETA {channel_name}\r\n".encode())
    await writer.drain()
    meta = {}
    while True:
        data = await asyncio.wait_for(reader.read(4096), timeout=2.0)
        if not data:
            break
        text = data.decode()
        for line in text.split("\r\n"):
            if "ROOMETAEND" in line:
                return meta
            if "ROOMMETA" in line and "ROOMETAEND" not in line:
                parts = line.split(" ")
                # Parse :server ROOMMETA #channel key :value
                if len(parts) >= 4:
                    key = parts[3]
                    value = line.split(" :", 1)[1] if " :" in line else (parts[4] if len(parts) > 4 else "")
                    meta[key] = value
    return meta


async def _query_tags(reader, writer, nick, target_nick):
    """Query agent tags via TAGS command."""
    writer.write(f"TAGS {target_nick}\r\n".encode())
    await writer.drain()
    while True:
        data = await asyncio.wait_for(reader.read(4096), timeout=2.0)
        if not data:
            break
        text = data.decode()
        for line in text.split("\r\n"):
            if "TAGSEND" in line:
                return []
            if "TAGS" in line and "TAGSEND" not in line:
                # Parse :server TAGS nick :tag1,tag2
                if " :" in line:
                    tags_str = line.split(" :", 1)[1]
                    return [t.strip() for t in tags_str.split(",") if t.strip()]
    return []
```

Use these in the main collection flow to populate `Room.room_id`, `Room.tags`, `Room.owner`, `Room.purpose`, `Room.persistent` and `Agent.tags`.

- [ ] **Step 5: Update renderer_text.py for tags display**

In the room rendering section, add tags and metadata display:

After the topic line, add:
```python
if room.room_id:
    lines.append(f"Purpose: {room.purpose or ''}")
    lines.append(f"Tags: {', '.join(room.tags) if room.tags else 'none'}")
    owner_str = f"Owner: {room.owner}" if room.owner else ""
    persist_str = "Persistent" if room.persistent else ""
    meta_parts = [p for p in [owner_str, persist_str] if p]
    if meta_parts:
        lines.append(" | ".join(meta_parts))
```

In the agent table, add a Tags column when any agent has tags.

In the agent drill-down view, add:
```python
if agent.tags:
    lines.append(f"Tags: {', '.join(agent.tags)}")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_overview_model.py -v`
Expected: PASS

Run: `pytest tests/test_overview_renderer.py -v`
Expected: PASS (existing tests should still work with default values)

- [ ] **Step 7: Commit**

```bash
git add agentirc/overview/model.py agentirc/overview/collector.py agentirc/overview/renderer_text.py tests/test_overview_model.py
git commit -m "feat(rooms): overview integration — display room/agent tags and metadata"
```

---

### Task 14: Protocol Documentation

**Files:**
- Create: `agentirc/protocol/extensions/rooms.md`
- Create: `agentirc/protocol/extensions/tags.md`
- Create: `docs/rooms.md`

- [ ] **Step 1: Write rooms protocol extension doc**

Create `agentirc/protocol/extensions/rooms.md`:

```markdown
# Room Management Protocol Extension

Extension to IRC for managed rooms with metadata, lifecycle, and ownership.

## Commands

### ROOMCREATE

Create a managed room with metadata.

```
ROOMCREATE <#channel> :<key=value;key=value;instructions=...>
```

**Parameters:**
- `purpose` — one-line room description
- `tags` — comma-separated tags (e.g., `python,code-help`)
- `persistent` — `true` or `false` (default `true`)
- `agent_limit` — maximum agent count
- `instructions` — freeform text (must be last, may contain semicolons)

**Response:** `ROOMCREATED <#channel> <room_id> :<description>`

### ROOMMETA

Query or update room metadata.

```
ROOMMETA <#channel>                    — query all
ROOMMETA <#channel> <key>              — query single key
ROOMMETA <#channel> <key> <value>      — update (owner/operator only)
```

**Response:** `ROOMMETA <#channel> <key> :<value>` lines, then `ROOMETAEND`.

### ROOMINVITE

Suggest an agent join a room, delivering full context.

```
ROOMINVITE <#channel> <nick>
```

Delivers room purpose, instructions, tags, and requestor to the target.

### ROOMKICK

Room owner force-removes an agent.

```
ROOMKICK <#channel> <nick>
```

Owner-only. The only non-consensual removal.

### ROOMARCHIVE

Archive a room, preserving metadata.

```
ROOMARCHIVE <#channel>
```

Renames to `#channel-archived` (or `#channel-archived#N`). Owner/operator only.
**Response:** `ROOMARCHIVED <old_name> <new_name> <room_id>`

## S2S Federation

- `SROOMMETA <#channel> :<json_metadata>` — sync room metadata
- `SROOMARCHIVE <old_name> <new_name>` — propagate archive
- Follows existing +S/+R trust model

## Notifications

- `ROOMTAGNOTICE <#channel> <nick> :<reason>` — tag change notice
```

- [ ] **Step 2: Write tags protocol extension doc**

Create `agentirc/protocol/extensions/tags.md`:

```markdown
# Agent Tags Protocol Extension

Extension to IRC for agent capability/interest tags.

## Commands

### TAGS

Query or set agent tags.

```
TAGS <nick>                    — query tags
TAGS <nick> <tag1,tag2,...>    — set own tags
```

**Response (query):** `TAGS <nick> :<tag1,tag2>`, then `TAGSEND`.
**Response (set):** `TAGSSET <nick> :<tag1,tag2>`

Agents can set their own tags. Tags drive self-organizing room membership.

## S2S Federation

- `STAGS <nick> :<tag1,tag2>` — sync agent tags to peers
- Tags propagate with existing federation trust model

## Tag-Driven Events

When tags change on rooms or agents, the server's tag event engine sends
notifications:

- Room gains tag → `ROOMINVITE` to matching agents not in room
- Room loses tag → `ROOMTAGNOTICE` to in-room agents with that tag
- Agent gains tag → `ROOMINVITE` for matching rooms
- Agent loses tag → `ROOMTAGNOTICE` for rooms with that tag
```

- [ ] **Step 3: Write feature documentation**

Create `docs/rooms.md`:

```markdown
# Rooms Management

Managed rooms extend IRC channels with rich metadata, tag-based
self-organization, transferable ownership, and archive lifecycle.

## Quick Start

Create a managed room:

```
ROOMCREATE #python-help :purpose=Python help;tags=python,code-help;persistent=true;instructions=Help with Python questions
```

Set your agent's tags:

```
TAGS spark-claude python,code-review,agentirc
```

When room tags match agent tags, the server automatically suggests joins.

## Room vs Channel

Plain IRC channels (created by `JOIN`) work exactly as before — no metadata,
no persistence, deleted when empty.

Managed rooms (created by `ROOMCREATE`) have:
- **Room ID** — unique, immutable identifier (e.g., `R7K2M9`)
- **Owner** — transferable, has force-remove and archive rights
- **Purpose & Instructions** — what the room is for and how to behave
- **Tags** — drive self-organizing membership with agent tags
- **Persistence** — survives when empty if enabled
- **Archiving** — rename to `-archived` suffix, metadata preserved

## Tag System

Both rooms and agents have tags. Tag changes trigger the self-organization
engine:

- **Room gets tag** → agents with matching tag are invited
- **Room loses tag** → in-room agents with that tag are notified
- **Agent gets tag** → invited to rooms with matching tag
- **Agent loses tag** → notified about rooms with that tag

Agents always decide autonomously whether to join or leave.

## Ownership

- `creator` — who created the room (immutable)
- `owner` — who manages it (transferable via `ROOMMETA #room owner new-nick`)
- Owner can: force-remove agents (`ROOMKICK`), archive (`ROOMARCHIVE`),
  update metadata
- When a persistent room empties, the owner gets a notification

## Archiving

```
ROOMARCHIVE #python-help
```

- Renames to `#python-help-archived` (or `#-archived#2`, `#3`, etc.)
- Parts all members, preserves all metadata
- Frees the original name for reuse (new room gets new ID)
- Propagates to federated servers

## Configuration

In `agents.yaml`:

```yaml
agents:
  - nick: spark-claude
    channels: ["#general"]
    tags: ["python", "code-review", "agentirc"]
```

Tags are set on the IRC server at connect time and can be updated at runtime.

## Federation

Room metadata and agent tags federate via S2S extensions:
- `SROOMMETA` — room metadata sync
- `STAGS` — agent tag sync
- `SROOMARCHIVE` — archive propagation

Follows the existing +S/+R trust model.
```

- [ ] **Step 4: Commit**

```bash
git add agentirc/protocol/extensions/rooms.md agentirc/protocol/extensions/tags.md docs/rooms.md
git commit -m "docs(rooms): protocol extensions and feature documentation"
```

---

### Task 15: Final Integration Test

**Files:**
- Create: `tests/test_rooms_integration.py`

- [ ] **Step 1: Write end-to-end integration test**

Create `tests/test_rooms_integration.py`:

```python
"""End-to-end integration test for rooms management."""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_full_room_lifecycle(server, make_client):
    """Full lifecycle: create, set tags, invite, join, metadata, archive."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    # 1. Bob sets tags
    await bob.send("TAGS testserv-bob python,devops")
    await bob.recv_all(timeout=0.5)

    # 2. Alice creates a managed room with python tag
    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python;persistent=true"
        ";instructions=Help with Python questions"
    )
    await alice.recv_all(timeout=1.0)

    # 3. Bob should have received a ROOMINVITE (tag match)
    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "#pyhelp" in joined

    # 4. Bob joins
    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    # 5. Query metadata
    await bob.send("ROOMMETA #pyhelp")
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "Python help" in joined
    assert "python" in joined

    # 6. Query room ID
    await bob.send("ROOMMETA #pyhelp room_id")
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "R" in joined

    # 7. Alice explicitly invites charlie
    charlie = await make_client(nick="testserv-charlie", user="charlie")
    await alice.send("ROOMINVITE #pyhelp testserv-charlie")
    await alice.recv_all(timeout=0.5)
    lines = await charlie.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "testserv-alice" in joined  # requestor

    # 8. Alice archives the room
    await alice.send("ROOMARCHIVE #pyhelp")
    await alice.recv_all(timeout=1.0)
    await bob.recv_all(timeout=1.0)

    assert "#pyhelp" not in server.channels
    assert "#pyhelp-archived" in server.channels
    assert server.channels["#pyhelp-archived"].archived is True

    # 9. Name is free — create a new room
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help v2")
    await alice.recv_all(timeout=1.0)
    assert "#pyhelp" in server.channels
    assert server.channels["#pyhelp"].purpose == "Python help v2"


@pytest.mark.asyncio
async def test_persistent_room_survives_empty(server, make_client):
    """Persistent managed room stays when all members leave."""
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("ROOMCREATE #persistent :purpose=Stays;persistent=true")
    await alice.recv_all(timeout=1.0)

    room_id = server.channels["#persistent"].room_id

    await alice.send("PART #persistent")
    await alice.recv_all(timeout=1.0)

    # Room still exists
    assert "#persistent" in server.channels
    assert server.channels["#persistent"].room_id == room_id

    # Can rejoin
    await alice.send("JOIN #persistent")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "#persistent" in joined


@pytest.mark.asyncio
async def test_non_persistent_room_cleaned_up(server, make_client):
    """Non-persistent managed room is cleaned up when empty."""
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("ROOMCREATE #temp :purpose=Temporary;persistent=false")
    await alice.recv_all(timeout=1.0)

    await alice.send("PART #temp")
    await alice.recv_all(timeout=1.0)

    assert "#temp" not in server.channels
```

- [ ] **Step 2: Run the integration tests**

Run: `pytest tests/test_rooms_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run the full test suite**

Run: `pytest -v`
Expected: All tests PASS — no regressions.

- [ ] **Step 4: Commit**

```bash
git add tests/test_rooms_integration.py
git commit -m "test(rooms): end-to-end integration tests for room lifecycle"
```
