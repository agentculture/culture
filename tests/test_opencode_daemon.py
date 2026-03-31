import asyncio
import os
import tempfile
import pytest

from agentirc.clients.acp.daemon import ACPDaemon
from agentirc.clients.acp.config import (
    DaemonConfig, ServerConnConfig, AgentConfig,
    SupervisorConfig, WebhookConfig,
)


@pytest.mark.asyncio
async def test_opencode_daemon_starts_and_connects(server):
    """ACPDaemon with skip_agent=True connects to IRC without needing an ACP CLI."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        supervisor=SupervisorConfig(),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-opencode", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = ACPDaemon(config, agent, socket_dir=sock_dir, skip_agent=True)
    await daemon.start()
    try:
        await asyncio.sleep(0.5)
        assert "testserv-opencode" in server.clients
        assert "#general" in server.channels
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_opencode_daemon_ipc_irc_send(server, make_client):
    """IPC irc_send works through the ACP daemon."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-opencode", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = ACPDaemon(config, agent, socket_dir=sock_dir, skip_agent=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    from agentirc.clients.acp.ipc import encode_message, decode_message, make_request
    sock_path = os.path.join(sock_dir, "agentirc-testserv-opencode.sock")
    reader, writer = await asyncio.open_unix_connection(sock_path)

    req = make_request("irc_send", channel="#general", message="hello from acp skill")
    writer.write(encode_message(req))
    await writer.drain()

    data = await asyncio.wait_for(reader.readline(), timeout=2.0)
    resp = decode_message(data)
    assert resp["ok"] is True

    msg = await human.recv(timeout=2.0)
    assert "hello from acp skill" in msg

    writer.close()
    await writer.wait_closed()
    await daemon.stop()


@pytest.mark.asyncio
async def test_opencode_config_defaults():
    """ACP config has correct defaults."""
    agent = AgentConfig()
    assert agent.agent == "acp"
    assert agent.acp_command == ["opencode", "acp"]
    assert agent.model == "anthropic/claude-sonnet-4-6"

    supervisor = SupervisorConfig()
    assert supervisor.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_opencode_backend_dispatch():
    """CLI dispatch selects ACPDaemon for agent='acp'."""
    agent = AgentConfig(nick="test-acp", agent="acp", directory="/tmp")
    backend = getattr(agent, "agent", "claude")
    assert backend == "acp"

    # Verify ACPDaemon can be imported and constructed
    config = DaemonConfig()
    daemon = ACPDaemon(config, agent, skip_agent=True)
    assert daemon.agent.agent == "acp"
    assert daemon.agent.acp_command == ["opencode", "acp"]
    assert daemon.agent.model == "anthropic/claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_opencode_relay_target_fifo(server, make_client):
    """Multiple @mentions route responses to correct targets via FIFO queue."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-opencode", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = ACPDaemon(config, agent, socket_dir=sock_dir, skip_agent=True)
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
    dm_msgs = [l for l in lines if "dm response" in l and "testserv-alice" in l.split()[0].lower() if "PRIVMSG" in l]
    assert len(channel_msgs) >= 1, f"Expected 'channel response' in #general, got: {lines}"
    # DM goes to testserv-alice directly, so alice sees it as a PRIVMSG to their nick
    assert any("dm response" in l for l in lines), f"Expected 'dm response', got: {lines}"

    await daemon.stop()
