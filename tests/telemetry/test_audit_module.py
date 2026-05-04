"""Module-level tests for AuditSink + init_audit + helpers.

Exercises the sink directly without spinning up an IRCd; integration
tests live in tests/telemetry/test_audit_*.py (Task 7)."""

from __future__ import annotations

import asyncio
import json

import pytest
from agentirc.protocol import Event, EventType

from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.telemetry import AuditSink, init_audit
from culture.telemetry.audit import build_audit_record as _build_audit_record
from culture.telemetry.audit import reset_for_tests as _reset_audit
from culture.telemetry.audit import utc_iso_timestamp as _utc_iso_timestamp
from culture.telemetry.metrics import init_metrics
from culture.telemetry.metrics import reset_for_tests as _reset_metrics


@pytest.fixture(autouse=True)
def _reset():
    _reset_metrics()
    _reset_audit()
    yield
    _reset_audit()
    _reset_metrics()


def _build_config(tmp_path, **overrides):
    tcfg = TelemetryConfig(audit_dir=str(tmp_path), **overrides)
    return ServerConfig(name="testserv", telemetry=tcfg)


# --- _utc_iso_timestamp ---------------------------------------------------


def test_iso_timestamp_format():
    ts = _utc_iso_timestamp(0.0)
    assert ts == "1970-01-01T00:00:00.000000Z"


def test_iso_timestamp_microseconds_padded():
    ts = _utc_iso_timestamp(0.000005)
    assert ts.endswith(".000005Z")


# --- _build_audit_record --------------------------------------------------


def test_record_local_message_event():
    event = Event(
        type=EventType.MESSAGE,
        channel="#general",
        nick="testserv-alice",
        data={"text": "hi"},
        timestamp=0.0,
    )
    record = _build_audit_record(
        server_name="testserv",
        event=event,
        origin_tag=None,
        trace_id="",
        span_id="",
    )
    assert record["server"] == "testserv"
    assert record["event_type"] == "message"
    assert record["origin"] == "local"
    assert record["peer"] == ""
    assert record["target"] == {"kind": "channel", "name": "#general"}
    assert record["actor"]["nick"] == "testserv-alice"
    assert record["actor"]["kind"] == "human"
    assert record["payload"]["text"] == "hi"
    assert record["payload"]["nick"] == "testserv-alice"
    assert record["payload"]["channel"] == "#general"
    assert record["tags"] == {}


def test_record_federated_strips_underscore_keys():
    event = Event(
        type=EventType.MESSAGE,
        channel="#general",
        nick="alpha-alice",
        data={"text": "hi", "_origin": "alpha", "_internal": "x"},
        timestamp=0.0,
    )
    record = _build_audit_record(
        server_name="testserv",
        event=event,
        origin_tag="alpha",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
    )
    assert record["origin"] == "federated"
    assert record["peer"] == "alpha"
    assert record["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert "_origin" not in record["payload"]
    assert "_internal" not in record["payload"]
    assert record["payload"]["text"] == "hi"


def test_record_dm_target_kind_nick():
    event = Event(
        type=EventType.MESSAGE,
        channel=None,
        nick="testserv-alice",
        data={"text": "hi", "target": "testserv-bob"},
        timestamp=0.0,
    )
    record = _build_audit_record(
        server_name="testserv", event=event, origin_tag=None, trace_id="", span_id=""
    )
    assert record["target"] == {"kind": "nick", "name": "testserv-bob"}


def test_record_unknown_event_type_string():
    """Federated events sometimes carry event.type as a plain string."""
    event = Event(
        type="custom.future_event",  # type: ignore[arg-type]
        channel=None,
        nick="alpha-alice",
        data={"_origin": "alpha"},
        timestamp=0.0,
    )
    record = _build_audit_record(
        server_name="testserv", event=event, origin_tag="alpha", trace_id="", span_id=""
    )
    assert record["event_type"] == "custom.future_event"


def test_record_extra_tags_traceparent():
    event = Event(
        type=EventType.MESSAGE,
        channel="#general",
        nick="testserv-alice",
        data={"text": "hi"},
        timestamp=0.0,
    )
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    record = _build_audit_record(
        server_name="testserv",
        event=event,
        origin_tag=None,
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        extra_tags={"culture.dev/traceparent": tp},
    )
    assert record["tags"]["culture.dev/traceparent"] == tp


# --- init_audit -----------------------------------------------------------


def test_init_audit_returns_sink_when_disabled(tmp_path):
    cfg = _build_config(tmp_path, audit_enabled=False)
    metrics = init_metrics(cfg)
    sink = init_audit(cfg, metrics)
    assert isinstance(sink, AuditSink)
    assert sink.enabled is False
    # No-op submit must not raise.
    sink.submit({"event_type": "test"})


def test_init_audit_idempotent_same_config(tmp_path):
    cfg = _build_config(tmp_path, audit_enabled=False)
    metrics = init_metrics(cfg)
    sink1 = init_audit(cfg, metrics)
    sink2 = init_audit(cfg, metrics)
    assert sink1 is sink2


def test_init_audit_independent_of_telemetry_enabled(tmp_path):
    """audit_enabled=True should fire even with telemetry.enabled=False."""
    cfg = _build_config(tmp_path, enabled=False, audit_enabled=True)
    metrics = init_metrics(cfg)  # no-op proxy meter
    sink = init_audit(cfg, metrics)
    assert sink.enabled is True


def test_init_audit_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = _build_config(tmp_path)
    cfg.telemetry.audit_dir = "~/audit-test"
    metrics = init_metrics(cfg)
    sink = init_audit(cfg, metrics)
    assert sink.audit_dir == tmp_path / "audit-test"


# --- AuditSink lifecycle + write ------------------------------------------


@pytest.mark.asyncio
async def test_sink_start_creates_directory(tmp_path):
    audit_dir = tmp_path / "audit"
    cfg = _build_config(audit_dir)
    cfg.telemetry.audit_dir = str(audit_dir)
    metrics = init_metrics(cfg)
    sink = init_audit(cfg, metrics)
    await sink.start()
    try:
        assert audit_dir.exists()
        assert audit_dir.is_dir()
    finally:
        await sink.shutdown()


@pytest.mark.asyncio
async def test_sink_writes_record_to_jsonl(tmp_path):
    cfg = _build_config(tmp_path)
    metrics = init_metrics(cfg)
    sink = init_audit(cfg, metrics)
    await sink.start()
    try:
        sink.submit({"event_type": "test", "n": 1})
        sink.submit({"event_type": "test", "n": 2})
        await asyncio.wait_for(sink.queue.join(), timeout=2.0)
    finally:
        await sink.shutdown()

    files = list(tmp_path.glob("testserv-*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["n"] == 1
    assert json.loads(lines[1])["n"] == 2


@pytest.mark.asyncio
async def test_sink_disabled_writes_nothing(tmp_path):
    cfg = _build_config(tmp_path, audit_enabled=False)
    metrics = init_metrics(cfg)
    sink = init_audit(cfg, metrics)
    await sink.start()
    try:
        sink.submit({"event_type": "test"})
        await asyncio.sleep(0.05)
    finally:
        await sink.shutdown()
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_sink_size_rotation(tmp_path):
    cfg = _build_config(tmp_path, audit_max_file_bytes=200)
    metrics = init_metrics(cfg)
    sink = init_audit(cfg, metrics)
    await sink.start()
    try:
        for i in range(20):
            sink.submit({"event_type": "test", "i": i, "padding": "x" * 50})
        await asyncio.wait_for(sink.queue.join(), timeout=2.0)
    finally:
        await sink.shutdown()

    files = sorted(tmp_path.glob("testserv-*.jsonl*"))
    # With 200-byte cap and ~80-byte records, expect at least 2 files.
    assert len(files) >= 2, f"expected >=2 rotated files, got {[f.name for f in files]}"


@pytest.mark.asyncio
async def test_sink_overflow_drops_records(tmp_path):
    """Queue depth 2; flood 10. Some must be dropped."""
    cfg = _build_config(tmp_path, audit_queue_depth=2)
    metrics = init_metrics(cfg)
    sink = init_audit(cfg, metrics)
    await sink.start()
    # Don't await between submits — pile records up before the writer
    # task can pull them.
    try:
        for i in range(10):
            sink.submit({"event_type": "test", "i": i})
        # Let writer finish what it can.
        await asyncio.sleep(0.1)
    finally:
        await sink.shutdown()
    files = list(tmp_path.glob("*.jsonl"))
    written = files[0].read_text().splitlines() if files else []
    # Some records should have been dropped — strict equality on count is
    # racy because the writer may have drained 1 before we filled, so just
    # assert "less than 10".
    assert len(written) < 10, f"expected drops, all 10 written: {written}"


@pytest.mark.asyncio
async def test_sink_writes_oversized_record_into_fresh_file(tmp_path):
    """A single record larger than audit_max_file_bytes is still written —
    the cap is a soft ceiling for accumulated bytes, not a hard reject."""
    cfg = _build_config(tmp_path, audit_max_file_bytes=100)
    metrics = init_metrics(cfg)
    sink = init_audit(cfg, metrics)
    await sink.start()
    try:
        big = {"event_type": "test", "padding": "x" * 500}  # ~530 bytes
        sink.submit(big)
        sink.submit({"event_type": "test", "n": 2})
        await asyncio.wait_for(sink.queue.join(), timeout=2.0)
    finally:
        await sink.shutdown()
    files = sorted(tmp_path.glob("testserv-*.jsonl*"))
    # Oversized record forces rotation; second record forces another.
    assert len(files) >= 2
    # The oversized record must be in one of the files.
    all_lines = "\n".join(f.read_text() for f in files)
    assert "x" * 500 in all_lines
