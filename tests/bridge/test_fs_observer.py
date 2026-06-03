"""Phase 5.4 — bridge FS observer pushes broker file events to CC.

Covers the three watched directories under ``~/.culture/``:

  perm-queue/         -> ``perm_request`` IPC kind
  perm-decisions/     -> ``perm_decision`` IPC kind
  perm-demote-notices/ -> ``inbound_mention`` (tag ``demote-notice``)

Real watchdog + real filesystem under a tmp ``CULTURE_HOME``. No mocks.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from culture.clients.bridge._fs_observer import (
    _HAS_WATCHDOG,
    KIND_INBOUND_MENTION,
    KIND_PERM_DECISION,
    KIND_PERM_REQUEST,
    TAG_DEMOTE_NOTICE,
    BridgeFSObserver,
    _PollingFallback,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    """Per-test ``CULTURE_HOME`` so tests are xdist-safe."""
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def _atomic_write_json(path: str, payload: dict) -> None:
    """Mirror the broker's atomic-write contract so observer sees a complete file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


async def _collect_events(
    events: list[tuple[str, dict]],
    expected: int,
    timeout: float = 5.0,
) -> None:
    """Yield to the loop until ``expected`` events have arrived (or timeout)."""
    deadline = time.monotonic() + timeout
    while len(events) < expected and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


def _start_observer(culture_root, events) -> BridgeFSObserver:
    loop = asyncio.get_running_loop()

    def _ipc_push(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    queue_dir = os.path.join(str(culture_root), "perm-queue")
    decisions_dir = os.path.join(str(culture_root), "perm-decisions")
    demote_dir = os.path.join(str(culture_root), "perm-demote-notices")
    obs = BridgeFSObserver(
        loop=loop,
        ipc_push=_ipc_push,
        queue_dir=queue_dir,
        decisions_dir=decisions_dir,
        demote_dir=demote_dir,
        poll_interval=0.05,
    )
    obs.start()
    return obs


# ---------------------------------------------------------------------------
# Push-path tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perm_queue_new_file_emits_perm_request(culture_root) -> None:
    events: list[tuple[str, dict]] = []
    obs = _start_observer(culture_root, events)
    try:
        request = {
            "id": "req-2026-06-03T00-00-00-000000-abc123",
            "helper_nick": "testserv-worker",
            "boss": "testserv-boss",
            "tool_name": "Bash",
            "input": {"command": "ls /"},
            "created_at": "2026-06-03T00:00:00.000Z",
        }
        path = os.path.join(culture_root, "perm-queue", f"{request['id']}.json")
        _atomic_write_json(path, request)
        await _collect_events(events, 1)
        assert len(events) >= 1
        kind, payload = events[0]
        assert kind == KIND_PERM_REQUEST
        assert payload["id"] == request["id"]
        assert payload["helper_nick"] == "testserv-worker"
        assert payload["boss"] == "testserv-boss"
        assert payload["tool_name"] == "Bash"
        assert payload["source"] == "fs_observer"
    finally:
        obs.stop()


@pytest.mark.asyncio
async def test_perm_decisions_new_file_emits_perm_decision(culture_root) -> None:
    events: list[tuple[str, dict]] = []
    obs = _start_observer(culture_root, events)
    try:
        decision = {
            "id": "req-2026-06-03T00-00-01-000000-def456",
            "verdict": "allow",
            "scope": "once",
            "decided_by": "testserv-boss",
            "decided_at": "2026-06-03T00:00:01.000Z",
        }
        path = os.path.join(culture_root, "perm-decisions", f"{decision['id']}.json")
        _atomic_write_json(path, decision)
        await _collect_events(events, 1)
        assert len(events) >= 1
        kind, payload = events[0]
        assert kind == KIND_PERM_DECISION
        assert payload["id"] == decision["id"]
        assert payload["verdict"] == "allow"
        assert payload["scope"] == "once"
        assert payload["decided_by"] == "testserv-boss"
    finally:
        obs.stop()


@pytest.mark.asyncio
async def test_demote_notice_emits_inbound_mention_with_tag(culture_root) -> None:
    """Use the broker's ``_write_demote_notice`` directly so the test
    exercises the REAL on-disk schema. The previous version fabricated a
    notice using the observer's assumed schema (``id`` / ``tool_name`` /
    ``reason``) — that masked Rev-C HIGH-1 where the broker actually
    writes ``request_id`` / ``original_tool`` / ``demote_reason`` and
    the observer's renderer fell back to ``'?'`` for every field.
    """
    from culture.clients import _perm_broker

    request_id = "req-2026-06-03T00-00-02-000000-ghi789"
    events: list[tuple[str, dict]] = []
    obs = _start_observer(culture_root, events)
    try:
        _perm_broker._write_demote_notice(
            request_id,
            "Bash",
            "no input_regex for high-risk tool",
            boss="testserv-boss",
            helper_nick="testserv-worker",
        )
        await _collect_events(events, 1)
        assert len(events) >= 1
        kind, payload = events[0]
        assert kind == KIND_INBOUND_MENTION
        assert payload["tag"] == TAG_DEMOTE_NOTICE
        assert payload["sender"] == "bridge"
        assert payload["target"] == "testserv-boss"
        # The rendered text must contain the original tool and the
        # request id — both were silently dropped to ``'?'`` before
        # the fix.
        assert "Bash" in payload["text"]
        assert request_id in payload["text"]
        assert "no input_regex" in payload["text"]
        assert payload["request_id"] == request_id
        assert payload["original_tool"] == "Bash"
    finally:
        obs.stop()


@pytest.mark.asyncio
async def test_observer_ignores_tmp_atomic_write_artifacts(culture_root) -> None:
    """``.tmp-...json`` half-written files must not produce events."""
    events: list[tuple[str, dict]] = []
    obs = _start_observer(culture_root, events)
    try:
        queue_dir = os.path.join(culture_root, "perm-queue")
        os.makedirs(queue_dir, exist_ok=True)
        # Drop a tempfile that mimics the atomic-write half-state.
        tmp_path = os.path.join(queue_dir, ".tmp-abc.json")
        with open(tmp_path, "w") as fh:
            fh.write('{"id": "x"}')
        # Give the observer a chance.
        await asyncio.sleep(0.3)
        # No events from the tempfile.
        assert all(p.get("id") != "x" for _k, p in events)
    finally:
        obs.stop()


@pytest.mark.asyncio
async def test_observer_idempotent_start_stop(culture_root) -> None:
    """start() twice + stop() twice are both no-ops."""
    events: list[tuple[str, dict]] = []
    obs = _start_observer(culture_root, events)
    try:
        # Second start() should be a no-op.
        obs.start()
    finally:
        obs.stop()
        # Second stop() should be a no-op.
        obs.stop()


# ---------------------------------------------------------------------------
# Fallback path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_fallback_surfaces_new_files(culture_root) -> None:
    """When watchdog is forced off, the polling fallback still pushes."""
    events: list[tuple[str, dict]] = []
    loop = asyncio.get_running_loop()

    def _ipc_push(kind: str, payload: dict) -> None:
        events.append((kind, payload))

    queue_dir = os.path.join(str(culture_root), "perm-queue")
    decisions_dir = os.path.join(str(culture_root), "perm-decisions")
    demote_dir = os.path.join(str(culture_root), "perm-demote-notices")
    for d in (queue_dir, decisions_dir, demote_dir):
        os.makedirs(d, exist_ok=True)

    obs = BridgeFSObserver(
        loop=loop,
        ipc_push=_ipc_push,
        queue_dir=queue_dir,
        decisions_dir=decisions_dir,
        demote_dir=demote_dir,
        poll_interval=0.05,
    )
    # Force the fallback path by monkeypatching the module-level flag.
    import culture.clients.bridge._fs_observer as fs_mod

    saved = fs_mod._HAS_WATCHDOG
    fs_mod._HAS_WATCHDOG = False
    try:
        obs.start()
        assert obs.using_fallback
        assert not obs.using_watchdog
        request = {
            "id": "req-2026-06-03T00-00-03-000000-jkl012",
            "helper_nick": "testserv-worker2",
            "tool_name": "Edit",
            "input": {"file_path": "/tmp/x"},
        }
        path = os.path.join(queue_dir, f"{request['id']}.json")
        _atomic_write_json(path, request)
        await _collect_events(events, 1, timeout=3.0)
        assert len(events) >= 1
        kind, payload = events[0]
        assert kind == KIND_PERM_REQUEST
        assert payload["id"] == request["id"]
    finally:
        obs.stop()
        fs_mod._HAS_WATCHDOG = saved


@pytest.mark.asyncio
async def test_polling_fallback_diffs_against_seed(culture_root) -> None:
    """Pre-existing files at start time should not replay as new events."""
    queue_dir = os.path.join(str(culture_root), "perm-queue")
    os.makedirs(queue_dir, exist_ok=True)
    _atomic_write_json(
        os.path.join(queue_dir, "req-2026-06-03T00-00-04-000000-mno345.json"),
        {"id": "req-2026-06-03T00-00-04-000000-mno345"},
    )

    loop = asyncio.get_running_loop()
    seen: list[tuple[str, str]] = []

    def _dispatch(label: str, path: str) -> None:
        seen.append((label, path))

    decisions_dir = os.path.join(str(culture_root), "perm-decisions")
    demote_dir = os.path.join(str(culture_root), "perm-demote-notices")
    for d in (decisions_dir, demote_dir):
        os.makedirs(d, exist_ok=True)

    poller = _PollingFallback(
        loop=loop,
        dispatcher=_dispatch,
        directories={
            "perm-queue": queue_dir,
            "perm-decisions": decisions_dir,
            "perm-demote-notices": demote_dir,
        },
        poll_interval=0.05,
    )
    poller.start()
    try:
        await asyncio.sleep(0.3)
        # Pre-existing file must NOT have surfaced.
        assert seen == []
    finally:
        poller.stop()


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_has_watchdog_flag_is_bool() -> None:
    """Module-level ``_HAS_WATCHDOG`` is a bool — callers can branch on it."""
    assert isinstance(_HAS_WATCHDOG, bool)
