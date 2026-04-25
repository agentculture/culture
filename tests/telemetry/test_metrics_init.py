"""Tests for init_metrics + reset_for_tests."""

from __future__ import annotations

import pytest

from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.telemetry import MetricsRegistry, init_metrics
from culture.telemetry.metrics import reset_for_tests as _reset_metrics


@pytest.fixture(autouse=True)
def _reset():
    _reset_metrics()
    yield
    _reset_metrics()


def test_init_metrics_returns_registry_when_disabled():
    cfg = ServerConfig(name="testserv", telemetry=TelemetryConfig(enabled=False))
    reg = init_metrics(cfg)
    assert isinstance(reg, MetricsRegistry)
    # No-op meter still satisfies the interface — call sites can record unconditionally.
    reg.events_emitted.add(1, {"event.type": "MESSAGE", "origin": "local"})


def test_init_metrics_returns_registry_when_metrics_disabled_only():
    cfg = ServerConfig(
        name="testserv",
        telemetry=TelemetryConfig(enabled=True, metrics_enabled=False),
    )
    reg = init_metrics(cfg)
    assert isinstance(reg, MetricsRegistry)


def test_init_metrics_idempotent_same_config():
    cfg = ServerConfig(name="testserv", telemetry=TelemetryConfig(enabled=False))
    reg1 = init_metrics(cfg)
    reg2 = init_metrics(cfg)
    assert reg1 is reg2


def test_init_metrics_reinit_on_config_mutation():
    tcfg = TelemetryConfig(enabled=False)
    cfg = ServerConfig(name="testserv", telemetry=tcfg)
    reg1 = init_metrics(cfg)
    # Mutate the config in place — snapshot diff should trigger reinit.
    tcfg.metrics_export_interval_ms = 5000
    reg2 = init_metrics(cfg)
    assert reg1 is not reg2


def test_reset_for_tests_clears_state():
    cfg = ServerConfig(name="testserv", telemetry=TelemetryConfig(enabled=False))
    reg1 = init_metrics(cfg)
    _reset_metrics()
    reg2 = init_metrics(cfg)
    assert reg1 is not reg2
