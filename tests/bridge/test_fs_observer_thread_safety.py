"""Phase 5.4 — thread-safety stress test for the bridge FS observer.

The ``watchdog`` project explicitly documents that "a full thread safety
audit has not been completed" (per the rearchitecture spec's iter-2 C-2
from agent 10). The mitigation is this stress test: write 50 perm-queue
files in rapid succession and assert the observer surfaces 50 IPC
events with no drops over a 60-second window. Runs on Darwin (FSEvents
backend) and Linux (inotify backend).

The test does NOT mock the filesystem or the observer — it relies on
real watchdog Observer + real ``os.replace`` atomic writes from a hot
loop. Drops would manifest as ``< 50`` events seen.

Cleanup discipline: ``BridgeFSObserver.stop()`` joins the watchdog
thread with a 2s timeout, so a failed test cleans up its observer.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from culture.clients.bridge._fs_observer import (
    KIND_PERM_REQUEST,
    BridgeFSObserver,
)

N_FILES = 50
TEST_TIMEOUT_S = 60.0


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def _atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


@pytest.mark.asyncio
async def test_50_rapid_creates_surface_all_events(culture_root) -> None:
    """Burst of 50 file creates: observer surfaces 50 perm_request IPCs."""
    events: list[tuple[str, dict]] = []
    seen_ids: set[str] = set()
    loop = asyncio.get_running_loop()

    def _ipc_push(kind: str, payload: dict) -> None:
        events.append((kind, payload))
        rid = payload.get("id", "")
        if rid:
            seen_ids.add(rid)

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
    try:
        # Hot loop: 50 atomic writes in rapid succession.
        request_ids = [f"req-2026-06-03T00-00-{n:02d}-000000-stress{n:02d}" for n in range(N_FILES)]
        for rid in request_ids:
            _atomic_write_json(
                os.path.join(queue_dir, f"{rid}.json"),
                {
                    "id": rid,
                    "helper_nick": "testserv-stress",
                    "boss": "testserv-boss",
                    "tool_name": "Read",
                    "input": {"file_path": f"/{rid}"},
                    "created_at": "2026-06-03T00:00:00.000Z",
                },
            )
        # Wait up to 60s for all 50 events to surface. In practice this
        # completes in well under a second on both platforms.
        deadline = time.monotonic() + TEST_TIMEOUT_S
        while len(seen_ids) < N_FILES and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert len(seen_ids) == N_FILES, (
            f"only {len(seen_ids)}/{N_FILES} events surfaced — "
            f"missed: {set(request_ids) - seen_ids}"
        )
        # All events should be perm_request kind.
        kinds = {kind for kind, _payload in events}
        assert kinds == {KIND_PERM_REQUEST}, f"unexpected kinds: {kinds}"
    finally:
        obs.stop()


@pytest.mark.asyncio
async def test_50_rapid_creates_under_polling_fallback(culture_root) -> None:
    """Same stress test under the polling fallback — also no drops."""
    events: list[tuple[str, dict]] = []
    seen_ids: set[str] = set()
    loop = asyncio.get_running_loop()

    def _ipc_push(kind: str, payload: dict) -> None:
        events.append((kind, payload))
        rid = payload.get("id", "")
        if rid:
            seen_ids.add(rid)

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

    # Force the polling fallback so we exercise the diff-based observer
    # under the same rapid-create burst.
    import culture.clients.bridge._fs_observer as fs_mod

    saved = fs_mod._HAS_WATCHDOG
    fs_mod._HAS_WATCHDOG = False
    try:
        obs.start()
        assert obs.using_fallback
        request_ids = [f"req-2026-06-03T00-01-{n:02d}-000000-pollx{n:02d}" for n in range(N_FILES)]
        for rid in request_ids:
            _atomic_write_json(
                os.path.join(queue_dir, f"{rid}.json"),
                {
                    "id": rid,
                    "helper_nick": "testserv-stress",
                    "boss": "testserv-boss",
                    "tool_name": "Read",
                    "input": {"file_path": f"/{rid}"},
                    "created_at": "2026-06-03T00:01:00.000Z",
                },
            )
        deadline = time.monotonic() + TEST_TIMEOUT_S
        while len(seen_ids) < N_FILES and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert len(seen_ids) == N_FILES, (
            f"polling fallback dropped events: {len(seen_ids)}/{N_FILES} "
            f"missed: {set(request_ids) - seen_ids}"
        )
    finally:
        obs.stop()
        fs_mod._HAS_WATCHDOG = saved
