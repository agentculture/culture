import yaml

from culture.config import load_server_config


def _write_yaml(tmp_path, data):
    p = tmp_path / "server.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_load_server_config_default_telemetry(tmp_path):
    p = _write_yaml(tmp_path, {"server": {"name": "alpha"}})
    cfg = load_server_config(p)
    assert cfg.telemetry.enabled is False
    assert cfg.telemetry.service_name == "culture.agentirc"


def test_load_server_config_custom_telemetry(tmp_path):
    p = _write_yaml(
        tmp_path,
        {
            "server": {"name": "alpha"},
            "telemetry": {
                "enabled": True,
                "service_name": "culture.agentirc.alpha",
                "otlp_endpoint": "http://collector:4317",
                "traces_sampler": "parentbased_traceidratio:0.1",
            },
        },
    )
    cfg = load_server_config(p)
    assert cfg.telemetry.enabled is True
    assert cfg.telemetry.service_name == "culture.agentirc.alpha"
    assert cfg.telemetry.otlp_endpoint == "http://collector:4317"
    assert cfg.telemetry.traces_sampler == "parentbased_traceidratio:0.1"
