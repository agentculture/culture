"""Tests for culture/clients/copilot/agent_runner.py OTEL instrumentation.

Tests are isolated — no real Copilot SDK, no IRCd. The copilot SDK classes
(``CopilotClient``, ``PermissionHandler``, ``SubprocessConfig``) are stubbed
via ``sys.modules`` so CI can run without the ``copilot`` package installed.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# ---------------------------------------------------------------------------
# Stub out the copilot SDK if not installed so CI can import agent_runner.py
# ---------------------------------------------------------------------------


def _stub_copilot_sdk():
    """Insert minimal stubs for the copilot SDK into sys.modules."""
    if "copilot" in sys.modules:
        return

    mod = types.ModuleType("copilot")

    class CopilotClient:
        """Stub for SDK type."""

        def __init__(self, config=None):
            """Stub for SDK type."""

        async def start(self):
            """Stub for SDK type."""
            await asyncio.sleep(0)

        async def stop(self):
            """Stub for SDK type."""
            await asyncio.sleep(0)

        async def create_session(self, **kwargs):
            await asyncio.sleep(0)
            return MagicMock()

    class PermissionHandler:
        approve_all = staticmethod(lambda req: True)

    class SubprocessConfig:
        def __init__(self, cwd=None, env=None):
            self.cwd = cwd
            self.env = env

    mod.CopilotClient = CopilotClient
    mod.PermissionHandler = PermissionHandler
    mod.SubprocessConfig = SubprocessConfig

    sys.modules["copilot"] = mod


_stub_copilot_sdk()

# Now safe to import
from culture.clients.copilot.agent_runner import CopilotAgentRunner  # noqa: E402
from culture.clients.copilot.telemetry import (  # noqa: E402
    HarnessMetricsRegistry,
    _build_registry,
    reset_for_tests,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_otel():
    """Reset all OTEL globals before and after every test."""
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture
def metrics_reader():
    """Install an InMemoryMetricReader and return the reader."""
    reader = InMemoryMetricReader()
    provider = SdkMeterProvider(
        resource=Resource.create({"service.name": "test-copilot-runner"}),
        metric_readers=[reader],
    )
    otel_metrics.set_meter_provider(provider)
    return reader


@pytest.fixture
def tracing_exporter():
    """Install an InMemorySpanExporter and return (exporter, provider)."""
    exporter = InMemorySpanExporter()
    provider = SdkTracerProvider(
        resource=Resource.create({"service.name": "test-copilot-runner-tracing"}),
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter, provider


@pytest.fixture
def registry(metrics_reader):
    """Return a real HarnessMetricsRegistry bound to the test meter provider."""
    from opentelemetry import metrics as _m

    meter = _m.get_meter("culture.harness.copilot")
    return _build_registry(meter)


def _make_runner(registry=None, nick="spark-copilot", model="gpt-4.1"):
    """Build a minimal CopilotAgentRunner with telemetry wired up."""
    runner = CopilotAgentRunner(
        model=model,
        directory=tempfile.mkdtemp(prefix="culture-test-copilot-"),
        metrics=registry,
        nick=nick,
    )
    # Pre-wire a mock session so _execute_single_turn can call send_and_wait
    runner._session = AsyncMock()
    return runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_metric_value(reader, metric_name, attributes=None):
    """Extract a sum value from the reader for a given metric name + attrs."""
    data = reader.get_metrics_data()
    if data is None:
        return None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == metric_name:
                    for dp in m.data.data_points:
                        if attributes is None:
                            return dp.value
                        if all(dp.attributes.get(k) == v for k, v in attributes.items()):
                            return dp.value
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_single_turn_records_success(metrics_reader, tracing_exporter, registry):
    """Success path: span opened with correct copilot attrs, metrics incremented.

    Token counters do NOT increment — copilot defers token extraction to issue #299.
    """
    exporter, _ = tracing_exporter
    runner = _make_runner(registry=registry, nick="spark-copilot", model="gpt-4.1")

    # Stub send_and_wait to return a fake response with data.content
    fake_response = SimpleNamespace(data=SimpleNamespace(content="Hello from copilot!"))
    runner._session.send_and_wait = AsyncMock(return_value=fake_response)

    await runner._execute_single_turn("hello")

    # Check span was created with correct attributes
    spans = exporter.get_finished_spans()
    llm_spans = [s for s in spans if s.name == "harness.llm.call"]
    assert len(llm_spans) == 1
    span = llm_spans[0]
    assert span.attributes.get("harness.backend") == "copilot"
    assert span.attributes.get("harness.model") == "gpt-4.1"
    assert span.attributes.get("harness.nick") == "spark-copilot"

    # Check llm_calls metric incremented with outcome=success
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "copilot", "model": "gpt-4.1", "outcome": "success"},
    )
    assert calls_val == 1

    # Token counters must NOT be recorded — copilot defers token extraction (issue #299)
    input_val = _get_metric_value(metrics_reader, "culture.harness.llm.tokens.input")
    assert input_val is None

    output_val = _get_metric_value(metrics_reader, "culture.harness.llm.tokens.output")
    assert output_val is None


@pytest.mark.asyncio
async def test_execute_single_turn_records_timeout(metrics_reader, registry):
    """Timeout path: asyncio.TimeoutError → outcome=timeout increments llm_calls."""
    runner = _make_runner(registry=registry, nick="spark-copilot", model="gpt-4.1")

    runner._session.send_and_wait = AsyncMock(side_effect=asyncio.TimeoutError())

    await runner._execute_single_turn("hello")

    # Check outcome=timeout
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "copilot", "model": "gpt-4.1", "outcome": "timeout"},
    )
    assert calls_val == 1


@pytest.mark.asyncio
async def test_execute_single_turn_records_error(metrics_reader, registry):
    """Error path: generic exception → outcome=error increments llm_calls."""
    runner = _make_runner(registry=registry, nick="spark-copilot", model="gpt-4.1")

    runner._session.send_and_wait = AsyncMock(side_effect=RuntimeError("SDK exploded"))

    await runner._execute_single_turn("hello")

    # Check outcome=error
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "copilot", "model": "gpt-4.1", "outcome": "error"},
    )
    assert calls_val == 1


@pytest.mark.asyncio
async def test_execute_single_turn_no_metrics_no_recording(metrics_reader):
    """When metrics=None, no metric data is recorded."""
    runner = _make_runner(registry=None, nick="spark-copilot")

    fake_response = SimpleNamespace(data=SimpleNamespace(content="Hello!"))
    runner._session.send_and_wait = AsyncMock(return_value=fake_response)

    await runner._execute_single_turn("hello")

    # No metric data should be recorded for llm_calls
    calls_val = _get_metric_value(metrics_reader, "culture.harness.llm.calls")
    assert calls_val is None


@pytest.mark.asyncio
async def test_execute_single_turn_outer_timeout_fires_on_wedged_send_and_wait(
    metrics_reader, registry
):
    """Outer asyncio.wait_for safety net: if send_and_wait hangs, restart cleanly."""
    import asyncio

    runner = CopilotAgentRunner(
        model="gpt-4.1",
        directory=tempfile.mkdtemp(prefix="culture-test-copilot-"),
        metrics=registry,
        nick="spark-copilot",
        on_turn_error=AsyncMock(),
        turn_timeout_seconds=0.05,
    )
    runner._session = AsyncMock()

    async def _hang_send(*a, **kw):
        # Coroutine that awaits a never-resolving future — bypasses the
        # SDK's own 120s inner timeout so we can verify the outer wrap
        # is what catches it.
        await asyncio.Future()

    runner._session.send_and_wait = _hang_send

    await runner._execute_single_turn("hello")

    runner.on_turn_error.assert_awaited_once()
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "copilot", "model": "gpt-4.1", "outcome": "timeout"},
    )
    assert calls_val == 1
