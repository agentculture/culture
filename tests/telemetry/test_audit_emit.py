"""Tests for IRCd.emit_event audit submit wiring.

Verifies a single emit_event produces a JSONL record with the right
shape (event_type, origin/peer, trace_id/span_id, actor, target,
payload, tags). Federation, parse_error, and queue overflow live in
their own test files (Tasks 6/7)."""

from __future__ import annotations

import asyncio
import json

import pytest

from culture.agentirc.config import ServerConfig, TelemetryConfig
from culture.agentirc.ircd import IRCd
from culture.agentirc.skill import Event, EventType


def _build_server(audit_dir):
    cfg = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        telemetry=TelemetryConfig(audit_dir=str(audit_dir)),
    )
    return IRCd(cfg)


def _read_records(audit_dir, server_name="testserv"):
    files = sorted(audit_dir.glob(f"{server_name}-*.jsonl*"))
    out = []
    for f in files:
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


@pytest.mark.asyncio
async def test_emit_event_writes_audit_record(audit_dir, tracing_exporter):
    server = _build_server(audit_dir)
    await server.start()
    try:
        await server.emit_event(
            Event(
                type=EventType.MESSAGE,
                channel="#test",
                nick="testserv-alice",
                data={"text": "hello world"},
            )
        )
        await asyncio.wait_for(server.audit.queue.join(), timeout=2.0)
    finally:
        await server.stop()

    records = _read_records(audit_dir)
    # Records: SERVER_WAKE (from start), our MESSAGE, SERVER_SLEEP (from stop).
    msg_records = [r for r in records if r["event_type"] == "message"]
    assert len(msg_records) == 1, f"expected 1 message record, got {records}"
    rec = msg_records[0]
    assert rec["server"] == "testserv"
    assert rec["origin"] == "local"
    assert rec["peer"] == ""
    assert rec["target"] == {"kind": "channel", "name": "#test"}
    assert rec["actor"]["nick"] == "testserv-alice"
    assert rec["actor"]["kind"] == "human"
    assert rec["payload"]["text"] == "hello world"
    assert rec["payload"]["nick"] == "testserv-alice"
    assert rec["payload"]["channel"] == "#test"
    # ts shape
    assert "T" in rec["ts"] and rec["ts"].endswith("Z")
    # trace_id/span_id may be empty if no span context wraps the
    # caller, but emit_event opens its own span so they MUST be set.
    assert len(rec["trace_id"]) == 32
    assert len(rec["span_id"]) == 16
    assert rec["tags"]["culture.dev/traceparent"].startswith("00-")
    assert rec["trace_id"] in rec["tags"]["culture.dev/traceparent"]


@pytest.mark.asyncio
async def test_emit_event_strips_underscore_keys_from_payload(audit_dir):
    server = _build_server(audit_dir)
    await server.start()
    try:
        await server.emit_event(
            Event(
                type=EventType.MESSAGE,
                channel="#test",
                nick="testserv-bob",
                data={
                    "text": "hi",
                    "_origin": "alpha",
                    "_internal_state": "x",
                },
            )
        )
        await asyncio.wait_for(server.audit.queue.join(), timeout=2.0)
    finally:
        await server.stop()

    records = _read_records(audit_dir)
    msg_records = [r for r in records if r["event_type"] == "message"]
    assert msg_records, f"expected message record, got {records}"
    rec = msg_records[0]
    assert "_origin" not in rec["payload"]
    assert "_internal_state" not in rec["payload"]
    # _origin makes the event federated.
    assert rec["origin"] == "federated"
    assert rec["peer"] == "alpha"


@pytest.mark.asyncio
async def test_emit_event_dm_target(audit_dir):
    server = _build_server(audit_dir)
    await server.start()
    try:
        await server.emit_event(
            Event(
                type=EventType.MESSAGE,
                channel=None,
                nick="testserv-alice",
                data={"text": "secret", "target": "testserv-bob"},
            )
        )
        await asyncio.wait_for(server.audit.queue.join(), timeout=2.0)
    finally:
        await server.stop()

    records = _read_records(audit_dir)
    msg_records = [r for r in records if r["event_type"] == "message"]
    assert msg_records
    rec = msg_records[0]
    assert rec["target"] == {"kind": "nick", "name": "testserv-bob"}


@pytest.mark.asyncio
async def test_server_wake_and_sleep_appear_in_audit(audit_dir):
    server = _build_server(audit_dir)
    await server.start()
    await server.stop()
    records = _read_records(audit_dir)
    types = [r["event_type"] for r in records]
    assert "server.wake" in types
    assert "server.sleep" in types
