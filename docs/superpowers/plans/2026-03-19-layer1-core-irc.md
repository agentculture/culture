---
title: "Layer 1 Plan"
parent: "Design"
nav_order: 3
---

# Layer 1: Core IRC Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working IRC server that passes the "connect with weechat and chat" milestone.

**Architecture:** Async Python IRCd using asyncio. A shared `protocol/` package handles message parsing and formatting per RFC 2812. The `server/` package implements the IRCd — TCP listener, client state, channel state, and message routing. Nick format enforcement (`<server>-<agent>`) is built in from the start.

**Tech Stack:** Python 3.12+, asyncio, pytest, pytest-asyncio, uv

**Spec:** `docs/superpowers/specs/2026-03-19-agentirc-design.md`

---

## File Structure

```text
culture/
├── pyproject.toml                # Project config, dependencies (uv)
├── protocol/
│   ├── __init__.py
│   ├── message.py                # IRC message parsing/formatting (RFC 2812 §2.3.1)
│   ├── replies.py                # Numeric reply code constants
│   └── commands.py               # Command verb constants
├── server/
│   ├── __init__.py
│   ├── __main__.py               # Entry point: culture server start
│   ├── config.py                 # Server configuration dataclass
│   ├── ircd.py                   # Main IRCd class (asyncio TCP listener)
│   ├── client.py                 # Connected client state & command handlers
│   └── channel.py                # Channel state
├── tests/
│   ├── conftest.py               # Shared fixtures (server, test client helper)
│   ├── test_message.py           # Protocol message parsing/formatting
│   ├── test_connection.py        # NICK, USER, QUIT, PING/PONG, nick enforcement
│   ├── test_channel.py           # JOIN, PART, TOPIC, NAMES
│   └── test_messaging.py         # PRIVMSG, NOTICE (channel + DM)
└── docs/
    └── layer1-core-irc.md        # Feature doc for Layer 1
```

**Responsibilities:**

| File | Does | Does NOT |
|------|------|----------|
| `protocol/message.py` | Parse and format IRC wire messages | Know about server state or clients |
| `protocol/replies.py` | Define numeric codes as constants | Format reply messages |
| `protocol/commands.py` | Define command verbs as constants | Handle commands |
| `server/ircd.py` | Accept TCP connections, own client/channel registries | Handle individual IRC commands |
| `server/client.py` | Buffer input, dispatch commands, send responses | Own channel state |
| `server/channel.py` | Track members and topic | Send messages |

---

## Chunk 1: Project Setup + Protocol Layer

### Task 1: Project Scaffolding

**Files:**

- Create: `pyproject.toml`
- Create: `protocol/__init__.py`
- Create: `server/__init__.py`
- Create: `tests/__init__.py` (empty)

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "culture"
version = "0.1.0"
description = "IRC Protocol ChatRooms for AI Agents (And humans allowed)"
requires-python = ">=3.12"
license = "MIT"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.25",
]
```

- [ ] **Step 2: Create empty init files**

```python
# protocol/__init__.py — empty
# server/__init__.py — empty
# tests/__init__.py — empty
```

- [ ] **Step 3: Install dependencies**

Run: `uv sync`
Expected: Creates `.venv/` and installs pytest + pytest-asyncio

- [ ] **Step 4: Verify pytest runs**

Run: `uv run pytest --co`
Expected: "no tests ran" (no error)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock protocol/ server/ tests/
git commit -m "chore: project scaffolding with uv, pytest"
```

---

### Task 2: IRC Message Parser

**Files:**

- Create: `protocol/message.py`
- Create: `tests/test_message.py`

- [ ] **Step 1: Write failing tests for message parsing**

```python
# tests/test_message.py
from culture.protocol.message import Message


class TestMessageParse:
    def test_simple_command(self):
        msg = Message.parse("QUIT\r\n")
        assert msg.command == "QUIT"
        assert msg.prefix is None
        assert msg.params == []

    def test_command_with_params(self):
        msg = Message.parse("NICK spark-culture\r\n")
        assert msg.command == "NICK"
        assert msg.params == ["spark-culture"]

    def test_command_with_trailing(self):
        msg = Message.parse("PRIVMSG #general :Hello world\r\n")
        assert msg.command == "PRIVMSG"
        assert msg.params == ["#general", "Hello world"]

    def test_command_with_prefix(self):
        msg = Message.parse(":spark-ori!ori@localhost PRIVMSG #general :hi\r\n")
        assert msg.prefix == "spark-ori!ori@localhost"
        assert msg.command == "PRIVMSG"
        assert msg.params == ["#general", "hi"]

    def test_user_command(self):
        msg = Message.parse("USER ori 0 * :Ori Nachum\r\n")
        assert msg.command == "USER"
        assert msg.params == ["ori", "0", "*", "Ori Nachum"]

    def test_command_case_normalized(self):
        msg = Message.parse("nick spark-culture\r\n")
        assert msg.command == "NICK"

    def test_no_trailing_crlf(self):
        msg = Message.parse("PING :token123")
        assert msg.command == "PING"
        assert msg.params == ["token123"]

    def test_empty_trailing(self):
        msg = Message.parse("PRIVMSG #general :\r\n")
        assert msg.params == ["#general", ""]

    def test_multiple_middle_params(self):
        msg = Message.parse("MODE #channel +o spark-culture\r\n")
        assert msg.command == "MODE"
        assert msg.params == ["#channel", "+o", "spark-culture"]


class TestMessageFormat:
    def test_simple_command(self):
        msg = Message(prefix=None, command="QUIT", params=[])
        assert msg.format() == "QUIT\r\n"

    def test_with_prefix(self):
        msg = Message(prefix="server", command="PONG", params=["server", "token"])
        assert msg.format() == ":server PONG server token\r\n"

    def test_trailing_with_spaces(self):
        msg = Message(prefix=None, command="PRIVMSG", params=["#general", "Hello world"])
        assert msg.format() == "PRIVMSG #general :Hello world\r\n"

    def test_trailing_empty(self):
        msg = Message(prefix=None, command="PRIVMSG", params=["#general", ""])
        assert msg.format() == "PRIVMSG #general :\r\n"

    def test_single_word_trailing(self):
        msg = Message(prefix=None, command="NICK", params=["spark-culture"])
        assert msg.format() == "NICK spark-culture\r\n"

    def test_roundtrip(self):
        original = ":spark-ori!ori@localhost PRIVMSG #general :Hello world"
        msg = Message.parse(original + "\r\n")
        assert msg.format() == original + "\r\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_message.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'protocol.message'`

- [ ] **Step 3: Implement message parser**

```python
# protocol/message.py
from dataclasses import dataclass, field


@dataclass
class Message:
    """An IRC protocol message per RFC 2812 §2.3.1.

    Wire format: [:prefix SPACE] command [params] CRLF
    """

    prefix: str | None
    command: str
    params: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, line: str) -> "Message":
        """Parse a raw IRC line into a Message."""
        line = line.rstrip("\r\n")

        prefix = None
        if line.startswith(":"):
            prefix, line = line.split(" ", 1)
            prefix = prefix[1:]  # strip leading ':'

        trailing = None
        if " :" in line:
            line, trailing = line.split(" :", 1)

        parts = line.split()
        command = parts[0].upper()
        params = parts[1:]

        if trailing is not None:
            params.append(trailing)

        return cls(prefix=prefix, command=command, params=params)

    def format(self) -> str:
        """Format this message as an IRC wire line."""
        parts = []
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

Run: `uv run pytest tests/test_message.py -v`
Expected: All 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add protocol/message.py tests/test_message.py
git commit -m "feat: IRC message parser and formatter (RFC 2812)"
```

---

### Task 3: Reply Codes and Command Constants

**Files:**

- Create: `protocol/replies.py`
- Create: `protocol/commands.py`

- [ ] **Step 1: Create reply codes**

```python
# protocol/replies.py
"""IRC numeric reply codes (RFC 2812 §5)."""

# Connection registration
RPL_WELCOME = "001"
RPL_YOURHOST = "002"
RPL_CREATED = "003"
RPL_MYINFO = "004"

# Channel
RPL_TOPIC = "332"
RPL_NOTOPIC = "331"
RPL_NAMREPLY = "353"
RPL_ENDOFNAMES = "366"

# Errors
ERR_NOSUCHNICK = "401"
ERR_NOSUCHCHANNEL = "403"
ERR_CANNOTSENDTOCHAN = "404"
ERR_UNKNOWNCOMMAND = "421"
ERR_NONICKNAMEGIVEN = "431"
ERR_ERRONEUSNICKNAME = "432"
ERR_NICKNAMEINUSE = "433"
ERR_NOTONCHANNEL = "442"
ERR_NEEDMOREPARAMS = "461"
ERR_ALREADYREGISTRED = "462"
```

- [ ] **Step 2: Create command constants**

```python
# protocol/commands.py
"""IRC command verb constants (RFC 2812 §3)."""

NICK = "NICK"
USER = "USER"
QUIT = "QUIT"
JOIN = "JOIN"
PART = "PART"
PRIVMSG = "PRIVMSG"
NOTICE = "NOTICE"
PING = "PING"
PONG = "PONG"
TOPIC = "TOPIC"
NAMES = "NAMES"
```

- [ ] **Step 3: Commit**

```bash
git add protocol/replies.py protocol/commands.py
git commit -m "feat: IRC reply codes and command constants"
```

---

## Chunk 2: Server Core + Connection Registration

### Task 4: Server Configuration and Channel State

**Files:**

- Create: `server/config.py`
- Create: `server/channel.py`

- [ ] **Step 1: Create server config**

```python
# server/config.py
from dataclasses import dataclass


@dataclass
class ServerConfig:
    """Configuration for an culture server instance."""

    name: str = "culture"
    host: str = "0.0.0.0"
    port: int = 6667
```

- [ ] **Step 2: Create channel state**

```python
# server/channel.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from culture.server.client import Client


class Channel:
    """Represents an IRC channel with members and topic."""

    def __init__(self, name: str):
        self.name = name
        self.topic: str | None = None
        self.members: set[Client] = set()

    def add(self, client: Client) -> None:
        self.members.add(client)

    def remove(self, client: Client) -> None:
        self.members.discard(client)
```

- [ ] **Step 3: Commit**

```bash
git add server/config.py server/channel.py
git commit -m "feat: server config and channel state"
```

---

### Task 5: TCP Listener (IRCd)

**Files:**

- Create: `server/ircd.py`
- Create: `server/client.py` (minimal — just enough for IRCd to compile)
- Create: `tests/conftest.py`
- Create: `tests/test_connection.py`

- [ ] **Step 1: Create test fixtures and write failing tests**

Create `tests/conftest.py` first (fixtures must exist before tests reference them):

```python
# tests/conftest.py
import asyncio
import pytest_asyncio
from culture.server.config import ServerConfig
from culture.server.ircd import IRCd


class IRCTestClient:
    """A minimal IRC test client over raw TCP."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self._buffer = ""

    async def send(self, text: str) -> None:
        self.writer.write(f"{text}\r\n".encode())
        await self.writer.drain()

    async def recv(self, timeout: float = 2.0) -> str:
        while "\r\n" not in self._buffer:
            data = await asyncio.wait_for(self.reader.read(4096), timeout=timeout)
            if not data:
                raise ConnectionError("Connection closed")
            self._buffer += data.decode()
        line, self._buffer = self._buffer.split("\r\n", 1)
        return line

    async def recv_all(self, timeout: float = 0.5) -> list[str]:
        lines = []
        try:
            while True:
                lines.append(await self.recv(timeout=timeout))
        except (asyncio.TimeoutError, ConnectionError):
            pass
        return lines

    async def close(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass


@pytest_asyncio.fixture
async def server():
    config = ServerConfig(name="testserv", host="127.0.0.1", port=0)
    ircd = IRCd(config)
    await ircd.start()
    # Get actual port from OS-assigned random port
    ircd.config.port = ircd._server.sockets[0].getsockname()[1]
    yield ircd
    await ircd.stop()


@pytest_asyncio.fixture
async def make_client(server):
    clients = []

    async def _make(nick: str | None = None, user: str | None = None) -> IRCTestClient:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
        client = IRCTestClient(reader, writer)
        if nick:
            await client.send(f"NICK {nick}")
        if user:
            await client.send(f"USER {user} 0 * :{user}")
        if nick and user:
            # Drain welcome messages
            await client.recv_all(timeout=0.5)
        clients.append(client)
        return client

    yield _make

    for c in clients:
        try:
            await c.close()
        except Exception:
            pass
```

Then create `tests/test_connection.py`:

```python
# tests/test_connection.py
import asyncio
import pytest


@pytest.mark.asyncio
async def test_server_accepts_connection(server):
    """Server accepts a TCP connection."""
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_server_responds_to_ping(server, make_client):
    """Server responds to PING with PONG."""
    client = await make_client()
    await client.send("PING :hello")
    response = await client.recv()
    assert "PONG" in response
    assert "hello" in response
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_connection.py -v`
Expected: FAIL — import errors (server/ircd.py does not exist yet)

- [ ] **Step 4: Implement IRCd**

```python
# server/ircd.py
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from culture.server.config import ServerConfig
from culture.server.channel import Channel

if TYPE_CHECKING:
    from culture.server.client import Client


class IRCd:
    """The culture IRC server."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self.clients: dict[str, Client] = {}  # nick -> Client
        self.channels: dict[str, Channel] = {}  # name -> Channel
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.config.host,
            self.config.port,
        )

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        from culture.server.client import Client

        client = Client(reader, writer, self)
        try:
            await client.handle()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._remove_client(client)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass

    def _remove_client(self, client: Client) -> None:
        if client.nick and client.nick in self.clients:
            del self.clients[client.nick]
        for channel in list(client.channels):
            channel.remove(client)
            if not channel.members:
                del self.channels[channel.name]

    def get_or_create_channel(self, name: str) -> Channel:
        if name not in self.channels:
            self.channels[name] = Channel(name)
        return self.channels[name]
```

- [ ] **Step 5: Implement minimal Client (PING/PONG only, no registration yet)**

```python
# server/client.py
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from culture.protocol.message import Message
from culture.protocol import replies

if TYPE_CHECKING:
    from culture.server.ircd import IRCd
    from culture.server.channel import Channel


class Client:
    """A connected IRC client."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        server: IRCd,
    ):
        self.reader = reader
        self.writer = writer
        self.server = server
        self.nick: str | None = None
        self.user: str | None = None
        self.realname: str | None = None
        self.host: str = writer.get_extra_info("peername", ("unknown", 0))[0]
        self.channels: set[Channel] = set()
        self._registered = False

    @property
    def prefix(self) -> str:
        return f"{self.nick}!{self.user}@{self.host}"

    async def send(self, message: Message) -> None:
        self.writer.write(message.format().encode("utf-8"))
        await self.writer.drain()

    async def send_numeric(self, code: str, *params: str) -> None:
        target = self.nick or "*"
        msg = Message(
            prefix=self.server.config.name,
            command=code,
            params=[target, *params],
        )
        await self.send(msg)

    async def handle(self) -> None:
        buffer = ""
        while True:
            data = await self.reader.read(4096)
            if not data:
                break
            buffer += data.decode("utf-8", errors="replace")
            # Normalize bare \n to \r\n for clients that don't send proper CRLF
            buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line:
                    msg = Message.parse(line)
                    await self._dispatch(msg)

    async def _dispatch(self, msg: Message) -> None:
        handler = getattr(self, f"_handle_{msg.command.lower()}", None)
        if handler:
            await handler(msg)
        else:
            await self.send_numeric(
                replies.ERR_UNKNOWNCOMMAND, msg.command, "Unknown command"
            )

    async def _handle_ping(self, msg: Message) -> None:
        token = msg.params[0] if msg.params else ""
        await self.send(
            Message(
                prefix=self.server.config.name,
                command="PONG",
                params=[self.server.config.name, token],
            )
        )

    async def _handle_pong(self, msg: Message) -> None:
        pass  # Client responding to our ping
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_connection.py -v`
Expected: 2 tests PASS

- [ ] **Step 7: Commit**

```bash
git add server/ircd.py server/client.py tests/conftest.py tests/test_connection.py
git commit -m "feat: TCP listener with PING/PONG support"
```

---

### Task 6: Client Registration (NICK, USER, Welcome)

**Files:**

- Modify: `server/client.py`
- Modify: `tests/test_connection.py`

- [ ] **Step 1: Write failing tests for registration and nick enforcement**

Append to `tests/test_connection.py`:

```python
@pytest.mark.asyncio
async def test_registration_welcome(server, make_client):
    """Client receives 001-004 after NICK + USER."""
    client = await make_client()
    await client.send("NICK testserv-ori")
    await client.send("USER ori 0 * :Ori Nachum")
    lines = await client.recv_all(timeout=1.0)
    codes = [line.split()[1] for line in lines]
    assert "001" in codes
    assert "002" in codes
    assert "003" in codes
    assert "004" in codes


@pytest.mark.asyncio
async def test_nick_must_have_server_prefix(server, make_client):
    """Nick without server name prefix is rejected."""
    client = await make_client()
    await client.send("NICK claude")
    response = await client.recv()
    assert "432" in response  # ERR_ERRONEUSNICKNAME


@pytest.mark.asyncio
async def test_nick_with_correct_prefix(server, make_client):
    """Nick with correct server prefix is accepted."""
    client = await make_client()
    await client.send("NICK testserv-claude")
    await client.send("USER claude 0 * :Claude")
    lines = await client.recv_all(timeout=1.0)
    codes = [line.split()[1] for line in lines]
    assert "001" in codes


@pytest.mark.asyncio
async def test_duplicate_nick_rejected(server, make_client):
    """Second client with same nick is rejected."""
    await make_client(nick="testserv-claude", user="claude")
    client2 = await make_client()
    await client2.send("NICK testserv-claude")
    response = await client2.recv()
    assert "433" in response  # ERR_NICKNAMEINUSE


@pytest.mark.asyncio
async def test_nick_no_param(server, make_client):
    """NICK without parameter returns ERR_NONICKNAMEGIVEN."""
    client = await make_client()
    await client.send("NICK")
    response = await client.recv()
    assert "431" in response


@pytest.mark.asyncio
async def test_user_without_nick(server, make_client):
    """USER without prior NICK does not trigger welcome."""
    client = await make_client()
    await client.send("USER ori 0 * :Ori")
    lines = await client.recv_all(timeout=0.5)
    codes = [line.split()[1] for line in lines if len(line.split()) > 1]
    assert "001" not in codes


@pytest.mark.asyncio
async def test_double_registration_rejected(server, make_client):
    """USER sent twice returns ERR_ALREADYREGISTRED."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("USER ori 0 * :Ori again")
    response = await client.recv()
    assert "462" in response
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_connection.py -v`
Expected: New tests FAIL — `_handle_nick` not defined

- [ ] **Step 3: Add NICK, USER, and welcome handlers to Client**

Add these methods to `server/client.py` in the `Client` class:

```python
    async def _handle_nick(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NONICKNAMEGIVEN, "No nickname given")
            return

        nick = msg.params[0]

        # Enforce server name prefix
        expected_prefix = f"{self.server.config.name}-"
        if not nick.startswith(expected_prefix):
            await self.send_numeric(
                replies.ERR_ERRONEUSNICKNAME,
                nick,
                f"Nickname must start with {expected_prefix}",
            )
            return

        if nick in self.server.clients:
            await self.send_numeric(
                replies.ERR_NICKNAMEINUSE, nick, "Nickname is already in use"
            )
            return

        old_nick = self.nick
        if old_nick and old_nick in self.server.clients:
            del self.server.clients[old_nick]

        self.nick = nick
        self.server.clients[nick] = self
        await self._try_register()

    async def _handle_user(self, msg: Message) -> None:
        if self._registered:
            await self.send_numeric(
                replies.ERR_ALREADYREGISTRED, "You may not reregister"
            )
            return
        if len(msg.params) < 4:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "USER", "Not enough parameters"
            )
            return

        self.user = msg.params[0]
        self.realname = msg.params[3]
        await self._try_register()

    async def _try_register(self) -> None:
        if self.nick and self.user and not self._registered:
            self._registered = True
            await self._send_welcome()

    async def _send_welcome(self) -> None:
        await self.send_numeric(
            replies.RPL_WELCOME,
            f"Welcome to {self.server.config.name} IRC Network {self.prefix}",
        )
        await self.send_numeric(
            replies.RPL_YOURHOST,
            f"Your host is {self.server.config.name}, running culture",
        )
        await self.send_numeric(
            replies.RPL_CREATED,
            "This server was created today",
        )
        await self.send_numeric(
            replies.RPL_MYINFO,
            self.server.config.name,
            "culture",
            "o",
            "o",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_connection.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add server/client.py tests/test_connection.py
git commit -m "feat: client registration with nick format enforcement"
```

---

## Chunk 3: Channels + Messaging + QUIT

### Task 7: Channel Operations (JOIN, PART, NAMES, TOPIC)

**Files:**

- Modify: `server/client.py`
- Create: `tests/test_channel.py`

- [ ] **Step 1: Write failing tests for channel operations**

```python
# tests/test_channel.py
import pytest


@pytest.mark.asyncio
async def test_join_channel(server, make_client):
    """Client can join a channel and receives NAMES list."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "#general" in joined
    assert "353" in joined  # RPL_NAMREPLY
    assert "366" in joined  # RPL_ENDOFNAMES
    assert "testserv-ori" in joined


@pytest.mark.asyncio
async def test_join_notifies_others(server, make_client):
    """Existing channel members see the JOIN."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")

    # client1 should see client2's join
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "testserv-claude" in joined


@pytest.mark.asyncio
async def test_part_channel(server, make_client):
    """Client can leave a channel."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    await client.recv_all(timeout=0.5)

    await client.send("PART #general :bye")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "PART" in joined
    assert "#general" in joined


@pytest.mark.asyncio
async def test_part_not_on_channel(server, make_client):
    """PART on a channel you're not in returns error."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("PART #general")
    response = await client.recv()
    assert "442" in response  # ERR_NOTONCHANNEL


@pytest.mark.asyncio
async def test_topic_set_and_get(server, make_client):
    """Client can set and query channel topic."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    await client.recv_all(timeout=0.5)

    await client.send("TOPIC #general :Building culture")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "TOPIC" in joined
    assert "Building culture" in joined

    await client.send("TOPIC #general")
    response = await client.recv()
    assert "332" in response  # RPL_TOPIC
    assert "Building culture" in response


@pytest.mark.asyncio
async def test_topic_no_topic_set(server, make_client):
    """Querying topic on channel without topic returns RPL_NOTOPIC."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    await client.recv_all(timeout=0.5)

    await client.send("TOPIC #general")
    response = await client.recv()
    assert "331" in response  # RPL_NOTOPIC


@pytest.mark.asyncio
async def test_names_list(server, make_client):
    """NAMES returns all members of a channel."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)

    await client1.send("NAMES #general")
    lines = await client1.recv_all(timeout=1.0)
    names_line = [l for l in lines if "353" in l][0]
    assert "testserv-ori" in names_line
    assert "testserv-claude" in names_line


@pytest.mark.asyncio
async def test_join_channel_with_topic(server, make_client):
    """Joining a channel with a topic shows the topic."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client1.send("TOPIC #general :Welcome to culture")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    lines = await client2.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "332" in joined
    assert "Welcome to culture" in joined


@pytest.mark.asyncio
async def test_channel_name_must_start_with_hash(server, make_client):
    """JOIN without # prefix is silently ignored."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN general")
    lines = await client.recv_all(timeout=0.5)
    # Should get nothing back (no join, no error)
    join_lines = [l for l in lines if "JOIN" in l]
    assert len(join_lines) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_channel.py -v`
Expected: FAIL — `_handle_join` not defined

- [ ] **Step 3: Add JOIN, PART, TOPIC, NAMES handlers to Client**

Add these methods to `server/client.py` in the `Client` class:

```python
    async def _handle_join(self, msg: Message) -> None:
        if not self._registered:
            return
        if not msg.params:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "JOIN", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        if not channel_name.startswith("#"):
            return

        channel = self.server.get_or_create_channel(channel_name)
        if self in channel.members:
            return

        channel.add(self)
        self.channels.add(channel)

        # Notify all channel members (including self)
        join_msg = Message(
            prefix=self.prefix, command="JOIN", params=[channel_name]
        )
        for member in channel.members:
            await member.send(join_msg)

        # Send topic if set
        if channel.topic:
            await self.send_numeric(
                replies.RPL_TOPIC, channel_name, channel.topic
            )

        # Send names list
        await self._send_names(channel)

    async def _handle_part(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "PART", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        reason = msg.params[1] if len(msg.params) > 1 else ""

        channel = self.server.channels.get(channel_name)
        if not channel or self not in channel.members:
            await self.send_numeric(
                replies.ERR_NOTONCHANNEL,
                channel_name,
                "You're not on that channel",
            )
            return

        part_params = [channel_name, reason] if reason else [channel_name]
        part_msg = Message(
            prefix=self.prefix, command="PART", params=part_params
        )
        for member in channel.members:
            await member.send(part_msg)

        channel.remove(self)
        self.channels.discard(channel)

        if not channel.members:
            del self.server.channels[channel_name]

    async def _handle_topic(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "TOPIC", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel or self not in channel.members:
            await self.send_numeric(
                replies.ERR_NOTONCHANNEL,
                channel_name,
                "You're not on that channel",
            )
            return

        if len(msg.params) == 1:
            # Query topic
            if channel.topic:
                await self.send_numeric(
                    replies.RPL_TOPIC, channel_name, channel.topic
                )
            else:
                await self.send_numeric(
                    replies.RPL_NOTOPIC, channel_name, "No topic is set"
                )
        else:
            # Set topic
            channel.topic = msg.params[1]
            topic_msg = Message(
                prefix=self.prefix,
                command="TOPIC",
                params=[channel_name, channel.topic],
            )
            for member in channel.members:
                await member.send(topic_msg)

    async def _handle_names(self, msg: Message) -> None:
        if not msg.params:
            return
        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if channel:
            await self._send_names(channel)

    async def _send_names(self, channel: Channel) -> None:
        nicks = " ".join(m.nick for m in channel.members)
        await self.send_numeric(
            replies.RPL_NAMREPLY, "=", channel.name, nicks
        )
        await self.send_numeric(
            replies.RPL_ENDOFNAMES, channel.name, "End of /NAMES list"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_channel.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add server/client.py tests/test_channel.py
git commit -m "feat: channel operations — JOIN, PART, TOPIC, NAMES"
```

---

### Task 8: Messaging (PRIVMSG, NOTICE)

**Files:**

- Modify: `server/client.py`
- Create: `tests/test_messaging.py`

- [ ] **Step 1: Write failing tests for messaging**

```python
# tests/test_messaging.py
import pytest


@pytest.mark.asyncio
async def test_privmsg_to_channel(server, make_client):
    """PRIVMSG to a channel is relayed to other members."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    # Drain client1's notification of client2 joining
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :Hello agents!")
    response = await client2.recv()
    assert "PRIVMSG" in response
    assert "#general" in response
    assert "Hello agents!" in response
    assert "testserv-ori" in response


@pytest.mark.asyncio
async def test_privmsg_not_echoed_to_sender(server, make_client):
    """Sender does not receive their own PRIVMSG."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :talking to myself")
    lines = await client1.recv_all(timeout=0.5)
    privmsg_lines = [l for l in lines if "PRIVMSG" in l]
    assert len(privmsg_lines) == 0


@pytest.mark.asyncio
async def test_privmsg_dm(server, make_client):
    """PRIVMSG to a nick sends a DM."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")

    await client1.send("PRIVMSG testserv-claude :hey, need your help")
    response = await client2.recv()
    assert "PRIVMSG" in response
    assert "testserv-claude" in response
    assert "hey, need your help" in response


@pytest.mark.asyncio
async def test_privmsg_to_nonexistent_nick(server, make_client):
    """PRIVMSG to unknown nick returns ERR_NOSUCHNICK."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("PRIVMSG testserv-nobody :hello?")
    response = await client.recv()
    assert "401" in response  # ERR_NOSUCHNICK


@pytest.mark.asyncio
async def test_privmsg_to_nonexistent_channel(server, make_client):
    """PRIVMSG to unknown channel returns ERR_NOSUCHCHANNEL."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("PRIVMSG #doesnotexist :hello?")
    response = await client.recv()
    assert "403" in response  # ERR_NOSUCHCHANNEL


@pytest.mark.asyncio
async def test_notice_to_channel(server, make_client):
    """NOTICE to a channel is relayed but generates no error replies."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("NOTICE #general :FYI check the benchmark results")
    response = await client2.recv()
    assert "NOTICE" in response
    assert "FYI check the benchmark results" in response


@pytest.mark.asyncio
async def test_notice_to_nonexistent_channel_no_error(server, make_client):
    """NOTICE to unknown channel produces no error (per RFC)."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("NOTICE #doesnotexist :hello")
    lines = await client.recv_all(timeout=0.5)
    assert len(lines) == 0


@pytest.mark.asyncio
async def test_notice_dm(server, make_client):
    """NOTICE to a nick sends a DM."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")

    await client1.send("NOTICE testserv-claude :ping")
    response = await client2.recv()
    assert "NOTICE" in response
    assert "ping" in response
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_messaging.py -v`
Expected: FAIL — `_handle_privmsg` not defined

- [ ] **Step 3: Add PRIVMSG and NOTICE handlers to Client**

Add these methods to `server/client.py` in the `Client` class:

```python
    async def _handle_privmsg(self, msg: Message) -> None:
        if len(msg.params) < 2:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "PRIVMSG", "Not enough parameters"
            )
            return

        target = msg.params[0]
        text = msg.params[1]
        relay = Message(
            prefix=self.prefix, command="PRIVMSG", params=[target, text]
        )

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if not channel:
                await self.send_numeric(
                    replies.ERR_NOSUCHCHANNEL, target, "No such channel"
                )
                return
            if self not in channel.members:
                await self.send_numeric(
                    replies.ERR_CANNOTSENDTOCHAN, target, "Cannot send to channel"
                )
                return
            for member in channel.members:
                if member is not self:
                    await member.send(relay)
        else:
            recipient = self.server.clients.get(target)
            if not recipient:
                await self.send_numeric(
                    replies.ERR_NOSUCHNICK, target, "No such nick"
                )
                return
            await recipient.send(relay)

    async def _handle_notice(self, msg: Message) -> None:
        # Same as PRIVMSG but no error replies per RFC 2812
        if len(msg.params) < 2:
            return

        target = msg.params[0]
        text = msg.params[1]
        relay = Message(
            prefix=self.prefix, command="NOTICE", params=[target, text]
        )

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if not channel:
                return
            for member in channel.members:
                if member is not self:
                    await member.send(relay)
        else:
            recipient = self.server.clients.get(target)
            if recipient:
                await recipient.send(relay)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_messaging.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add server/client.py tests/test_messaging.py
git commit -m "feat: PRIVMSG and NOTICE — channel and DM messaging"
```

---

### Task 9: QUIT Handler

**Files:**

- Modify: `server/client.py`
- Modify: `tests/test_connection.py`

- [ ] **Step 1: Write failing test for QUIT**

Append to `tests/test_connection.py`:

```python
@pytest.mark.asyncio
async def test_quit_notifies_channel_members(server, make_client):
    """QUIT sends quit message to channel members."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client2.send("QUIT :going offline")
    lines = await client1.recv_all(timeout=1.0)
    quit_lines = [l for l in lines if "QUIT" in l]
    assert len(quit_lines) > 0
    assert "going offline" in quit_lines[0]
    assert "testserv-claude" in quit_lines[0]


@pytest.mark.asyncio
async def test_quit_removes_from_server(server, make_client):
    """After QUIT, nick is available again."""
    client1 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("QUIT :bye")
    await client1.recv_all(timeout=0.5)
    # Small delay for server cleanup
    import asyncio
    await asyncio.sleep(0.2)

    # Should be able to reuse the nick
    client2 = await make_client(nick="testserv-claude", user="claude2")
    assert client2 is not None  # registered successfully
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_connection.py::test_quit_notifies_channel_members -v`
Expected: FAIL — `_handle_quit` not defined or no QUIT relayed

- [ ] **Step 3: Add QUIT handler to Client**

Add this method to `server/client.py` in the `Client` class:

```python
    async def _handle_quit(self, msg: Message) -> None:
        reason = msg.params[0] if msg.params else "Quit"
        quit_msg = Message(
            prefix=self.prefix, command="QUIT", params=[reason]
        )

        notified: set[Client] = set()
        for channel in self.channels:
            for member in channel.members:
                if member is not self and member not in notified:
                    await member.send(quit_msg)
                    notified.add(member)

        raise ConnectionError("Client quit")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_connection.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS (message: 15, connection: 11, channel: 9, messaging: 9 = 44 total)

- [ ] **Step 6: Commit**

```bash
git add server/client.py tests/test_connection.py
git commit -m "feat: QUIT handler with channel notification"
```

---

## Chunk 4: Entry Point + Documentation

### Task 10: Server Entry Point

**Files:**

- Create: `server/__main__.py`

- [ ] **Step 1: Create entry point**

```python
# server/__main__.py
import argparse
import asyncio

from culture.server.config import ServerConfig
from culture.server.ircd import IRCd


async def main() -> None:
    parser = argparse.ArgumentParser(description="culture IRC server")
    parser.add_argument("--name", default="culture", help="Server name (used in nick prefix)")
    parser.add_argument("--host", default="0.0.0.0", help="Listen address")
    parser.add_argument("--port", type=int, default=6667, help="Listen port")
    args = parser.parse_args()

    config = ServerConfig(name=args.name, host=args.host, port=args.port)
    ircd = IRCd(config)
    await ircd.start()
    print(f"culture '{config.name}' listening on {config.host}:{config.port}")

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await ircd.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify it starts**

Run: `timeout 2 uv run culture server start --name spark --port 16667 || true`
Expected: Prints `culture 'spark' listening on 0.0.0.0:16667` then exits on timeout

- [ ] **Step 3: Commit**

```bash
git add server/__main__.py
git commit -m "feat: server entry point — culture server start"
```

---

### Task 11: Feature Documentation

**Files:**

- Create: `docs/layer1-core-irc.md`

- [ ] **Step 1: Write feature doc**

```markdown
# Layer 1: Core IRC Server

## What This Is

A minimal IRC server implementing the core of RFC 2812. Accepts connections from any standard IRC client (weechat, irssi, etc.). Supports channels, messaging, and DMs.

## Running

```bash
# Start with default settings (name: culture, port: 6667)
uv run culture server start

# Start with custom name and port
uv run culture server start --name spark --port 6667
```

## Supported Commands

| Command | Description |
|---------|-------------|
| NICK | Set nickname (must be prefixed with server name, e.g., `spark-ori`) |
| USER | Set username and realname |
| JOIN | Join a channel (channel names start with `#`) |
| PART | Leave a channel |
| PRIVMSG | Send a message to a channel or user (DM) |
| NOTICE | Send a notice (no error replies per RFC) |
| TOPIC | Set or query channel topic |
| NAMES | List members of a channel |
| PING/PONG | Keepalive |
| QUIT | Disconnect |

## Nick Format Enforcement

The server enforces that all nicks start with the server's name followed by a hyphen. On a server named `spark`, only nicks matching `spark-*` are accepted. This ensures globally unique nicks across federated servers.

## Connecting with weechat

```
/server add culture localhost/6667 -autoconnect
/set irc.server.culture.nicks "spark-ori"
/connect culture
/join #general
```

## Testing

```bash
# Run all tests
uv run pytest -v

# Run specific test file
uv run pytest tests/test_channel.py -v
```

```

- [ ] **Step 2: Commit**

```bash
git add docs/layer1-core-irc.md
git commit -m "docs: Layer 1 core IRC feature documentation"
```

---

### Task 12: Manual Validation with weechat

This is not automated — it's the milestone from the spec.

- [ ] **Step 1: Start the server**

Run: `uv run culture server start --name spark --port 6667`

- [ ] **Step 2: Connect with weechat (in a separate terminal)**

```
/server add culture localhost/6667
/set irc.server.culture.nicks "spark-ori"
/connect culture
```

Expected: See welcome messages (001-004)

- [ ] **Step 3: Join a channel and send a message**

```
/join #general
/msg #general Hello from weechat!
```

Expected: Channel joined, message appears

- [ ] **Step 4: Open a second weechat (or other IRC client) and verify two-way chat**

Connect as `spark-culture`, join `#general`, exchange messages.

- [ ] **Step 5: Test DMs**

```
/msg spark-culture Hey, direct message test
```

Expected: Message arrives in the other client

- [ ] **Step 6: Verify nick enforcement**

Try connecting with nick `claude` (no prefix). Expected: Rejected with error.
