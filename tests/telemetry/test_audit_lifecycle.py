"""Smoke test for IRCd-managed AuditSink lifecycle.

This test exists to verify the wiring: IRCd.start() actually awaits
sink.start(), and IRCd.stop() actually awaits sink.shutdown().
The audit sink itself is exercised in test_audit_module.py; integration
tests covering submit() during the lifecycle land in Task 5/Task 7."""

from __future__ import annotations

import pytest
from agentirc.ircd import IRCd

from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.telemetry.audit import reset_for_tests as _reset_audit
from culture.telemetry.metrics import reset_for_tests as _reset_metrics


@pytest.fixture(autouse=True)
def _reset():
    _reset_metrics()
    _reset_audit()
    yield
    _reset_audit()
    _reset_metrics()


@pytest.mark.asyncio
async def test_audit_sink_starts_and_shuts_down_with_ircd(audit_dir):
    """IRCd.start() opens the audit dir and writer task; stop() drains it."""
    cfg = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        telemetry=TelemetryConfig(audit_dir=str(audit_dir)),
    )
    server = IRCd(cfg)
    assert server.audit is not None
    assert server.audit._writer_task is None  # not started yet

    await server.start()
    try:
        assert server.audit._writer_task is not None
        assert audit_dir.exists()
    finally:
        await server.stop()

    # After stop, the writer task should be cancelled.
    assert server.audit._writer_task is None


@pytest.mark.asyncio
async def test_audit_disabled_does_not_create_directory(tmp_path):
    """audit_enabled=False should not create the audit dir."""
    audit_dir = tmp_path / "should-not-exist"
    cfg = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        telemetry=TelemetryConfig(
            audit_dir=str(audit_dir),
            audit_enabled=False,
        ),
    )
    server = IRCd(cfg)
    await server.start()
    try:
        assert not audit_dir.exists(), f"audit_enabled=False but dir was created at {audit_dir}"
    finally:
        await server.stop()
