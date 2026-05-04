"""PARSE_ERROR audit records from Client._process_buffer.

Verifies a malformed inbound line produces a PARSE_ERROR JSONL record
with the right shape: line_preview (truncated to 64 chars), error type,
remote_addr from peer info, trace_id/span_id from the active
irc.client.process_buffer span.

NOTE: Message.parse is fully tolerant and never raises for any syntactically
constructable input. Tests therefore use mock.patch to force a ValueError
from Message.parse — this is the only reliable way to exercise the
except-branch in _process_buffer. The unit tests below validate the
record shape directly via _submit_parse_error_audit, and via a patched
_process_buffer round-trip against a real IRCd.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from agentirc.ircd import IRCd

from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.protocol.message import Message
from culture.transport.client import Client
from tests.telemetry._fakes import FakeWriter

# ---------------------------------------------------------------------------
# Unit-level: _submit_parse_error_audit builds the right record shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_parse_error_audit_record_shape(audit_dir, tracing_exporter):
    """_submit_parse_error_audit enqueues a well-formed PARSE_ERROR record."""
    cfg = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        telemetry=TelemetryConfig(audit_dir=str(audit_dir)),
    )
    server = IRCd(cfg)
    await server.start()
    try:
        client = Client(reader=None, writer=FakeWriter(), server=server)  # type: ignore[arg-type]
        client.nick = "testserv-alice"

        # Call _submit_parse_error_audit directly (no real parse failure needed).
        client._submit_parse_error_audit("GARBAGE_LINE_THAT_FAILED", ValueError("bad"))

        await asyncio.wait_for(server.audit.queue.join(), timeout=2.0)
    finally:
        await server.stop()

    files = sorted(audit_dir.glob("testserv-*.jsonl*"))
    records = []
    for f in files:
        for line in f.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))

    parse_errors = [r for r in records if r["event_type"] == "PARSE_ERROR"]
    assert len(parse_errors) == 1, f"expected 1 PARSE_ERROR record, got {records}"

    rec = parse_errors[0]
    assert rec["server"] == "testserv"
    assert rec["origin"] == "local"
    assert rec["peer"] == ""
    assert rec["target"] == {"kind": "", "name": ""}
    assert rec["actor"]["nick"] == "testserv-alice"
    assert rec["actor"]["kind"] == "human"
    # FakeWriter returns ("testaddr", 12345) for peername.
    assert rec["actor"]["remote_addr"] == "testaddr:12345"
    assert rec["payload"]["line_preview"] == "GARBAGE_LINE_THAT_FAILED"
    assert rec["payload"]["error"] == "ValueError"
    assert "T" in rec["ts"] and rec["ts"].endswith("Z")


@pytest.mark.asyncio
async def test_submit_parse_error_line_preview_truncated_to_64(audit_dir, tracing_exporter):
    """line_preview is truncated to 64 characters."""
    cfg = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        telemetry=TelemetryConfig(audit_dir=str(audit_dir)),
    )
    server = IRCd(cfg)
    await server.start()
    try:
        client = Client(reader=None, writer=FakeWriter(), server=server)  # type: ignore[arg-type]
        client.nick = "testserv-bob"

        long_line = "X" * 200
        client._submit_parse_error_audit(long_line, RuntimeError("too long"))

        await asyncio.wait_for(server.audit.queue.join(), timeout=2.0)
    finally:
        await server.stop()

    files = sorted(audit_dir.glob("testserv-*.jsonl*"))
    records = []
    for f in files:
        for line in f.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))

    parse_errors = [r for r in records if r["event_type"] == "PARSE_ERROR"]
    assert parse_errors, "expected a PARSE_ERROR record"
    rec = parse_errors[0]
    assert len(rec["payload"]["line_preview"]) == 64
    assert rec["payload"]["error"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Integration: _process_buffer calls _submit_parse_error_audit on exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_buffer_submits_audit_on_parse_exception(audit_dir, tracing_exporter, server):
    """When Message.parse raises, _process_buffer calls _submit_parse_error_audit."""
    client_obj = Client(reader=None, writer=FakeWriter(), server=server)  # type: ignore[arg-type]
    client_obj.nick = "testserv-carol"

    submitted: list[dict] = []
    original_submit = server.audit.submit

    def _capturing_submit(record):
        submitted.append(record)
        original_submit(record)

    server.audit.submit = _capturing_submit  # type: ignore[method-assign]

    def _boom(_line):
        raise ValueError("deliberate parse failure")

    with patch.object(Message, "parse", side_effect=_boom):
        await client_obj._process_buffer("BAD LINE\n")

    assert len(submitted) == 1
    rec = submitted[0]
    assert rec["event_type"] == "PARSE_ERROR"
    assert rec["server"] == server.config.name
    assert rec["payload"]["line_preview"].startswith("BAD LINE")
    assert rec["payload"]["error"] == "ValueError"
    assert rec["origin"] == "local"
    assert rec["peer"] == ""
    assert rec["target"] == {"kind": "", "name": ""}
    assert rec["actor"]["kind"] == "human"
    # trace_id and span_id should be set — process_buffer opens a span.
    assert len(rec["trace_id"]) == 32
    assert len(rec["span_id"]) == 16
    assert rec["tags"]["culture.dev/traceparent"].startswith("00-")


@pytest.mark.asyncio
async def test_process_buffer_audit_disabled_does_not_raise(tracing_exporter):
    """When audit is disabled, _process_buffer parse errors don't crash."""
    cfg = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        telemetry=TelemetryConfig(audit_enabled=False),
    )
    server = IRCd(cfg)
    client_obj = Client(reader=None, writer=FakeWriter(), server=server)  # type: ignore[arg-type]
    client_obj.nick = "testserv-dave"

    def _boom(_line):
        raise ValueError("deliberate parse failure")

    with patch.object(Message, "parse", side_effect=_boom):
        # Should not raise even though audit is disabled (submit is a no-op).
        await client_obj._process_buffer("BAD LINE\n")
