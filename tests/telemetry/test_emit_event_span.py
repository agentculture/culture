import pytest

from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.agentirc.ircd import IRCd
from culture.agentirc.skill import Event, EventType


@pytest.mark.asyncio
async def test_emit_event_creates_span(tracing_exporter):
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    # IRCd uses the module-level tracer from telemetry.tracing; the fixture
    # installed a real SDK provider so spans land in the exporter regardless
    # of telemetry.enabled.
    ircd = IRCd(cfg)
    await ircd.emit_event(
        Event(type=EventType.MESSAGE, channel="#c", nick="spark-alice", data={"body": "hi"})
    )
    spans = tracing_exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "irc.event.emit" in names
    event_span = next(s for s in spans if s.name == "irc.event.emit")
    assert event_span.attributes["event.type"] == "message"
    assert event_span.attributes["event.channel"] == "#c"
    assert event_span.attributes["event.origin"] == "local"


@pytest.mark.asyncio
async def test_emit_event_federated_origin(tracing_exporter):
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    ircd = IRCd(cfg)
    await ircd.emit_event(
        Event(
            type=EventType.MESSAGE,
            channel="#c",
            nick="thor-bob",
            data={"body": "hi", "_origin": "thor"},
        )
    )
    spans = tracing_exporter.get_finished_spans()
    event_span = next(s for s in spans if s.name == "irc.event.emit")
    assert event_span.attributes["event.origin"] == "federated"
    assert event_span.attributes["culture.federation.peer"] == "thor"
