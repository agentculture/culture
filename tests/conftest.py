# tests/conftest.py
import asyncio
from unittest.mock import patch

import pytest
import pytest_asyncio
from agentirc.config import LinkConfig, ServerConfig, TelemetryConfig
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from culture.agentirc.ircd import IRCd
from culture.telemetry.metrics import reset_for_tests as _reset_metrics
from culture.telemetry.tracing import reset_for_tests as _reset_telemetry

# Test-only link password — not a real credential (S2068)
TEST_LINK_PASSWORD = "testlink123"

# Default total wait for recv_until / recv. Callers needing a different
# bound should wrap their own `async with asyncio.timeout(...)` around the
# call rather than passing a parameter (per python:S7483).
RECV_TIMEOUT_SECONDS = 2.0

_BOTS_DIR_MANAGER = "culture.bots.bot_manager.BOTS_DIR"
_BOTS_DIR_CONFIG = "culture.bots.config.BOTS_DIR"
_BOTS_DIR_BOT = "culture.bots.bot.BOTS_DIR"


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

    async def recv_until(self, marker: str) -> str:
        """Read lines until one contains marker.

        Bounded by `RECV_TIMEOUT_SECONDS` (module constant). For a
        different bound, wrap the call in `async with asyncio.timeout(...)`.
        """
        collected = []
        try:
            async with asyncio.timeout(RECV_TIMEOUT_SECONDS):
                while True:
                    line = await self.recv()
                    collected.append(line)
                    if marker in line:
                        return "\r\n".join(collected)
        except (asyncio.TimeoutError, TimeoutError, ConnectionError):
            pass
        return "\r\n".join(collected)

    async def count_until_idle(self, marker: str, seconds: float = 1.0) -> int:
        """Read lines until timeout; return count of lines containing marker."""
        count = 0
        try:
            while True:
                line = await self.recv(timeout=seconds)
                if marker in line:
                    count += 1
        except (asyncio.TimeoutError, ConnectionError):
            pass
        return count

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
    config = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit")),
    )
    with (
        patch(_BOTS_DIR_MANAGER, empty_bots),
        patch(_BOTS_DIR_CONFIG, empty_bots),
        patch(_BOTS_DIR_BOT, empty_bots),
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
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit_alpha")),
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit_beta")),
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)

    with (
        patch(_BOTS_DIR_MANAGER, empty_bots),
        patch(_BOTS_DIR_CONFIG, empty_bots),
        patch(_BOTS_DIR_BOT, empty_bots),
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


@pytest_asyncio.fixture
async def server_welcome_disabled(tmp_path):
    """Server instance with the welcome system bot disabled via config."""
    empty_bots = tmp_path / "_bots"
    empty_bots.mkdir()
    config = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        system_bots={"welcome": {"enabled": False}},
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit")),
    )
    with (
        patch(_BOTS_DIR_MANAGER, empty_bots),
        patch(_BOTS_DIR_CONFIG, empty_bots),
        patch(_BOTS_DIR_BOT, empty_bots),
    ):
        ircd = IRCd(config)
        await ircd.start()
        ircd.config.port = ircd._server.sockets[0].getsockname()[1]
        yield ircd
        await ircd.stop()


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
async def tracing_exporter():
    """In-memory span exporter for telemetry integration tests.

    Installs a dedicated SDK TracerProvider with a SimpleSpanProcessor so
    every finished span lands in the returned exporter synchronously. Cleans
    up after the test so it doesn't leak into parallel workers.
    """
    _reset_telemetry()
    exporter = InMemorySpanExporter()
    provider = SdkTracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    try:
        yield exporter
    finally:
        exporter.clear()
        _reset_telemetry()


@pytest_asyncio.fixture
async def metrics_reader():
    """In-memory metric reader for telemetry integration tests.

    Installs a dedicated SDK MeterProvider with an InMemoryMetricReader so
    tests can `reader.get_metrics_data()` to walk recorded data points.
    Cleans up after the test so it doesn't leak into parallel workers.
    """
    _reset_metrics()
    reader = InMemoryMetricReader()
    provider = SdkMeterProvider(
        resource=Resource.create({"service.name": "test"}),
        metric_readers=[reader],
    )
    otel_metrics.set_meter_provider(provider)
    try:
        yield reader
    finally:
        _reset_metrics()


@pytest.fixture
def audit_dir(tmp_path):
    """Yields a Path for tests to use as `telemetry.audit_dir`.

    Tests build a ServerConfig with `telemetry=TelemetryConfig(audit_dir=str(tmp_path))`
    and inspect file contents via `Path(audit_dir).glob("*.jsonl*")`.
    """
    return tmp_path
