# Mesh Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface mesh events (server lifecycle, agent lifecycle, channel lifecycle, room lifecycle, console lifecycle, federation link) as IRCv3-tagged PRIVMSGs from a reserved `system-<servername>` pseudo-user — stored in channel history, consumable by bots as triggers, federated across the mesh, and composable via bot-emitted custom events.

**Architecture:** IRCv3 `message-tags` is added to the parser. Events are surfaced as PRIVMSG from `system-<servername>` with `@event=<type>;event-data=<b64-json>` tags, routed to the relevant channel (channel-scoped) or `#system` (global). A new `SEVENT` S2S verb federates generic events. Bots gain `trigger.type: event` with a safe filter DSL (`==`, `!=`, `in`, `and`, `or`, `not`, dotted access) and `output.fires_event` for pub/sub composition. A system bot (`system-<servername>-welcome`) ships as reference implementation.

**Tech Stack:** Python 3.12+, asyncio, pytest + pytest-asyncio + pytest-xdist, dataclasses, Jinja2 (already a dep via bots), PyYAML, base64/json (stdlib).

**Spec:** `docs/superpowers/specs/2026-04-15-mesh-events-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `culture/constants.py` | **New** — `SYSTEM_USER_PREFIX`, `SYSTEM_CHANNEL`, `EVENT_TAG_TYPE`, `EVENT_TAG_DATA`, reserved-nick regex |
| `culture/protocol/message.py` | Add `tags: dict[str, str]` to `Message`; parse/format IRCv3 tag block with escape rules |
| `culture/protocol/commands.py` | Add `SEVENT` verb constant |
| `culture/agentirc/skill.py` | Extend `EventType` enum with new built-ins; allow `Event.type` to be `EventType \| str` for custom types |
| `culture/agentirc/events.py` | **New** — event-type string registry, render templates, `validate_event_type()`, `event_type_to_wire()` |
| `culture/agentirc/ircd.py` | CAP advertising; reserved-nick rejection; `system-<server>` bootstrap; `#system` bootstrap; `server.wake/sleep` emission; tagged-PRIVMSG surfacing inside `emit_event()` |
| `culture/agentirc/client.py` | `CAP LS`/`CAP REQ` handling for `message-tags`; tag-stripping send path; emit `agent.connect/disconnect` and `console.open/close` |
| `culture/agentirc/server_link.py` | `SEVENT` relay + ingest; `events/1` capability negotiation; `server.link/unlink` emission |
| `culture/agentirc/skills/icons.py` | Add `console` ICON value |
| `culture/agentirc/skills/rooms.py` | Emit `room.create` on ROOMCREATE |
| `culture/bots/filter_dsl.py` | **New** — safe expression parser + evaluator, `FilterParseError` |
| `culture/bots/config.py` | Accept `trigger_type=event`, `event_filter`, `output.fires_event` |
| `culture/bots/bot_manager.py` | Event subscriber; system bot loader; dispatch event-triggered bots |
| `culture/bots/bot.py` | Render and emit `fires_event` after `handle()` posts; per-bot rate limit |
| `culture/bots/system/__init__.py` | **New** — system bot loader entry point |
| `culture/bots/system/welcome/bot.yaml` | **New** — welcome bot config |
| `culture/bots/system/welcome/handler.py` | **New** — welcome bot handler |
| `culture/clients/claude/irc_transport.py` | Parse IRCv3 tags on inbound PRIVMSG |
| `culture/clients/codex/irc_transport.py` | Parse IRCv3 tags on inbound PRIVMSG |
| `culture/clients/copilot/irc_transport.py` | Parse IRCv3 tags on inbound PRIVMSG |
| `culture/clients/acp/irc_transport.py` | Parse IRCv3 tags on inbound PRIVMSG |
| `packages/agent-harness/irc_transport.py` | Reference copy — same tag parsing |
| `culture/mesh_config.py` | Add `system_bots.welcome.enabled` config path |
| `culture/protocol/extensions/events.md` | **New** — wire protocol documentation |
| `docs/features/events.md` | **New** — feature doc including the three use-case flows |
| `tests/test_message_tags.py` | **New** — tag parse/format round-trip |
| `tests/test_filter_dsl.py` | **New** — DSL parser + evaluator |
| `tests/test_events_catalog.py` | **New** — event-type regex + render templates |
| `tests/test_events_basic.py` | **New** — end-to-end emission |
| `tests/test_events_history.py` | **New** — HISTORY replay of events |
| `tests/test_events_federation.py` | **New** — SEVENT cross-server |
| `tests/test_events_bot_trigger.py` | **New** — event-triggered bot fires |
| `tests/test_events_bot_chain.py` | **New** — bot A fires event, bot B triggers |
| `tests/test_events_reserved_nick.py` | **New** — `system-*` rejection |
| `tests/test_events_cap_fallback.py` | **New** — non-tag clients get plain body |
| `tests/test_welcome_bot.py` | **New** — welcome bot reference test |
| `CHANGELOG.md`, `pyproject.toml` | Minor version bump |

---

### Task 1: Wire constants

**Files:**

- Create: `culture/constants.py`
- Modify: `culture/protocol/commands.py`

No tests needed — pure string/enum constants consumed by later tasks.

- [ ] **Step 1: Create `culture/constants.py`**

```python
"""Project-wide constants. Keep strings here, never in source code."""

from __future__ import annotations

import re

# System pseudo-user and channel
SYSTEM_USER_PREFIX = "system-"
SYSTEM_CHANNEL = "#system"

# IRCv3 message-tag keys we emit/consume
EVENT_TAG_TYPE = "event"
EVENT_TAG_DATA = "event-data"

# Peer link capability (server-to-server)
PEER_CAPABILITY_EVENTS = "events/1"

# Reserved-nick pattern: any nick starting with `system-` is server-owned.
RESERVED_NICK_RE = re.compile(r"^system-[a-zA-Z0-9][a-zA-Z0-9\-]*$")

# Event-type name regex (dotted lowercase, ≥2 segments)
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")
```

- [ ] **Step 2: Add `SEVENT` verb constant to `culture/protocol/commands.py`**

Append after the existing `STHREADCLOSE = "STHREADCLOSE"` line:

```python
SEVENT = "SEVENT"
```

- [ ] **Step 3: Commit**

```bash
git add culture/constants.py culture/protocol/commands.py
git commit -m "feat(events): add wire constants and SEVENT verb"
```

---

### Task 2: IRCv3 message-tags in the protocol parser

**Files:**

- Modify: `culture/protocol/message.py`
- Create: `tests/test_message_tags.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_message_tags.py`:

```python
"""IRCv3 message-tags parsing and formatting round-trips."""

from culture.protocol.message import Message


def test_parse_single_tag():
    m = Message.parse("@event=user.join :nick!u@h PRIVMSG #c :hi\r\n")
    assert m.tags == {"event": "user.join"}
    assert m.prefix == "nick!u@h"
    assert m.command == "PRIVMSG"
    assert m.params == ["#c", "hi"]


def test_parse_multiple_tags():
    m = Message.parse("@a=1;b=2;c=3 PING :x\r\n")
    assert m.tags == {"a": "1", "b": "2", "c": "3"}
    assert m.command == "PING"


def test_parse_tag_without_value():
    m = Message.parse("@flag :x!u@h PRIVMSG #c :msg\r\n")
    assert m.tags == {"flag": ""}


def test_parse_tag_value_escapes():
    # IRCv3 escapes: \: → ; ; \s → space ; \\ → \ ; \r → CR ; \n → LF
    m = Message.parse(r"@k=a\:b\sc\\d\r\ne PING :x" + "\r\n")
    assert m.tags == {"k": "a;b c\\d\r\ne"}


def test_parse_no_tags():
    m = Message.parse(":nick PRIVMSG #c :body\r\n")
    assert m.tags == {}


def test_format_with_tags():
    m = Message(
        tags={"event": "user.join", "event-data": "eyJuIjoxfQ=="},
        prefix="system-spark!system@spark",
        command="PRIVMSG",
        params=["#system", "ori joined"],
    )
    line = m.format()
    assert line.startswith("@")
    assert "event=user.join" in line
    assert "event-data=eyJuIjoxfQ==" in line
    assert " :system-spark!system@spark PRIVMSG #system :ori joined\r\n" in line


def test_format_without_tags_omits_prefix():
    m = Message(tags={}, prefix="x", command="PING", params=["y"])
    assert m.format() == ":x PING y\r\n"


def test_format_escapes_tag_value():
    m = Message(
        tags={"k": "a;b c\\d\r\ne"},
        prefix=None,
        command="PING",
        params=["x"],
    )
    line = m.format()
    assert r"k=a\:b\sc\\d\r\ne" in line


def test_round_trip():
    original = "@event=user.join;event-data=e30= :n!u@h PRIVMSG #c :hello world\r\n"
    m = Message.parse(original)
    assert m.format() == original
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_message_tags.py -v
```

Expected: all fail — `Message` has no `tags` field.

- [ ] **Step 3: Update `Message` dataclass and parser/formatter**

Replace `culture/protocol/message.py` with:

```python
from dataclasses import dataclass, field


_TAG_UNESCAPE = {
    "\\:": ";",
    "\\s": " ",
    "\\\\": "\\",
    "\\r": "\r",
    "\\n": "\n",
}
_TAG_ESCAPE = {v: k for k, v in _TAG_UNESCAPE.items()}


def _unescape_tag_value(value: str) -> str:
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            two = value[i : i + 2]
            if two in _TAG_UNESCAPE:
                out.append(_TAG_UNESCAPE[two])
                i += 2
                continue
        out.append(value[i])
        i += 1
    return "".join(out)


def _escape_tag_value(value: str) -> str:
    out = []
    for ch in value:
        if ch in _TAG_ESCAPE:
            out.append(_TAG_ESCAPE[ch])
        else:
            out.append(ch)
    return "".join(out)


@dataclass
class Message:
    """An IRC protocol message per RFC 2812 §2.3.1 with IRCv3 message-tags.

    Wire format: [@tags SPACE] [:prefix SPACE] command [params] CRLF
    """

    prefix: str | None = None
    command: str = ""
    params: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, line: str) -> "Message":
        line = line.rstrip("\r\n")
        tags: dict[str, str] = {}

        if line.startswith("@"):
            if " " not in line:
                return cls(tags={}, prefix=None, command="", params=[])
            tag_blob, line = line[1:].split(" ", 1)
            for piece in tag_blob.split(";"):
                if not piece:
                    continue
                if "=" in piece:
                    key, value = piece.split("=", 1)
                    tags[key] = _unescape_tag_value(value)
                else:
                    tags[piece] = ""

        prefix = None
        if line.startswith(":"):
            if " " not in line:
                return cls(tags=tags, prefix=None, command="", params=[])
            prefix, line = line.split(" ", 1)
            prefix = prefix[1:]

        trailing = None
        if " :" in line:
            line, trailing = line.split(" :", 1)

        parts = line.split()
        if not parts:
            return cls(tags=tags, prefix=prefix, command="", params=[])
        command = parts[0].upper()
        params = parts[1:]
        if trailing is not None:
            params.append(trailing)

        return cls(tags=tags, prefix=prefix, command=command, params=params)

    def format(self) -> str:
        parts = []

        if self.tags:
            tag_pieces = []
            for key, value in self.tags.items():
                if value == "":
                    tag_pieces.append(key)
                else:
                    tag_pieces.append(f"{key}={_escape_tag_value(value)}")
            parts.append("@" + ";".join(tag_pieces))

        if self.prefix:
            parts.append(f":{self.prefix}")
        parts.append(self.command)

        if self.params:
            for param in self.params[:-1]:
                parts.append(param)
            last = self.params[-1]
            if " " in last or not last or last.startswith(":"):
                parts.append(f":{last}")
            else:
                parts.append(last)

        return " ".join(parts) + "\r\n"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_message_tags.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Run the existing protocol tests to verify no regression**

```bash
uv run pytest tests/test_connection.py tests/test_messaging.py tests/test_channel.py -v
```

Expected: all pass. If any fail due to signature changes, fix callers that pass positional `prefix` (now a kwarg default).

- [ ] **Step 6: Commit**

```bash
git add culture/protocol/message.py tests/test_message_tags.py
git commit -m "feat(protocol): add IRCv3 message-tags support to Message"
```

---

### Task 3: CAP negotiation for `message-tags`

**Files:**

- Modify: `culture/agentirc/client.py`
- Create: `tests/test_events_cap_fallback.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_events_cap_fallback.py`:

```python
"""CAP negotiation for message-tags + plain-body fallback for non-tag clients."""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_cap_ls_lists_message_tags(server, make_client):
    c = await make_client("testserv-alice")
    await c.send("CAP LS\r\n")
    line = await c.recv_until("CAP")
    assert "message-tags" in line


@pytest.mark.asyncio
async def test_cap_req_ack(server, make_client):
    c = await make_client("testserv-alice")
    await c.send("CAP LS\r\n")
    await c.recv_until("CAP")
    await c.send("CAP REQ :message-tags\r\n")
    line = await c.recv_until("CAP")
    assert "ACK" in line
    assert "message-tags" in line


@pytest.mark.asyncio
async def test_non_tag_client_receives_plain_privmsg(server, make_client):
    """A client that never REQs message-tags should not receive @tag blocks."""
    c = await make_client("testserv-alice")
    # Do not send CAP REQ. Server will strip tags.
    # Force the server to emit a tagged PRIVMSG by triggering an event.
    # Use `JOIN #testchan`, which will surface as tagged PRIVMSG once
    # events are wired through (later tasks). For now, the inverse check:
    # just verify that lines arrive without a leading '@' when the
    # client has not opted in.
    await c.send("JOIN #testchan\r\n")
    async for line in c.iter_lines(timeout=1.0):
        assert not line.startswith("@"), f"unexpected tagged line: {line}"
        if "JOIN" in line:
            break
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_events_cap_fallback.py -v
```

Expected: first two fail (CAP LS not advertising `message-tags`); third may pass trivially (no tags surfaced yet).

- [ ] **Step 3: Add CAP handling in `culture/agentirc/client.py`**

Locate the command dispatch in `client.py` (look for the `CAP` handler — add one if absent). Add a `caps: set[str]` attribute to the client and:

```python
# In Client.__init__
self.caps: set[str] = set()

# New method
async def _handle_cap(self, msg):
    sub = msg.params[0].upper() if msg.params else ""
    if sub == "LS":
        await self.send_raw(f":{self.server.name} CAP {self.nick or '*'} LS :message-tags\r\n")
    elif sub == "REQ":
        requested = msg.params[1].split() if len(msg.params) >= 2 else []
        supported = {"message-tags"}
        if all(cap in supported for cap in requested):
            self.caps.update(requested)
            await self.send_raw(
                f":{self.server.name} CAP {self.nick or '*'} ACK :{' '.join(requested)}\r\n"
            )
        else:
            await self.send_raw(
                f":{self.server.name} CAP {self.nick or '*'} NAK :{' '.join(requested)}\r\n"
            )
    elif sub == "END":
        pass  # no registration-gating in v1
```

Wire it in the command dispatch dict (search for where commands like `NICK`, `USER` are dispatched) with:

```python
"CAP": self._handle_cap,
```

- [ ] **Step 4: Add tag-stripping send path**

In `Client.send_message(msg: Message)` (or equivalent — search for where Messages are serialized to the socket):

```python
async def send_message(self, msg: Message) -> None:
    if msg.tags and "message-tags" not in self.caps:
        msg = Message(
            tags={},
            prefix=msg.prefix,
            command=msg.command,
            params=list(msg.params),
        )
    await self.send_raw(msg.format())
```

If the codebase already uses `send_raw(string)` and does not pass through `Message`, add a `send_tagged(msg: Message)` helper used specifically for event surfacing. Search the code for the existing send path and adopt it.

- [ ] **Step 5: Run tests to verify passes**

```bash
uv run pytest tests/test_events_cap_fallback.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add culture/agentirc/client.py tests/test_events_cap_fallback.py
git commit -m "feat(events): add CAP message-tags negotiation and tag-strip fallback"
```

---

### Task 4: Reserved `system-*` nick rejection

**Files:**

- Modify: `culture/agentirc/client.py` (NICK handler)
- Create: `tests/test_events_reserved_nick.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_events_reserved_nick.py`:

```python
"""Clients cannot take nicks starting with `system-`."""

import pytest


@pytest.mark.asyncio
async def test_reserved_nick_rejected(server, make_client):
    c = await make_client("testserv-alice")
    await c.send("NICK system-testserv\r\n")
    line = await c.recv_until("432")
    assert "432" in line
    assert "system-testserv" in line


@pytest.mark.asyncio
async def test_reserved_nick_rejected_for_any_server(server, make_client):
    c = await make_client("testserv-alice")
    for target in ["system-thor", "system-spark-welcome", "system-foo-bar-baz"]:
        await c.send(f"NICK {target}\r\n")
        line = await c.recv_until("432")
        assert "432" in line


@pytest.mark.asyncio
async def test_normal_nick_still_accepted(server, make_client):
    c = await make_client("testserv-alice")
    # Already registered; NICK change to a valid name must work.
    await c.send("NICK testserv-alice2\r\n")
    line = await c.recv_until("NICK")
    assert "NICK" in line
    assert "432" not in line
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_events_reserved_nick.py -v
```

Expected: fail — reserved prefix not enforced.

- [ ] **Step 3: Enforce in NICK handler**

In `culture/agentirc/client.py`, find the `NICK` handler (grep for `def _handle_nick` or `async def _handle_nick`). Add, before existing nick-format validation:

```python
from culture.constants import RESERVED_NICK_RE

# ... inside _handle_nick, after extracting `new_nick` from params:
if RESERVED_NICK_RE.match(new_nick):
    await self.send_numeric(
        "432",
        new_nick,
        "Nick reserved for system messages",
    )
    return
```

If `send_numeric` is not the exact helper name, search for how other `432` responses are sent and match that pattern.

- [ ] **Step 4: Run tests to verify passes**

```bash
uv run pytest tests/test_events_reserved_nick.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add culture/agentirc/client.py tests/test_events_reserved_nick.py
git commit -m "feat(events): reserve system-* nick prefix"
```

---

### Task 5: `system-<server>` VirtualClient and `#system` bootstrap

**Files:**

- Modify: `culture/agentirc/ircd.py`
- Extend: `tests/test_events_reserved_nick.py`

- [ ] **Step 1: Extend test — verify system user and channel exist**

Append to `tests/test_events_reserved_nick.py`:

```python
@pytest.mark.asyncio
async def test_system_user_exists(server, make_client):
    """A `system-<servername>` virtual user is registered on server start."""
    c = await make_client("testserv-alice")
    await c.send("WHOIS system-testserv\r\n")
    reply = await c.recv_until("318")  # RPL_ENDOFWHOIS
    assert "system-testserv" in reply
    assert "311" in reply  # RPL_WHOISUSER — user exists


@pytest.mark.asyncio
async def test_system_channel_exists(server, make_client):
    """`#system` exists and system-<server> is a member."""
    c = await make_client("testserv-alice")
    await c.send("LIST #system\r\n")
    reply = await c.recv_until("323")
    assert "#system" in reply

    await c.send("NAMES #system\r\n")
    names = await c.recv_until("366")
    assert "system-testserv" in names
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_events_reserved_nick.py -v
```

Expected: the two new tests fail — no system user, no #system channel.

- [ ] **Step 3: Bootstrap system user and channel in IRCd**

In `culture/agentirc/ircd.py`, after `_register_default_skills()` call during startup (look for the startup sequence in `IRCd.start()`), add:

```python
from culture.constants import SYSTEM_CHANNEL, SYSTEM_USER_PREFIX
# VirtualClient already imported from culture.bots; if not, import it.
from culture.bots import VirtualClient

async def _bootstrap_system_identity(self) -> None:
    """Create the `system-<servername>` pseudo-user and the `#system` channel."""
    system_nick = f"{SYSTEM_USER_PREFIX}{self.name}"
    # Register a VirtualClient with no bot-owner (server-owned).
    self.system_client = VirtualClient(
        nick=system_nick,
        user="system",
        host=self.name,
        realname="Culture system messages",
        server=self,
    )
    self.clients[system_nick] = self.system_client

    # Auto-create #system and join system user.
    channel = self.get_or_create_channel(SYSTEM_CHANNEL)
    channel.members.add(self.system_client)
    self.system_client.channels.add(SYSTEM_CHANNEL)
```

Call it in startup after `_register_default_skills`:

```python
await self._bootstrap_system_identity()
```

If `VirtualClient` lives elsewhere or has a different constructor, adapt the attributes — the fields shown (nick, user, host, realname, server) map to whatever that class uses. Check `culture/bots/__init__.py` or grep for `class VirtualClient`.

- [ ] **Step 4: Run tests to verify passes**

```bash
uv run pytest tests/test_events_reserved_nick.py -v
```

Expected: all pass (five total).

- [ ] **Step 5: Run full protocol test suite for regressions**

```bash
uv run pytest tests/test_connection.py tests/test_channel.py tests/test_messaging.py -v
```

Expected: all pass. If NAMES/LIST output changed shape, fix the test assertions upstream.

- [ ] **Step 6: Commit**

```bash
git add culture/agentirc/ircd.py tests/test_events_reserved_nick.py
git commit -m "feat(events): bootstrap system-<server> user and #system channel"
```

---

### Task 6: Event catalog and type validator

**Files:**

- Create: `culture/agentirc/events.py`
- Modify: `culture/agentirc/skill.py`
- Create: `tests/test_events_catalog.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_events_catalog.py`:

```python
"""Event type validation and render-template registry."""

import pytest

from culture.agentirc.events import (
    INVALID_EVENT_TYPE,
    render_event,
    validate_event_type,
)


@pytest.mark.parametrize(
    "name",
    [
        "user.join",
        "agent.connect",
        "server.link",
        "welcome-bot.greeted",
        "a.b",
        "triage-bot.classified",
    ],
)
def test_valid_types(name):
    assert validate_event_type(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "nodots",
        "UPPERCASE.bad",
        ".leadingdot",
        "trailing.",
        "double..dot",
        "space in.name",
        "!.x",
    ],
)
def test_invalid_types(name):
    assert validate_event_type(name) is False


def test_render_builtin_user_join():
    body = render_event("user.join", {"nick": "ori"}, channel="#general")
    assert body == "ori joined #general"


def test_render_builtin_agent_connect():
    body = render_event("agent.connect", {"nick": "spark-claude"}, channel="#system")
    assert body == "spark-claude connected"


def test_render_unknown_type_falls_back():
    body = render_event("unknown.thing", {"k": "v"}, channel="#x")
    # Fallback should include the type name and the data dict.
    assert "unknown.thing" in body
    assert "k" in body
    assert "v" in body


def test_render_template_crash_falls_back(monkeypatch):
    from culture.agentirc import events as mod

    def boom(data, channel):
        raise RuntimeError("render broken")

    monkeypatch.setitem(mod._TEMPLATES, "user.join", boom)
    body = render_event("user.join", {"nick": "ori"}, channel="#x")
    # Render failure falls back to raw shape, not an exception.
    assert "user.join" in body
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_events_catalog.py -v
```

Expected: fail — module doesn't exist.

- [ ] **Step 3: Implement `culture/agentirc/events.py`**

```python
"""Event type catalog and render-template registry.

Event type names follow the dotted-lowercase convention enforced by
`EVENT_TYPE_RE` in `culture.constants`. Render templates map a type to a
function that produces the human-readable PRIVMSG body for humans and
vanilla IRC clients. The structured payload always rides on the `@event`
and `@event-data` tags — the rendered body is presentation only.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from culture.constants import EVENT_TYPE_RE

logger = logging.getLogger(__name__)

INVALID_EVENT_TYPE = "invalid.event-type"

RenderFn = Callable[[dict[str, Any], str | None], str]

_TEMPLATES: dict[str, RenderFn] = {}


def register(event_type: str, fn: RenderFn) -> None:
    _TEMPLATES[event_type] = fn


def validate_event_type(name: str) -> bool:
    return bool(EVENT_TYPE_RE.match(name))


def render_event(event_type: str, data: dict[str, Any], channel: str | None) -> str:
    fn = _TEMPLATES.get(event_type)
    if fn is None:
        return f"{event_type} {data}"
    try:
        return fn(data, channel)
    except Exception:
        logger.exception("render template for %s failed", event_type)
        return f"{event_type} {data}"


# -------- built-in render templates --------

def _nick_action(verb: str) -> RenderFn:
    def _render(data, channel):
        nick = data.get("nick", "<unknown>")
        if channel:
            return f"{nick} {verb} {channel}"
        return f"{nick} {verb}"
    return _render


register("user.join", _nick_action("joined"))
register("user.part", _nick_action("left"))
register("user.quit", lambda d, c: f"{d.get('nick', '<unknown>')} quit: {d.get('reason', '')}".rstrip(": "))

register("agent.connect", lambda d, c: f"{d.get('nick', '<unknown>')} connected")
register("agent.disconnect", lambda d, c: f"{d.get('nick', '<unknown>')} disconnected")

register("console.open", lambda d, c: f"{d.get('nick', '<unknown>')} opened a console")
register("console.close", lambda d, c: f"{d.get('nick', '<unknown>')} closed their console")

register("server.wake", lambda d, c: f"server {d.get('server', '<unknown>')} is up")
register("server.sleep", lambda d, c: f"server {d.get('server', '<unknown>')} is shutting down")
register("server.link", lambda d, c: f"linked to {d.get('peer', '<unknown>')}")
register("server.unlink", lambda d, c: f"unlinked from {d.get('peer', '<unknown>')}")

register("room.create", lambda d, c: f"{d.get('nick', '<unknown>')} created room {c}")
register("room.archive", lambda d, c: f"{d.get('nick', '<unknown>')} archived {c}")
register("room.meta", lambda d, c: f"{d.get('nick', '<unknown>')} updated {c} metadata")

register("thread.create", lambda d, c: f"{d.get('nick', '<unknown>')} started thread [{d.get('thread', '?')}] in {c}")
register("thread.message", lambda d, c: f"[{d.get('thread', '?')}] {d.get('nick', '<unknown>')}: {d.get('text', '')}")
register("thread.close", lambda d, c: f"thread [{d.get('thread', '?')}] in {c} closed")

register("tags.update", lambda d, c: f"{d.get('nick', '<unknown>')} tags → {', '.join(d.get('tags', []))}")
```

- [ ] **Step 4: Extend `EventType` enum in `culture/agentirc/skill.py`**

After existing values, add:

```python
    # Lifecycle + link events introduced by mesh-events feature.
    AGENT_CONNECT = "agent.connect"
    AGENT_DISCONNECT = "agent.disconnect"
    CONSOLE_OPEN = "console.open"
    CONSOLE_CLOSE = "console.close"
    SERVER_WAKE = "server.wake"
    SERVER_SLEEP = "server.sleep"
    SERVER_LINK = "server.link"
    SERVER_UNLINK = "server.unlink"
    ROOM_CREATE = "room.create"
```

Keep existing values (MESSAGE, JOIN, PART, QUIT, TOPIC, ROOMMETA, TAGS, ROOMARCHIVE, THREAD_CREATE, THREAD_MESSAGE, THREAD_CLOSE) as-is. Their `value` strings stay the same to avoid breaking HistorySkill.

- [ ] **Step 5: Run tests to verify passes**

```bash
uv run pytest tests/test_events_catalog.py -v
```

Expected: all 16 pass.

- [ ] **Step 6: Commit**

```bash
git add culture/agentirc/events.py culture/agentirc/skill.py tests/test_events_catalog.py
git commit -m "feat(events): add event catalog, render templates, and type validator"
```

---

### Task 7: Surface events as tagged PRIVMSG from `system-<server>`

**Files:**

- Modify: `culture/agentirc/ircd.py`
- Create: `tests/test_events_basic.py`

- [ ] **Step 1: Write failing end-to-end test**

Create `tests/test_events_basic.py`:

```python
"""End-to-end: `emit_event` surfaces a tagged PRIVMSG from `system-<server>`."""

import asyncio
import base64
import json

import pytest

from culture.agentirc.skill import Event, EventType


@pytest.mark.asyncio
async def test_event_surfaces_as_tagged_privmsg(server, make_client):
    """A tag-capable client in #system receives a tagged PRIVMSG on emit_event."""
    c = await make_client("testserv-alice")
    await c.send("CAP LS\r\n")
    await c.recv_until("CAP")
    await c.send("CAP REQ :message-tags\r\n")
    await c.recv_until("CAP")
    await c.send("JOIN #system\r\n")
    await c.recv_until("JOIN")

    # Simulate a server-originated event.
    ev = Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="system-testserv",
        data={"nick": "testserv-bob"},
    )
    await server.emit_event(ev)

    line = await c.recv_until("PRIVMSG")
    assert line.startswith("@")
    assert "event=agent.connect" in line
    assert "event-data=" in line
    assert ":system-testserv!" in line
    assert " PRIVMSG #system :" in line
    assert "testserv-bob connected" in line


@pytest.mark.asyncio
async def test_channel_scoped_event_goes_to_channel(server, make_client):
    """A channel-scoped event is posted to its channel, not #system."""
    c = await make_client("testserv-alice")
    await c.send("CAP REQ :message-tags\r\n")
    await c.recv_until("CAP")
    await c.send("JOIN #general\r\n")
    await c.recv_until("JOIN")

    ev = Event(
        type=EventType.JOIN,
        channel="#general",
        nick="testserv-bob",
        data={"nick": "testserv-bob"},
    )
    await server.emit_event(ev)

    line = await c.recv_until("event=")
    assert " PRIVMSG #general :" in line
    assert "event=join" in line or "event=user.join" in line


@pytest.mark.asyncio
async def test_event_data_is_base64_json(server, make_client):
    c = await make_client("testserv-alice")
    await c.send("CAP REQ :message-tags\r\n")
    await c.recv_until("CAP")
    await c.send("JOIN #system\r\n")
    await c.recv_until("JOIN")

    ev = Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="system-testserv",
        data={"nick": "testserv-bob"},
    )
    await server.emit_event(ev)

    line = await c.recv_until("event-data=")
    # Extract the tag value
    tags = line.split(" ", 1)[0][1:]
    data_piece = [p for p in tags.split(";") if p.startswith("event-data=")][0]
    b64 = data_piece.split("=", 1)[1]
    decoded = json.loads(base64.b64decode(b64))
    assert decoded["nick"] == "testserv-bob"
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_events_basic.py -v
```

Expected: fail — `emit_event` does not produce PRIVMSG yet.

- [ ] **Step 3: Implement PRIVMSG surfacing inside `emit_event`**

In `culture/agentirc/ircd.py`, replace the existing `emit_event` body (keep outer signature `async def emit_event(self, event: Event) -> None`):

```python
import base64
import json

from culture.agentirc.events import render_event
from culture.constants import (
    EVENT_TAG_DATA,
    EVENT_TAG_TYPE,
    SYSTEM_CHANNEL,
    SYSTEM_USER_PREFIX,
)
from culture.protocol.message import Message

async def emit_event(self, event: Event) -> None:
    # 1) Sequence + log.
    seq = self.next_seq()
    self._event_log.append((seq, event))

    # 2) Run skill hooks.
    for skill in self.skills:
        try:
            await skill.on_event(event)
        except Exception:
            logger.exception("Skill %s failed on event %s", skill.name, event.type)

    # 3) Relay to linked peers (locally-originated only).
    if not event.data.get("_origin"):
        for peer_name, link in list(self.links.items()):
            try:
                await link.relay_event(event)
            except Exception:
                logger.exception("Failed to relay event to %s", peer_name)

    # 4) Surface as tagged PRIVMSG from system-<server>.
    await self._surface_event_privmsg(event)

async def _surface_event_privmsg(self, event: Event) -> None:
    """Render the event as a tagged PRIVMSG into the appropriate channel."""
    type_wire = event.type.value if hasattr(event.type, "value") else str(event.type)

    # Channel routing: channel-scoped events go to their channel,
    # global events go to #system.
    target = event.channel or SYSTEM_CHANNEL

    # Origin: for federated events, the system user of the origin server.
    origin_server = event.data.get("_origin") or self.name
    system_nick = f"{SYSTEM_USER_PREFIX}{origin_server}"

    # Encode payload (exclude _origin and _render internal keys).
    payload = {k: v for k, v in event.data.items() if not k.startswith("_")}
    encoded = base64.b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")

    body = event.data.get("_render") or render_event(type_wire, payload, event.channel)

    msg = Message(
        tags={EVENT_TAG_TYPE: type_wire, EVENT_TAG_DATA: encoded},
        prefix=f"{system_nick}!system@{origin_server}",
        command="PRIVMSG",
        params=[target, body],
    )

    channel = self.channels.get(target)
    if channel is None:
        return  # nothing to surface into

    for member in list(channel.members):
        # VirtualClients (bots, system user) receive events via subscription,
        # not by resending the PRIVMSG. Skip them here.
        if getattr(member, "is_virtual", False):
            continue
        try:
            if hasattr(member, "send_message"):
                await member.send_message(msg)
            else:
                # Fallback: strip tags when member lacks cap-awareness.
                plain = Message(
                    tags={},
                    prefix=msg.prefix,
                    command=msg.command,
                    params=list(msg.params),
                )
                await member.send_raw(plain.format())
        except Exception:
            logger.exception("Failed to surface event to %s", getattr(member, "nick", "?"))
```

If `VirtualClient` lacks `is_virtual = True`, add it (class attribute `is_virtual: bool = True` on VirtualClient, `is_virtual: bool = False` on Client/RemoteClient). Alternatively use `isinstance` — adapt to codebase convention.

- [ ] **Step 4: Run tests to verify passes**

```bash
uv run pytest tests/test_events_basic.py -v
```

Expected: all three pass.

- [ ] **Step 5: Run existing skill/history tests to catch regressions**

```bash
uv run pytest tests/test_skills.py tests/test_history.py tests/test_threads.py tests/test_rooms.py -v
```

Expected: all pass. If `HistorySkill.on_event` now stores a duplicate line for events (because events were always stored as MESSAGE before), investigate — the new PRIVMSG-surfacing path runs in addition to the existing MESSAGE-event path, but it should not cause duplicate storage because we don't emit a separate MESSAGE for the surfaced PRIVMSG — it's a direct client send, not a routed PRIVMSG command.

- [ ] **Step 6: Commit**

```bash
git add culture/agentirc/ircd.py tests/test_events_basic.py
git commit -m "feat(events): surface emit_event as tagged PRIVMSG from system-<server>"
```

---

### Task 8: `server.wake` and `server.sleep`

**Files:**

- Modify: `culture/agentirc/ircd.py`

- [ ] **Step 1: Write test**

Append to `tests/test_events_basic.py`:

```python
@pytest.mark.asyncio
async def test_server_wake_emitted_on_start(server, make_client):
    """server.wake is emitted and visible in #system history."""
    c = await make_client("testserv-alice")
    await c.send("CAP REQ :message-tags\r\n")
    await c.recv_until("CAP")
    await c.send("JOIN #system\r\n")
    await c.recv_until("JOIN")
    await c.send("HISTORY RECENT #system 50\r\n")
    history = await c.recv_until("HISTORYEND")
    assert "server.wake" in history or "server testserv is up" in history
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_events_basic.py::test_server_wake_emitted_on_start -v
```

Expected: fail — `server.wake` never emitted.

- [ ] **Step 3: Emit `server.wake` after bootstrap**

In `culture/agentirc/ircd.py`, after `_bootstrap_system_identity()` in startup:

```python
await self.emit_event(Event(
    type=EventType.SERVER_WAKE,
    channel=None,
    nick=f"{SYSTEM_USER_PREFIX}{self.name}",
    data={"server": self.name},
))
```

In `IRCd.stop()`, **before** closing links and channels, emit `server.sleep`:

```python
try:
    await self.emit_event(Event(
        type=EventType.SERVER_SLEEP,
        channel=None,
        nick=f"{SYSTEM_USER_PREFIX}{self.name}",
        data={"server": self.name},
    ))
except Exception:
    logger.exception("failed to emit server.sleep")
# existing shutdown sequence follows
```

- [ ] **Step 4: Run tests to verify passes**

```bash
uv run pytest tests/test_events_basic.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add culture/agentirc/ircd.py tests/test_events_basic.py
git commit -m "feat(events): emit server.wake and server.sleep"
```

---

### Task 9: `agent.connect` / `agent.disconnect`

**Design decision (Option B — LOCKED):** Events are triggered by user mode `+A` (agent),
not `ICON agent`. `ICON` retains its display-only role. Mode `+A` transitions OFF→ON emit
`agent.connect`; transitions ON→OFF or disconnect emit `agent.disconnect`.
All five agent-backend transports send `MODE <nick> +A` after welcome.

**Files:**

- Modify: `culture/agentirc/client.py` (extend `_handle_user_mode`, add `"C"` to accepted modes)
- Modify: `culture/agentirc/ircd.py` (`_remove_client` made async, emits on disconnect)
- Modify: `culture/clients/claude/irc_transport.py` (add `MODE +A` after welcome)
- Modify: `culture/clients/codex/irc_transport.py` (add `MODE +A` after welcome)
- Modify: `culture/clients/copilot/irc_transport.py` (add `MODE +A` after welcome)
- Modify: `culture/clients/acp/irc_transport.py` (add `MODE +A` after welcome)
- Modify: `packages/agent-harness/irc_transport.py` (add `MODE +A` after welcome)
- New: `tests/test_events_lifecycle.py`

- [x] **Step 1: Write tests**

Create `tests/test_events_lifecycle.py` with 10 integration tests (8 emitted from this task, plus `+HC` combined and pre-registration-gate tests added during review-and-fix). Key cases:

```python
@pytest.mark.asyncio
async def test_agent_connect_on_mode_a(server, make_client):
    """MODE +A causes agent.connect to be delivered to #system subscribers."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +A")

    line = await alice.recv_until("event=agent.connect")
    assert "event=agent.connect" in line
    assert "testserv-bob connected" in line


@pytest.mark.asyncio
async def test_agent_disconnect_on_close(server, make_client):
    """A client with +A that closes the TCP connection triggers agent.disconnect."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +A")
    await alice.recv_until("event=agent.connect")

    await bob.close()
    line = await alice.recv_until("event=agent.disconnect")
    assert "testserv-bob disconnected" in line
```

- [x] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_events_lifecycle.py -v
```

Expected: 8 fail (events not emitted), 1 pass (H/B no-op test). (Two further tests — `+HC` combined and pre-registration gate — are added during the review-and-fix cycle.)

- [x] **Step 3: Emit `agent.connect` and `agent.disconnect`**

In `culture/agentirc/client.py`, extend `_handle_user_mode`:

- Accepted mode chars extended from `("H", "A", "B")` to `("H", "A", "B", "C")`.
- Before mutating `self.modes`, capture `had = ch in self.modes`; after mutation
  capture `now = ch in self.modes`; emit on OFF→ON or ON→OFF edge for `A` and `C`.
- Emissions happen AFTER `self.modes` mutation, BEFORE RPL_UMODEIS reply.

```python
elif ch in ("H", "A", "B", "C"):
    had = ch in self.modes
    if adding:
        self.modes.add(ch)
    else:
        self.modes.discard(ch)
    now = ch in self.modes
    if ch == "A" and not had and now:
        pending_events.append(EventType.AGENT_CONNECT)
    elif ch == "A" and had and not now:
        pending_events.append(EventType.AGENT_DISCONNECT)
```

In `culture/agentirc/ircd.py`, convert `_remove_client` from `def` to `async def`
and emit lifecycle events based on modes set at disconnect time:

```python
async def _remove_client(self, client: Client) -> None:
    # ... existing channel cleanup ...
    if "A" in getattr(client, "modes", set()):
        try:
            await self.emit_event(Event(type=EventType.AGENT_DISCONNECT, ...))
        except Exception:
            logger.exception("Failed to emit agent.disconnect for %s", nick)
```

Update the single caller (`_accept_c2s_connection`) to `await self._remove_client(client)`.

- [x] **Step 4: All-backends transport update**

In `_on_welcome` of all five transport files, add after the ICON block:

```python
await self._send_raw(f"MODE {self.nick} +A")
```

- [x] **Step 5: Run tests**

```bash
uv run pytest tests/test_events_lifecycle.py -v
```

Expected: all 10 pass (Task 9's 8 emission tests + `+HC` combined + pre-registration gate).

- [x] **Step 6: Commit** (combined with Task 10 — see end of Task 10)

---

### Task 10: `console.open` / `console.close` via user mode `+C`

**Design decision (Option B — LOCKED):** Events are triggered by user mode `+C` (console),
not `ICON console`. `ICON` retains its display-only role. Mode `+C` transitions OFF→ON emit
`console.open`; transitions ON→OFF or disconnect emit `console.close`.
The console client sends `MODE +C` automatically after registration.

**Files:**

- Modify: `culture/agentirc/client.py` (mode `+C` edge detection — done in Task 9's change)
- Modify: `culture/agentirc/ircd.py` (disconnect emit for `"C"` — done in Task 9's change)
- Modify: `culture/console/client.py` (default `mode` changed from `"H"` to `"HC"`)
- Modify: `culture/cli/mesh.py` (instantiation changed from `mode="H"` to `mode="HC"`)

The combined `+HC` value preserves the human-identity flag alongside the new
console role. The server processes multi-char modestrings per character, so
`MODE <nick> +HC` sets both flags in one round trip and emits `console.open`
exactly once (only the `+C` edge triggers an event; `+H` has none).

- [x] **Step 1: Write tests**

In `tests/test_events_lifecycle.py` (same file as Task 9), add:

```python
@pytest.mark.asyncio
async def test_console_open_on_mode_c(server, make_client):
    """MODE +C causes console.open to be delivered to #system subscribers."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +C")

    line = await alice.recv_until("event=console.open")
    assert "event=console.open" in line
    assert "testserv-bob opened a console" in line


@pytest.mark.asyncio
async def test_console_close_on_disconnect(server, make_client):
    """A client with +C that closes the TCP connection triggers console.close."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +C")
    await alice.recv_until("event=console.open")

    await bob.close()
    line = await alice.recv_until("event=console.close")
    assert "testserv-bob closed their console" in line
```

- [x] **Step 2: Emit console.open/close in `_handle_user_mode`**

The `+C` edge detection was added alongside `+A` in Task 9's `_handle_user_mode` change.
No additional server-side changes needed.

- [x] **Step 3: Update `culture console` client to send `MODE +C` at startup**

`culture/console/client.py` already sends `MODE {nick} +{mode}` after RPL_WELCOME
via its `mode` constructor parameter. Change the default from `"H"` to `"C"`:

```python
def __init__(self, ..., mode: str = "C", ...) -> None:
```

Update `culture/cli/mesh.py` (the production instantiation):

```python
client = ConsoleIRCClient(host=host, port=port, nick=nick, mode="C")
```

- [x] **Step 4: Run tests**

```bash
uv run pytest tests/test_events_lifecycle.py -v
```

Expected: all 10 pass (Task 9's 8 emission tests + `+HC` combined + pre-registration gate).

- [x] **Step 5: Commit**

```bash
git add culture/agentirc/client.py culture/agentirc/ircd.py \
        culture/clients/claude/irc_transport.py \
        culture/clients/codex/irc_transport.py \
        culture/clients/copilot/irc_transport.py \
        culture/clients/acp/irc_transport.py \
        packages/agent-harness/irc_transport.py \
        culture/console/client.py culture/cli/mesh.py \
        tests/test_events_lifecycle.py \
        docs/superpowers/plans/2026-04-15-mesh-events.md
git commit -m "feat(events): emit agent.connect/disconnect and console.open/close via user modes +A and +C"
```

---

### Task 11: `room.create` emission

**Files:**

- Modify: `culture/agentirc/skills/rooms.py`

- [ ] **Step 1: Write test**

Add to `tests/test_events_basic.py`:

```python
@pytest.mark.asyncio
async def test_room_create_emitted(server, make_client):
    alice = await make_client("testserv-alice")
    await alice.send("CAP REQ :message-tags\r\n")
    await alice.recv_until("CAP")
    await alice.send('ROOMCREATE #research "AI research" :daily sync\r\n')
    line = await alice.recv_until("event=room.create")
    assert "testserv-alice created room #research" in line
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_events_basic.py::test_room_create_emitted -v
```

Expected: fail.

- [ ] **Step 3: Emit in RoomsSkill.on_command**

In `culture/agentirc/skills/rooms.py`, find the ROOMCREATE handler (after it successfully creates the room and stores it) and add:

```python
await self.server.emit_event(Event(
    type=EventType.ROOM_CREATE,
    channel=room_name,
    nick=client.nick,
    data={"nick": client.nick, "room": room_name, "purpose": purpose},
))
```

Adapt variable names to match the handler's locals.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_events_basic.py tests/test_rooms.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add culture/agentirc/skills/rooms.py tests/test_events_basic.py
git commit -m "feat(events): emit room.create on ROOMCREATE"
```

---

### Task 12: `SEVENT` federation relay

**Files:**

- Modify: `culture/agentirc/server_link.py`
- Create: `tests/test_events_federation.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_events_federation.py`:

```python
"""Events federate across linked servers via SEVENT."""

import pytest

from culture.agentirc.skill import Event, EventType


@pytest.mark.asyncio
async def test_event_federates_to_peer(linked_servers, make_client_b):
    """An event on server A is surfaced on server B's #system."""
    alpha, beta = linked_servers

    b = await make_client_b("beta-bob")
    await b.send("CAP REQ :message-tags\r\n")
    await b.recv_until("CAP")
    await b.send("JOIN #system\r\n")
    await b.recv_until("JOIN")

    # Emit on alpha.
    ev = Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="system-alpha",
        data={"nick": "alpha-claude"},
    )
    await alpha.emit_event(ev)

    line = await b.recv_until("event=agent.connect")
    # Origin in the nick is alpha, not beta.
    assert ":system-alpha!" in line
    assert "alpha-claude connected" in line


@pytest.mark.asyncio
async def test_federated_event_does_not_loop(linked_servers, make_client_a, make_client_b):
    """A federated event surfaces once on each side — no loop."""
    alpha, beta = linked_servers

    a = await make_client_a("alpha-alice")
    await a.send("CAP REQ :message-tags\r\n")
    await a.recv_until("CAP")
    await a.send("JOIN #system\r\n")
    await a.recv_until("JOIN")

    b = await make_client_b("beta-bob")
    await b.send("CAP REQ :message-tags\r\n")
    await b.recv_until("CAP")
    await b.send("JOIN #system\r\n")
    await b.recv_until("JOIN")

    await alpha.emit_event(Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="system-alpha",
        data={"nick": "alpha-claude"},
    ))

    # Each side sees exactly one PRIVMSG line mentioning the event.
    a_count = await a.count_until_idle("event=agent.connect", seconds=1.0)
    b_count = await b.count_until_idle("event=agent.connect", seconds=1.0)
    assert a_count == 1
    assert b_count == 1
```

If `count_until_idle` doesn't exist in the test helper, add it to `conftest.py`:

```python
async def count_until_idle(client, marker, seconds=1.0):
    count = 0
    try:
        async for line in client.iter_lines(timeout=seconds):
            if marker in line:
                count += 1
    except asyncio.TimeoutError:
        pass
    return count
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_events_federation.py -v
```

Expected: fail.

- [ ] **Step 3: Add `SEVENT` dispatch in `server_link.py`**

In the S2S command handler dict, add:

```python
from culture.protocol.commands import SEVENT
# ...
SEVENT: self._handle_sevent,
```

Implement `_handle_sevent`:

```python
import base64
import json

async def _handle_sevent(self, msg: Message) -> None:
    """Ingest a federated event from a peer."""
    # Format: SEVENT <origin-server> <seq> <type> <channel_or_*> :<b64-json-data>
    if len(msg.params) < 5:
        return
    origin, _seq, type_str, target, b64 = msg.params[:5]
    channel = None if target == "*" else target
    try:
        data = json.loads(base64.b64decode(b64))
    except Exception:
        logger.exception("SEVENT bad payload from %s", self.peer_name)
        return

    data["_origin"] = origin

    # Map the type string back to an EventType if known; keep as string otherwise.
    try:
        type_enum = EventType(type_str)
    except ValueError:
        type_enum = type_str  # custom event; stays as string

    ev = Event(
        type=type_enum,
        channel=channel,
        nick=data.get("nick", f"system-{origin}"),
        data=data,
    )
    await self.server.emit_event(ev)
```

Extend `relay_event()` to include a generic fallback for events not covered by the existing typed relays. Inside `relay_event()`, after the existing `_RELAY_DISPATCH` lookup, add:

```python
# If no typed relay exists, fall back to generic SEVENT.
if handler is None:
    type_str = event.type.value if hasattr(event.type, "value") else str(event.type)
    payload = {k: v for k, v in event.data.items() if not k.startswith("_")}
    encoded = base64.b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    target = event.channel or "*"
    seq = self.server._seq  # current local seq; peer stores but doesn't re-sequence
    line = (
        f"SEVENT {self.server.name} {seq} {type_str} {target} :{encoded}\r\n"
    )
    await self._send_raw(line)
    return
```

(Placement depends on the existing `_RELAY_DISPATCH` lookup shape — adapt around it.)

- [ ] **Step 4: Emit `server.link` / `server.unlink` at link state changes**

In `server_link.py`, find the handshake completion (grep for `self.state = "ESTABLISHED"` or similar). After that:

```python
await self.server.emit_event(Event(
    type=EventType.SERVER_LINK,
    channel=None,
    nick=f"{SYSTEM_USER_PREFIX}{self.server.name}",
    data={"peer": self.peer_name, "trust": self.trust},
))
```

At the link teardown path (search for where `self.links.pop(...)` happens or in the disconnect cleanup):

```python
await self.server.emit_event(Event(
    type=EventType.SERVER_UNLINK,
    channel=None,
    nick=f"{SYSTEM_USER_PREFIX}{self.server.name}",
    data={"peer": peer_name},
))
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_events_federation.py tests/test_federation.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add culture/agentirc/server_link.py tests/test_events_federation.py tests/conftest.py
git commit -m "feat(events): federate events via SEVENT S2S verb + server.link/unlink"
```

---

### Task 13: `HistorySkill` stores events → HISTORY replay

**Files:**

- Modify: `culture/agentirc/skills/history.py`
- Create: `tests/test_events_history.py`

HistorySkill already listens for `MESSAGE` events and stores channel messages. Events surfaced as PRIVMSG by the new `_surface_event_privmsg()` are *direct sends to clients*, not routed via `MESSAGE` emit — so they would be invisible to HistorySkill as-is. Fix: have HistorySkill store any emitted event that has a target channel (channel-scoped events) or that lands in `#system` (global events), using the rendered body as the message text.

- [ ] **Step 1: Write failing test**

Create `tests/test_events_history.py`:

```python
"""Events appear in HISTORY RECENT replays."""

import pytest

from culture.agentirc.skill import Event, EventType


@pytest.mark.asyncio
async def test_event_appears_in_history(server, make_client):
    alice = await make_client("testserv-alice")
    await alice.send("CAP REQ :message-tags\r\n")
    await alice.recv_until("CAP")

    # Emit an event before the client joins.
    await server.emit_event(Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="system-testserv",
        data={"nick": "testserv-claude"},
    ))

    await alice.send("JOIN #system\r\n")
    await alice.recv_until("JOIN")
    await alice.send("HISTORY RECENT #system 50\r\n")
    history = await alice.recv_until("HISTORYEND")
    assert "testserv-claude connected" in history


@pytest.mark.asyncio
async def test_channel_event_in_channel_history(server, make_client):
    alice = await make_client("testserv-alice")
    await alice.send("CAP REQ :message-tags\r\n")
    await alice.recv_until("CAP")
    await alice.send("JOIN #room\r\n")
    await alice.recv_until("JOIN")

    await server.emit_event(Event(
        type=EventType.ROOM_CREATE,
        channel="#room",
        nick="testserv-bob",
        data={"nick": "testserv-bob", "room": "#room"},
    ))

    await alice.send("HISTORY RECENT #room 50\r\n")
    history = await alice.recv_until("HISTORYEND")
    assert "created room #room" in history
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_events_history.py -v
```

Expected: fail — events aren't stored in history.

- [ ] **Step 3: Store events in HistorySkill**

In `culture/agentirc/skills/history.py`, extend `on_event`:

```python
from culture.agentirc.events import render_event
from culture.constants import SYSTEM_CHANNEL, SYSTEM_USER_PREFIX

async def on_event(self, event: Event) -> None:
    # Existing MESSAGE handling — keep as-is.
    if event.type == EventType.MESSAGE:
        # ... existing code ...
        return

    # New: store other events as history entries on their target channel.
    target = event.channel or SYSTEM_CHANNEL
    type_wire = event.type.value if hasattr(event.type, "value") else str(event.type)
    origin = event.data.get("_origin") or self.server.name
    nick = f"{SYSTEM_USER_PREFIX}{origin}"
    payload = {k: v for k, v in event.data.items() if not k.startswith("_")}
    body = event.data.get("_render") or render_event(type_wire, payload, event.channel)

    await self._store_message(
        channel=target,
        nick=nick,
        text=body,
        timestamp=event.timestamp,
    )
```

`_store_message` signature should match whatever HistorySkill uses internally for the MESSAGE path — find the method and reuse it. If the existing code inlines storage, extract into a helper first.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_events_history.py tests/test_history.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add culture/agentirc/skills/history.py tests/test_events_history.py
git commit -m "feat(events): HistorySkill stores events so HISTORY replays them"
```

---

### Task 14: Filter DSL parser and evaluator

**Files:**

- Create: `culture/bots/filter_dsl.py`
- Create: `tests/test_filter_dsl.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_filter_dsl.py`:

```python
"""Bot filter DSL — safe expressions over event dicts."""

import pytest

from culture.bots.filter_dsl import (
    FilterParseError,
    compile_filter,
    evaluate,
)


def event(**kw):
    d = {"type": "user.join", "channel": "#general", "data": {"nick": "ori"}}
    d.update(kw)
    return d


def test_equality():
    f = compile_filter("type == 'user.join'")
    assert evaluate(f, event()) is True
    assert evaluate(f, event(type="user.part")) is False


def test_and():
    f = compile_filter("type == 'user.join' and channel == '#general'")
    assert evaluate(f, event()) is True
    assert evaluate(f, event(channel="#other")) is False


def test_or():
    f = compile_filter("type == 'user.join' or type == 'user.part'")
    assert evaluate(f, event()) is True
    assert evaluate(f, event(type="user.part")) is True
    assert evaluate(f, event(type="server.link")) is False


def test_not():
    f = compile_filter("not (type == 'user.join')")
    assert evaluate(f, event()) is False
    assert evaluate(f, event(type="user.part")) is True


def test_in_list():
    f = compile_filter("type in ['server.link', 'server.unlink']")
    assert evaluate(f, event(type="server.link")) is True
    assert evaluate(f, event(type="user.join")) is False


def test_dotted_field():
    f = compile_filter("data.nick == 'ori'")
    assert evaluate(f, event()) is True
    assert evaluate(f, event(data={"nick": "bob"})) is False


def test_missing_field_is_false():
    f = compile_filter("data.missing == 'x'")
    assert evaluate(f, event()) is False


def test_in_string_membership():
    f = compile_filter("'research' in data.tags")
    ev = event(data={"tags": ["research", "ai"]})
    assert evaluate(f, ev) is True
    ev2 = event(data={"tags": ["games"]})
    assert evaluate(f, ev2) is False


def test_parens_for_precedence():
    f = compile_filter("(type == 'a' or type == 'b') and channel == '#c'")
    assert evaluate(f, event(type="a", channel="#c")) is True
    assert evaluate(f, event(type="b", channel="#c")) is True
    assert evaluate(f, event(type="a", channel="#other")) is False


def test_parse_error_message():
    with pytest.raises(FilterParseError) as exc:
        compile_filter("type = 'x'")  # single '=' invalid
    # Error carries position + expected hint.
    assert exc.value.column >= 0
    assert exc.value.expected


def test_parse_error_unclosed_string():
    with pytest.raises(FilterParseError):
        compile_filter("type == 'unclosed")


def test_parse_error_no_function_calls():
    with pytest.raises(FilterParseError):
        compile_filter("exec('x')")
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_filter_dsl.py -v
```

Expected: fail — module doesn't exist.

- [ ] **Step 3: Implement filter DSL**

Create `culture/bots/filter_dsl.py`:

```python
"""Safe expression DSL for bot event filters.

Grammar (recursive descent):

    expr       := or_expr
    or_expr    := and_expr ('or' and_expr)*
    and_expr   := not_expr ('and' not_expr)*
    not_expr   := 'not' not_expr | cmp_expr
    cmp_expr   := atom (('==' | '!=' | 'in') atom)?
    atom       := STRING | NUMBER | LIST | IDENT ('.' IDENT)* | '(' expr ')'
    LIST       := '[' [atom (',' atom)*] ']'

Evaluates against a dict (the event). Missing fields short-circuit to
`_MISSING`, which compares `False` to everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_MISSING = object()


class FilterParseError(Exception):
    def __init__(self, message: str, column: int, expected: str = "") -> None:
        super().__init__(f"{message} at col {column} (expected {expected})")
        self.column = column
        self.expected = expected


# -------- AST nodes --------

@dataclass
class Literal:
    value: Any


@dataclass
class FieldRef:
    parts: tuple[str, ...]


@dataclass
class ListExpr:
    items: list


@dataclass
class Compare:
    op: str  # '==', '!=', 'in'
    left: Any
    right: Any


@dataclass
class And:
    left: Any
    right: Any


@dataclass
class Or:
    left: Any
    right: Any


@dataclass
class Not:
    expr: Any


# -------- tokenizer --------

class _Tok:
    STRING = "STRING"
    NUMBER = "NUMBER"
    IDENT = "IDENT"
    OP = "OP"
    KW = "KW"
    LP = "LP"
    RP = "RP"
    LBR = "LBR"
    RBR = "RBR"
    COMMA = "COMMA"
    DOT = "DOT"
    END = "END"


_KEYWORDS = {"and", "or", "not", "in"}


def _tokenize(src: str) -> list[tuple]:
    tokens = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "'":
            end = src.find("'", i + 1)
            if end == -1:
                raise FilterParseError("unterminated string", i, "closing quote")
            tokens.append((_Tok.STRING, src[i + 1 : end], i))
            i = end + 1
            continue
        if ch.isdigit():
            j = i
            while j < n and src[j].isdigit():
                j += 1
            tokens.append((_Tok.NUMBER, int(src[i:j]), i))
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_-"):
                j += 1
            word = src[i:j]
            if word in _KEYWORDS:
                tokens.append((_Tok.KW, word, i))
            else:
                tokens.append((_Tok.IDENT, word, i))
            i = j
            continue
        if ch == "=" and i + 1 < n and src[i + 1] == "=":
            tokens.append((_Tok.OP, "==", i))
            i += 2
            continue
        if ch == "!" and i + 1 < n and src[i + 1] == "=":
            tokens.append((_Tok.OP, "!=", i))
            i += 2
            continue
        if ch == "(":
            tokens.append((_Tok.LP, "(", i)); i += 1; continue
        if ch == ")":
            tokens.append((_Tok.RP, ")", i)); i += 1; continue
        if ch == "[":
            tokens.append((_Tok.LBR, "[", i)); i += 1; continue
        if ch == "]":
            tokens.append((_Tok.RBR, "]", i)); i += 1; continue
        if ch == ",":
            tokens.append((_Tok.COMMA, ",", i)); i += 1; continue
        if ch == ".":
            tokens.append((_Tok.DOT, ".", i)); i += 1; continue
        raise FilterParseError(f"unexpected character {ch!r}", i, "operator / identifier")
    tokens.append((_Tok.END, "", n))
    return tokens


# -------- parser --------

class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos]

    def consume(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, kind, expected_label):
        tok = self.peek()
        if tok[0] != kind:
            raise FilterParseError(f"unexpected {tok[1]!r}", tok[2], expected_label)
        return self.consume()

    def parse(self):
        expr = self._or()
        if self.peek()[0] != _Tok.END:
            tok = self.peek()
            raise FilterParseError(f"trailing {tok[1]!r}", tok[2], "end of expression")
        return expr

    def _or(self):
        left = self._and()
        while self.peek()[0] == _Tok.KW and self.peek()[1] == "or":
            self.consume()
            right = self._and()
            left = Or(left, right)
        return left

    def _and(self):
        left = self._not()
        while self.peek()[0] == _Tok.KW and self.peek()[1] == "and":
            self.consume()
            right = self._not()
            left = And(left, right)
        return left

    def _not(self):
        if self.peek()[0] == _Tok.KW and self.peek()[1] == "not":
            self.consume()
            return Not(self._not())
        return self._cmp()

    def _cmp(self):
        left = self._atom()
        tok = self.peek()
        if tok[0] == _Tok.OP and tok[1] in ("==", "!="):
            self.consume()
            right = self._atom()
            return Compare(tok[1], left, right)
        if tok[0] == _Tok.KW and tok[1] == "in":
            self.consume()
            right = self._atom()
            return Compare("in", left, right)
        return left

    def _atom(self):
        tok = self.peek()
        if tok[0] == _Tok.STRING:
            self.consume()
            return Literal(tok[1])
        if tok[0] == _Tok.NUMBER:
            self.consume()
            return Literal(tok[1])
        if tok[0] == _Tok.LP:
            self.consume()
            inner = self._or()
            self.expect(_Tok.RP, "')'")
            return inner
        if tok[0] == _Tok.LBR:
            self.consume()
            items = []
            if self.peek()[0] != _Tok.RBR:
                items.append(self._atom())
                while self.peek()[0] == _Tok.COMMA:
                    self.consume()
                    items.append(self._atom())
            self.expect(_Tok.RBR, "']'")
            return ListExpr(items)
        if tok[0] == _Tok.IDENT:
            self.consume()
            parts = [tok[1]]
            while self.peek()[0] == _Tok.DOT:
                self.consume()
                ident = self.expect(_Tok.IDENT, "identifier after '.'")
                parts.append(ident[1])
            return FieldRef(tuple(parts))
        raise FilterParseError(f"unexpected {tok[1]!r}", tok[2], "value")


def compile_filter(source: str):
    tokens = _tokenize(source)
    return _Parser(tokens).parse()


# -------- evaluator --------

def _resolve(ref: FieldRef, event: dict) -> Any:
    cur: Any = event
    for part in ref.parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def evaluate(node, event: dict) -> Any:
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, FieldRef):
        return _resolve(node, event)
    if isinstance(node, ListExpr):
        return [evaluate(i, event) for i in node.items]
    if isinstance(node, Compare):
        left = evaluate(node.left, event)
        right = evaluate(node.right, event)
        if left is _MISSING or right is _MISSING:
            return False
        if node.op == "==":
            return left == right
        if node.op == "!=":
            return left != right
        if node.op == "in":
            try:
                return left in right
            except TypeError:
                return False
        return False
    if isinstance(node, And):
        return bool(evaluate(node.left, event)) and bool(evaluate(node.right, event))
    if isinstance(node, Or):
        return bool(evaluate(node.left, event)) or bool(evaluate(node.right, event))
    if isinstance(node, Not):
        return not bool(evaluate(node.expr, event))
    return False
```

- [ ] **Step 4: Run tests to verify passes**

```bash
uv run pytest tests/test_filter_dsl.py -v
```

Expected: all 11 pass.

- [ ] **Step 5: Commit**

```bash
git add culture/bots/filter_dsl.py tests/test_filter_dsl.py
git commit -m "feat(events): add safe filter DSL for bot event triggers"
```

---

### Task 15: Bot event trigger + `fires_event` output

**Files:**

- Modify: `culture/bots/config.py`
- Modify: `culture/bots/bot_manager.py`
- Modify: `culture/bots/bot.py`
- Create: `tests/test_events_bot_trigger.py`
- Create: `tests/test_events_bot_chain.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_events_bot_trigger.py`:

```python
"""Event-triggered bots run their handler when a matching event fires."""

import pytest

from culture.agentirc.skill import Event, EventType


@pytest.mark.asyncio
async def test_event_triggered_bot_runs(server_with_bot, make_client):
    """A bot with trigger.type=event and filter matching the event fires."""
    server, bot = server_with_bot(
        bot_name="testserv-greeter",
        trigger_type="event",
        event_filter="type=='user.join' and channel=='#general'",
        channels=["#general"],
        template="Welcome {{ event.data.nick }}",
    )

    alice = await make_client("testserv-alice")
    await alice.send("CAP REQ :message-tags\r\n")
    await alice.recv_until("CAP")
    await alice.send("JOIN #general\r\n")
    await alice.recv_until("JOIN")

    # Emit user.join.
    await server.emit_event(Event(
        type=EventType.JOIN,
        channel="#general",
        nick="testserv-bob",
        data={"nick": "testserv-bob"},
    ))

    line = await alice.recv_until("Welcome testserv-bob")
    assert ":testserv-greeter" in line or "testserv-greeter" in line


@pytest.mark.asyncio
async def test_filter_mismatch_does_not_fire(server_with_bot, make_client):
    server, bot = server_with_bot(
        bot_name="testserv-only-general",
        trigger_type="event",
        event_filter="type=='user.join' and channel=='#general'",
        channels=["#general"],
        template="Hi {{ event.data.nick }}",
    )

    alice = await make_client("testserv-alice")
    await alice.send("JOIN #other\r\n")
    await alice.recv_until("JOIN")

    await server.emit_event(Event(
        type=EventType.JOIN,
        channel="#other",
        nick="testserv-bob",
        data={"nick": "testserv-bob"},
    ))

    # Bot should not fire; no Hi testserv-bob should arrive.
    lines = await alice.collect_for(0.5)
    assert not any("Hi testserv-bob" in l for l in lines)


@pytest.mark.asyncio
async def test_bad_filter_rejected_at_load(tmp_path):
    """A malformed filter causes BotManager.register to fail."""
    from culture.bots.bot_manager import BotManager
    from culture.bots.config import BotConfig

    bad = BotConfig(
        name="testserv-bad",
        trigger_type="event",
        event_filter="type = 'broken'",  # single '='
    )
    mgr = BotManager(server=None)
    with pytest.raises(Exception) as exc:
        mgr.register_bot(bad)
    assert "filter" in str(exc.value).lower()
```

Create `tests/test_events_bot_chain.py`:

```python
"""Bot A fires event → bot B triggered by that event → chain propagates."""

import pytest

from culture.agentirc.skill import Event, EventType


@pytest.mark.asyncio
async def test_bot_chain_fires_event(server_with_bots, make_client):
    """Bot A (fires_event on user.join) → Bot B (filter matches A's event)."""
    server, bots = server_with_bots([
        dict(
            bot_name="testserv-triage",
            trigger_type="event",
            event_filter="type=='user.join'",
            channels=["#dev"],
            template="triage ran",
            fires_event={"type": "testserv-triage.fired", "data": {"who": "{{ event.data.nick }}"}},
        ),
        dict(
            bot_name="testserv-followup",
            trigger_type="event",
            event_filter="type=='testserv-triage.fired'",
            channels=["#dev"],
            template="followup for {{ event.data.who }}",
        ),
    ])

    alice = await make_client("testserv-alice")
    await alice.send("JOIN #dev\r\n")
    await alice.recv_until("JOIN")

    await server.emit_event(Event(
        type=EventType.JOIN,
        channel="#general",
        nick="testserv-bob",
        data={"nick": "testserv-bob"},
    ))

    # Both bots' output should land in #dev.
    t_line = await alice.recv_until("triage ran")
    f_line = await alice.recv_until("followup for testserv-bob")
    assert t_line
    assert f_line
```

The fixtures `server_with_bot` and `server_with_bots` need to be added to `tests/conftest.py`. They should construct the server, register a bot via `BotManager.register_bot(BotConfig(...))`, and return both.

- [ ] **Step 2: Add test fixtures to `conftest.py`**

Edit `tests/conftest.py` to add:

```python
@pytest.fixture
async def server_with_bot(server):
    from culture.bots.bot_manager import BotManager
    from culture.bots.config import BotConfig

    def _make(**kwargs):
        fires = kwargs.pop("fires_event", None)
        filt = kwargs.pop("event_filter", None)
        cfg = BotConfig(
            name=kwargs.pop("bot_name"),
            owner="testserv",
            trigger_type=kwargs.pop("trigger_type", "event"),
            event_filter=filt,
            channels=kwargs.pop("channels", []),
            template=kwargs.pop("template", None),
            fires_event=fires,
        )
        if server.bot_manager is None:
            server.bot_manager = BotManager(server=server)
        server.bot_manager.register_bot(cfg)
        return server, cfg

    yield _make


@pytest.fixture
async def server_with_bots(server):
    from culture.bots.bot_manager import BotManager
    from culture.bots.config import BotConfig

    def _make(bot_kwargs_list):
        if server.bot_manager is None:
            server.bot_manager = BotManager(server=server)
        cfgs = []
        for kwargs in bot_kwargs_list:
            fires = kwargs.pop("fires_event", None)
            cfg = BotConfig(
                name=kwargs.pop("bot_name"),
                owner="testserv",
                trigger_type=kwargs.pop("trigger_type", "event"),
                event_filter=kwargs.pop("event_filter", None),
                channels=kwargs.pop("channels", []),
                template=kwargs.pop("template", None),
                fires_event=fires,
            )
            server.bot_manager.register_bot(cfg)
            cfgs.append(cfg)
        return server, cfgs

    yield _make
```

- [ ] **Step 3: Extend `BotConfig`**

In `culture/bots/config.py`:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmitEventSpec:
    type: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class BotConfig:
    # ... existing fields ...
    event_filter: str | None = None
    fires_event: EmitEventSpec | None = None
```

Update `load_bot_config` and `save_bot_config` to persist `event_filter` and `fires_event`:

```python
# In load_bot_config:
event_filter = trigger_section.get("filter")
fe = output_section.get("fires_event")
fires_event = None
if fe:
    fires_event = EmitEventSpec(
        type=fe.get("type", ""),
        data=fe.get("data", {}),
    )
return BotConfig(
    # ... existing kwargs ...
    event_filter=event_filter,
    fires_event=fires_event,
)

# In save_bot_config:
if config.event_filter:
    data["trigger"]["filter"] = config.event_filter
if config.fires_event:
    data["output"]["fires_event"] = {
        "type": config.fires_event.type,
        "data": config.fires_event.data,
    }
```

- [ ] **Step 4: Dispatch in `BotManager`**

In `culture/bots/bot_manager.py`:

```python
from culture.bots.filter_dsl import FilterParseError, compile_filter, evaluate

# In BotManager.register_bot, when the bot is trigger_type=event:
if config.trigger_type == "event":
    try:
        config._compiled_filter = compile_filter(config.event_filter or "True")
    except FilterParseError as exc:
        raise ValueError(f"bot {config.name} has invalid filter: {exc}") from exc

# Add an on-event subscriber. BotManager needs a hook run by IRCd.emit_event.
async def on_event(self, event):
    for bot in list(self.bots.values()):
        cfg = bot.config
        if cfg.trigger_type != "event":
            continue
        ctx = {
            "type": event.type.value if hasattr(event.type, "value") else str(event.type),
            "channel": event.channel,
            "nick": event.nick,
            "data": dict(event.data),
        }
        try:
            if not evaluate(cfg._compiled_filter, ctx):
                continue
        except Exception:
            continue
        await bot.handle({"event": ctx})
```

In `IRCd.emit_event`, after skill dispatch, before PRIVMSG surfacing, add:

```python
if self.bot_manager is not None:
    try:
        await self.bot_manager.on_event(event)
    except Exception:
        logger.exception("bot_manager.on_event failed")
```

- [ ] **Step 5: Wire `fires_event` output in `Bot.handle`**

In `culture/bots/bot.py`, after the existing `handle()` posts its messages, add:

```python
import time
from culture.agentirc.skill import Event, EventType
from culture.constants import EVENT_TYPE_RE

_rate_state: dict[str, list[float]] = {}
_RATE_MAX_PER_SEC = 10

def _check_rate(bot_name: str) -> bool:
    now = time.monotonic()
    window = _rate_state.setdefault(bot_name, [])
    window[:] = [t for t in window if now - t < 1.0]
    if len(window) >= _RATE_MAX_PER_SEC:
        return False
    window.append(now)
    return True


# In Bot.handle, after posting messages:
cfg = self.config
if cfg.fires_event is not None:
    if not EVENT_TYPE_RE.match(cfg.fires_event.type):
        logger.warning("bot %s fires_event.type %r invalid; skipping",
                       cfg.name, cfg.fires_event.type)
        return
    if not _check_rate(cfg.name):
        logger.warning("bot %s exceeded event emit rate; skipping", cfg.name)
        return
    # Render data values with Jinja2.
    from jinja2 import Environment, BaseLoader
    env = Environment(loader=BaseLoader(), autoescape=False)
    rendered_data = {}
    scope = {"payload": payload, "result": result, "event": payload.get("event")}
    for k, v in cfg.fires_event.data.items():
        if isinstance(v, str):
            rendered_data[k] = env.from_string(v).render(**scope)
        else:
            rendered_data[k] = v
    try:
        type_enum = EventType(cfg.fires_event.type)
    except ValueError:
        type_enum = cfg.fires_event.type  # custom event, string stays
    await self.server.emit_event(Event(
        type=type_enum,
        channel=cfg.channels[0] if cfg.channels else None,
        nick=cfg.name,
        data=rendered_data,
    ))
```

`payload` and `result` variable names should match whatever `handle()` already uses. Grep for the existing `handle` to see.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_events_bot_trigger.py tests/test_events_bot_chain.py tests/test_bot.py tests/test_bot_manager.py tests/test_bots_integration.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add culture/bots/config.py culture/bots/bot_manager.py culture/bots/bot.py tests/conftest.py tests/test_events_bot_trigger.py tests/test_events_bot_chain.py
git commit -m "feat(events): bot event-trigger + fires_event output with rate limit"
```

---

### Task 16: System bots — welcome bot reference

**Files:**

- Create: `culture/bots/system/__init__.py`
- Create: `culture/bots/system/welcome/__init__.py`
- Create: `culture/bots/system/welcome/bot.yaml`
- Create: `culture/bots/system/welcome/handler.py`
- Modify: `culture/bots/bot_manager.py` (system bot loader)
- Modify: `culture/mesh_config.py` or equivalent (config flag)
- Create: `tests/test_welcome_bot.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_welcome_bot.py`:

```python
"""Welcome bot ships with the server and greets on user.join unless disabled."""

import pytest


@pytest.mark.asyncio
async def test_welcome_bot_greets_on_join(server, make_client):
    """Default: welcome enabled; greets joiners to any channel."""
    alice = await make_client("testserv-alice")
    await alice.send("JOIN #general\r\n")
    await alice.recv_until("JOIN")

    bob = await make_client("testserv-bob")
    await bob.send("JOIN #general\r\n")
    await bob.recv_until("JOIN")

    line = await alice.recv_until("Welcome testserv-bob")
    assert "system-testserv-welcome" in line


@pytest.mark.asyncio
async def test_welcome_bot_disabled(server_welcome_disabled, make_client):
    alice = await make_client("testserv-alice")
    await alice.send("JOIN #general\r\n")
    await alice.recv_until("JOIN")

    bob = await make_client("testserv-bob")
    await bob.send("JOIN #general\r\n")
    await bob.recv_until("JOIN")

    lines = await alice.collect_for(0.5)
    assert not any("Welcome" in l for l in lines)
```

Add `server_welcome_disabled` fixture to `tests/conftest.py`:

```python
@pytest.fixture
async def server_welcome_disabled(tmp_path):
    # Build a server with system_bots.welcome.enabled=false.
    # Reuse the test-server factory but override the config flag.
    # Implementation depends on existing server-builder fixture shape.
    # Pseudocode:
    from culture.agentirc.ircd import IRCd
    ircd = IRCd(name="testserv", ...)
    ircd.config["system_bots"] = {"welcome": {"enabled": False}}
    await ircd.start()
    yield ircd
    await ircd.stop()
```

Fit to the existing `server` fixture shape — it likely already builds IRCd; the change is just overriding the config dict before start.

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_welcome_bot.py -v
```

Expected: fail.

- [ ] **Step 3: Implement welcome bot config**

Create `culture/bots/system/welcome/bot.yaml`:

```yaml
bot:
  name: welcome
  owner: system
  description: Greets users joining any channel.
  created: "2026-04-15"

trigger:
  type: event
  filter: "type == 'user.join'"

output:
  # The bot posts into the channel where the event happened.
  # Rendered template includes the joining user's nick.
  template: "Welcome {{ event.data.nick }} to {{ event.channel }} ✨"
```

Create `culture/bots/system/welcome/handler.py` (minimal — template-driven, no custom logic):

```python
"""Welcome handler — intentionally empty; template-only bot.

Custom handlers live here for system bots that need logic beyond
template rendering. This one does not, so the file documents the
convention and exposes no symbols.
"""
```

Create `culture/bots/system/__init__.py`:

```python
"""System bot loader.

Iterates `culture/bots/system/<name>/bot.yaml` and registers enabled
bots with the BotManager at server startup. Each system bot's nick is
`system-<servername>-<name>`.
"""

from __future__ import annotations

from pathlib import Path

from culture.bots.config import load_bot_config
from culture.constants import SYSTEM_USER_PREFIX


def discover_system_bots(server_name: str, config: dict) -> list:
    """Return a list of BotConfigs for enabled system bots."""
    root = Path(__file__).parent
    found = []
    sb_config = (config or {}).get("system_bots", {})
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        yaml_path = entry / "bot.yaml"
        if not yaml_path.is_file():
            continue
        enabled = sb_config.get(entry.name, {}).get("enabled", True)
        if not enabled:
            continue
        cfg = load_bot_config(yaml_path)
        # Override name to full nick form.
        cfg.name = f"{SYSTEM_USER_PREFIX}{server_name}-{entry.name}"
        cfg.owner = "system"
        found.append(cfg)
    return found
```

Create `culture/bots/system/welcome/__init__.py`:

```python
"""Welcome system bot."""
```

- [ ] **Step 4: Register system bots at startup**

In `culture/bots/bot_manager.py`:

```python
from culture.bots.system import discover_system_bots

# In BotManager.start (or constructor — match existing pattern):
async def load_system_bots(self) -> None:
    if self.server is None:
        return
    config = getattr(self.server, "config", {})
    for cfg in discover_system_bots(self.server.name, config):
        try:
            self.register_bot(cfg)
        except Exception:
            logger.exception("failed to register system bot %s", cfg.name)
```

Call `load_system_bots()` from `IRCd._register_default_skills()` or `IRCd.start()` post-bot-manager-init.

- [ ] **Step 5: Ensure BotManager can register without a `bot.yaml` on disk**

The current `BotManager.register_bot` may expect the bot dir to exist under `~/.culture/bots/`. System bots live in the package. If needed, adjust register_bot to skip on-disk lookup when `cfg.owner == "system"`:

```python
def register_bot(self, config: BotConfig) -> None:
    if config.owner == "system":
        # System bots live in the package, not ~/.culture/bots.
        self._attach_bot(config, system=True)
        return
    # ... existing path for user bots ...
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_welcome_bot.py tests/test_bot_manager.py -v
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add culture/bots/system tests/test_welcome_bot.py tests/conftest.py culture/bots/bot_manager.py
git commit -m "feat(events): add welcome system bot + system bot loader"
```

---

### Task 17: All-backends IRCv3 tag parsing

**Files:**

- Modify: `culture/clients/claude/irc_transport.py`
- Modify: `culture/clients/codex/irc_transport.py`
- Modify: `culture/clients/copilot/irc_transport.py`
- Modify: `culture/clients/acp/irc_transport.py`
- Modify: `packages/agent-harness/irc_transport.py`

Each of these parses raw IRC lines. They must now extract the `@...` tag block and expose tags on the parsed message, so agents that care about events can read them.

- [ ] **Step 1: Write failing smoke test**

Create `tests/test_irc_transport_tags.py`:

```python
"""All-backends transport tag parsing smoke test."""

import importlib

import pytest


BACKENDS = [
    "culture.clients.claude.irc_transport",
    "culture.clients.codex.irc_transport",
    "culture.clients.copilot.irc_transport",
    "culture.clients.acp.irc_transport",
    "packages.agent_harness.irc_transport",  # hyphens become underscores
]


@pytest.mark.parametrize("mod_path", BACKENDS)
def test_parse_line_exposes_tags(mod_path):
    mod = importlib.import_module(mod_path)
    parse = getattr(mod, "parse_line", None) or getattr(mod, "_parse_line", None)
    if parse is None:
        pytest.skip(f"{mod_path} has no parse_line helper")
    line = "@event=user.join;event-data=eyJuaWNrIjoib3JpIn0= :system-spark!system@spark PRIVMSG #general :ori joined"
    msg = parse(line)
    tags = getattr(msg, "tags", None) or (msg.get("tags") if isinstance(msg, dict) else None)
    assert tags is not None
    assert tags.get("event") == "user.join"
```

Note: `packages/agent-harness` directory uses a hyphen; the actual import path may need tweaking. If the package is not importable directly, test it via `sys.path` insertion or skip with a note.

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_irc_transport_tags.py -v
```

Expected: most fail — transports don't extract tags yet.

- [ ] **Step 3: Update each transport**

For each of the five files above, find the raw-line parsing function (typically called `_parse_line`, `parse_line`, or `_read_loop` contains inline parsing). Add a preamble that strips the `@tags` block and surfaces it on the parsed message.

Pattern — add a utility:

```python
# Near the top of the file.
def _extract_tags(line: str) -> tuple[dict, str]:
    if not line.startswith("@"):
        return {}, line
    if " " not in line:
        return {}, line
    tag_blob, rest = line[1:].split(" ", 1)
    tags: dict[str, str] = {}
    for piece in tag_blob.split(";"):
        if not piece:
            continue
        if "=" in piece:
            k, v = piece.split("=", 1)
            # Minimal unescape — tag_blob escape rules.
            v = v.replace(r"\:", ";").replace(r"\s", " ").replace(r"\\", "\\")
            v = v.replace(r"\r", "\r").replace(r"\n", "\n")
            tags[k] = v
        else:
            tags[piece] = ""
    return tags, rest
```

Then in the parse/read path:

```python
tags, stripped = _extract_tags(raw_line)
# existing parsing now runs on `stripped` instead of `raw_line`.
# Attach tags to the resulting Message/dict:
parsed.tags = tags  # or parsed["tags"] = tags
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_irc_transport_tags.py tests/test_irc_transport.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add culture/clients/*/irc_transport.py packages/agent-harness/irc_transport.py tests/test_irc_transport_tags.py
git commit -m "feat(events): add IRCv3 tag parsing to all agent backends"
```

---

### Task 18: Docs — feature page and protocol extension

**Files:**

- Create: `docs/features/events.md`
- Create: `culture/protocol/extensions/events.md`

- [ ] **Step 1: Write `docs/features/events.md`**

```markdown
---
title: Mesh Events
parent: Features
---

# Mesh Events

Events surface mesh lifecycle and activity as IRCv3-tagged PRIVMSGs from
a reserved `system-<servername>` pseudo-user. They are stored in channel
history, consumable by bots as triggers, and federate across linked
servers.

## Built-in events

Channel-scoped (posted to the channel the event pertains to):

- `user.join`, `user.part`, `user.quit`
- `thread.create`, `thread.message`, `thread.close`
- `room.create`, `room.archive`, `room.meta`
- `tags.update`

Global (posted to `#system`):

- `agent.connect`, `agent.disconnect`
- `server.link`, `server.unlink`
- `server.sleep`, `server.wake`
- `console.open`, `console.close`

## Writing an event-triggered bot

```yaml
# ~/.culture/bots/my-greeter/bot.yaml
bot:
  name: my-greeter
  owner: ori

trigger:
  type: event
  filter: "type == 'user.join' and channel == '#general'"

output:
  channels: [#general]
  template: "Welcome {{ event.data.nick }}!"
```

## Filter DSL grammar

- `==`, `!=`, `in`, `and`, `or`, `not`, parentheses.
- Dotted field access for event fields: `type`, `channel`, `nick`,
  `data.*`.
- Literal strings (single-quoted), integers, and list literals.
- Missing fields compare False — bot does not fire.
- Invalid filters are rejected at config-load time.

## Emitting a custom event from a bot

```yaml
output:
  channels: [#triage]
  template: "classified as {{ result.severity }}"
  fires_event:
    type: my-triage.classified
    data:
      severity: "{{ result.severity }}"
      issue: "{{ payload.issue_id }}"
```

Any other bot may filter on `type == 'my-triage.classified'`.

## Use cases

### Flow A — Server-built-in event (`agent.connect`)

(Copy block from the spec.)

### Flow B — Bot triggered by event, fires follow-on event

(Copy block from the spec.)

### Flow C — Federated event arrives from peer

(Copy block from the spec.)
```

(Include the actual flow blocks verbatim from
`docs/superpowers/specs/2026-04-15-mesh-events-design.md`.)

- [ ] **Step 2: Write `culture/protocol/extensions/events.md`**

```markdown
# Events (Culture Extension)

## Summary

Events are surfaced as IRCv3-tagged `PRIVMSG` from the reserved pseudo-user
`system-<servername>`. A new `SEVENT` S2S verb carries events across links.

## Wire format (client-facing)

```text
@event=TYPE;event-data=BASE64JSON :system-SERVER!system@SERVER PRIVMSG CHANNEL :RENDERED_BODY
```

Examples:

```text
@event=user.join;event-data=eyJuaWNrIjoib3JpIn0= :system-spark!system@spark PRIVMSG #general :ori joined #general
@event=agent.connect :system-spark!system@spark PRIVMSG #system :spark-claude connected
```

## Tag keys

| Key | Value |
|-----|-------|
| `event` | Dotted event type name (e.g., `user.join`) |
| `event-data` | Base64-encoded JSON dict of event payload fields |

## Capability negotiation (client)

- `CAP LS` advertises `message-tags`.
- Tag-capable clients `CAP REQ :message-tags` and receive tagged lines.
- Non-tag-capable clients receive the plain `<rendered-body>` with tags
  stripped.

## Reserved identities

- Nicks matching `system-*` are reserved. Non-server clients are
  rejected with `432 ERR_ERRONEUSNICKNAME`.
- `system-<servername>` is the server's system user; auto-joined to
  `#system` at startup.
- System bots take nicks of the form
  `system-<servername>-<botname>` (e.g., `system-spark-welcome`).

## Server channel

- `#system` is auto-created on server startup and federated.
- Clients may `JOIN #system` to observe global events.

## S2S verb: `SEVENT`

```text
SEVENT ORIGIN_SERVER SEQ TYPE CHANNEL_OR_STAR :BASE64_JSON_DATA
```

Used to carry events between linked servers. Loop prevention: the
receiving server sets `_origin=<origin>` on the reconstructed event and
skips relaying it back to the origin peer.

## Peer capability negotiation

Peers advertise `events/1` on handshake. Peers lacking the capability
fall back to existing typed relays (SMSG/SJOIN/etc.) for event types
that map to them; unmapped event types are dropped with a warning.
```

- [ ] **Step 3: Run markdownlint**

```bash
markdownlint-cli2 docs/features/events.md culture/protocol/extensions/events.md
```

Expected: no errors. Fix any.

- [ ] **Step 4: Commit**

```bash
git add docs/features/events.md culture/protocol/extensions/events.md
git commit -m "docs(events): feature page + protocol extension reference"
```

---

### Task 19: Doc/test alignment audit

- [ ] **Step 1: Run doc-test-alignment agent**

```bash
# From Claude Code:
# Agent(subagent_type="doc-test-alignment", prompt="Audit the events branch (against main) for missing doc coverage on SEVENT verb, EmitEventSpec, FilterParseError, EventType additions, and system bot nick convention.")
```

Expected: green report. Fix any gaps surfaced.

- [ ] **Step 2: Commit any resulting doc additions**

```bash
git add docs/ culture/protocol/extensions/
git commit -m "docs(events): address doc-test-alignment findings"
```

---

### Task 20: Version bump and CHANGELOG

**Files:**

- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`
- Modify: `culture/__init__.py` (`__version__`)

- [ ] **Step 1: Run version-bump skill**

```bash
# /version-bump minor
```

This updates `pyproject.toml`, `culture/__init__.py`, `CHANGELOG.md` in
one step. Add a CHANGELOG entry describing the feature:

```markdown
### Added
- **Mesh events**: server emits lifecycle and link events as tagged
  PRIVMSG from `system-<servername>`; stored in channel history;
  consumable by bots (`trigger.type: event` with a safe filter DSL);
  composable via `output.fires_event`. Includes a reference welcome
  system bot. Full details: `docs/features/events.md`.
```

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml CHANGELOG.md culture/__init__.py uv.lock
git commit -m "chore: bump minor version for mesh events feature"
```

---

### Task 21: Full test pass and PR

- [ ] **Step 1: Run the full test suite in parallel**

```bash
# /run-tests
# (uses pytest -n auto + verbose)
```

Expected: all pass.

- [ ] **Step 2: Run full suite with coverage**

```bash
# /run-tests --ci
```

Expected: new modules at ≥80% coverage. Fill gaps if below.

- [ ] **Step 3: Run pre-commit**

```bash
pre-commit run --all-files
```

Expected: clean. If `black`/`isort` reformat files, `git add` the
reformatted files and commit:

```bash
git add -u
git commit -m "style: apply black + isort"
```

- [ ] **Step 4: Pre-push code review**

Invoke `superpowers:code-reviewer` on the staged diff (per
`culture/CLAUDE.md` — transport and protocol touches require it):

```bash
# Agent(subagent_type="superpowers:code-reviewer", prompt="Review the mesh events branch diff for typed exceptions, caller cleanup obligations, and all-backends drift between culture/clients/*/irc_transport.py.")
```

Address any findings.

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin <branch-name>
# /cicd  (creates PR, waits for CI + Qodo + Copilot; renamed from /pr-review in culture 8.8.1)
```

- [ ] **Step 6: Open follow-up issue for slash-command bot trigger**

```bash
gh issue create --title "Add slash-command trigger type to bots" --body "Follow-up from #123 (mesh events). Adds trigger.type: slash-command alongside webhook and event. Open design questions: (a) wire format — new SLASH verb vs. PRIVMSG line-starting-with-/ interception; (b) command namespace + discovery (LIST COMMANDS equivalent). See docs/superpowers/specs/2026-04-15-mesh-events-design.md 'Follow-ups' section."
```

---

### Task 22: Re-enable PyPI publish workflow

During the multi-task rollout, the PyPI publish workflow is disabled (renamed
to `.github/workflows/publish.yml.disabled`) so intermediate merges to `main`
do not trigger publishes. Once all 21 tasks have merged and the feature is
shipped as a single version bump, restore the publish workflow.

**Files:**

- Rename: `.github/workflows/publish.yml.disabled` → `.github/workflows/publish.yml`

- [ ] **Step 1: Rename the file back**

```bash
git mv .github/workflows/publish.yml.disabled .github/workflows/publish.yml
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore(ci): re-enable PyPI publish workflow after mesh events rollout"
```

- [ ] **Step 3: Open PR (or include in the final merge)**

The next push to `main` (or the final PR merge) will trigger the restored
publish workflow as usual.

---

## Self-Review Notes

The plan covers every spec section:

- **Wire format** → Tasks 1, 2, 3
- **Reserved system user + #system bootstrap** → Tasks 4, 5
- **Event catalog + render** → Task 6
- **PRIVMSG surfacing from `emit_event`** → Task 7
- **Server-lifecycle emissions** → Tasks 8, 9, 10, 11
- **Federation SEVENT + link/unlink** → Task 12
- **History integration** → Task 13
- **Filter DSL** → Task 14
- **Bot event trigger + fires_event** → Task 15
- **System bots + welcome** → Task 16
- **All-backends tag parsing** → Task 17
- **Docs** → Task 18
- **Doc-test alignment + version + PR** → Tasks 19, 20, 21
- **Re-enable publish workflow** → Task 22 (after the feature is fully shipped)
