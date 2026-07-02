from unittest.mock import patch

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider

from culture_core.agentirc.config import ServerConfig, TelemetryConfig
from culture_core.telemetry import init_telemetry
from culture_core.telemetry.tracing import reset_for_tests


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


def test_init_disabled_returns_noop_tracer():
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    tracer = init_telemetry(cfg)
    # NoOpTracer spans are context managers that do nothing.
    with tracer.start_as_current_span("test") as span:
        # NoOp spans have no recording behavior; is_recording() is False.
        assert span.is_recording() is False


def test_init_enabled_creates_sdk_tracer():
    cfg = ServerConfig(
        name="spark",
        telemetry=TelemetryConfig(enabled=True, otlp_endpoint="http://localhost:4317"),
    )
    with patch(
        "culture_core.telemetry.tracing.OTLPSpanExporter"
    ) as mock_exporter:  # avoid real gRPC connection in tests
        init_telemetry(cfg)
    mock_exporter.assert_called_once()
    call_kwargs = mock_exporter.call_args.kwargs
    assert call_kwargs["endpoint"] == "http://localhost:4317"
    # Tracer should resolve to the SDK provider we installed.
    provider = trace.get_tracer_provider()
    assert isinstance(provider, SdkTracerProvider)


def test_init_is_idempotent():
    cfg = ServerConfig(name="spark", telemetry=TelemetryConfig(enabled=False))
    t1 = init_telemetry(cfg)
    t2 = init_telemetry(cfg)
    assert t1 is t2
