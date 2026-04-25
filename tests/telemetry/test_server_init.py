import pytest

from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.agentirc.ircd import IRCd


def test_ircd_init_sets_tracer_attribute():
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    ircd = IRCd(cfg)
    assert ircd.tracer is not None
    # No-op tracer can still produce spans; is_recording will be False
    with ircd.tracer.start_as_current_span("check") as span:
        assert span.is_recording() is False


@pytest.mark.asyncio
async def test_ircd_init_does_not_disturb_installed_provider(tracing_exporter):
    # tracing_exporter fixture already installed an SDK provider; IRCd.__init__
    # with enabled=False must NOT tear that down (its no-op path must not call
    # trace.set_tracer_provider).
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    IRCd(cfg)
    # Emit a span via the exporter's provider — must land in the exporter.
    from opentelemetry import trace

    tracer = trace.get_tracer("smoke")
    with tracer.start_as_current_span("smoke") as span:
        assert span.is_recording()
    assert len(tracing_exporter.get_finished_spans()) == 1
