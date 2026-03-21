# Layer 5: Agent Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the daemon + Claude Code skill that turns Claude Code into an IRC-native AI agent with supervisor oversight and webhook alerting.

**Architecture:** Each agent is an independent daemon process (Python asyncio) that maintains an IRC connection, manages a Claude Code subprocess, and runs a Sonnet 4.6 supervisor via Agent SDK. The Claude Code process gets IRC tools via a skill installed at `~/.claude/skills/irc/`, communicating with the daemon over a Unix socket using JSON Lines.

**Tech Stack:** Python 3.12+ asyncio, Claude Agent SDK (anthropic), PyYAML, existing protocol/ and server/ layers.

**Spec:** `docs/superpowers/specs/2026-03-21-layer5-agent-harness-design.md`

---

## File Structure

```text
clients/
└── claude/
    ├── __init__.py              # Package marker
    ├── __main__.py              # CLI entry point (agentirc start/stop)
    ├── config.py                # YAML config loading: DaemonConfig, AgentConfig, etc.
    ├── ipc.py                   # IPC message types, JSON Lines encode/decode
    ├── irc_transport.py         # Async IRC client: connect, register, join, buffer
    ├── message_buffer.py        # Per-channel ring buffer with read-cursor tracking
    ├── socket_server.py         # Unix socket server for skill IPC
    ├── webhook.py               # HTTP POST + IRC #alerts dual delivery
    ├── agent_runner.py          # Claude Agent SDK session lifecycle (query, resume, prompt queue)
    ├── supervisor.py            # Sonnet 4.6 supervisor via Agent SDK
    ├── daemon.py                # Main orchestrator tying all components together
    └── skill/                   # Claude Code skill (installed to ~/.claude/skills/irc/)
        ├── SKILL.md             # Skill definition: tool descriptions, usage
        └── irc_client.py        # Standalone CLI: connects to daemon socket, runs tools

tests/
├── test_daemon_config.py        # Config loading tests
├── test_ipc.py                  # IPC protocol encode/decode tests
├── test_irc_transport.py        # IRC transport against real server
├── test_message_buffer.py       # Ring buffer + cursor tests
├── test_socket_server.py        # Unix socket server/client tests
├── test_webhook.py              # Webhook delivery tests
├── test_agent_runner.py         # Claude Code process management tests
├── test_supervisor.py           # Supervisor evaluation logic tests
├── test_daemon.py               # Daemon orchestrator integration tests
└── test_skill_client.py         # Skill IPC client tests

docs/
└── clients/
    └── claude/
        ├── overview.md
        ├── irc-tools.md
        ├── supervisor.md
        ├── context-management.md
        ├── webhooks.md
        └── configuration.md
```

---

### Task 1: Dependencies and Config

**Files:**

- Modify: `pyproject.toml`
- Create: `clients/__init__.py`
- Create: `clients/claude/__init__.py`
- Create: `clients/claude/config.py`
- Create: `tests/test_daemon_config.py`

This task adds required dependencies and implements YAML config loading. The config is the foundation — every other component reads from it.

- [ ] **Step 1: Write failing tests for config loading**

```python
# tests/test_daemon_config.py
import pytest
import tempfile
import os
from pathlib import Path


def test_load_config_from_yaml():
    """Load a complete agents.yaml and verify all fields parse."""
    from clients.claude.config import load_config

    yaml_content = """\
server:
  host: 127.0.0.1
  port: 6667

supervisor:
  model: claude-sonnet-4-6
  thinking: medium
  window_size: 20
  eval_interval: 5
  escalation_threshold: 3

webhooks:
  url: "https://example.com/webhook"
  irc_channel: "#alerts"
  events:
    - agent_spiraling
    - agent_error

buffer_size: 300

agents:
  - nick: spark-claude
    directory: /tmp/test
    channels:
      - "#general"
      - "#dev"
    model: claude-opus-4-6
    thinking: medium
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            assert config.server.host == "127.0.0.1"
            assert config.server.port == 6667
            assert config.supervisor.model == "claude-sonnet-4-6"
            assert config.supervisor.window_size == 20
            assert config.supervisor.eval_interval == 5
            assert config.supervisor.escalation_threshold == 3
            assert config.webhooks.url == "https://example.com/webhook"
            assert config.webhooks.irc_channel == "#alerts"
            assert len(config.webhooks.events) == 2
            assert config.buffer_size == 300
            assert len(config.agents) == 1
            agent = config.agents[0]
            assert agent.nick == "spark-claude"
            assert agent.directory == "/tmp/test"
            assert agent.channels == ["#general", "#dev"]
            assert agent.model == "claude-opus-4-6"
            assert agent.thinking == "medium"
        finally:
            os.unlink(f.name)


def test_load_config_defaults():
    """Missing optional fields get defaults."""
    from clients.claude.config import load_config

    yaml_content = """\
agents:
  - nick: spark-claude
    directory: /tmp
    channels:
      - "#general"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            assert config.server.host == "0.0.0.0"
            assert config.server.port == 6667
            assert config.supervisor.model == "claude-sonnet-4-6"
            assert config.supervisor.thinking == "medium"
            assert config.supervisor.window_size == 20
            assert config.supervisor.eval_interval == 5
            assert config.supervisor.escalation_threshold == 3
            assert config.webhooks.url is None
            assert config.webhooks.irc_channel == "#alerts"
            assert config.buffer_size == 500
            agent = config.agents[0]
            assert agent.model == "claude-opus-4-6"
            assert agent.thinking == "medium"
        finally:
            os.unlink(f.name)


def test_get_agent_by_nick():
    """Look up an agent config by nick."""
    from clients.claude.config import load_config

    yaml_content = """\
agents:
  - nick: spark-claude
    directory: /tmp/a
    channels: ["#general"]
  - nick: spark-claude2
    directory: /tmp/b
    channels: ["#dev"]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            agent = config.get_agent("spark-claude2")
            assert agent is not None
            assert agent.directory == "/tmp/b"
            assert config.get_agent("nonexistent") is None
        finally:
            os.unlink(f.name)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_daemon_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clients'`

- [ ] **Step 3: Add dependencies to pyproject.toml**

```toml
# Add to [project] section:
dependencies = [
    "pyyaml>=6.0",
    "anthropic>=1.0",
]

# Update [tool.hatch.build.targets.wheel]:
packages = ["protocol", "server", "clients"]
```

- [ ] **Step 4: Create package init files**

```python
# clients/__init__.py
# (empty)

# clients/claude/__init__.py
# (empty)
```

- [ ] **Step 5: Implement config.py**

```python
# clients/claude/config.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ServerConnConfig:
    """IRC server connection settings."""
    host: str = "0.0.0.0"
    port: int = 6667


@dataclass
class SupervisorConfig:
    """Supervisor sub-agent settings."""
    model: str = "claude-sonnet-4-6"
    thinking: str = "medium"
    window_size: int = 20
    eval_interval: int = 5
    escalation_threshold: int = 3


@dataclass
class WebhookConfig:
    """Webhook alerting settings."""
    url: str | None = None
    irc_channel: str = "#alerts"
    events: list[str] = field(default_factory=lambda: [
        "agent_spiraling", "agent_error", "agent_question",
        "agent_timeout", "agent_complete",
    ])


@dataclass
class AgentConfig:
    """Per-agent settings."""
    nick: str = ""
    directory: str = "."
    channels: list[str] = field(default_factory=lambda: ["#general"])
    model: str = "claude-opus-4-6"
    thinking: str = "medium"


@dataclass
class DaemonConfig:
    """Top-level daemon configuration."""
    server: ServerConnConfig = field(default_factory=ServerConnConfig)
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    buffer_size: int = 500
    agents: list[AgentConfig] = field(default_factory=list)

    def get_agent(self, nick: str) -> AgentConfig | None:
        for agent in self.agents:
            if agent.nick == nick:
                return agent
        return None


def load_config(path: str | Path) -> DaemonConfig:
    """Load daemon config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server = ServerConnConfig(**raw.get("server", {}))
    supervisor = SupervisorConfig(**raw.get("supervisor", {}))

    webhooks_raw = raw.get("webhooks", {})
    webhooks = WebhookConfig(**webhooks_raw) if webhooks_raw else WebhookConfig()

    agents = []
    for agent_raw in raw.get("agents", []):
        agents.append(AgentConfig(**agent_raw))

    return DaemonConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        buffer_size=raw.get("buffer_size", 500),
        agents=agents,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_daemon_config.py -v`
Expected: All 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add clients/ tests/test_daemon_config.py pyproject.toml
git commit -m "feat(layer5): add daemon config loading from YAML"
```

---

### Task 2: IPC Protocol

**Files:**

- Create: `clients/claude/ipc.py`
- Create: `tests/test_ipc.py`

JSON Lines encode/decode for daemon ↔ skill communication. Foundation for socket server and skill client.

- [ ] **Step 1: Write failing tests for IPC encoding/decoding**

```python
# tests/test_ipc.py
import json
import uuid

from clients.claude.ipc import (
    encode_message,
    decode_message,
    make_request,
    make_response,
    make_whisper,
    MSG_TYPE_RESPONSE,
    MSG_TYPE_WHISPER,
)


def test_encode_decode_roundtrip():
    """A message survives encode → decode."""
    msg = {"type": "irc_send", "id": "abc", "channel": "#general", "message": "hello"}
    line = encode_message(msg)
    assert line.endswith(b"\n")
    decoded = decode_message(line)
    assert decoded == msg


def test_make_request_has_uuid():
    """make_request generates a unique ID."""
    req = make_request("irc_send", channel="#general", message="hi")
    assert req["type"] == "irc_send"
    assert "id" in req
    # Verify it's a valid UUID
    uuid.UUID(req["id"])
    assert req["channel"] == "#general"
    assert req["message"] == "hi"


def test_make_response():
    """make_response creates a response tied to a request ID."""
    resp = make_response("abc123", ok=True, data={"messages": []})
    assert resp["type"] == MSG_TYPE_RESPONSE
    assert resp["id"] == "abc123"
    assert resp["ok"] is True
    assert resp["data"] == {"messages": []}


def test_make_response_error():
    """make_response with error."""
    resp = make_response("abc123", ok=False, error="channel not found")
    assert resp["ok"] is False
    assert resp["error"] == "channel not found"


def test_make_whisper():
    """make_whisper creates a supervisor whisper."""
    w = make_whisper("You're spiraling", "CORRECTION")
    assert w["type"] == MSG_TYPE_WHISPER
    assert w["message"] == "You're spiraling"
    assert w["whisper_type"] == "CORRECTION"


def test_decode_ignores_blank_lines():
    """Blank or whitespace-only lines return None."""
    assert decode_message(b"\n") is None
    assert decode_message(b"  \n") is None
    assert decode_message(b"") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ipc.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement ipc.py**

```python
# clients/claude/ipc.py
from __future__ import annotations

import json
import uuid
from typing import Any

MSG_TYPE_RESPONSE = "response"
MSG_TYPE_WHISPER = "whisper"


def encode_message(msg: dict[str, Any]) -> bytes:
    """Encode a message as a JSON line (newline-terminated bytes)."""
    return json.dumps(msg, separators=(",", ":")).encode() + b"\n"


def decode_message(line: bytes) -> dict[str, Any] | None:
    """Decode a JSON line into a message dict. Returns None for blank lines."""
    stripped = line.strip()
    if not stripped:
        return None
    return json.loads(stripped)


def make_request(msg_type: str, **kwargs: Any) -> dict[str, Any]:
    """Create a request message with a unique ID."""
    return {"type": msg_type, "id": str(uuid.uuid4()), **kwargs}


def make_response(
    request_id: str,
    ok: bool = True,
    data: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Create a response message tied to a request ID."""
    msg: dict[str, Any] = {"type": MSG_TYPE_RESPONSE, "id": request_id, "ok": ok}
    if data is not None:
        msg["data"] = data
    if error is not None:
        msg["error"] = error
    return msg


def make_whisper(message: str, whisper_type: str) -> dict[str, Any]:
    """Create a supervisor whisper message."""
    return {"type": MSG_TYPE_WHISPER, "message": message, "whisper_type": whisper_type}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ipc.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add clients/claude/ipc.py tests/test_ipc.py
git commit -m "feat(layer5): add IPC protocol for daemon-skill communication"
```

---

### Task 3: Message Buffer

**Files:**

- Create: `clients/claude/message_buffer.py`
- Create: `tests/test_message_buffer.py`

Per-channel ring buffer with read-cursor tracking. Separated from IRC transport for testability.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_message_buffer.py
import time
from clients.claude.message_buffer import MessageBuffer, BufferedMessage


def test_add_and_read():
    """Add messages, read them back."""
    buf = MessageBuffer(max_per_channel=100)
    buf.add("#general", "spark-ori", "hello")
    buf.add("#general", "spark-claude", "hi there")

    msgs = buf.read("#general", limit=50)
    assert len(msgs) == 2
    assert msgs[0].nick == "spark-ori"
    assert msgs[0].text == "hello"
    assert msgs[1].nick == "spark-claude"


def test_read_returns_since_last_read():
    """Second read only returns new messages."""
    buf = MessageBuffer(max_per_channel=100)
    buf.add("#general", "a", "msg1")
    buf.add("#general", "b", "msg2")

    msgs1 = buf.read("#general", limit=50)
    assert len(msgs1) == 2

    buf.add("#general", "c", "msg3")
    msgs2 = buf.read("#general", limit=50)
    assert len(msgs2) == 1
    assert msgs2[0].text == "msg3"


def test_read_empty_channel():
    """Reading a channel with no messages returns empty list."""
    buf = MessageBuffer(max_per_channel=100)
    assert buf.read("#empty", limit=50) == []


def test_ring_buffer_eviction():
    """Oldest messages are evicted when buffer is full."""
    buf = MessageBuffer(max_per_channel=5)
    for i in range(10):
        buf.add("#general", "bot", f"msg{i}")

    # Buffer holds last 5
    msgs = buf.read("#general", limit=100)
    assert len(msgs) == 5
    assert msgs[0].text == "msg5"
    assert msgs[-1].text == "msg9"


def test_limit_caps_results():
    """Limit parameter caps the number of returned messages."""
    buf = MessageBuffer(max_per_channel=100)
    for i in range(20):
        buf.add("#general", "bot", f"msg{i}")

    msgs = buf.read("#general", limit=5)
    # Returns the 5 most recent
    assert len(msgs) == 5
    assert msgs[0].text == "msg15"


def test_multiple_channels_independent():
    """Channels have independent buffers and cursors."""
    buf = MessageBuffer(max_per_channel=100)
    buf.add("#general", "a", "gen1")
    buf.add("#dev", "b", "dev1")

    gen_msgs = buf.read("#general", limit=50)
    assert len(gen_msgs) == 1
    assert gen_msgs[0].text == "gen1"

    dev_msgs = buf.read("#dev", limit=50)
    assert len(dev_msgs) == 1
    assert dev_msgs[0].text == "dev1"


def test_messages_have_timestamps():
    """Each buffered message has a timestamp."""
    buf = MessageBuffer(max_per_channel=100)
    before = time.time()
    buf.add("#general", "ori", "test")
    after = time.time()

    msgs = buf.read("#general", limit=1)
    assert before <= msgs[0].timestamp <= after
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_message_buffer.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement message_buffer.py**

```python
# clients/claude/message_buffer.py
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class BufferedMessage:
    nick: str
    text: str
    timestamp: float


class MessageBuffer:
    """Per-channel ring buffer with read-cursor tracking."""

    def __init__(self, max_per_channel: int = 500):
        self.max_per_channel = max_per_channel
        self._buffers: dict[str, deque[BufferedMessage]] = {}
        self._cursors: dict[str, int] = {}  # channel -> index of next unread
        self._totals: dict[str, int] = {}   # channel -> total messages ever added

    def add(self, channel: str, nick: str, text: str) -> None:
        """Add a message to a channel's buffer."""
        if channel not in self._buffers:
            self._buffers[channel] = deque(maxlen=self.max_per_channel)
            self._totals[channel] = 0
            self._cursors[channel] = 0

        self._buffers[channel].append(
            BufferedMessage(nick=nick, text=text, timestamp=time.time())
        )
        self._totals[channel] += 1

    def read(self, channel: str, limit: int = 50) -> list[BufferedMessage]:
        """Read messages since last read, up to limit (most recent)."""
        buf = self._buffers.get(channel)
        if not buf:
            return []

        total = self._totals[channel]
        cursor = self._cursors.get(channel, 0)

        # How many new messages since last read
        new_count = total - cursor
        if new_count <= 0:
            return []

        # The buffer is a deque — new messages are at the end
        # We want the last new_count entries, capped by limit
        available = list(buf)
        new_messages = available[-new_count:] if new_count <= len(available) else available

        # Apply limit (return most recent)
        if len(new_messages) > limit:
            new_messages = new_messages[-limit:]

        # Advance cursor
        self._cursors[channel] = total

        return new_messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_message_buffer.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add clients/claude/message_buffer.py tests/test_message_buffer.py
git commit -m "feat(layer5): add per-channel message buffer with read cursors"
```

---

### Task 4: IRC Transport

**Files:**

- Create: `clients/claude/irc_transport.py`
- Create: `tests/test_irc_transport.py`

Async IRC client that connects to the server, registers a nick, joins channels, and buffers incoming messages. Uses the existing `protocol/message.py` for parsing. Tests against real server instances using the existing `conftest.py` fixtures.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_irc_transport.py
import asyncio
import pytest
import pytest_asyncio
from clients.claude.irc_transport import IRCTransport
from clients.claude.message_buffer import MessageBuffer


@pytest.mark.asyncio
async def test_connect_and_register(server):
    """Transport connects and registers nick."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    try:
        # Wait for registration to complete
        await asyncio.sleep(0.3)
        assert transport.connected
        assert "testserv-bot" in server.clients
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_joins_channels(server):
    """Transport auto-joins configured channels after registration."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general", "#dev"],
        buffer=buf,
    )
    await transport.connect()
    try:
        await asyncio.sleep(0.3)
        assert "#general" in server.channels
        assert "#dev" in server.channels
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_buffers_incoming_messages(server, make_client):
    """Messages from other clients are buffered."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)

    # Another client joins and sends a message
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #general :hello bot")
    await asyncio.sleep(0.3)

    msgs = buf.read("#general", limit=50)
    assert any(m.text == "hello bot" and m.nick == "testserv-ori" for m in msgs)

    await transport.disconnect()


@pytest.mark.asyncio
async def test_send_privmsg(server, make_client):
    """Transport can send PRIVMSG to a channel."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    await transport.send_privmsg("#general", "hello human")
    response = await human.recv(timeout=2.0)
    assert "hello human" in response

    await transport.disconnect()


@pytest.mark.asyncio
async def test_send_join_part(server):
    """Transport can join and part channels dynamically."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)

    await transport.join_channel("#new")
    await asyncio.sleep(0.2)
    assert "#new" in server.channels

    await transport.part_channel("#new")
    await asyncio.sleep(0.2)
    # Channel removed when last member leaves
    assert "#new" not in server.channels

    await transport.disconnect()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_irc_transport.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement irc_transport.py**

```python
# clients/claude/irc_transport.py
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from protocol.message import Message
from clients.claude.message_buffer import MessageBuffer

logger = logging.getLogger(__name__)


class IRCTransport:
    """Async IRC client for the daemon. Connects, registers, joins, buffers."""

    def __init__(
        self,
        host: str,
        port: int,
        nick: str,
        user: str,
        channels: list[str],
        buffer: MessageBuffer,
        on_mention: Callable[[str, str, str], None] | None = None,
    ):
        self.host = host
        self.port = port
        self.nick = nick
        self.user = user
        self.channels = list(channels)
        self.buffer = buffer
        self.on_mention = on_mention
        self.connected = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._reconnecting = False
        self._should_run = False

    async def connect(self) -> None:
        """Connect to IRC server, register, and join channels."""
        self._should_run = True
        await self._do_connect()

    async def _do_connect(self) -> None:
        """Internal connect with actual TCP setup."""
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )
        await self._send_raw(f"NICK {self.nick}")
        await self._send_raw(f"USER {self.user} 0 * :{self.user}")
        self._read_task = asyncio.create_task(self._read_loop())

    async def disconnect(self) -> None:
        """Disconnect from IRC server."""
        self._should_run = False
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                await self._send_raw("QUIT :daemon shutdown")
            except (ConnectionError, OSError):
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
        self.connected = False

    async def send_privmsg(self, target: str, text: str) -> None:
        """Send a PRIVMSG to a channel or nick."""
        await self._send_raw(f"PRIVMSG {target} :{text}")

    async def join_channel(self, channel: str) -> None:
        """Join a channel."""
        await self._send_raw(f"JOIN {channel}")
        if channel not in self.channels:
            self.channels.append(channel)

    async def part_channel(self, channel: str) -> None:
        """Leave a channel."""
        await self._send_raw(f"PART {channel}")
        if channel in self.channels:
            self.channels.remove(channel)

    async def send_who(self, target: str) -> list[str]:
        """Send WHO and collect responses. Returns raw response lines."""
        # WHO responses handled in _read_loop, collected via future
        # For simplicity, this is fire-and-forget; the caller reads from buffer
        await self._send_raw(f"WHO {target}")

    async def _send_raw(self, line: str) -> None:
        """Send a raw IRC line."""
        if self._writer:
            self._writer.write(f"{line}\r\n".encode())
            await self._writer.drain()

    async def _read_loop(self) -> None:
        """Read IRC messages and dispatch."""
        buf = ""
        try:
            while True:
                data = await self._reader.read(4096)
                if not data:
                    break
                buf += data.decode("utf-8", errors="replace")
                buf = buf.replace("\r\n", "\n").replace("\r", "\n")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if line.strip():
                        msg = Message.parse(line)
                        await self._handle(msg)
        except asyncio.CancelledError:
            return
        except (ConnectionError, OSError):
            logger.warning("IRC connection lost")
        finally:
            self.connected = False
            if self._should_run and not self._reconnecting:
                asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Reconnect with exponential backoff (1s, 2s, 4s, ..., max 60s)."""
        self._reconnecting = True
        delay = 1
        while self._should_run:
            logger.info("Reconnecting to IRC in %ds...", delay)
            await asyncio.sleep(delay)
            try:
                await self._do_connect()
                logger.info("Reconnected to IRC")
                self._reconnecting = False
                return
            except (ConnectionError, OSError):
                delay = min(delay * 2, 60)

    async def _handle(self, msg: Message) -> None:
        """Handle an incoming IRC message."""
        if msg.command == "PING":
            token = msg.params[0] if msg.params else ""
            await self._send_raw(f"PONG :{token}")

        elif msg.command == "001":
            # Welcome — registration complete, join channels
            self.connected = True
            for channel in self.channels:
                await self._send_raw(f"JOIN {channel}")

        elif msg.command == "PRIVMSG" and len(msg.params) >= 2:
            target = msg.params[0]
            text = msg.params[1]
            sender = msg.prefix.split("!")[0] if msg.prefix else "unknown"

            if sender == self.nick:
                return  # Ignore own messages

            if target.startswith("#"):
                # Channel message — buffer it
                self.buffer.add(target, sender, text)
            else:
                # DM — buffer under sender's nick as channel key
                self.buffer.add(f"DM:{sender}", sender, text)

            # Check for @mention
            if self.on_mention and f"@{self.nick}" in text:
                self.on_mention(target, sender, text)

        elif msg.command == "NOTICE" and len(msg.params) >= 2:
            # Buffer notices too (mention notifications, etc.)
            target = msg.params[0]
            text = msg.params[1]
            sender = msg.prefix.split("!")[0] if msg.prefix else "server"
            if target.startswith("#"):
                self.buffer.add(target, sender, text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_irc_transport.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add clients/claude/irc_transport.py tests/test_irc_transport.py
git commit -m "feat(layer5): add async IRC transport with message buffering"
```

---

### Task 5: Socket Server

**Files:**

- Create: `clients/claude/socket_server.py`
- Create: `tests/test_socket_server.py`

Unix socket server that handles JSON Lines IPC between the daemon and the Claude Code skill. Routes incoming requests, delivers responses and whispers.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_socket_server.py
import asyncio
import json
import os
import tempfile
import pytest

from clients.claude.socket_server import SocketServer
from clients.claude.ipc import encode_message, decode_message, make_request


@pytest.mark.asyncio
async def test_socket_server_accepts_connection():
    """Server starts and accepts a client connection."""
    sock_path = os.path.join(tempfile.mkdtemp(), "test.sock")
    handler_called = asyncio.Event()
    received_msgs = []

    async def handler(msg):
        received_msgs.append(msg)
        handler_called.set()
        return {"type": "response", "id": msg["id"], "ok": True}

    srv = SocketServer(sock_path, handler)
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        req = make_request("irc_channels")
        writer.write(encode_message(req))
        await writer.drain()

        # Read response
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        resp = decode_message(data)
        assert resp["ok"] is True
        assert resp["id"] == req["id"]

        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()
        os.unlink(sock_path)


@pytest.mark.asyncio
async def test_socket_server_sends_whisper():
    """Server can push an unsolicited whisper to connected clients."""
    sock_path = os.path.join(tempfile.mkdtemp(), "test.sock")

    async def handler(msg):
        return {"type": "response", "id": msg["id"], "ok": True}

    srv = SocketServer(sock_path, handler)
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)

        # Push a whisper
        await srv.send_whisper("You're spiraling", "CORRECTION")

        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        whisper = decode_message(data)
        assert whisper["type"] == "whisper"
        assert whisper["whisper_type"] == "CORRECTION"
        assert "spiraling" in whisper["message"]

        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()
        os.unlink(sock_path)


@pytest.mark.asyncio
async def test_socket_permissions():
    """Socket file is created with 0600 permissions."""
    sock_path = os.path.join(tempfile.mkdtemp(), "test.sock")

    async def handler(msg):
        return {"type": "response", "id": msg["id"], "ok": True}

    srv = SocketServer(sock_path, handler)
    await srv.start()
    try:
        mode = os.stat(sock_path).st_mode & 0o777
        assert mode == 0o600
    finally:
        await srv.stop()
        os.unlink(sock_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_socket_server.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement socket_server.py**

```python
# clients/claude/socket_server.py
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Awaitable

from clients.claude.ipc import encode_message, decode_message, make_whisper

logger = logging.getLogger(__name__)

RequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class SocketServer:
    """Unix socket server for daemon ↔ skill IPC."""

    def __init__(self, path: str, handler: RequestHandler):
        self.path = path
        self.handler = handler
        self._server: asyncio.Server | None = None
        self._clients: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        """Start listening on the Unix socket."""
        # Remove stale socket
        if os.path.exists(self.path):
            os.unlink(self.path)

        self._server = await asyncio.start_unix_server(
            self._handle_client, path=self.path
        )
        # Set restrictive permissions
        os.chmod(self.path, 0o600)

    async def stop(self) -> None:
        """Stop the socket server and close all client connections."""
        for writer in self._clients:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError, OSError):
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def send_whisper(self, message: str, whisper_type: str) -> None:
        """Push a whisper to all connected clients."""
        whisper = make_whisper(message, whisper_type)
        data = encode_message(whisper)
        for writer in list(self._clients):
            try:
                writer.write(data)
                await writer.drain()
            except (ConnectionError, BrokenPipeError, OSError):
                self._clients.remove(writer)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a connected skill client."""
        self._clients.append(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                msg = decode_message(line)
                if msg is None:
                    continue
                try:
                    response = await self.handler(msg)
                    writer.write(encode_message(response))
                    await writer.drain()
                except Exception:
                    logger.exception("Handler error for message: %s", msg)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if writer in self._clients:
                self._clients.remove(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError, OSError):
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_socket_server.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add clients/claude/socket_server.py tests/test_socket_server.py
git commit -m "feat(layer5): add Unix socket server for skill IPC"
```

---

### Task 6: Webhook Client

**Files:**

- Create: `clients/claude/webhook.py`
- Create: `tests/test_webhook.py`

Dual-delivery alerting: HTTP POST to configured URL + IRC PRIVMSG to `#alerts`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_webhook.py
import asyncio
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import pytest

from clients.claude.webhook import WebhookClient, AlertEvent
from clients.claude.config import WebhookConfig


class WebhookCapture(BaseHTTPRequestHandler):
    """Captures POST requests for testing."""
    received = []

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        WebhookCapture.received.append(body)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass  # Suppress output


@pytest.mark.asyncio
async def test_webhook_http_post():
    """Fires HTTP POST with correct payload."""
    WebhookCapture.received.clear()
    http = HTTPServer(("127.0.0.1", 0), WebhookCapture)
    port = http.server_address[1]
    thread = threading.Thread(target=http.handle_request, daemon=True)
    thread.start()

    config = WebhookConfig(
        url=f"http://127.0.0.1:{port}/webhook",
        irc_channel="#alerts",
        events=["agent_error"],
    )
    client = WebhookClient(config, irc_send=None)
    event = AlertEvent(
        event_type="agent_error",
        nick="spark-claude",
        message='[ERROR] spark-claude crashed: exit code 1',
    )
    await client.fire(event)
    thread.join(timeout=2.0)

    assert len(WebhookCapture.received) == 1
    assert "spark-claude" in WebhookCapture.received[0]["content"]
    http.server_close()


@pytest.mark.asyncio
async def test_webhook_irc_fallback():
    """Fires IRC PRIVMSG to #alerts channel."""
    sent_messages = []

    async def mock_irc_send(channel, text):
        sent_messages.append((channel, text))

    config = WebhookConfig(url=None, irc_channel="#alerts", events=["agent_error"])
    client = WebhookClient(config, irc_send=mock_irc_send)
    event = AlertEvent(
        event_type="agent_error",
        nick="spark-claude",
        message="[ERROR] spark-claude crashed",
    )
    await client.fire(event)

    assert len(sent_messages) == 1
    assert sent_messages[0][0] == "#alerts"
    assert "spark-claude" in sent_messages[0][1]


@pytest.mark.asyncio
async def test_webhook_skips_unconfigured_events():
    """Events not in the config's event list are silently skipped."""
    sent_messages = []

    async def mock_irc_send(channel, text):
        sent_messages.append((channel, text))

    config = WebhookConfig(url=None, irc_channel="#alerts", events=["agent_error"])
    client = WebhookClient(config, irc_send=mock_irc_send)
    event = AlertEvent(
        event_type="agent_complete",  # Not in events list
        nick="spark-claude",
        message="[COMPLETE] done",
    )
    await client.fire(event)

    assert len(sent_messages) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_webhook.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement webhook.py**

```python
# clients/claude/webhook.py
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from clients.claude.config import WebhookConfig

logger = logging.getLogger(__name__)


@dataclass
class AlertEvent:
    event_type: str
    nick: str
    message: str


class WebhookClient:
    """Dual-delivery alerting: HTTP POST + IRC channel."""

    def __init__(
        self,
        config: WebhookConfig,
        irc_send: Callable[[str, str], Awaitable[None]] | None = None,
    ):
        self.config = config
        self.irc_send = irc_send

    async def fire(self, event: AlertEvent) -> None:
        """Fire an alert event to all configured channels."""
        if event.event_type not in self.config.events:
            return

        # IRC fallback (always, if irc_send is available)
        if self.irc_send:
            try:
                await self.irc_send(self.config.irc_channel, event.message)
            except Exception:
                logger.exception("Failed to send IRC alert")

        # HTTP webhook
        if self.config.url:
            try:
                await self._http_post(event)
            except Exception:
                logger.exception("Webhook POST failed to %s", self.config.url)

    async def _http_post(self, event: AlertEvent) -> None:
        """POST to webhook URL. Runs in thread to avoid blocking event loop."""
        payload = json.dumps({"content": event.message}).encode()

        def _post():
            req = urllib.request.Request(
                self.config.url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)

        await asyncio.to_thread(_post)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webhook.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add clients/claude/webhook.py tests/test_webhook.py
git commit -m "feat(layer5): add webhook client with HTTP + IRC dual delivery"
```

---

### Task 7: Agent Runner

**Files:**

- Create: `clients/claude/agent_runner.py`
- Create: `tests/test_agent_runner.py`

Manages the Claude Agent SDK session lifecycle. Uses `query()` with `permission_mode="bypassPermissions"`, queues prompts for commands (compact/clear), handles crash recovery.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agent_runner.py
import asyncio
import pytest

from clients.claude.agent_runner import AgentRunner


@pytest.mark.asyncio
async def test_spawn_process():
    """AgentRunner spawns a subprocess and reports it running."""
    # Use a simple long-running command instead of claude for testing
    runner = AgentRunner(
        command=["python3", "-u", "-c", "import time; time.sleep(60)"],
        directory="/tmp",
    )
    await runner.start()
    try:
        assert runner.is_running()
    finally:
        await runner.stop()
    assert not runner.is_running()


@pytest.mark.asyncio
async def test_stdin_pipe():
    """Can write to the subprocess stdin."""
    # Echo back what we send via stdin
    runner = AgentRunner(
        command=["python3", "-u", "-c",
                 "import sys\nfor line in sys.stdin:\n    print('GOT:' + line.strip(), flush=True)"],
        directory="/tmp",
    )
    await runner.start()
    try:
        await runner.write_stdin("hello\n")
        # Read from stdout
        line = await asyncio.wait_for(runner.read_stdout_line(), timeout=2.0)
        assert "GOT:hello" in line
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_on_exit_callback():
    """on_exit callback fires when process exits."""
    exit_codes = []

    async def on_exit(code):
        exit_codes.append(code)

    runner = AgentRunner(
        command=["python3", "-c", "pass"],  # Exits immediately
        directory="/tmp",
        on_exit=on_exit,
    )
    await runner.start()
    # Wait for process to exit
    await asyncio.sleep(0.5)
    assert len(exit_codes) == 1
    assert exit_codes[0] == 0


@pytest.mark.asyncio
async def test_crash_detection():
    """Detects non-zero exit code."""
    exit_codes = []

    async def on_exit(code):
        exit_codes.append(code)

    runner = AgentRunner(
        command=["python3", "-c", "raise SystemExit(1)"],
        directory="/tmp",
        on_exit=on_exit,
    )
    await runner.start()
    await asyncio.sleep(0.5)
    assert len(exit_codes) == 1
    assert exit_codes[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement agent_runner.py**

```python
# clients/claude/agent_runner.py
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


class AgentRunner:
    """Manages a Claude Code subprocess."""

    def __init__(
        self,
        command: list[str],
        directory: str,
        on_exit: Callable[[int], Awaitable[None]] | None = None,
        on_stdout: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.command = command
        self.directory = directory
        self.on_exit = on_exit
        self.on_stdout = on_stdout
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task | None = None
        self._stdout_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Spawn the subprocess."""
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.directory,
        )
        self._monitor_task = asyncio.create_task(self._monitor())
        if self.on_stdout:
            self._stdout_task = asyncio.create_task(self._read_stdout())

    async def stop(self) -> None:
        """Terminate the subprocess."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        if self._stdout_task:
            self._stdout_task.cancel()
            try:
                await self._stdout_task
            except asyncio.CancelledError:
                pass

    def is_running(self) -> bool:
        """Check if subprocess is alive."""
        return self._process is not None and self._process.returncode is None

    async def write_stdin(self, text: str) -> None:
        """Write text to the subprocess stdin."""
        if self._process and self._process.stdin:
            self._process.stdin.write(text.encode())
            await self._process.stdin.drain()

    async def read_stdout_line(self) -> str:
        """Read a single line from stdout."""
        if self._process and self._process.stdout:
            line = await self._process.stdout.readline()
            return line.decode().rstrip("\n")
        return ""

    async def _monitor(self) -> None:
        """Wait for process exit and fire callback."""
        if not self._process:
            return
        code = await self._process.wait()
        if self.on_exit:
            await self.on_exit(code)

    async def _read_stdout(self) -> None:
        """Continuously read stdout and fire callback."""
        try:
            while self._process and self._process.stdout:
                line = await self._process.stdout.readline()
                if not line:
                    break
                if self.on_stdout:
                    await self.on_stdout(line.decode().rstrip("\n"))
        except asyncio.CancelledError:
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent_runner.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add clients/claude/agent_runner.py tests/test_agent_runner.py
git commit -m "feat(layer5): add agent runner for Claude Code subprocess management"
```

---

### Task 8: Supervisor

**Files:**

- Create: `clients/claude/supervisor.py`
- Create: `tests/test_supervisor.py`

The supervisor evaluates agent activity and generates whispers. Tests use a mock evaluation function to avoid real API calls.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_supervisor.py
import asyncio
import pytest

from clients.claude.supervisor import Supervisor, SupervisorVerdict


def test_verdict_parsing():
    """Parse supervisor model output into structured verdict."""
    assert SupervisorVerdict.parse("OK") == SupervisorVerdict(action="OK", message="")
    assert SupervisorVerdict.parse("CORRECTION You're spiraling") == SupervisorVerdict(
        action="CORRECTION", message="You're spiraling"
    )
    assert SupervisorVerdict.parse("THINK_DEEPER This needs more thought") == SupervisorVerdict(
        action="THINK_DEEPER", message="This needs more thought"
    )
    assert SupervisorVerdict.parse("ESCALATION Still stuck") == SupervisorVerdict(
        action="ESCALATION", message="Still stuck"
    )


@pytest.mark.asyncio
async def test_rolling_window():
    """Supervisor maintains a rolling window of activity."""
    whispers = []

    async def on_whisper(msg, wtype):
        whispers.append((msg, wtype))

    # Mock evaluator that always returns OK
    async def mock_eval(window, task):
        return SupervisorVerdict(action="OK", message="")

    sup = Supervisor(
        window_size=5,
        eval_interval=3,
        escalation_threshold=3,
        evaluate_fn=mock_eval,
        on_whisper=on_whisper,
        on_escalation=None,
        task_description="test task",
    )

    # Feed 6 turns — triggers eval after every 3
    for i in range(6):
        await sup.observe({"turn": i, "type": "response", "content": f"turn {i}"})

    assert len(sup._window) == 5  # Window capped
    assert len(whispers) == 0  # All OK, no whispers


@pytest.mark.asyncio
async def test_whisper_on_correction():
    """Supervisor whispers when evaluator returns CORRECTION."""
    whispers = []

    async def on_whisper(msg, wtype):
        whispers.append((msg, wtype))

    async def mock_eval(window, task):
        return SupervisorVerdict(action="CORRECTION", message="Stop retrying")

    sup = Supervisor(
        window_size=20,
        eval_interval=2,
        escalation_threshold=3,
        evaluate_fn=mock_eval,
        on_whisper=on_whisper,
        on_escalation=None,
        task_description="test task",
    )

    for i in range(2):
        await sup.observe({"turn": i})

    assert len(whispers) == 1
    assert whispers[0] == ("Stop retrying", "CORRECTION")


@pytest.mark.asyncio
async def test_escalation_after_threshold():
    """Supervisor escalates after escalation_threshold consecutive non-OK verdicts."""
    whispers = []
    escalated = []

    async def on_whisper(msg, wtype):
        whispers.append((msg, wtype))

    async def on_escalation(msg):
        escalated.append(msg)

    call_count = 0

    async def mock_eval(window, task):
        nonlocal call_count
        call_count += 1
        return SupervisorVerdict(action="CORRECTION", message=f"Attempt {call_count}")

    sup = Supervisor(
        window_size=20,
        eval_interval=1,  # Eval every turn
        escalation_threshold=3,
        evaluate_fn=mock_eval,
        on_whisper=on_whisper,
        on_escalation=on_escalation,
        task_description="test task",
    )

    for i in range(3):
        await sup.observe({"turn": i})

    # First 2 are whispers, 3rd triggers escalation
    assert len(whispers) == 2
    assert len(escalated) == 1


@pytest.mark.asyncio
async def test_ok_resets_escalation_counter():
    """An OK verdict resets the consecutive failure counter."""
    whispers = []
    escalated = []

    async def on_whisper(msg, wtype):
        whispers.append((msg, wtype))

    async def on_escalation(msg):
        escalated.append(msg)

    verdicts = iter([
        SupervisorVerdict(action="CORRECTION", message="warn1"),
        SupervisorVerdict(action="CORRECTION", message="warn2"),
        SupervisorVerdict(action="OK", message=""),  # Resets counter
        SupervisorVerdict(action="CORRECTION", message="warn3"),
        SupervisorVerdict(action="CORRECTION", message="warn4"),
    ])

    async def mock_eval(window, task):
        return next(verdicts)

    sup = Supervisor(
        window_size=20,
        eval_interval=1,
        escalation_threshold=3,
        evaluate_fn=mock_eval,
        on_whisper=on_whisper,
        on_escalation=on_escalation,
        task_description="test task",
    )

    for i in range(5):
        await sup.observe({"turn": i})

    # No escalation — OK reset the counter before reaching threshold
    assert len(escalated) == 0
    assert len(whispers) == 4  # All 4 corrections delivered as whispers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_supervisor.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement supervisor.py**

```python
# clients/claude/supervisor.py
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class SupervisorVerdict:
    action: str  # OK, CORRECTION, THINK_DEEPER, ESCALATION
    message: str

    @classmethod
    def parse(cls, text: str) -> SupervisorVerdict:
        """Parse raw model output into a verdict."""
        text = text.strip()
        if text == "OK":
            return cls(action="OK", message="")
        parts = text.split(" ", 1)
        action = parts[0]
        message = parts[1] if len(parts) > 1 else ""
        return cls(action=action, message=message)


# Type alias for the evaluation function
EvaluateFn = Callable[[list[dict[str, Any]], str], Awaitable[SupervisorVerdict]]


class Supervisor:
    """Watches agent activity and intervenes when unproductive."""

    def __init__(
        self,
        window_size: int,
        eval_interval: int,
        escalation_threshold: int,
        evaluate_fn: EvaluateFn,
        on_whisper: Callable[[str, str], Awaitable[None]] | None,
        on_escalation: Callable[[str], Awaitable[None]] | None,
        task_description: str = "",
    ):
        self.window_size = window_size
        self.eval_interval = eval_interval
        self.escalation_threshold = escalation_threshold
        self.evaluate_fn = evaluate_fn
        self.on_whisper = on_whisper
        self.on_escalation = on_escalation
        self.task_description = task_description

        self._window: deque[dict[str, Any]] = deque(maxlen=window_size)
        self._turn_count: int = 0
        self._consecutive_failures: int = 0
        self.paused: bool = False

    async def observe(self, turn: dict[str, Any]) -> None:
        """Record an agent turn and evaluate if interval reached."""
        self._window.append(turn)
        self._turn_count += 1

        if self._turn_count % self.eval_interval == 0:
            await self._evaluate()

    async def _evaluate(self) -> None:
        """Run the evaluation function on the rolling window."""
        if self.paused:
            return

        try:
            verdict = await self.evaluate_fn(list(self._window), self.task_description)
        except Exception:
            logger.exception("Supervisor evaluation failed")
            return

        if verdict.action == "OK":
            self._consecutive_failures = 0
            return

        self._consecutive_failures += 1

        if self._consecutive_failures >= self.escalation_threshold:
            # Escalate
            self.paused = True
            if self.on_escalation:
                await self.on_escalation(verdict.message)
        else:
            # Whisper
            if self.on_whisper:
                await self.on_whisper(verdict.message, verdict.action)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_supervisor.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add clients/claude/supervisor.py tests/test_supervisor.py
git commit -m "feat(layer5): add supervisor with rolling window and escalation ladder"
```

---

### Task 9: Daemon Orchestrator

**Files:**

- Create: `clients/claude/daemon.py`
- Create: `tests/test_daemon.py`

Ties all components together. Starts IRC transport, socket server, agent runner, supervisor, and webhook client. Routes messages between them.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_daemon.py
import asyncio
import os
import tempfile
import pytest

from clients.claude.daemon import AgentDaemon
from clients.claude.config import (
    DaemonConfig, ServerConnConfig, AgentConfig,
    SupervisorConfig, WebhookConfig,
)


@pytest.mark.asyncio
async def test_daemon_starts_and_connects(server):
    """Daemon starts, connects to IRC, and registers nick."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        supervisor=SupervisorConfig(),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(
        nick="testserv-bot",
        directory="/tmp",
        channels=["#general"],
    )

    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    try:
        await asyncio.sleep(0.5)
        assert "testserv-bot" in server.clients
        assert "#general" in server.channels
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_ipc_irc_send(server, make_client):
    """Skill client can send IRC messages through the daemon."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(
        nick="testserv-bot",
        directory="/tmp",
        channels=["#general"],
    )

    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    # A human joins to receive messages
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    # Connect to daemon socket and send irc_send request
    from clients.claude.ipc import encode_message, decode_message, make_request
    sock_path = os.path.join(sock_dir, "testserv-bot.sock")
    reader, writer = await asyncio.open_unix_connection(sock_path)

    req = make_request("irc_send", channel="#general", message="hello from skill")
    writer.write(encode_message(req))
    await writer.drain()

    # Read response from socket
    data = await asyncio.wait_for(reader.readline(), timeout=2.0)
    resp = decode_message(data)
    assert resp["ok"] is True

    # Verify human received the message
    msg = await human.recv(timeout=2.0)
    assert "hello from skill" in msg

    writer.close()
    await writer.wait_closed()
    await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_ipc_irc_read(server, make_client):
    """Skill client can read buffered messages through the daemon."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(
        nick="testserv-bot",
        directory="/tmp",
        channels=["#general"],
    )

    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    # Human sends a message
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #general :test message")
    await asyncio.sleep(0.3)

    # Connect to daemon socket and send irc_read request
    from clients.claude.ipc import encode_message, decode_message, make_request
    sock_path = os.path.join(sock_dir, "testserv-bot.sock")
    reader, writer = await asyncio.open_unix_connection(sock_path)

    req = make_request("irc_read", channel="#general", limit=50)
    writer.write(encode_message(req))
    await writer.drain()

    data = await asyncio.wait_for(reader.readline(), timeout=2.0)
    resp = decode_message(data)
    assert resp["ok"] is True
    assert len(resp["data"]["messages"]) >= 1
    assert any("test message" in m["text"] for m in resp["data"]["messages"])

    writer.close()
    await writer.wait_closed()
    await daemon.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement daemon.py**

```python
# clients/claude/daemon.py
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import time

from clients.claude.config import DaemonConfig, AgentConfig
from clients.claude.ipc import make_response, MSG_TYPE_RESPONSE
from clients.claude.irc_transport import IRCTransport
from clients.claude.message_buffer import MessageBuffer
from clients.claude.socket_server import SocketServer
from clients.claude.webhook import WebhookClient, AlertEvent
from clients.claude.agent_runner import AgentRunner
from clients.claude.supervisor import Supervisor, SupervisorVerdict

logger = logging.getLogger(__name__)

MAX_CRASH_COUNT = 3
CRASH_WINDOW_SECONDS = 300  # 5 minutes
CRASH_RESTART_DELAY = 5  # seconds


class AgentDaemon:
    """Main orchestrator for a single agent."""

    def __init__(
        self,
        config: DaemonConfig,
        agent: AgentConfig,
        socket_dir: str | None = None,
        skip_claude: bool = False,
    ):
        self.config = config
        self.agent = agent
        self.skip_claude = skip_claude

        self.buffer = MessageBuffer(max_per_channel=config.buffer_size)

        # Resolve socket path
        runtime_dir = socket_dir or os.environ.get(
            "XDG_RUNTIME_DIR", "/tmp"
        )
        self.socket_path = os.path.join(runtime_dir, f"{agent.nick}.sock")

        # Components (initialized in start())
        self.transport: IRCTransport | None = None
        self.socket_server: SocketServer | None = None
        self.webhook: WebhookClient | None = None
        self.supervisor: Supervisor | None = None
        self.agent_runner: AgentRunner | None = None

        # Crash recovery state
        self._crash_times: list[float] = []

    async def start(self) -> None:
        """Start all daemon components."""
        # IRC Transport
        self.transport = IRCTransport(
            host=self.config.server.host,
            port=self.config.server.port,
            nick=self.agent.nick,
            user=self.agent.nick.split("-", 1)[-1] if "-" in self.agent.nick else self.agent.nick,
            channels=list(self.agent.channels),
            buffer=self.buffer,
        )
        await self.transport.connect()

        # Webhook client
        self.webhook = WebhookClient(
            config=self.config.webhooks,
            irc_send=self.transport.send_privmsg,
        )

        # Socket server
        self.socket_server = SocketServer(self.socket_path, self._handle_ipc)
        await self.socket_server.start()

        # Agent runner (Claude Agent SDK session)
        if not self.skip_claude:
            await self._start_agent()

    async def _start_agent(self) -> None:
        """Start the Claude Agent SDK session."""
        self.agent_runner = AgentRunner(
            # Uses Claude Agent SDK query() with permission_mode="bypassPermissions"
            directory=self.agent.directory,
            on_exit=self._on_agent_exit,
        )
        await self.agent_runner.start()

    async def _on_agent_exit(self, code: int) -> None:
        """Handle Claude Code process exit with crash recovery."""
        if code == 0:
            # Clean exit
            if self.webhook:
                await self.webhook.fire(AlertEvent(
                    event_type="agent_complete",
                    nick=self.agent.nick,
                    message=f"[COMPLETE] {self.agent.nick} session ended cleanly.",
                ))
            return

        # Crash — record and check circuit breaker
        now = time.time()
        self._crash_times.append(now)
        # Keep only crashes within the window
        self._crash_times = [t for t in self._crash_times if now - t < CRASH_WINDOW_SECONDS]

        if self.webhook:
            await self.webhook.fire(AlertEvent(
                event_type="agent_error",
                nick=self.agent.nick,
                message=f"[ERROR] {self.agent.nick} crashed with exit code {code}.",
            ))

        if len(self._crash_times) >= MAX_CRASH_COUNT:
            # Circuit breaker tripped
            logger.error("%s crashed %d times in %ds — stopping restarts",
                         self.agent.nick, MAX_CRASH_COUNT, CRASH_WINDOW_SECONDS)
            if self.webhook:
                await self.webhook.fire(AlertEvent(
                    event_type="agent_spiraling",
                    nick=self.agent.nick,
                    message=f"[ESCALATION] {self.agent.nick} crashed {MAX_CRASH_COUNT} times "
                            f"in {CRASH_WINDOW_SECONDS}s. Manual intervention required.",
                ))
            return

        # Restart after delay
        logger.info("Restarting %s in %ds...", self.agent.nick, CRASH_RESTART_DELAY)
        await asyncio.sleep(CRASH_RESTART_DELAY)
        await self._start_agent()

    async def stop(self) -> None:
        """Stop all daemon components."""
        if self.agent_runner:
            await self.agent_runner.stop()
        if self.socket_server:
            await self.socket_server.stop()
        if self.transport:
            await self.transport.disconnect()
        # Clean up socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    async def _handle_ipc(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Route IPC requests from the skill client."""
        msg_type = msg.get("type", "")
        msg_id = msg.get("id", "")

        try:
            if msg_type == "irc_send":
                await self.transport.send_privmsg(msg["channel"], msg["message"])
                return make_response(msg_id, ok=True)

            elif msg_type == "irc_read":
                messages = self.buffer.read(
                    msg["channel"], limit=msg.get("limit", 50)
                )
                return make_response(msg_id, ok=True, data={
                    "messages": [
                        {"nick": m.nick, "text": m.text, "timestamp": m.timestamp}
                        for m in messages
                    ]
                })

            elif msg_type == "irc_join":
                await self.transport.join_channel(msg["channel"])
                return make_response(msg_id, ok=True)

            elif msg_type == "irc_part":
                await self.transport.part_channel(msg["channel"])
                return make_response(msg_id, ok=True)

            elif msg_type == "irc_channels":
                return make_response(msg_id, ok=True, data={
                    "channels": self.transport.channels,
                })

            elif msg_type == "irc_who":
                await self.transport.send_who(msg["channel"])
                return make_response(msg_id, ok=True)

            elif msg_type == "irc_ask":
                # Post question and wait for @mention response
                channel = msg["channel"]
                question = msg["question"]
                timeout = msg.get("timeout", 300)
                await self.transport.send_privmsg(channel, question)
                # Fire webhook for question event
                if self.webhook:
                    await self.webhook.fire(AlertEvent(
                        event_type="agent_question",
                        nick=self.agent.nick,
                        message=f"[QUESTION] {self.agent.nick} needs input: \"{question}\"",
                    ))
                # TODO: implement response waiting with mention matching
                return make_response(msg_id, ok=True, data={"response": None})

            elif msg_type == "compact":
                if self.agent_runner and self.agent_runner.is_running():
                    await self.agent_runner.write_stdin("/compact\n")
                return make_response(msg_id, ok=True)

            elif msg_type == "clear":
                if self.agent_runner and self.agent_runner.is_running():
                    await self.agent_runner.write_stdin("/clear\n")
                return make_response(msg_id, ok=True)

            else:
                return make_response(msg_id, ok=False, error=f"Unknown type: {msg_type}")

        except Exception as e:
            logger.exception("IPC handler error for %s", msg_type)
            return make_response(msg_id, ok=False, error=str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_daemon.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add clients/claude/daemon.py tests/test_daemon.py
git commit -m "feat(layer5): add daemon orchestrator with IPC routing"
```

---

### Task 10: IRC Skill Client

**Files:**

- Create: `clients/claude/skill/SKILL.md`
- Create: `clients/claude/skill/irc_client.py`
- Create: `tests/test_skill_client.py`

Standalone CLI tool that Claude Code invokes via Bash. Connects to the daemon's Unix socket, sends requests, returns results.

- [ ] **Step 1: Write failing test**

```python
# tests/test_skill_client.py
import asyncio
import json
import os
import tempfile
import pytest

from clients.claude.ipc import make_response, encode_message
from clients.claude.skill.irc_client import SkillClient


@pytest.mark.asyncio
async def test_skill_client_send():
    """Skill client sends irc_send and gets response."""
    sock_dir = tempfile.mkdtemp()
    sock_path = os.path.join(sock_dir, "test-agent.sock")

    # Mock daemon socket server
    async def mock_handler(reader, writer):
        data = await reader.readline()
        msg = json.loads(data)
        resp = make_response(msg["id"], ok=True)
        writer.write(encode_message(resp))
        await writer.drain()
        writer.close()

    srv = await asyncio.start_unix_server(mock_handler, path=sock_path)
    try:
        client = SkillClient(sock_path)
        await client.connect()
        result = await client.irc_send("#general", "hello")
        assert result["ok"] is True
        await client.close()
    finally:
        srv.close()
        await srv.wait_closed()
        os.unlink(sock_path)


@pytest.mark.asyncio
async def test_skill_client_read():
    """Skill client sends irc_read and gets buffered messages."""
    sock_dir = tempfile.mkdtemp()
    sock_path = os.path.join(sock_dir, "test-agent.sock")

    async def mock_handler(reader, writer):
        data = await reader.readline()
        msg = json.loads(data)
        resp = make_response(msg["id"], ok=True, data={
            "messages": [{"nick": "ori", "text": "hello", "timestamp": 123.0}]
        })
        writer.write(encode_message(resp))
        await writer.drain()
        writer.close()

    srv = await asyncio.start_unix_server(mock_handler, path=sock_path)
    try:
        client = SkillClient(sock_path)
        await client.connect()
        result = await client.irc_read("#general", limit=50)
        assert result["ok"] is True
        assert len(result["data"]["messages"]) == 1
        await client.close()
    finally:
        srv.close()
        await srv.wait_closed()
        os.unlink(sock_path)


@pytest.mark.asyncio
async def test_skill_client_queues_whispers():
    """Whispers received between calls are queued."""
    sock_dir = tempfile.mkdtemp()
    sock_path = os.path.join(sock_dir, "test-agent.sock")

    from clients.claude.ipc import make_whisper

    async def mock_handler(reader, writer):
        # Send a whisper first (unsolicited)
        whisper = make_whisper("Stop retrying", "CORRECTION")
        writer.write(encode_message(whisper))
        await writer.drain()
        # Then handle the request
        data = await reader.readline()
        msg = json.loads(data)
        resp = make_response(msg["id"], ok=True, data={"channels": ["#general"]})
        writer.write(encode_message(resp))
        await writer.drain()
        writer.close()

    srv = await asyncio.start_unix_server(mock_handler, path=sock_path)
    try:
        client = SkillClient(sock_path)
        await client.connect()
        # Give time for whisper to arrive
        await asyncio.sleep(0.1)
        assert len(client.pending_whispers) == 1
        assert client.pending_whispers[0]["whisper_type"] == "CORRECTION"
        await client.close()
    finally:
        srv.close()
        await srv.wait_closed()
        os.unlink(sock_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_skill_client.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create skill directory and \_\_init\_\_.py**

```python
# clients/claude/skill/__init__.py
# (empty)
```

- [ ] **Step 4: Implement irc_client.py**

```python
# clients/claude/skill/irc_client.py
"""
Standalone IRC skill client for Claude Code.
Connects to the daemon's Unix socket and provides IRC tools.

Usage (from Claude Code Bash tool):
    python -m clients.claude.skill.irc_client send "#general" "hello"
    python -m clients.claude.skill.irc_client read "#general" --limit 50
    python -m clients.claude.skill.irc_client ask "#general" "question?" --timeout 300
    python -m clients.claude.skill.irc_client join "#channel"
    python -m clients.claude.skill.irc_client part "#channel"
    python -m clients.claude.skill.irc_client channels
    python -m clients.claude.skill.irc_client who "#channel"
    python -m clients.claude.skill.irc_client compact
    python -m clients.claude.skill.irc_client clear
    python -m clients.claude.skill.irc_client set-directory /path
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from clients.claude.ipc import encode_message, decode_message, make_request, MSG_TYPE_WHISPER


class SkillClient:
    """Async client that communicates with the daemon over Unix socket."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self.pending_whispers: list[dict[str, Any]] = []

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(
            self.socket_path
        )
        self._read_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, BrokenPipeError, OSError):
                pass

    async def _read_loop(self) -> None:
        """Read responses and whispers from the daemon."""
        try:
            while self._reader:
                line = await self._reader.readline()
                if not line:
                    break
                msg = decode_message(line)
                if msg is None:
                    continue
                if msg.get("type") == MSG_TYPE_WHISPER:
                    self.pending_whispers.append(msg)
                elif "id" in msg and msg["id"] in self._pending:
                    self._pending[msg["id"]].set_result(msg)
        except asyncio.CancelledError:
            return
        except (ConnectionError, OSError):
            pass

    async def _request(self, msg_type: str, **kwargs: Any) -> dict[str, Any]:
        """Send a request and wait for response."""
        req = make_request(msg_type, **kwargs)
        future = asyncio.get_running_loop().create_future()
        self._pending[req["id"]] = future

        self._writer.write(encode_message(req))
        await self._writer.drain()

        try:
            return await asyncio.wait_for(future, timeout=600)
        finally:
            self._pending.pop(req["id"], None)

    async def irc_send(self, channel: str, message: str) -> dict[str, Any]:
        return await self._request("irc_send", channel=channel, message=message)

    async def irc_read(self, channel: str, limit: int = 50) -> dict[str, Any]:
        return await self._request("irc_read", channel=channel, limit=limit)

    async def irc_ask(self, channel: str, question: str, timeout: int = 300) -> dict[str, Any]:
        return await self._request("irc_ask", channel=channel, question=question, timeout=timeout)

    async def irc_join(self, channel: str) -> dict[str, Any]:
        return await self._request("irc_join", channel=channel)

    async def irc_part(self, channel: str) -> dict[str, Any]:
        return await self._request("irc_part", channel=channel)

    async def irc_channels(self) -> dict[str, Any]:
        return await self._request("irc_channels")

    async def irc_who(self, channel: str) -> dict[str, Any]:
        return await self._request("irc_who", channel=channel)

    async def compact(self) -> dict[str, Any]:
        return await self._request("compact")

    async def clear(self) -> dict[str, Any]:
        return await self._request("clear")

    async def set_directory(self, path: str) -> dict[str, Any]:
        return await self._request("set_directory", path=path)

    def drain_whispers(self) -> list[dict[str, Any]]:
        """Return and clear any pending whispers."""
        whispers = list(self.pending_whispers)
        self.pending_whispers.clear()
        return whispers


def _resolve_socket_path() -> str:
    """Find the daemon socket. Reads AGENTIRC_NICK env var."""
    nick = os.environ.get("AGENTIRC_NICK", "")
    if not nick:
        print("Error: AGENTIRC_NICK environment variable not set", file=sys.stderr)
        sys.exit(1)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return os.path.join(runtime_dir, f"{nick}.sock")


async def _main(args: list[str]) -> None:
    """CLI entry point."""
    if not args:
        print("Usage: irc_client.py <command> [args...]", file=sys.stderr)
        sys.exit(1)

    client = SkillClient(_resolve_socket_path())
    await client.connect()

    try:
        cmd = args[0]
        if cmd == "send" and len(args) >= 3:
            result = await client.irc_send(args[1], " ".join(args[2:]))
        elif cmd == "read" and len(args) >= 2:
            limit = 50
            if "--limit" in args:
                idx = args.index("--limit")
                limit = int(args[idx + 1])
            result = await client.irc_read(args[1], limit=limit)
        elif cmd == "ask" and len(args) >= 3:
            timeout = 300
            remaining = args[2:]
            if "--timeout" in remaining:
                idx = remaining.index("--timeout")
                timeout = int(remaining[idx + 1])
                remaining = remaining[:idx] + remaining[idx + 2:]
            question = " ".join(remaining)
            result = await client.irc_ask(args[1], question, timeout=timeout)
        elif cmd == "join" and len(args) >= 2:
            result = await client.irc_join(args[1])
        elif cmd == "part" and len(args) >= 2:
            result = await client.irc_part(args[1])
        elif cmd == "channels":
            result = await client.irc_channels()
        elif cmd == "who" and len(args) >= 2:
            result = await client.irc_who(args[1])
        elif cmd == "compact":
            result = await client.compact()
        elif cmd == "clear":
            result = await client.clear()
        elif cmd == "set-directory" and len(args) >= 2:
            result = await client.set_directory(args[1])
        else:
            print(f"Unknown command: {cmd}", file=sys.stderr)
            sys.exit(1)

        # Prepend any whispers
        whispers = client.drain_whispers()
        for w in whispers:
            print(f"[SUPERVISOR/{w['whisper_type']}] {w['message']}")

        # Print result
        if result.get("ok"):
            data = result.get("data")
            if data:
                print(json.dumps(data, indent=2))
            else:
                print("OK")
        else:
            print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
            sys.exit(1)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
```

- [ ] **Step 5: Write SKILL.md**

````markdown
# IRC Skill

Connect to IRC channels, communicate with other agents and humans,
and manage your working context through the agentirc daemon.

## Tools

### irc_send
Send a message to an IRC channel or user.
```bash
python -m clients.claude.skill.irc_client send "<channel>" "<message>"
```

### irc_read

Read recent messages from a channel since your last read.

```bash
python -m clients.claude.skill.irc_client read "<channel>" --limit 50
```

### irc_ask

Post a question and wait for a response directed at you.

```bash
python -m clients.claude.skill.irc_client ask "<channel>" "<question>" --timeout 300
```

### irc_join / irc_part

Join or leave an IRC channel.

```bash
python -m clients.claude.skill.irc_client join "<channel>"
python -m clients.claude.skill.irc_client part "<channel>"
```

### channels / who

List your channels or members of a channel.

```bash
python -m clients.claude.skill.irc_client channels
python -m clients.claude.skill.irc_client who "<channel>"
```

### compact / clear

Manage your conversation context.

```bash
python -m clients.claude.skill.irc_client compact
python -m clients.claude.skill.irc_client clear
```

### set-directory

Change your working directory and load its CLAUDE.md.

```bash
python -m clients.claude.skill.irc_client set-directory "/path/to/project"
```

## Guidelines

- Check IRC periodically between subtasks with `irc_read`
- Share progress via `irc_send` after completing significant work
- Use `irc_ask` when you need input from others
- Compact your context when transitioning between phases
- Clear your context when starting a completely new task

````

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_skill_client.py -v`
Expected: All 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add clients/claude/skill/ tests/test_skill_client.py
git commit -m "feat(layer5): add IRC skill client for Claude Code"
```

---

### Task 11: CLI Entry Point

**Files:**

- Create: `clients/claude/__main__.py`
- Modify: `pyproject.toml` (add script entry point)

The `agentirc` command that starts agents from config.

- [ ] **Step 1: Implement \_\_main\_\_.py**

```python
# clients/claude/__main__.py
"""CLI entry point for the agentirc daemon.

Usage:
    agentirc start <nick>       Start a single agent by nick
    agentirc start --all        Start all agents from config
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from clients.claude.config import load_config
from clients.claude.daemon import AgentDaemon

logger = logging.getLogger("agentirc")

DEFAULT_CONFIG = os.path.expanduser("~/.agentirc/agents.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentirc", description="agentirc agent daemon")
    sub = parser.add_subparsers(dest="command")

    start_parser = sub.add_parser("start", help="Start agent daemon(s)")
    start_parser.add_argument("nick", nargs="?", help="Agent nick to start")
    start_parser.add_argument("--all", action="store_true", help="Start all agents")
    start_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    args = parser.parse_args()

    if args.command != "start":
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config = load_config(args.config)

    if args.all:
        agents = config.agents
    elif args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            logger.error("Agent '%s' not found in config", args.nick)
            sys.exit(1)
        agents = [agent]
    else:
        start_parser.print_help()
        sys.exit(1)

    if not agents:
        logger.error("No agents configured")
        sys.exit(1)

    if len(agents) == 1:
        # Single agent — run in this process
        asyncio.run(_run_single(config, agents[0]))
    else:
        # Multiple agents — fork each as a separate process
        _run_multi(config, agents)


async def _run_single(config, agent) -> None:
    """Run a single agent daemon in the current process."""
    daemon = AgentDaemon(config, agent)
    await daemon.start()
    logger.info("Agent %s started", agent.nick)

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()
    logger.info("Shutting down %s", agent.nick)
    await daemon.stop()


def _run_multi(config, agents) -> None:
    """Fork a separate process per agent."""
    pids = []
    for agent in agents:
        pid = os.fork()
        if pid == 0:
            # Child process
            asyncio.run(_run_single(config, agent))
            sys.exit(0)
        else:
            pids.append((pid, agent.nick))
            logger.info("Started %s (pid %d)", agent.nick, pid)

    # Parent waits for all children
    for pid, nick in pids:
        os.waitpid(pid, 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add script entry point to pyproject.toml**

```toml
[project.scripts]
agentirc = "clients.claude.__main__:main"
```

- [ ] **Step 3: Verify CLI help works**

Run: `uv run agentirc --help`
Expected: Shows usage with start subcommand

- [ ] **Step 4: Commit**

```bash
git add clients/claude/__main__.py pyproject.toml
git commit -m "feat(layer5): add agentirc CLI entry point"
```

---

### Task 12: Documentation

**Files:**

- Create: `docs/clients/claude/overview.md`
- Create: `docs/clients/claude/irc-tools.md`
- Create: `docs/clients/claude/supervisor.md`
- Create: `docs/clients/claude/context-management.md`
- Create: `docs/clients/claude/webhooks.md`
- Create: `docs/clients/claude/configuration.md`
- Create: `docs/layer5-agent-harness.md`

Behavioral documentation — what each feature does and when to use it. Not implementation details.

- [ ] **Step 1: Create docs directory**

Run: `mkdir -p docs/clients/claude`

- [ ] **Step 2: Write overview.md**

Covers: what the daemon is, the three components (IRC transport, Claude Code process, supervisor), how they work together, and the daemon lifecycle.

- [ ] **Step 3: Write irc-tools.md**

Covers: each IRC tool (send, read, ask, join, part, channels, who), their behavior, and when to use them. Include the CLI invocation syntax.

- [ ] **Step 4: Write supervisor.md**

Covers: what the supervisor watches for, whisper types (CORRECTION, THINK_DEEPER, ESCALATION), the escalation ladder, and pause/resume behavior.

- [ ] **Step 5: Write context-management.md**

Covers: compact and clear, when to use each, set_directory behavior, and how the agent's system prompt encourages proactive context management.

- [ ] **Step 6: Write webhooks.md**

Covers: events that trigger alerts, dual delivery (HTTP + IRC), message format, and how to configure webhooks.

- [ ] **Step 7: Write configuration.md**

Covers: the `~/.agentirc/agents.yaml` format with all fields and defaults, CLI usage (`agentirc start`), and example configs.

- [ ] **Step 8: Write docs/layer5-agent-harness.md**

Top-level Layer 5 doc matching the pattern of `layer1-core-irc.md` through `layer4-federation.md`.

- [ ] **Step 9: Run markdownlint on all docs**

Run: `markdownlint-cli2 "docs/clients/claude/*.md" "docs/layer5-agent-harness.md"`
Expected: 0 errors

- [ ] **Step 10: Commit**

```bash
git add docs/
git commit -m "docs(layer5): add agent harness feature documentation"
```

---

### Task 13: Integration Tests

**Files:**

- Create: `tests/test_integration_layer5.py`

End-to-end test: start a real IRC server, start a daemon (without Claude Code), verify the full flow through the socket.

- [ ] **Step 1: Write integration tests**

```python
# tests/test_integration_layer5.py
"""End-to-end Layer 5 integration tests.

Starts a real IRC server and daemon, verifies the full flow:
skill client → Unix socket → daemon → IRC server → human client.
"""
import asyncio
import json
import os
import tempfile
import pytest

from clients.claude.config import (
    DaemonConfig, ServerConnConfig, AgentConfig, WebhookConfig,
)
from clients.claude.daemon import AgentDaemon
from clients.claude.skill.irc_client import SkillClient


@pytest.mark.asyncio
async def test_full_send_receive_flow(server, make_client):
    """Agent sends via skill → human receives on IRC → human replies → agent reads."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])

    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    # Human joins
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    # Skill client connects
    sock_path = os.path.join(sock_dir, "testserv-bot.sock")
    skill = SkillClient(sock_path)
    await skill.connect()

    try:
        # Agent sends
        result = await skill.irc_send("#general", "hello from agent")
        assert result["ok"]

        msg = await human.recv(timeout=2.0)
        assert "hello from agent" in msg

        # Human replies
        await human.send("PRIVMSG #general :hello back agent")
        await asyncio.sleep(0.3)

        # Agent reads
        result = await skill.irc_read("#general", limit=50)
        assert result["ok"]
        messages = result["data"]["messages"]
        assert any("hello back agent" in m["text"] for m in messages)

    finally:
        await skill.close()
        await daemon.stop()


@pytest.mark.asyncio
async def test_join_part_via_skill(server):
    """Skill client can join and part channels dynamically."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])

    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    sock_path = os.path.join(sock_dir, "testserv-bot.sock")
    skill = SkillClient(sock_path)
    await skill.connect()

    try:
        result = await skill.irc_join("#testing")
        assert result["ok"]
        await asyncio.sleep(0.2)
        assert "#testing" in server.channels

        result = await skill.irc_channels()
        assert result["ok"]
        assert "#testing" in result["data"]["channels"]

        result = await skill.irc_part("#testing")
        assert result["ok"]
        await asyncio.sleep(0.2)
        assert "#testing" not in server.channels

    finally:
        await skill.close()
        await daemon.stop()


@pytest.mark.asyncio
async def test_webhook_fires_on_question(server, make_client):
    """Webhook fires when agent uses irc_ask."""
    irc_alerts = []

    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None, irc_channel="#alerts", events=["agent_question"]),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general", "#alerts"])

    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    # Human watching #alerts
    watcher = await make_client(nick="testserv-watch", user="watch")
    await watcher.send("JOIN #alerts")
    await watcher.recv_all(timeout=0.3)

    sock_path = os.path.join(sock_dir, "testserv-bot.sock")
    skill = SkillClient(sock_path)
    await skill.connect()

    try:
        await skill.irc_ask("#general", "what cmake flags?", timeout=1)
        await asyncio.sleep(0.5)

        # Check that #alerts got the notification
        alerts = await watcher.recv_all(timeout=1.0)
        assert any("QUESTION" in line for line in alerts)

    finally:
        await skill.close()
        await daemon.stop()
```

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/test_integration_layer5.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: All existing tests (layers 1-4) still pass, plus all new Layer 5 tests

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration_layer5.py
git commit -m "test(layer5): add end-to-end integration tests"
```
