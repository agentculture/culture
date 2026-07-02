"""Tests for init_metrics + reset_for_tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider

from culture_core.agentirc.config import ServerConfig, TelemetryConfig
from culture_core.telemetry import MetricsRegistry, init_metrics
from culture_core.telemetry.metrics import reset_for_tests as _reset_metrics


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


def test_init_metrics_enabled_path_installs_meter_provider(monkeypatch):
    """The enabled-telemetry path constructs an OTLP exporter + MeterProvider.

    We don't want to hit a real OTLP endpoint, so mock the exporter and reader
    constructors. The point is to cover the enabled-init branch (instrument
    construction, resource attributes, provider registration) without network.
    """
    monkeypatch.setattr(
        "culture_core.telemetry.metrics.OTLPMetricExporter",
        lambda **kw: MagicMock(name="OTLPMetricExporter()"),
    )
    monkeypatch.setattr(
        "culture_core.telemetry.metrics.PeriodicExportingMetricReader",
        lambda **kw: MagicMock(name="PeriodicExportingMetricReader()"),
    )

    cfg = ServerConfig(
        name="testserv",
        telemetry=TelemetryConfig(
            enabled=True,
            metrics_enabled=True,
            otlp_endpoint="http://localhost:4317",
            otlp_compression="none",
        ),
    )
    reg = init_metrics(cfg)

    assert isinstance(reg, MetricsRegistry)
    # A real SDK MeterProvider was installed (not the proxy meter from the
    # disabled path).
    assert isinstance(otel_metrics.get_meter_provider(), MeterProvider)


def test_init_metrics_reinit_tears_down_previous_provider(monkeypatch):
    """Calling init_metrics again with a different config shuts down the old
    MeterProvider before installing a new one (the teardown branch in
    init_metrics).
    """
    # Fresh exporter+reader per construction call, so MeterProvider doesn't
    # reject a re-registered reader instance.
    monkeypatch.setattr(
        "culture_core.telemetry.metrics.OTLPMetricExporter",
        lambda **kw: MagicMock(name="OTLPMetricExporter()"),
    )
    monkeypatch.setattr(
        "culture_core.telemetry.metrics.PeriodicExportingMetricReader",
        lambda **kw: MagicMock(name="PeriodicExportingMetricReader()"),
    )

    tcfg = TelemetryConfig(enabled=True, metrics_enabled=True)
    cfg = ServerConfig(name="testserv", telemetry=tcfg)
    init_metrics(cfg)

    from culture_core.telemetry import metrics as metrics_mod

    first_provider = metrics_mod._meter_provider
    assert first_provider is not None

    # Mutate to force a reinit. The previous provider's shutdown() should be
    # called as part of the teardown branch.
    tcfg.metrics_export_interval_ms = 5000
    init_metrics(cfg)

    assert metrics_mod._meter_provider is not first_provider
