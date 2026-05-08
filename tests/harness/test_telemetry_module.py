"""Unit tests for culture/clients/shared/telemetry.py.

Tests are isolated — no IRCd, no real OTLP exporter. Each test resets all
global OTEL provider state via ``reset_for_tests()`` before and after so
parallel xdist workers don't leak providers.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# config is still cited per backend; imported via sys.path set in conftest.py.
# pylint: disable=import-error
from config import AgentConfig, DaemonConfig, ServerConnConfig, TelemetryConfig
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource

from culture.clients.shared.telemetry import (
    HarnessMetricsRegistry,
    init_harness_telemetry,
    record_llm_call,
    reset_for_tests,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset all OTEL globals before and after every test."""
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture
def disabled_config():
    """DaemonConfig with telemetry disabled (default)."""
    return DaemonConfig()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_counter_sum(reader, metric_name: str) -> float:
    """Sum all data point values for a counter metric across all attributes."""
    data = reader.get_metrics_data()
    if data is None:
        return 0.0
    total = 0.0
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == metric_name:
                    for dp in m.data.data_points:
                        total += dp.value
    return total


# ---------------------------------------------------------------------------
# test_init_disabled_returns_noop_tracer_and_proxy_registry
# ---------------------------------------------------------------------------


def test_init_disabled_returns_noop_tracer_and_proxy_registry(disabled_config):
    """Disabled telemetry yields a no-op tracer and a registry that doesn't raise."""
    tracer, registry = init_harness_telemetry(disabled_config)

    # Tracer should be a proxy / no-op — starting a span yields a NonRecordingSpan.
    with tracer.start_as_current_span("test-span") as span:
        assert not span.is_recording()

    # All 4 instruments present on the registry.
    assert isinstance(registry, HarnessMetricsRegistry)
    assert registry.llm_tokens_input is not None
    assert registry.llm_tokens_output is not None
    assert registry.llm_call_duration is not None
    assert registry.llm_calls is not None

    # Calls on proxy instruments must not raise.
    registry.llm_calls.add(1, {"backend": "test", "model": "m", "outcome": "success"})
    registry.llm_call_duration.record(10.0, {"backend": "test", "model": "m", "outcome": "success"})
    registry.llm_tokens_input.add(5, {"backend": "test", "model": "m", "harness.nick": "n"})
    registry.llm_tokens_output.add(3, {"backend": "test", "model": "m", "harness.nick": "n"})


# ---------------------------------------------------------------------------
# test_init_idempotent
# ---------------------------------------------------------------------------


def test_init_idempotent(disabled_config):
    """Calling init twice with same config returns the same tracer + registry."""
    tracer1, registry1 = init_harness_telemetry(disabled_config)
    tracer2, registry2 = init_harness_telemetry(disabled_config)

    assert tracer1 is tracer2
    assert registry1 is registry2


# ---------------------------------------------------------------------------
# test_init_reinit_on_config_change
# ---------------------------------------------------------------------------


def test_init_reinit_on_config_change():
    """Mutating TelemetryConfig triggers fresh provider install on next call."""
    tcfg = TelemetryConfig(enabled=False)
    config = DaemonConfig(telemetry=tcfg)

    _tracer1, registry1 = init_harness_telemetry(config)

    # Mutate config — snapshot diff should force reinit.
    tcfg.metrics_export_interval_ms = 1000

    _tracer2, registry2 = init_harness_telemetry(config)
    assert registry1 is not registry2


# ---------------------------------------------------------------------------
# test_init_reinit_with_real_provider_shuts_down_old
# ---------------------------------------------------------------------------


def test_init_reinit_with_real_provider_shuts_down_old():
    """Reinit with changed config shuts down the previous real MeterProvider.

    First call installs a real SdkMeterProvider (enabled=True, metrics_on).
    A config mutation then triggers reinit, which must call shutdown() on the
    old provider.  We patch MeterProvider.shutdown to record invocations so
    the assertion is deterministic without touching live exporters.
    """
    reader = InMemoryMetricReader()
    first_provider = SdkMeterProvider(
        resource=Resource.create({"service.name": "test-harness-reinit"}),
        metric_readers=[reader],
    )
    otel_metrics.set_meter_provider(first_provider)

    from culture.clients.shared import telemetry as _tel_module

    _tel_module._meter_provider = first_provider

    shutdown_calls = []
    original_shutdown = SdkMeterProvider.shutdown

    def _record_shutdown(self, *args, **kwargs):
        shutdown_calls.append(self)
        return original_shutdown(self, *args, **kwargs)

    tcfg = TelemetryConfig(enabled=False)
    config = DaemonConfig(telemetry=tcfg)
    _tel_module._initialized_for = {
        "telemetry": {
            "enabled": False,
            "service_name": "culture.harness",
            "otlp_endpoint": "http://localhost:4317",
            "otlp_protocol": "grpc",
            "otlp_timeout_ms": 5000,
            "otlp_compression": "gzip",
            "traces_enabled": True,
            "traces_sampler": "parentbased_always_on",
            "metrics_enabled": True,
            "metrics_export_interval_ms": 10000,
        },
        "nick": "culture",
    }

    with patch.object(SdkMeterProvider, "shutdown", _record_shutdown):
        tcfg.metrics_export_interval_ms = 5000
        init_harness_telemetry(config)

    assert shutdown_calls, "expected old MeterProvider.shutdown() to be called on reinit"
    assert shutdown_calls[0] is first_provider


# ---------------------------------------------------------------------------
# test_init_with_metrics_reader_records_calls
# ---------------------------------------------------------------------------


def test_init_with_metrics_reader_records_calls(harness_metrics_reader):
    """When a real MeterProvider is pre-installed, record_llm_call records data."""
    # Build a config that would be disabled so init_harness_telemetry uses the
    # proxy meter pointing at the already-installed test MeterProvider.
    config = DaemonConfig()
    _tracer_obj, registry = init_harness_telemetry(config)

    record_llm_call(
        registry,
        backend="claude",
        model="claude-opus-4-6",
        nick="spark-claude",
        usage=None,
        duration_ms=42.0,
        outcome="success",
    )

    assert _get_counter_sum(harness_metrics_reader, "culture.harness.llm.calls") == pytest.approx(
        1.0
    )


# ---------------------------------------------------------------------------
# test_reset_for_tests_clears_globals
# ---------------------------------------------------------------------------


def test_reset_for_tests_clears_globals(disabled_config):
    """After reset_for_tests() all module globals are None and OTEL unset."""
    from culture.clients.shared import telemetry as _tel_module

    init_harness_telemetry(disabled_config)

    # After reset: module globals cleared.
    reset_for_tests()
    assert _tel_module._initialized_for is None
    assert _tel_module._tracer is None
    assert _tel_module._meter_provider is None
    assert _tel_module._registry is None

    # OTEL trace provider unset.
    assert trace._TRACER_PROVIDER is None  # type: ignore[attr-defined]

    # OTEL metrics provider unset.
    import opentelemetry.metrics._internal as _mi  # type: ignore[attr-defined]

    assert _mi._METER_PROVIDER is None


# ---------------------------------------------------------------------------
# test_nick_identity_from_agents
# ---------------------------------------------------------------------------


def test_nick_identity_from_agents():
    """When agents list is non-empty, identity is built from agent nicks."""
    config = DaemonConfig(
        agents=[AgentConfig(nick="spark-claude"), AgentConfig(nick="spark-daria")],
    )
    tracer, registry = init_harness_telemetry(config)
    assert tracer is not None
    assert registry is not None


# ---------------------------------------------------------------------------
# test_nick_identity_from_server_name
# ---------------------------------------------------------------------------


def test_nick_identity_from_server_name():
    """When agents list is empty, identity falls back to server.name."""
    config = DaemonConfig(server=ServerConnConfig(name="mytestserver"))
    tracer, registry = init_harness_telemetry(config)
    assert tracer is not None
    assert registry is not None
