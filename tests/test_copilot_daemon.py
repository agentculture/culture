import asyncio
import os
import tempfile
import pytest

from agentirc.clients.copilot.daemon import CopilotDaemon
from agentirc.clients.copilot.config import (
    DaemonConfig, ServerConnConfig, AgentConfig,
    SupervisorConfig, WebhookConfig,
)


@pytest.mark.asyncio
async def test_copilot_daemon_starts_and_connects(server):
    """CopilotDaemon with skip_copilot=True connects to IRC without needing copilot CLI."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        supervisor=SupervisorConfig(),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-copilot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = CopilotDaemon(config, agent, socket_dir=sock_dir, skip_copilot=True)
    await daemon.start()
    try:
        await asyncio.sleep(0.5)
        assert "testserv-copilot" in server.clients
        assert "#general" in server.channels
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_copilot_daemon_ipc_irc_send(server, make_client):
    """IPC irc_send works through the Copilot daemon."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-copilot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = CopilotDaemon(config, agent, socket_dir=sock_dir, skip_copilot=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    from agentirc.clients.copilot.ipc import encode_message, decode_message, make_request
    sock_path = os.path.join(sock_dir, "agentirc-testserv-copilot.sock")
    reader, writer = await asyncio.open_unix_connection(sock_path)

    req = make_request("irc_send", channel="#general", message="hello from copilot skill")
    writer.write(encode_message(req))
    await writer.drain()

    data = await asyncio.wait_for(reader.readline(), timeout=2.0)
    resp = decode_message(data)
    assert resp["ok"] is True

    msg = await human.recv(timeout=2.0)
    assert "hello from copilot skill" in msg

    writer.close()
    await writer.wait_closed()
    await daemon.stop()


@pytest.mark.asyncio
async def test_copilot_config_defaults():
    """Copilot config has correct backend-specific defaults."""
    agent = AgentConfig()
    assert agent.agent == "copilot"
    assert agent.model == "gpt-4.1"

    supervisor = SupervisorConfig()
    assert supervisor.model == "gpt-4.1"


@pytest.mark.asyncio
async def test_copilot_backend_dispatch():
    """CopilotDaemon can be imported and constructed for agent='copilot'."""
    agent = AgentConfig(nick="test-copilot", agent="copilot", directory="/tmp")
    backend = getattr(agent, "agent", "claude")
    assert backend == "copilot"

    # Verify CopilotDaemon can be imported and constructed
    config = DaemonConfig()
    daemon = CopilotDaemon(config, agent, skip_copilot=True)
    assert daemon.agent.agent == "copilot"
    assert daemon.agent.model == "gpt-4.1"


@pytest.mark.asyncio
async def test_copilot_relay_target_fifo(server, make_client):
    """Multiple @mentions route responses to correct targets via FIFO queue."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-copilot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = CopilotDaemon(config, agent, socket_dir=sock_dir, skip_copilot=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    # Join humans to observe messages
    human = await make_client(nick="testserv-alice", user="alice")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    # Directly enqueue two relay targets (simulates @mentions without
    # needing a running agent runner — _on_mention guards on is_running)
    daemon._mention_targets.append("#general")
    daemon._mention_targets.append("testserv-alice")

    # Verify FIFO queue has two entries
    assert len(daemon._mention_targets) == 2

    # First agent response dequeues first target (#general)
    await daemon._on_agent_message({
        "content": [{"type": "text", "text": "channel response"}],
    })
    assert len(daemon._mention_targets) == 1

    # Second agent response dequeues second target (DM to testserv-alice)
    await daemon._on_agent_message({
        "content": [{"type": "text", "text": "dm response"}],
    })
    assert len(daemon._mention_targets) == 0

    # Verify alice received the channel message in #general
    lines = await human.recv_all(timeout=1.0)
    channel_msgs = [l for l in lines if "#general" in l and "channel response" in l]
    assert len(channel_msgs) >= 1, f"Expected 'channel response' in #general, got: {lines}"
    # DM goes to testserv-alice directly, so alice sees it as a PRIVMSG to their nick
    assert any("dm response" in l for l in lines), f"Expected 'dm response', got: {lines}"

    await daemon.stop()
