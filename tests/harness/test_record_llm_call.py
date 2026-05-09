"""Tests for record_llm_call() in culture/clients/shared/telemetry.py.

Exercises all usage-dict shapes: None, partial, full, and verifies that
``llm_calls`` and ``llm_call_duration`` always record regardless of usage,
while token counters only record when non-None int values are present.
"""

from __future__ import annotations

import pytest

# config still imported via sys.path set in conftest.py.
# pylint: disable=import-error
from config import DaemonConfig

from culture.clients.shared.telemetry import (
    init_harness_telemetry,
    record_llm_call,
    reset_for_tests,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset OTEL globals before and after every test."""
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture
def harness_metrics_reader_with_registry(harness_metrics_reader):
    """Extend the shared harness_metrics_reader fixture with a registry.

    Builds a registry from the proxy meter that points at the pre-installed
    real MeterProvider so ``record_llm_call`` writes to the in-memory store.

    Returns a (reader, registry) pair — tests that need both use this fixture.
    """
    config = DaemonConfig()
    _, registry = init_harness_telemetry(config)
    return harness_metrics_reader, registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attrs_match(point_attrs, expected: dict | None) -> bool:
    """Return True if all expected key/value pairs appear in point_attrs."""
    if not expected:
        return True
    return all(dict(point_attrs).get(k) == v for k, v in expected.items())


def _walk_data_points(reader, metric_name: str):
    """Yield every data point for ``metric_name`` across all resource/scope metrics."""
    data = reader.get_metrics_data()
    if data is None:
        return
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == metric_name:
                    yield from m.data.data_points


def _get_counter_sum(reader, metric_name: str, attrs: dict | None = None) -> float:
    """Sum counter data point values for ``metric_name``, optionally filtered by attrs."""
    return sum(
        dp.value
        for dp in _walk_data_points(reader, metric_name)
        if _attrs_match(dp.attributes, attrs)
    )


def _get_histogram_count(reader, metric_name: str, attrs: dict | None = None) -> int:
    """Count histogram recordings for ``metric_name``, optionally filtered by attrs."""
    return sum(
        dp.count
        for dp in _walk_data_points(reader, metric_name)
        if _attrs_match(dp.attributes, attrs)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_records_calls_counter_and_duration_histogram_unconditionally(
    harness_metrics_reader_with_registry,
):
    """llm_calls and llm_call_duration always record even when usage is None."""
    reader, registry = harness_metrics_reader_with_registry

    record_llm_call(
        registry,
        backend="codex",
        model="gpt-4o",
        nick="spark-codex",
        usage=None,
        duration_ms=99.5,
        outcome="success",
    )

    assert _get_counter_sum(reader, "culture.harness.llm.calls") == pytest.approx(1.0)
    assert _get_histogram_count(reader, "culture.harness.llm.call.duration") == 1
    # Token counters must NOT have been touched.
    assert _get_counter_sum(reader, "culture.harness.llm.tokens.input") == pytest.approx(0.0)
    assert _get_counter_sum(reader, "culture.harness.llm.tokens.output") == pytest.approx(0.0)


def test_skips_missing_usage_keys(harness_metrics_reader_with_registry):
    """Partial usage dict: only present non-None int keys record."""
    reader, registry = harness_metrics_reader_with_registry

    record_llm_call(
        registry,
        backend="claude",
        model="claude-opus-4-6",
        nick="spark-claude",
        usage={"tokens_input": 100},  # tokens_output absent
        duration_ms=50.0,
        outcome="success",
    )

    assert _get_counter_sum(reader, "culture.harness.llm.tokens.input") == pytest.approx(100.0)
    assert _get_counter_sum(reader, "culture.harness.llm.tokens.output") == pytest.approx(0.0)


def test_records_both_token_counters_when_present(harness_metrics_reader_with_registry):
    """Full usage dict: both token counters record with harness.nick label."""
    reader, registry = harness_metrics_reader_with_registry

    record_llm_call(
        registry,
        backend="claude",
        model="claude-opus-4-6",
        nick="spark-claude",
        usage={"tokens_input": 50, "tokens_output": 200},
        duration_ms=120.0,
        outcome="success",
    )

    assert _get_counter_sum(
        reader,
        "culture.harness.llm.tokens.input",
        {"backend": "claude", "model": "claude-opus-4-6", "harness.nick": "spark-claude"},
    ) == pytest.approx(50.0)
    assert _get_counter_sum(
        reader,
        "culture.harness.llm.tokens.output",
        {"backend": "claude", "model": "claude-opus-4-6", "harness.nick": "spark-claude"},
    ) == pytest.approx(200.0)


def test_outcome_label_propagates(harness_metrics_reader_with_registry):
    """outcome=error must appear on llm_calls and llm_call_duration."""
    reader, registry = harness_metrics_reader_with_registry

    record_llm_call(
        registry,
        backend="acp",
        model="my-model",
        nick="spark-acp",
        usage=None,
        duration_ms=5.0,
        outcome="error",
    )

    assert _get_counter_sum(
        reader, "culture.harness.llm.calls", {"backend": "acp", "outcome": "error"}
    ) == pytest.approx(1.0)
    assert (
        _get_histogram_count(
            reader,
            "culture.harness.llm.call.duration",
            {"backend": "acp", "outcome": "error"},
        )
        == 1
    )


def test_multiple_calls_accumulate(harness_metrics_reader_with_registry):
    """Multiple calls accumulate in the counter."""
    reader, registry = harness_metrics_reader_with_registry

    for _ in range(3):
        record_llm_call(
            registry,
            backend="copilot",
            model="gpt-4o",
            nick="spark-copilot",
            usage=None,
            duration_ms=10.0,
            outcome="success",
        )

    assert _get_counter_sum(reader, "culture.harness.llm.calls") == pytest.approx(3.0)


def test_none_valued_usage_keys_are_skipped(harness_metrics_reader_with_registry):
    """usage keys present but set to None are silently skipped (not 0)."""
    reader, registry = harness_metrics_reader_with_registry

    record_llm_call(
        registry,
        backend="claude",
        model="claude-haiku",
        nick="spark-claude",
        usage={"tokens_input": None, "tokens_output": None},
        duration_ms=8.0,
        outcome="success",
    )

    # None values must not add to the token counters.
    assert _get_counter_sum(reader, "culture.harness.llm.tokens.input") == pytest.approx(0.0)
    assert _get_counter_sum(reader, "culture.harness.llm.tokens.output") == pytest.approx(0.0)
    # But calls and duration still record.
    assert _get_counter_sum(reader, "culture.harness.llm.calls") == pytest.approx(1.0)
    assert _get_histogram_count(reader, "culture.harness.llm.call.duration") == 1


def test_timeout_outcome(harness_metrics_reader_with_registry):
    """outcome=timeout is valid and records correctly."""
    reader, registry = harness_metrics_reader_with_registry

    record_llm_call(
        registry,
        backend="codex",
        model="gpt-4o",
        nick="spark-codex",
        usage=None,
        duration_ms=300000.0,
        outcome="timeout",
    )

    assert _get_counter_sum(
        reader, "culture.harness.llm.calls", {"outcome": "timeout"}
    ) == pytest.approx(1.0)
