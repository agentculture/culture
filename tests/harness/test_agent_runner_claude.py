"""Tests for culture/clients/claude/agent_runner.py OTEL instrumentation.

Tests are isolated — no real Claude SDK, no IRCd. The SDK's ``query()``
async generator is replaced with a mock so CI can run without ``claude_agent_sdk``
installed.
"""

from __future__ import annotations

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
# Async iterator stubs — replace if-False-yield workarounds (S5797/S3626)
# ---------------------------------------------------------------------------


class _StubAsyncIter:
    """Async iterator that yields a fixed sequence of pre-built messages."""

    def __init__(self, messages):
        self._iter = iter(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _RaisingAsyncIter:
    """Async iterator that raises immediately on iteration."""

    def __init__(self, exc):
        self._exc = exc

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self._exc


# ---------------------------------------------------------------------------
# Stub out claude_agent_sdk if not installed so CI can import agent_runner.py
# ---------------------------------------------------------------------------


def _stub_claude_sdk():
    """Insert minimal stubs for claude_agent_sdk into sys.modules."""
    if "claude_agent_sdk" in sys.modules:
        return

    mod = types.ModuleType("claude_agent_sdk")

    class _Base:
        pass

    class AssistantMessage(_Base):
        def __init__(self, model="stub-model", content=None):
            self.model = model
            self.content = content or []

    class ResultMessage(_Base):
        def __init__(self, session_id="sid-1", is_error=False, result="", usage=None):
            self.session_id = session_id
            self.is_error = is_error
            self.result = result
            self.usage = usage

    class ClaudeAgentOptions(_Base):
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class TextBlock(_Base):
        pass

    class ThinkingBlock(_Base):
        pass

    class ToolUseBlock(_Base):
        pass

    class ToolResultBlock(_Base):
        pass

    def query(**kwargs):
        return _StubAsyncIter([])

    mod.AssistantMessage = AssistantMessage
    mod.ResultMessage = ResultMessage
    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.TextBlock = TextBlock
    mod.ThinkingBlock = ThinkingBlock
    mod.ToolUseBlock = ToolUseBlock
    mod.ToolResultBlock = ToolResultBlock
    mod.query = query

    sys.modules["claude_agent_sdk"] = mod


_stub_claude_sdk()

# Now safe to import
from culture.clients.claude.agent_runner import AgentRunner  # noqa: E402
from culture.clients.claude.config import DaemonConfig, TelemetryConfig  # noqa: E402
from culture.clients.claude.telemetry import (  # noqa: E402
    HarnessMetricsRegistry,
    init_harness_telemetry,
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
        resource=Resource.create({"service.name": "test-claude-runner"}),
        metric_readers=[reader],
    )
    otel_metrics.set_meter_provider(provider)
    return reader


@pytest.fixture
def tracing_exporter():
    """Install an InMemorySpanExporter and return (exporter, provider)."""
    exporter = InMemorySpanExporter()
    provider = SdkTracerProvider(
        resource=Resource.create({"service.name": "test-claude-runner-tracing"}),
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter, provider


@pytest.fixture
def registry(metrics_reader):
    """Return a real HarnessMetricsRegistry bound to the test meter provider."""
    from opentelemetry import metrics as _m

    meter = _m.get_meter("culture.harness.claude")
    from culture.clients.claude.telemetry import _build_registry

    return _build_registry(meter)


def _make_runner(registry=None, nick="spark-claude", model="claude-opus-4-6"):
    """Build a minimal AgentRunner with telemetry wired up."""
    return AgentRunner(
        model=model,
        directory=tempfile.mkdtemp(prefix="culture-test-claude-"),
        metrics=registry,
        nick=nick,
    )


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
async def test_process_turn_records_llm_call_success(metrics_reader, tracing_exporter, registry):
    """Success path: span opened with correct attrs, metrics incremented."""
    exporter, _ = tracing_exporter
    runner = _make_runner(registry=registry, nick="spark-claude", model="claude-opus-4-6")

    # Fake ResultMessage with usage dict
    sdk = sys.modules["claude_agent_sdk"]
    fake_result = sdk.ResultMessage(
        session_id="sid-1",
        is_error=False,
        usage={"input_tokens": 100, "output_tokens": 200},
    )

    async def _fake_query(**kwargs):
        yield fake_result

    with patch("culture.clients.claude.agent_runner.query", _fake_query):
        result = await runner._process_turn("hello")

    assert result is True

    # Check span
    spans = exporter.get_finished_spans()
    llm_spans = [s for s in spans if s.name == "harness.llm.call"]
    assert len(llm_spans) == 1
    span = llm_spans[0]
    assert span.attributes.get("harness.backend") == "claude"
    assert span.attributes.get("harness.model") == "claude-opus-4-6"
    assert span.attributes.get("harness.nick") == "spark-claude"

    # Check llm_calls metric
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "claude", "model": "claude-opus-4-6", "outcome": "success"},
    )
    assert calls_val == 1

    # Check token counters
    input_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.tokens.input",
        {"backend": "claude", "model": "claude-opus-4-6", "harness.nick": "spark-claude"},
    )
    assert input_val == 100

    output_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.tokens.output",
        {"backend": "claude", "model": "claude-opus-4-6", "harness.nick": "spark-claude"},
    )
    assert output_val == 200


@pytest.mark.asyncio
async def test_process_turn_records_error_outcome(metrics_reader, registry):
    """Error path: outcome=error increments llm_calls{outcome=error}."""
    runner = _make_runner(registry=registry, nick="spark-claude", model="claude-opus-4-6")

    with patch(
        "culture.clients.claude.agent_runner.query",
        lambda **kw: _RaisingAsyncIter(RuntimeError("SDK exploded")),
    ):
        result = await runner._process_turn("hello")

    assert result is False

    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "claude", "model": "claude-opus-4-6", "outcome": "error"},
    )
    assert calls_val == 1


@pytest.mark.asyncio
async def test_process_turn_no_metrics_no_recording(metrics_reader):
    """When metrics=None, no metric data is recorded."""
    runner = _make_runner(registry=None, nick="spark-claude")

    sdk = sys.modules["claude_agent_sdk"]
    fake_result = sdk.ResultMessage(
        session_id="sid-1",
        is_error=False,
        usage={"input_tokens": 50, "output_tokens": 75},
    )

    async def _fake_query(**kwargs):
        yield fake_result

    with patch("culture.clients.claude.agent_runner.query", _fake_query):
        result = await runner._process_turn("hello")

    assert result is True

    # No metric data should be recorded for llm_calls
    calls_val = _get_metric_value(metrics_reader, "culture.harness.llm.calls")
    assert calls_val is None


@pytest.mark.asyncio
async def test_process_turn_no_usage_skips_token_counters(metrics_reader, registry):
    """When ResultMessage has no usage, llm_calls increments but token counters do not."""
    runner = _make_runner(registry=registry, nick="spark-claude", model="claude-opus-4-6")

    sdk = sys.modules["claude_agent_sdk"]
    fake_result = sdk.ResultMessage(
        session_id="sid-1",
        is_error=False,
        usage=None,  # No usage
    )

    async def _fake_query(**kwargs):
        yield fake_result

    with patch("culture.clients.claude.agent_runner.query", _fake_query):
        result = await runner._process_turn("hello")

    assert result is True

    # llm_calls should increment
    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "claude", "model": "claude-opus-4-6", "outcome": "success"},
    )
    assert calls_val == 1

    # Token counters should NOT be recorded
    input_val = _get_metric_value(metrics_reader, "culture.harness.llm.tokens.input")
    assert input_val is None

    output_val = _get_metric_value(metrics_reader, "culture.harness.llm.tokens.output")
    assert output_val is None


@pytest.mark.asyncio
async def test_process_turn_records_attr_form_usage(metrics_reader, registry):
    """Attr-form usage (SimpleNamespace) is correctly extracted and recorded.

    The pre-fix bug affected only the attr path: ``getattr(u, 'input_tokens', None) or rhs``
    returns the rhs when the attr value is 0 (zero is falsy). This test verifies the
    explicit-branch fix correctly handles an attr-style usage object.
    """
    runner = _make_runner(registry=registry, nick="spark-claude", model="claude-opus-4-6")

    sdk = sys.modules["claude_agent_sdk"]
    # usage is an attribute-style object (not a dict)
    attr_usage = SimpleNamespace(input_tokens=300, output_tokens=150)
    fake_result = sdk.ResultMessage(
        session_id="sid-attr",
        is_error=False,
        usage=attr_usage,
    )

    async def _fake_query(**kwargs):
        yield fake_result

    with patch("culture.clients.claude.agent_runner.query", _fake_query):
        result = await runner._process_turn("hello")

    assert result is True

    input_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.tokens.input",
        {"backend": "claude", "model": "claude-opus-4-6", "harness.nick": "spark-claude"},
    )
    assert input_val == 300

    output_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.tokens.output",
        {"backend": "claude", "model": "claude-opus-4-6", "harness.nick": "spark-claude"},
    )
    assert output_val == 150


@pytest.mark.asyncio
async def test_process_turn_records_zero_token_usage(metrics_reader, registry):
    """Zero-valued token counts are recorded, not silenced.

    The pre-fix bug: ``getattr(u, 'input_tokens', None) or None`` evaluates to
    ``None`` when input_tokens==0 (zero is falsy), causing ``record_llm_call``'s
    ``isinstance(value, int)`` guard to drop the data point entirely. After the fix,
    ``_extract_usage`` uses explicit branching, so ``0`` survives and a data point
    with ``value=0`` is emitted.
    """
    runner = _make_runner(registry=registry, nick="spark-claude", model="claude-opus-4-6")

    sdk = sys.modules["claude_agent_sdk"]
    fake_result = sdk.ResultMessage(
        session_id="sid-zero",
        is_error=False,
        usage={"input_tokens": 0, "output_tokens": 0},
    )

    async def _fake_query(**kwargs):
        yield fake_result

    with patch("culture.clients.claude.agent_runner.query", _fake_query):
        result = await runner._process_turn("hello")

    assert result is True

    # Both token counters must exist with value 0 — the data point must be present.
    data = metrics_reader.get_metrics_data()
    assert data is not None

    def _find_data_points(metric_name):
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == metric_name:
                        return list(m.data.data_points)
        return []

    attrs = {"backend": "claude", "model": "claude-opus-4-6", "harness.nick": "spark-claude"}

    input_dps = [
        dp
        for dp in _find_data_points("culture.harness.llm.tokens.input")
        if all(dp.attributes.get(k) == v for k, v in attrs.items())
    ]
    assert len(input_dps) == 1, "Expected exactly one data point for tokens.input with zero value"
    assert input_dps[0].value == 0

    output_dps = [
        dp
        for dp in _find_data_points("culture.harness.llm.tokens.output")
        if all(dp.attributes.get(k) == v for k, v in attrs.items())
    ]
    assert len(output_dps) == 1, "Expected exactly one data point for tokens.output with zero value"
    assert output_dps[0].value == 0


@pytest.mark.asyncio
async def test_process_turn_timeout_records_timeout_outcome_and_calls_on_exit(
    metrics_reader, registry
):
    """Wedged SDK stream: outer asyncio.wait_for fires, outcome=timeout, on_exit(1)."""
    import asyncio

    on_exit = AsyncMock()
    runner = AgentRunner(
        model="claude-opus-4-6",
        directory=tempfile.mkdtemp(prefix="culture-test-claude-"),
        on_exit=on_exit,
        metrics=registry,
        nick="spark-claude",
        turn_timeout_seconds=0.05,
    )

    async def _hanging_query(**kwargs):
        # Never yields — simulates a wedged SDK stream (the failure
        # mode that motivated issue #349).
        await asyncio.Future()
        yield  # unreachable, but makes this a valid async generator

    with patch("culture.clients.claude.agent_runner.query", _hanging_query):
        result = await runner._process_turn("hello")

    assert result is False, "timed-out turn must return False so _run_loop exits"
    on_exit.assert_awaited_once_with(1)

    calls_val = _get_metric_value(
        metrics_reader,
        "culture.harness.llm.calls",
        {"backend": "claude", "model": "claude-opus-4-6", "outcome": "timeout"},
    )
    assert calls_val == 1, "metric must record outcome=timeout for the wedged turn"


@pytest.mark.asyncio
async def test_process_turn_timeout_disabled_when_zero(metrics_reader, registry):
    """turn_timeout_seconds=0 disables the wrap; success path still works."""
    on_exit = AsyncMock()
    runner = AgentRunner(
        model="claude-opus-4-6",
        directory=tempfile.mkdtemp(prefix="culture-test-claude-"),
        on_exit=on_exit,
        metrics=registry,
        nick="spark-claude",
        turn_timeout_seconds=0,
    )

    sdk = sys.modules["claude_agent_sdk"]
    fake_result = sdk.ResultMessage(
        session_id="sid-1",
        is_error=False,
        usage={"input_tokens": 10, "output_tokens": 20},
    )

    async def _fast_query(**kwargs):
        yield fake_result

    with patch("culture.clients.claude.agent_runner.query", _fast_query):
        result = await runner._process_turn("hello")

    assert result is True, "disabled-timeout path must complete normally"
    on_exit.assert_not_awaited()
