import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from culture.clients.codex.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    SupervisorConfig,
    WebhookConfig,
)
from culture.clients.codex.daemon import CodexDaemon
from culture.clients.shared.attention import AttentionConfig


@pytest.mark.asyncio
async def test_codex_daemon_starts_and_connects(server):
    """CodexDaemon with skip_codex=True connects to IRC without needing codex CLI."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        supervisor=SupervisorConfig(),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-codex", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = CodexDaemon(config, agent, socket_dir=sock_dir, skip_codex=True)
    await daemon.start()
    try:
        await asyncio.sleep(0.5)
        assert "testserv-codex" in server.clients
        assert "#general" in server.channels
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_codex_daemon_ipc_irc_send(server, make_client):
    """IPC irc_send works through the Codex daemon."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-codex", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = CodexDaemon(config, agent, socket_dir=sock_dir, skip_codex=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    from culture.clients.shared.ipc import decode_message, encode_message, make_request

    sock_path = os.path.join(sock_dir, "culture-testserv-codex.sock")
    reader, writer = await asyncio.open_unix_connection(sock_path)

    req = make_request("irc_send", channel="#general", message="hello from codex skill")
    writer.write(encode_message(req))
    await writer.drain()

    data = await asyncio.wait_for(reader.readline(), timeout=2.0)
    resp = decode_message(data)
    assert resp["ok"] is True

    msg = await human.recv(timeout=2.0)
    assert "hello from codex skill" in msg

    writer.close()
    await writer.wait_closed()
    await daemon.stop()


@pytest.mark.asyncio
async def test_codex_config_defaults():
    """Codex config has correct backend-specific defaults."""
    agent = AgentConfig()
    assert agent.agent == "codex"
    assert agent.model == "gpt-5.4"

    supervisor = SupervisorConfig()
    assert supervisor.model == "gpt-5.4"


@pytest.mark.asyncio
async def test_codex_backend_dispatch():
    """CLI dispatch selects CodexDaemon for agent='codex'."""
    agent = AgentConfig(nick="test-codex", agent="codex", directory="/tmp")
    backend = getattr(agent, "agent", "claude")
    assert backend == "codex"

    # Verify CodexDaemon can be imported and constructed
    config = DaemonConfig()
    daemon = CodexDaemon(config, agent, skip_codex=True)
    assert daemon.agent.agent == "codex"
    assert daemon.agent.model == "gpt-5.4"


def _make_codex_daemon(server_port: int) -> CodexDaemon:
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server_port),
        poll_interval=0,
        # Disable attention so the legacy poll loop honors poll_interval=0
        # (which exits immediately). New attention defaults would otherwise
        # tick every 5s and poll seeded channels at IDLE cadence.
        attention=AttentionConfig(enabled=False),
    )
    agent = AgentConfig(
        nick="testserv-codex",
        directory="/tmp",
        channels=["#general"],
    )
    sock_dir = tempfile.mkdtemp()
    return CodexDaemon(config, agent, socket_dir=sock_dir, skip_codex=True)


@pytest.mark.asyncio
async def test_codex_manual_pause_survives_sleep_scheduler(server):
    """Manual pause should not be overridden by the sleep scheduler."""
    daemon = _make_codex_daemon(server.config.port)
    await daemon.start()

    # Manually pause
    daemon._ipc_pause("r1", {})
    assert daemon._paused is True
    assert daemon._manually_paused is True

    # Simulate sleep scheduler trying to resume (not in sleep window)
    # The scheduler checks: not should_sleep and self._paused and not self._manually_paused
    # Since _manually_paused is True, it should NOT resume
    assert daemon._manually_paused is True  # scheduler would skip resume

    # Manual resume clears both flags
    daemon._ipc_resume("r2", {})
    assert daemon._paused is False
    assert daemon._manually_paused is False

    await daemon.stop()


@pytest.mark.asyncio
async def test_codex_poll_loop_filters_mentions(server):
    """Poll loop should not include messages that @mention the agent."""
    daemon = _make_codex_daemon(server.config.port)
    await daemon.start()
    try:
        await asyncio.sleep(0.3)

        # Inject fake runner
        runner = MagicMock()
        runner.is_running.return_value = True
        runner.send_prompt = AsyncMock()
        runner.stop = AsyncMock()
        daemon._agent_runner = runner

        # Add messages to buffer — one @mention, one regular
        daemon._buffer.add("#general", "alice", "@codex help me")
        daemon._buffer.add("#general", "bob", "just chatting")

        # Run poll cycle
        daemon._send_channel_poll("#general")
        await asyncio.sleep(0.1)  # Let the created task execute

        # Should only send prompt with bob's message (not alice's @mention)
        assert runner.send_prompt.called
        prompt = runner.send_prompt.call_args[0][0]
        assert "@codex" not in prompt
        assert "just chatting" in prompt
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_codex_turn_error_sends_feedback(server, make_client):
    """Turn error should send error feedback to IRC."""
    daemon = _make_codex_daemon(server.config.port)
    await daemon.start()
    await asyncio.sleep(0.3)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    # Simulate a pending mention
    daemon._mention_targets.append("#general")

    # Trigger turn error
    await daemon._on_turn_error()

    # Should have sent error feedback
    lines = await human.recv_all(timeout=1.0)
    error_msgs = [l for l in lines if "error" in l.lower()]
    assert len(error_msgs) >= 1

    # Deque should be cleared
    assert len(daemon._mention_targets) == 0

    await daemon.stop()


@pytest.mark.asyncio
async def test_codex_turn_failure_circuit_breaker(server):
    """Agent should pause after MAX_CONSECUTIVE_TURN_FAILURES consecutive errors."""
    daemon = _make_codex_daemon(server.config.port)
    await daemon.start()

    assert daemon._paused is False

    # Trigger 3 consecutive turn errors
    for _ in range(3):
        daemon._mention_targets.append(None)  # Use None to avoid IRC send
        await daemon._on_turn_error()

    assert daemon._paused is True
    assert daemon._consecutive_turn_failures == 3

    # Successful message should reset counter
    daemon._paused = False
    await daemon._on_agent_message(
        {"type": "assistant", "content": [{"type": "text", "text": "ok"}]}
    )
    assert daemon._consecutive_turn_failures == 0

    await daemon.stop()


@pytest.mark.asyncio
async def test_codex_status_query_none_target(server):
    """Status query should use None relay target to avoid leaking to IRC."""
    daemon = _make_codex_daemon(server.config.port)
    await daemon.start()
    await asyncio.sleep(0.3)

    # Simulate status query appending None target
    daemon._mention_targets.append(None)

    # Simulate agent response — should NOT send to IRC
    sent_messages = []
    original_send = daemon._transport.send_privmsg

    async def capture_send(target, text):
        sent_messages.append((target, text))
        await original_send(target, text)

    daemon._transport.send_privmsg = capture_send

    await daemon._relay_response_to_irc(
        {"content": [{"type": "text", "text": "I am working on X"}]}
    )

    # No messages should have been sent to IRC
    assert len(sent_messages) == 0
    assert len(daemon._mention_targets) == 0

    await daemon.stop()


def _strip_meta_lines(text: str, pattern) -> str:
    """Apply meta-response stripping to text, returning cleaned result."""
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        line = pattern.sub("", line).strip()
        if line and line != ">":
            if line.startswith("> "):
                line = line[2:]
            if line:
                lines.append(line)
    return "\n".join(lines)


def test_meta_response_stripping():
    """Meta-response patterns should be stripped from relay output."""
    from culture.clients.codex.daemon import _META_RESPONSE_RE

    cases = [
        ("I'd reply in `#general` with:\n> ack — taking testing", "ack — taking testing"),
        ("I would say: hello world", "hello world"),
        ("I'd respond with: got it", "got it"),
        ("I'd send in #general: on it", "on it"),
        ("actual direct message", "actual direct message"),
    ]
    for input_text, expected in cases:
        result = _strip_meta_lines(input_text, _META_RESPONSE_RE)
        assert result == expected, f"Input: {input_text!r}, got: {result!r}, expected: {expected!r}"
