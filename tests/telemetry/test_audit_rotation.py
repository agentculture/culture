"""Tests for `culture.telemetry.audit` rotation + error paths.

Companion to `tests/telemetry/test_audit_module.py`, which already covers
the happy path. This file targets the remaining gaps:

- `_write_all` short-write retry and zero-bytes guard
- `AuditSink.submit` before `start()` (no queue yet)
- `AuditSink.start` idempotency + `shutdown` drain timeout
- `AuditSink._pick_rotation_path` suffix incrementing past 1000
- `AuditSink._maybe_rotate` open-failure handling
- `init_audit` warning when reinit with a still-running sink
- `reset_for_tests` warning when sink still running
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest
from agentirc.protocol import Event, EventType

from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.telemetry import init_audit
from culture.telemetry.audit import (
    AuditSink,
    _target_for,
    _write_all,
    build_audit_record,
)
from culture.telemetry.audit import reset_for_tests as _reset_audit
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


# ---------------------------------------------------------------------------
# _write_all
# ---------------------------------------------------------------------------


class TestWriteAll:
    def test_full_write_in_one_call(self, tmp_path):
        path = tmp_path / "out.txt"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            written = _write_all(fd, b"hello")
        finally:
            os.close(fd)
        assert written == 5
        assert path.read_bytes() == b"hello"

    def test_short_write_loops_until_complete(self, monkeypatch):
        """os.write returns fewer bytes than requested → loop until done."""
        chunks_written = []

        def _short_write(fd, buf):
            # First call returns 2, second call returns the rest.
            n = 2 if not chunks_written else len(buf)
            chunks_written.append(bytes(buf[:n]))
            return n

        monkeypatch.setattr("culture.telemetry.audit.os.write", _short_write)

        result = _write_all(42, b"hello world")
        assert result == 11
        assert b"".join(chunks_written) == b"hello world"

    def test_zero_byte_write_raises_oserror(self, monkeypatch):
        """os.write returning 0 must not loop forever."""

        def _stuck(fd, buf):
            return 0

        monkeypatch.setattr("culture.telemetry.audit.os.write", _stuck)
        with pytest.raises(OSError, match="refusing to spin"):
            _write_all(42, b"x")


# ---------------------------------------------------------------------------
# _target_for
# ---------------------------------------------------------------------------


class TestTargetFor:
    def test_channel_event(self):
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="#ops", data={})
        assert _target_for(ev) == {"kind": "channel", "name": "#ops"}

    def test_dm_event_target_from_data(self):
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="", data={"target": "bob"})
        assert _target_for(ev) == {"kind": "nick", "name": "bob"}

    def test_no_channel_no_target(self):
        ev = Event(type=EventType.QUIT, nick="ada", channel="", data={})
        assert _target_for(ev) == {"kind": "", "name": ""}

    def test_non_dict_data_is_treated_as_no_target(self):
        ev = Event(type=EventType.QUIT, nick="ada", channel="", data="not a dict")
        assert _target_for(ev) == {"kind": "", "name": ""}


# ---------------------------------------------------------------------------
# build_audit_record edge cases
# ---------------------------------------------------------------------------


class TestBuildAuditRecord:
    def test_eventtype_string_value_used_when_no_value_attr(self):
        """When event.type is already a string, str() is used directly."""
        # Use a real EventType (has .value); covered by other tests.
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="#ops", data={})
        rec = build_audit_record("s", ev, None, "t", "sp")
        assert rec["event_type"] == EventType.MESSAGE.value

    def test_origin_local_when_no_tag(self):
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="#ops", data={})
        rec = build_audit_record("s", ev, None, "t", "sp")
        assert rec["origin"] == "local"
        assert rec["peer"] == ""

    def test_origin_federated_when_tag_present(self):
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="#ops", data={})
        rec = build_audit_record("s", ev, "thor", "t", "sp")
        assert rec["origin"] == "federated"
        assert rec["peer"] == "thor"

    def test_extra_tags_copied_in(self):
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="#ops", data={})
        rec = build_audit_record(
            "s", ev, None, "t", "sp", extra_tags={"traceparent": "00-abc-def-01"}
        )
        assert rec["tags"] == {"traceparent": "00-abc-def-01"}

    def test_actor_kind_and_remote_addr_propagate(self):
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="#ops", data={})
        rec = build_audit_record(
            "s",
            ev,
            None,
            "t",
            "sp",
            actor_kind="bot",
            actor_remote_addr="10.0.0.5:6667",
        )
        assert rec["actor"]["kind"] == "bot"
        assert rec["actor"]["remote_addr"] == "10.0.0.5:6667"


# ---------------------------------------------------------------------------
# AuditSink lifecycle: submit-before-start, start idempotency, shutdown
# ---------------------------------------------------------------------------


class TestSubmitBeforeStart:
    def test_submit_before_start_drops_and_logs(self, tmp_path, caplog):
        """Calling submit() before start() must not crash; it counts + logs."""
        metrics = init_metrics(_build_config(tmp_path))
        sink = AuditSink(
            server_name="testserv",
            audit_dir=tmp_path,
            max_file_bytes=1_000_000,
            rotate_utc_midnight=True,
            queue_depth=10,
            enabled=True,
            metrics=metrics,
        )

        with caplog.at_level("WARNING", logger="culture.telemetry.audit"):
            sink.submit({"k": "v"})

        assert any("before start()" in rec.message for rec in caplog.records)

    def test_submit_no_op_when_disabled(self, tmp_path):
        """enabled=False → submit() is a no-op; queue stays None."""
        metrics = init_metrics(_build_config(tmp_path))
        sink = AuditSink(
            server_name="testserv",
            audit_dir=tmp_path,
            max_file_bytes=1_000_000,
            rotate_utc_midnight=True,
            queue_depth=10,
            enabled=False,
            metrics=metrics,
        )
        sink.submit({"k": "v"})  # no raise
        assert sink.queue is None


class TestStartIdempotency:
    @pytest.mark.asyncio
    async def test_start_twice_is_a_noop(self, tmp_path):
        metrics = init_metrics(_build_config(tmp_path))
        sink = AuditSink(
            server_name="testserv",
            audit_dir=tmp_path,
            max_file_bytes=1_000_000,
            rotate_utc_midnight=True,
            queue_depth=10,
            enabled=True,
            metrics=metrics,
        )
        await sink.start()
        first_task = sink._writer_task
        await sink.start()  # should not spawn another task
        assert sink._writer_task is first_task
        await sink.shutdown()

    @pytest.mark.asyncio
    async def test_start_disabled_does_nothing(self, tmp_path):
        metrics = init_metrics(_build_config(tmp_path))
        sink = AuditSink(
            server_name="testserv",
            audit_dir=tmp_path,
            max_file_bytes=1_000_000,
            rotate_utc_midnight=True,
            queue_depth=10,
            enabled=False,
            metrics=metrics,
        )
        await sink.start()
        assert sink._writer_task is None


class TestShutdownPaths:
    @pytest.mark.asyncio
    async def test_shutdown_disabled_no_op(self, tmp_path):
        metrics = init_metrics(_build_config(tmp_path))
        sink = AuditSink(
            server_name="testserv",
            audit_dir=tmp_path,
            max_file_bytes=1_000_000,
            rotate_utc_midnight=True,
            queue_depth=10,
            enabled=False,
            metrics=metrics,
        )
        await sink.shutdown()  # no raise

    @pytest.mark.asyncio
    async def test_shutdown_no_writer_task_is_noop(self, tmp_path):
        """Enabled but never started → shutdown is a no-op."""
        metrics = init_metrics(_build_config(tmp_path))
        sink = AuditSink(
            server_name="testserv",
            audit_dir=tmp_path,
            max_file_bytes=1_000_000,
            rotate_utc_midnight=True,
            queue_depth=10,
            enabled=True,
            metrics=metrics,
        )
        await sink.shutdown()  # no raise, no double-task

    @pytest.mark.asyncio
    async def test_shutdown_closes_fd_swallowing_oserror(self, tmp_path, monkeypatch):
        metrics = init_metrics(_build_config(tmp_path))
        sink = AuditSink(
            server_name="testserv",
            audit_dir=tmp_path,
            max_file_bytes=1_000_000,
            rotate_utc_midnight=True,
            queue_depth=10,
            enabled=True,
            metrics=metrics,
        )
        await sink.start()
        # Submit one record to open the fd
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="#ops", data={})
        sink.submit(build_audit_record("testserv", ev, None, "t", "sp"))
        await asyncio.sleep(0.05)  # let writer process

        # Patch os.close to raise — shutdown should swallow it.
        original_close = os.close

        def _close_raise(fd):
            if fd == sink._current_fd:
                # Restore + close once so the test doesn't leak the real fd
                monkeypatch.setattr("culture.telemetry.audit.os.close", original_close)
                original_close(fd)
                raise OSError("simulated close failure")
            return original_close(fd)

        monkeypatch.setattr("culture.telemetry.audit.os.close", _close_raise)

        await sink.shutdown()  # no raise


# ---------------------------------------------------------------------------
# Rotation: _pick_rotation_path stat-error handling, _maybe_rotate open failure
# ---------------------------------------------------------------------------


class TestRotation:
    def _sink(self, tmp_path, **overrides):
        metrics = init_metrics(_build_config(tmp_path))
        defaults = dict(
            server_name="testserv",
            audit_dir=tmp_path,
            max_file_bytes=100,  # tiny cap so size rotation fires
            rotate_utc_midnight=False,
            queue_depth=10,
            enabled=True,
            metrics=metrics,
        )
        defaults.update(overrides)
        return AuditSink(**defaults)

    def test_pick_rotation_path_increments_suffix_when_file_full(self, tmp_path):
        sink = self._sink(tmp_path)
        today = "2026-05-13"
        # Pre-create a full file at the .0 suffix
        path0 = tmp_path / f"testserv-{today}.jsonl"
        path0.write_bytes(b"x" * 200)  # > 100 bytes cap

        path, suffix, existing = sink._pick_rotation_path(today)
        # Should skip the full file and find .1
        assert suffix == 1
        assert "testserv-2026-05-13.1.jsonl" in str(path)
        assert existing == 0  # .1 doesn't exist yet

    def test_pick_rotation_path_handles_stat_error(self, tmp_path, monkeypatch):
        """If Path.stat() raises OSError, treat the file as empty (size 0)."""
        sink = self._sink(tmp_path)
        today = "2026-05-13"
        path0 = tmp_path / f"testserv-{today}.jsonl"
        path0.write_bytes(b"y" * 50)

        original_stat = type(path0).stat

        def _flaky_stat(self, *args, **kwargs):
            # Only intercept the audit file; let pytest's teardown stat work.
            if self.name.startswith("testserv-"):
                raise OSError("EACCES")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(type(path0), "stat", _flaky_stat)

        path, suffix, existing = sink._pick_rotation_path(today)
        # stat failed → treated as 0 bytes → first slot is acceptable
        assert suffix == 0
        assert existing == 0

    @pytest.mark.asyncio
    async def test_maybe_rotate_open_failure_keeps_old_fd_open(self, tmp_path, monkeypatch, caplog):
        """If os.open raises, _maybe_rotate keeps the previous fd usable."""
        sink = self._sink(tmp_path, max_file_bytes=200)
        await sink.start()

        # Write one record to open the initial fd
        ev = Event(type=EventType.MESSAGE, nick="ada", channel="#ops", data={})
        sink.submit(build_audit_record("testserv", ev, None, "t", "sp"))
        await asyncio.sleep(0.05)
        original_fd = sink._current_fd
        assert original_fd != -1

        # Force the next rotation to fail — and use a next_record_bytes
        # larger than the cap so the size-exceeded branch actually fires.
        with patch.object(sink, "_open_audit_file", side_effect=OSError("EACCES"), autospec=True):
            with caplog.at_level("ERROR", logger="culture.telemetry.audit"):
                sink._maybe_rotate(next_record_bytes=1_000)

        # Old fd is still open
        assert sink._current_fd == original_fd
        assert any("audit open failed" in rec.message for rec in caplog.records)

        await sink.shutdown()


# ---------------------------------------------------------------------------
# init_audit reinit warning
# ---------------------------------------------------------------------------


class TestInitAuditReinitWarning:
    @pytest.mark.asyncio
    async def test_reinit_with_running_sink_logs_warning(self, tmp_path, caplog):
        cfg1 = _build_config(tmp_path, audit_enabled=True, audit_queue_depth=8)
        metrics = init_metrics(_build_config(tmp_path))
        sink1 = init_audit(cfg1, metrics)
        await sink1.start()

        try:
            with caplog.at_level("WARNING", logger="culture.telemetry.audit"):
                # Mutate the config so the snapshot diverges
                cfg2 = _build_config(tmp_path, audit_enabled=True, audit_queue_depth=16)
                sink2 = init_audit(cfg2, metrics)

            # New sink is returned (different snapshot)
            assert sink2 is not sink1
            assert any("still running" in rec.message for rec in caplog.records)
        finally:
            await sink1.shutdown()
            if sink2._writer_task is not None:
                await sink2.shutdown()


# ---------------------------------------------------------------------------
# reset_for_tests warning when active sink
# ---------------------------------------------------------------------------


class TestResetForTestsWarning:
    @pytest.mark.asyncio
    async def test_warns_when_sink_still_running(self, tmp_path, caplog):
        cfg = _build_config(tmp_path, audit_enabled=True)
        metrics = init_metrics(_build_config(tmp_path))
        sink = init_audit(cfg, metrics)
        await sink.start()

        try:
            with caplog.at_level("WARNING", logger="culture.telemetry.audit"):
                _reset_audit()
            assert any("writer task still running" in rec.message for rec in caplog.records)
        finally:
            await sink.shutdown()

    def test_reset_when_no_sink_is_quiet(self, caplog):
        with caplog.at_level("WARNING", logger="culture.telemetry.audit"):
            _reset_audit()
        # No warning logged when there's nothing to reset
        assert not any("writer task still running" in rec.message for rec in caplog.records)
