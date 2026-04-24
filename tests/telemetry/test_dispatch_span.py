import pytest

from culture.agentirc.client import Client
from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.agentirc.ircd import IRCd
from culture.protocol.message import Message
from culture.telemetry.context import TRACEPARENT_TAG
from tests.telemetry._fakes import FakeWriter


@pytest.mark.asyncio
async def test_dispatch_creates_command_span(tracing_exporter):
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    ircd = IRCd(cfg)
    client = Client(reader=None, writer=FakeWriter(), server=ircd)  # type: ignore[arg-type]
    client.nick = "spark-alice"
    await client._dispatch(Message(command="PING", params=["token1"]))
    names = [s.name for s in tracing_exporter.get_finished_spans()]
    assert "irc.command.PING" in names


@pytest.mark.asyncio
async def test_dispatch_extracts_traceparent(tracing_exporter):
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    ircd = IRCd(cfg)
    client = Client(reader=None, writer=FakeWriter(), server=ircd)  # type: ignore[arg-type]
    client.nick = "spark-alice"
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    await client._dispatch(Message(tags={TRACEPARENT_TAG: tp}, command="PING", params=["token1"]))
    span = next(s for s in tracing_exporter.get_finished_spans() if s.name == "irc.command.PING")
    # Trace ID encoded from traceparent (middle hex group).
    assert format(span.context.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert span.attributes["culture.trace.origin"] == "remote"


@pytest.mark.asyncio
async def test_dispatch_malformed_traceparent_drops_to_root(tracing_exporter):
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    ircd = IRCd(cfg)
    client = Client(reader=None, writer=FakeWriter(), server=ircd)  # type: ignore[arg-type]
    client.nick = "spark-alice"
    await client._dispatch(Message(tags={TRACEPARENT_TAG: "garbage"}, command="PING", params=["t"]))
    span = next(s for s in tracing_exporter.get_finished_spans() if s.name == "irc.command.PING")
    assert span.attributes["culture.trace.origin"] == "remote"
    assert span.attributes["culture.trace.dropped_reason"] == "malformed"
