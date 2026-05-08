"""Tests for culture/clients/acp/agent_runner.py OTEL instrumentation.

Tests are isolated — no real ACP process, no IRCd. The JSON-RPC
``_send_prompt_with_retry`` method is patched directly via
``unittest.mock.patch.object`` so CI can run without a live ``opencode``
or ``cline`` binary installed.
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

from culture.clients.acp.agent_runner import ACPAgentRunner
from culture.clients.shared.telemetry import (
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
        resource=Resource.create({"service.name": "test-acp-runner"}),
        metric_readers=[reader],
    )
    otel_metrics.set_meter_provider(provider)
    return reader


@pytest.fixture
def tracing_exporter():
    """Install an InMemorySpanExporter and return (exporter, provider)."""
    exporter = InMemorySpanExporter()
    provider = SdkTracerProvider(
        resource=Resource.create({"service.name": "test-acp-runner-tracing"}),
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter, provider


@pytest.fixture
def registry(metrics_reader):
    """Return a real HarnessMetricsRegistry bound to the test meter provider."""
    from opentelemetry import metrics as _m

    meter = _m.get_meter("culture.harness.acp")
    return _build_registry(meter)


def _make_runner(registry=None, nick="spark-acp", model="anthropic/claude-sonnet-4-6"):
    """Build a minimal ACPAgentRunner with telemetry wired up."""
    runner = ACPAgentRunner(
        model=model,
        directory=tempfile.mkdtemp(prefix="culture-test-acp-"),
        metrics=registry,
        nick=nick,
    )
    # Pre-set _session_id so _send_prompt_with_retry doesn't fail on None session
    runner._session_id = "test-session-id"
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
async def test_execute_single_prompt_records_success(metrics_reader, tracing_exporter, registry):
    """Success path: span opened with correct acp attrs, metrics incremented.

    Token counters do NOT increment — ACP v1 defers token extraction.
    """
    exporter, _ = tracing_exporter
    runner = _make_runner(registry=registry, nick="spark-acp", model="anthropic/claude-sonnet-4-6")

    with patch.object(
        ACPAgentRunner,
        "_send_prompt_with_retry",
        new_callable=AsyncMock,
        return_value={"result": {"stopReason": "end_turn"}},
    ):
        with patch.object(ACPAgentRunner, "_handle_prompt_result", new_callable=AsyncMock):
            await runner._execute_single_prompt("hello")

    # Check span was created with correct attributes
    spans = exporter.get_finished_spans()
    llm_spans = [s for s in spans if s.name == "harness.llm.call"]
    assert len(llm_spans) == 1
    span = llm_spans[0]
    assert span.attributes.get("harness.backend") == "acp"
    assert span.attributes.get("harness.model") == "anthropic/claude-sonnet-4-6"
    assert span.attributes.get("harness.nick") == "spark-acp"

    # Check llm_calls metric incremented with outcome=success
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {
            "backend": "acp",
            "model": "anthropic/claude-sonnet-4-6",
            "outcome": "success",
        },
    )
    assert calls_val == 1

    # Token counters must NOT be recorded — ACP v1 defers token extraction
    input_val = _get_metric_value(metrics_reader, "culture.harness.llm.tokens.input")
    assert input_val is None

    output_val = _get_metric_value(metrics_reader, "culture.harness.llm.tokens.output")
    assert output_val is None


@pytest.mark.asyncio
async def test_execute_single_prompt_records_timeout(metrics_reader, registry):
    """Timeout path: TimeoutError from retry-exhaustion → outcome=timeout."""
    runner = _make_runner(registry=registry, nick="spark-acp", model="anthropic/claude-sonnet-4-6")

    with patch.object(
        ACPAgentRunner,
        "_send_prompt_with_retry",
        new_callable=AsyncMock,
        side_effect=TimeoutError("ACP prompt timed out after retry"),
    ):
        await runner._execute_single_prompt("hello")

    # Check outcome=timeout
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {
            "backend": "acp",
            "model": "anthropic/claude-sonnet-4-6",
            "outcome": "timeout",
        },
    )
    assert calls_val == 1


@pytest.mark.asyncio
async def test_execute_single_prompt_records_error(metrics_reader, registry):
    """Error path: RuntimeError → outcome=error increments llm_calls."""
    runner = _make_runner(registry=registry, nick="spark-acp", model="anthropic/claude-sonnet-4-6")

    with patch.object(
        ACPAgentRunner,
        "_send_prompt_with_retry",
        new_callable=AsyncMock,
        side_effect=RuntimeError("ACP JSON-RPC exploded"),
    ):
        await runner._execute_single_prompt("hello")

    # Check outcome=error
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {
            "backend": "acp",
            "model": "anthropic/claude-sonnet-4-6",
            "outcome": "error",
        },
    )
    assert calls_val == 1


@pytest.mark.asyncio
async def test_execute_single_prompt_no_metrics_no_recording(metrics_reader):
    """When metrics=None, no metric data is recorded."""
    runner = _make_runner(registry=None, nick="spark-acp")

    with patch.object(
        ACPAgentRunner,
        "_send_prompt_with_retry",
        new_callable=AsyncMock,
        return_value={"result": {"stopReason": "end_turn"}},
    ):
        with patch.object(ACPAgentRunner, "_handle_prompt_result", new_callable=AsyncMock):
            await runner._execute_single_prompt("hello")

    # No metric data should be recorded for llm_calls
    calls_val = _get_metric_value(metrics_reader, "culture.harness.llm.calls")
    assert calls_val is None


@pytest.mark.asyncio
async def test_execute_single_prompt_outer_timeout_fires_on_busy_poll_wedge(
    metrics_reader, registry
):
    """Outer asyncio.timeout safety net: if the busy-poll never completes, restart."""
    import asyncio

    runner = ACPAgentRunner(
        model="anthropic/claude-sonnet-4-6",
        directory=tempfile.mkdtemp(prefix="culture-test-acp-"),
        metrics=registry,
        nick="spark-acp",
        on_turn_error=AsyncMock(),
        turn_timeout_seconds=0.05,
    )
    runner._session_id = "test-session-id"
    # Attach a fake subprocess so the timeout handler exercises the
    # terminate path that triggers _cleanup_process → on_exit.
    fake_process = MagicMock()
    fake_process.returncode = None
    runner._process = fake_process

    # _send_prompt_with_retry returns immediately (no inner timeout
    # fires); _handle_prompt_result hangs on a never-resolving future,
    # which is the failure mode that motivated issue #349.
    async def _hanging_handle(_self, _resp):
        # Patched onto the class — descriptor protocol binds self,
        # so the signature has to match an instance method.
        await asyncio.Future()

    with patch.object(
        ACPAgentRunner,
        "_send_prompt_with_retry",
        new_callable=AsyncMock,
        return_value={"result": {}},
    ):
        with patch.object(ACPAgentRunner, "_handle_prompt_result", _hanging_handle):
            await runner._execute_single_prompt("hello")

    runner.on_turn_error.assert_awaited_once()
    fake_process.terminate.assert_called_once()
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {
            "backend": "acp",
            "model": "anthropic/claude-sonnet-4-6",
            "outcome": "timeout",
        },
    )
    assert calls_val == 1
