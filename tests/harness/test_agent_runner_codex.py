"""Tests for culture/clients/codex/agent_runner.py OTEL instrumentation.

Tests are isolated — no real Codex process, no IRCd. The JSON-RPC
``_send_request`` method is patched so CI can run without a live ``codex``
binary installed.
"""

from __future__ import annotations

import asyncio
import tempfile
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

from culture.clients.codex.agent_runner import CodexAgentRunner
from culture.clients.codex.telemetry import (
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
    """Install an InMemoryMetricReader and return (reader, registry)."""
    reader = InMemoryMetricReader()
    provider = SdkMeterProvider(
        resource=Resource.create({"service.name": "test-codex-runner"}),
        metric_readers=[reader],
    )
    otel_metrics.set_meter_provider(provider)
    return reader


@pytest.fixture
def tracing_exporter():
    """Install an InMemorySpanExporter and return (exporter, provider)."""
    exporter = InMemorySpanExporter()
    provider = SdkTracerProvider(
        resource=Resource.create({"service.name": "test-codex-runner-tracing"}),
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter, provider


@pytest.fixture
def registry(metrics_reader):
    """Return a real HarnessMetricsRegistry bound to the test meter provider."""
    from opentelemetry import metrics as _m

    meter = _m.get_meter("culture.harness.codex")
    return _build_registry(meter)


def _make_runner(registry=None, nick="spark-codex", model="gpt-5.4"):
    """Build a minimal CodexAgentRunner with telemetry wired up."""
    runner = CodexAgentRunner(
        model=model,
        directory=tempfile.mkdtemp(prefix="culture-test-codex-"),
        metrics=registry,
        nick=nick,
    )
    # Pre-set _thread_id so _execute_single_turn doesn't fail on None thread
    runner._thread_id = "test-thread-id"
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
    """Success path: span opened with correct codex attrs, metrics incremented.

    Token counters do NOT increment — codex defers token extraction to issue #298.
    """
    exporter, _ = tracing_exporter
    runner = _make_runner(registry=registry, nick="spark-codex", model="gpt-5.4")

    async def _fake_send_request(method, params):
        # Simulate the turn/completed notification setting the event
        await asyncio.sleep(0)
        runner._turn_done.set()
        return {"result": {}}

    with patch.object(runner, "_send_request", side_effect=_fake_send_request):
        await runner._execute_single_turn("hello")

    # Check span was created with correct attributes
    spans = exporter.get_finished_spans()
    llm_spans = [s for s in spans if s.name == "harness.llm.call"]
    assert len(llm_spans) == 1
    span = llm_spans[0]
    assert span.attributes.get("harness.backend") == "codex"
    assert span.attributes.get("harness.model") == "gpt-5.4"
    assert span.attributes.get("harness.nick") == "spark-codex"

    # Check llm_calls metric incremented with outcome=success
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "codex", "model": "gpt-5.4", "outcome": "success"},
    )
    assert calls_val == 1

    # Token counters must NOT be recorded — codex defers token extraction (issue #298)
    input_val = _get_metric_value(metrics_reader, "culture.harness.llm.tokens.input")
    assert input_val is None

    output_val = _get_metric_value(metrics_reader, "culture.harness.llm.tokens.output")
    assert output_val is None


@pytest.mark.asyncio
async def test_execute_single_turn_records_timeout(metrics_reader, registry):
    """Timeout path: asyncio.TimeoutError → outcome=timeout increments llm_calls."""
    runner = _make_runner(registry=registry, nick="spark-codex", model="gpt-5.4")

    async def _fake_send_request(method, params):
        # Return without setting _turn_done — this will cause the 300s timeout.
        # We patch asyncio.timeout to make it expire immediately.
        await asyncio.sleep(0)
        return {"result": {}}

    # Create a context manager that raises TimeoutError immediately
    class _ImmediateTimeout:
        async def __aenter__(self):
            raise asyncio.TimeoutError()

        async def __aexit__(self, *args):
            """Stub for SDK type."""

    with patch.object(runner, "_send_request", side_effect=_fake_send_request):
        with patch(
            "culture.clients.codex.agent_runner.asyncio.timeout", return_value=_ImmediateTimeout()
        ):
            await runner._execute_single_turn("hello")

    # Check outcome=timeout
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "codex", "model": "gpt-5.4", "outcome": "timeout"},
    )
    assert calls_val == 1


@pytest.mark.asyncio
async def test_execute_single_turn_records_error(metrics_reader, registry):
    """Error path: _send_request raises → outcome=error increments llm_calls."""
    runner = _make_runner(registry=registry, nick="spark-codex", model="gpt-5.4")

    async def _raising_send_request(method, params):
        raise RuntimeError("JSON-RPC exploded")

    with patch.object(runner, "_send_request", side_effect=_raising_send_request):
        await runner._execute_single_turn("hello")

    # Check outcome=error
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "codex", "model": "gpt-5.4", "outcome": "error"},
    )
    assert calls_val == 1


@pytest.mark.asyncio
async def test_execute_single_turn_no_metrics_no_recording(metrics_reader):
    """When metrics=None, no metric data is recorded."""
    runner = _make_runner(registry=None, nick="spark-codex")

    async def _fake_send_request(method, params):
        await asyncio.sleep(0)
        runner._turn_done.set()
        return {"result": {}}

    with patch.object(runner, "_send_request", side_effect=_fake_send_request):
        await runner._execute_single_turn("hello")

    # No metric data should be recorded for llm_calls
    calls_val = _get_metric_value(metrics_reader, "culture.harness.llm.calls")
    assert calls_val is None


@pytest.mark.asyncio
async def test_execute_single_turn_honors_configured_turn_timeout(metrics_reader, registry):
    """turn_timeout_seconds replaces the previous hardcoded 300s.

    Build a runner with a tiny timeout, leave _turn_done unset, and let
    the outer asyncio.timeout fire — outcome must be "timeout" and the
    on_turn_error callback must be awaited.
    """
    runner = CodexAgentRunner(
        model="gpt-5.4",
        directory=tempfile.mkdtemp(prefix="culture-test-codex-"),
        metrics=registry,
        nick="spark-codex",
        on_turn_error=AsyncMock(),
        turn_timeout_seconds=0.05,
    )
    runner._thread_id = "test-thread-id"
    # Attach a fake subprocess so the timeout handler exercises the
    # terminate path that triggers _cleanup_codex_process → on_exit.
    fake_process = MagicMock()
    fake_process.returncode = None
    runner._process = fake_process

    # _send_request returns immediately without firing _turn_done; the
    # wait below would block forever without the outer timeout.
    with patch.object(
        runner,
        "_send_request",
        new_callable=AsyncMock,
        return_value={"result": {}},
    ):
        await runner._execute_single_turn("hello")

    runner.on_turn_error.assert_awaited_once()
    fake_process.terminate.assert_called_once()
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "codex", "model": "gpt-5.4", "outcome": "timeout"},
    )
    assert calls_val == 1
