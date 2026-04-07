# tests/conftest.py
import asyncio
from unittest.mock import patch

import pytest_asyncio

from culture.server.config import LinkConfig, ServerConfig
from culture.server.ircd import IRCd

# Test-only link password — not a real credential (S2068)
TEST_LINK_PASSWORD = "testlink123"


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
        except ConnectionError:
            pass


@pytest_asyncio.fixture
async def server(tmp_path):
    # Isolate bot loading to an empty temp directory so tests
    # never read/write the real ~/.culture/bots/ directory.
    empty_bots = tmp_path / "_bots"
    empty_bots.mkdir()
    config = ServerConfig(name="testserv", host="127.0.0.1", port=0, webhook_port=0)
    with (
        patch("culture.bots.bot_manager.BOTS_DIR", empty_bots),
        patch("culture.bots.config.BOTS_DIR", empty_bots),
        patch("culture.bots.bot.BOTS_DIR", empty_bots),
    ):
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


@pytest_asyncio.fixture
async def linked_servers(tmp_path):
    """Two IRCd instances linked via S2S federation."""
    link_password = TEST_LINK_PASSWORD
    empty_bots = tmp_path / "_bots"
    empty_bots.mkdir()

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)

    with (
        patch("culture.bots.bot_manager.BOTS_DIR", empty_bots),
        patch("culture.bots.config.BOTS_DIR", empty_bots),
        patch("culture.bots.bot.BOTS_DIR", empty_bots),
    ):
        await server_a.start()
        await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    # Update link configs with actual ports
    config_a.links[0].port = server_b.config.port
    config_b.links[0].port = server_a.config.port

    # Server A connects to Server B
    await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
    # Wait for handshake to complete
    for _ in range(50):
        if "beta" in server_a.links and "alpha" in server_b.links:
            break
        await asyncio.sleep(0.05)

    yield server_a, server_b

    await server_a.stop()
    await server_b.stop()


@pytest_asyncio.fixture
async def make_client_a(linked_servers):
    """Create test clients connected to server A."""
    server_a, _ = linked_servers
    clients = []

    async def _make(nick: str | None = None, user: str | None = None) -> IRCTestClient:
        reader, writer = await asyncio.open_connection("127.0.0.1", server_a.config.port)
        client = IRCTestClient(reader, writer)
        if nick:
            await client.send(f"NICK {nick}")
        if user:
            await client.send(f"USER {user} 0 * :{user}")
        if nick and user:
            await client.recv_all(timeout=0.5)
        clients.append(client)
        return client

    yield _make

    for c in clients:
        try:
            await c.close()
        except Exception:
            pass


@pytest_asyncio.fixture
async def make_client_b(linked_servers):
    """Create test clients connected to server B."""
    _, server_b = linked_servers
    clients = []

    async def _make(nick: str | None = None, user: str | None = None) -> IRCTestClient:
        reader, writer = await asyncio.open_connection("127.0.0.1", server_b.config.port)
        client = IRCTestClient(reader, writer)
        if nick:
            await client.send(f"NICK {nick}")
        if user:
            await client.send(f"USER {user} 0 * :{user}")
        if nick and user:
            await client.recv_all(timeout=0.5)
        clients.append(client)
        return client

    yield _make

    for c in clients:
        try:
            await c.close()
        except Exception:
            pass
