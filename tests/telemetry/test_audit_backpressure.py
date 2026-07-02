"""Tests for #17: audit overflow surfaces backpressure.

Every dropped record must (a) increment the dedicated
``culture.audit.dropped`` counter — distinct from
``culture.audit.writes{outcome=error}`` which counts write failures —
and (b) surface to the submitter via ``submit()`` returning False.
"""

from __future__ import annotations

import pytest

from culture_core.agentirc.config import ServerConfig, TelemetryConfig
from culture_core.telemetry import AuditSink
from culture_core.telemetry.metrics import init_metrics
from tests.telemetry._metrics_helpers import get_counter_value


def _build_sink(tmp_path, metrics, *, queue_depth: int = 10, enabled: bool = True) -> AuditSink:
    return AuditSink(
        server_name="testserv",
        audit_dir=tmp_path,
        max_file_bytes=1_000_000,
        rotate_utc_midnight=True,
        queue_depth=queue_depth,
        enabled=enabled,
        metrics=metrics,
    )


def _metrics_for(tmp_path):
    tcfg = TelemetryConfig(audit_dir=str(tmp_path))
    return init_metrics(ServerConfig(name="testserv", telemetry=tcfg))


@pytest.mark.asyncio
async def test_enqueued_record_returns_true_and_counts_nothing(tmp_path, metrics_reader):
    metrics = _metrics_for(tmp_path)
    sink = _build_sink(tmp_path, metrics)
    await sink.start()
    try:
        assert sink.submit({"k": "v"}) is True
    finally:
        await sink.shutdown()
    assert get_counter_value(metrics_reader, "culture.audit.dropped") == 0


@pytest.mark.asyncio
async def test_queue_full_drop_counted_and_surfaced(tmp_path, metrics_reader):
    """Synthetic overflow: queue_depth=1, three submits in one synchronous
    block — the writer task has no await point to drain between them, so
    the second and third deterministically hit QueueFull."""
    metrics = _metrics_for(tmp_path)
    sink = _build_sink(tmp_path, metrics, queue_depth=1)
    await sink.start()
    try:
        results = [sink.submit({"n": i}) for i in range(3)]
    finally:
        await sink.shutdown()

    assert results == [True, False, False]
    assert get_counter_value(metrics_reader, "culture.audit.dropped", {"reason": "queue_full"}) == 2
    # Drops are NOT write errors — the two counters must stay distinguishable.
    assert get_counter_value(metrics_reader, "culture.audit.writes", {"outcome": "error"}) == 0
    assert get_counter_value(metrics_reader, "culture.audit.writes", {"outcome": "ok"}) == 1


@pytest.mark.asyncio
async def test_submit_before_start_counts_not_started_and_returns_false(tmp_path, metrics_reader):
    metrics = _metrics_for(tmp_path)
    sink = _build_sink(tmp_path, metrics)

    assert sink.submit({"k": "v"}) is False
    assert (
        get_counter_value(metrics_reader, "culture.audit.dropped", {"reason": "not_started"}) == 1
    )
    assert get_counter_value(metrics_reader, "culture.audit.writes", {"outcome": "error"}) == 0


@pytest.mark.asyncio
async def test_disabled_sink_submit_is_true_and_uncounted(tmp_path, metrics_reader):
    """Disabled is a configuration choice, not backpressure."""
    metrics = _metrics_for(tmp_path)
    sink = _build_sink(tmp_path, metrics, enabled=False)

    assert sink.submit({"k": "v"}) is True
    assert get_counter_value(metrics_reader, "culture.audit.dropped") == 0
