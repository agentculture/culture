"""Regression: Qodo PR #50 #6 — DM spool async offload.

Synchronous ``sqlite3`` calls in async handlers freeze the asyncio
event loop. Under load (slow disk, WAL checkpoint, ``database is
locked`` retry) a single insert/query/gc can run for tens of ms,
which blocks EVERY connected IRC client for that duration.

These tests prove the async wrappers (:meth:`ainsert`,
:meth:`amark_delivered`, :meth:`aquery_for_nick`,
:meth:`aget_by_msg_id`, :meth:`agc`) deliver the same results as
their sync counterparts AND that a slow synthetic DB call does NOT
block a concurrent loop task.

The "does not block" assertion is the load-bearing one: it monkey-
patches the sync method to ``time.sleep(0.5)`` and asserts a heartbeat
coroutine continues ticking past the sleep window. Pre-fix, the
heartbeat would stall the entire 500 ms.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from culture.agentirc.dm_spool_store import DmSpoolStore


@pytest.fixture
def store(tmp_path):
    s = DmSpoolStore(str(tmp_path / "spool.sqlite3"))
    yield s
    s.close()


class TestAsyncWrappersAgreeWithSync:
    """Belt-and-braces: every async wrapper returns what its sync
    counterpart would for the same input."""

    @pytest.mark.asyncio
    async def test_ainsert_then_aquery_returns_row(self, store) -> None:
        await store.ainsert(
            msg_id="m-1",
            sender="alice",
            recipient="bob",
            ts=1.0,
            payload="hi bob",
            tags="",
        )
        rows = await store.aquery_for_nick("bob")
        assert [r["msg_id"] for r in rows] == ["m-1"]

    @pytest.mark.asyncio
    async def test_aget_by_msg_id_matches_sync(self, store) -> None:
        store.insert("m-2", "alice", "bob", 2.0, "hi", "")
        assert await store.aget_by_msg_id("bob", "m-2") is True
        assert await store.aget_by_msg_id("bob", "missing") is False
        assert await store.aget_by_msg_id("eve", "m-2") is False  # IDOR-safe

    @pytest.mark.asyncio
    async def test_amark_delivered_returns_true_then_false(self, store) -> None:
        store.insert("m-3", "alice", "bob", 3.0, "hi", "")
        assert await store.amark_delivered("m-3") is True
        # Idempotent — second call returns False because already delivered.
        assert await store.amark_delivered("m-3") is False

    @pytest.mark.asyncio
    async def test_agc_returns_expected_dict_shape(self, store) -> None:
        result = await store.agc()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"delivered", "undelivered"}


class TestEventLoopNotBlocked:
    """The load-bearing assertion: a slow DB call MUST NOT block other
    coroutines on the event loop. Pre-fix the sync method blocked the
    loop; post-fix the async wrapper offloads via ``to_thread``.
    """

    @pytest.mark.asyncio
    async def test_concurrent_heartbeat_runs_during_slow_insert(self, store, monkeypatch) -> None:
        # Synthetic slow DB call: 500 ms of pure CPU/IO outside the loop.
        original_insert = store.insert

        def _slow_insert(*args, **kwargs):
            time.sleep(0.5)  # blocks the calling thread, NOT the loop
            return original_insert(*args, **kwargs)

        monkeypatch.setattr(store, "insert", _slow_insert)

        ticks: list[float] = []
        stop = asyncio.Event()

        async def _heartbeat() -> None:
            t0 = asyncio.get_event_loop().time()
            while not stop.is_set():
                ticks.append(asyncio.get_event_loop().time() - t0)
                await asyncio.sleep(0.02)  # 20 ms between ticks

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            await store.ainsert(
                msg_id="m-slow",
                sender="alice",
                recipient="bob",
                ts=time.time(),
                payload="hi",
                tags="",
            )
        finally:
            stop.set()
            await heartbeat_task

        # During the 500 ms slow insert the heartbeat must have ticked
        # at least 10 times. Pre-fix (sync insert blocking the loop)
        # the heartbeat would tick 0 times in that window.
        assert len(ticks) >= 10, (
            f"heartbeat ticked only {len(ticks)} times during the slow "
            "insert — the event loop appears to be blocked"
        )
