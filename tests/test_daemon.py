import asyncio
import os
import tempfile

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    SupervisorConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon


@pytest.mark.asyncio
async def test_daemon_starts_and_connects(server):
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        supervisor=SupervisorConfig(),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
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
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    from culture.clients.shared.ipc import decode_message, encode_message, make_request

    sock_path = os.path.join(sock_dir, "culture-testserv-bot.sock")
    reader, writer = await asyncio.open_unix_connection(sock_path)
    req = make_request("irc_send", channel="#general", message="hello from skill")
    writer.write(encode_message(req))
    await writer.drain()
    data = await asyncio.wait_for(reader.readline(), timeout=2.0)
    resp = decode_message(data)
    assert resp["ok"] is True
    msg = await human.recv(timeout=2.0)
    assert "hello from skill" in msg
    writer.close()
    await writer.wait_closed()
    await daemon.stop()


@pytest.mark.asyncio
async def test_daemon_ipc_irc_read(server, make_client):
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #general :test message")
    await asyncio.sleep(0.3)
    from culture.clients.shared.ipc import decode_message, encode_message, make_request

    sock_path = os.path.join(sock_dir, "culture-testserv-bot.sock")
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
