from culture.agentirc.config import ServerConfig, TelemetryConfig


def test_default_server_config_has_telemetry_disabled():
    cfg = ServerConfig()
    assert cfg.telemetry.enabled is False
    assert cfg.telemetry.service_name == "culture.agentirc"
    assert cfg.telemetry.otlp_endpoint == "http://localhost:4317"
    assert cfg.telemetry.otlp_timeout_ms == 5000
    assert cfg.telemetry.traces_enabled is True
    assert cfg.telemetry.traces_sampler == "parentbased_always_on"


def test_telemetry_config_accepts_overrides():
    t = TelemetryConfig(
        enabled=True,
        service_name="culture.agentirc.alpha",
        otlp_endpoint="http://collector:4317",
        otlp_timeout_ms=2000,
        traces_sampler="parentbased_traceidratio:0.1",
    )
    assert t.enabled is True
    assert t.service_name == "culture.agentirc.alpha"
    assert t.otlp_endpoint == "http://collector:4317"
    assert t.otlp_timeout_ms == 2000
    assert t.traces_sampler == "parentbased_traceidratio:0.1"
