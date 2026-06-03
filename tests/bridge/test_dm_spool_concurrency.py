"""Regression: Qodo PR #50 round-3 #1 — SQLite cross-thread race.

The async wrappers added in PR #50 #6 (`ainsert`, `aquery_for_nick`,
`amark_delivered`, `aget_by_msg_id`, `agc`) use ``asyncio.to_thread``
to dispatch the underlying sync methods onto the default thread pool.
This is correct for unblocking the asyncio event loop — but the store
keeps ONE long-lived ``sqlite3.Connection``, and Python's ``sqlite3``
does NOT guarantee a single ``Connection`` is safe across concurrent
cross-thread ``execute()`` calls. With multiple connected IRC clients
hitting DM spool / CHATHISTORY / GC paths simultaneously, the
unsynchronised connection can corrupt cursor state or raise
``ProgrammingError: SQLite objects created in a thread can only be
used in that same thread`` despite the ``check_same_thread=False``
hint.

The remediation is a ``threading.RLock`` held by every sync method
around the ``self._conn.execute(...)`` / ``commit()`` calls. The
async wrappers inherit the protection for free.

These tests fire many concurrent DB operations from a real
``ThreadPoolExecutor`` to exercise the lock under load. Pre-fix, the
runs intermittently surfaced ``sqlite3.InterfaceError`` /
``ProgrammingError`` / lost-row errors; post-fix they are
deterministic.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from culture.agentirc.dm_spool_store import DmSpoolStore


@pytest.fixture
def store(tmp_path):
    s = DmSpoolStore(str(tmp_path / "spool-concurrency.sqlite3"))
    yield s
    s.close()


class TestLockExists:
    def test_store_has_rlock_attribute(self, store) -> None:
        """The fix is wired up: ``_lock`` exists and is a re-entrant lock."""
        assert hasattr(store, "_lock")
        # RLock instances are an instance of the private RLock type
        # AND expose ``acquire`` + ``release``. Cheap structural check.
        assert hasattr(store._lock, "acquire")
        assert hasattr(store._lock, "release")
        # RLock allows re-entry from the same thread — Lock would deadlock.
        with store._lock:
            with store._lock:
                pass  # nested acquire from same thread succeeds


class TestConcurrentInsertsAllPersist:
    def test_500_inserts_across_8_threads_all_persist(self, store) -> None:
        """500 concurrent inserts across 8 threads → every row written.

        Pre-fix this could throw or lose rows. Post-fix the lock
        serialises ``execute`` + ``commit`` so each insert is atomic
        from the connection's perspective.
        """
        # Pick numbers that divide cleanly so per-thread counts sum to total.
        threads = 8
        per = 64
        total = threads * per  # = 512

        def _worker(start: int, end: int) -> None:
            for i in range(start, end):
                store.insert(
                    msg_id=f"m-{i:05d}",
                    sender="alice",
                    recipient="bob",
                    ts=float(i),
                    payload=f"hi-{i}",
                    tags="",
                )

        with ThreadPoolExecutor(max_workers=threads) as ex:
            futures = [ex.submit(_worker, i * per, (i + 1) * per) for i in range(threads)]
            for f in futures:
                f.result()  # surface any exception

        rows = store.query_for_nick("bob", limit=total + 1)
        assert len(rows) == total
        # Every msg_id we wrote is present (no lost rows under concurrency).
        msg_ids = {r["msg_id"] for r in rows}
        expected = {f"m-{i:05d}" for i in range(total)}
        assert msg_ids == expected


class TestMixedReadWriteUnderLoad:
    def test_concurrent_reads_writes_marks_and_gc_no_exception(self, store) -> None:
        """A realistic mix — inserts + queries + mark_delivered + gc
        running concurrently — must complete without exceptions and
        leave a self-consistent spool."""
        # Seed.
        for i in range(100):
            store.insert(
                msg_id=f"seed-{i}",
                sender="alice",
                recipient="bob",
                ts=float(i),
                payload="x",
                tags="",
            )

        errors: list[BaseException] = []
        stop = threading.Event()

        def _inserter() -> None:
            try:
                i = 1000
                while not stop.is_set():
                    store.insert(
                        msg_id=f"live-{i}",
                        sender="alice",
                        recipient="bob",
                        ts=float(i),
                        payload="y",
                        tags="",
                    )
                    i += 1
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def _reader() -> None:
            try:
                while not stop.is_set():
                    rows = store.query_for_nick("bob", limit=200)
                    # Every row must round-trip — no torn / partial rows.
                    for r in rows:
                        assert r["msg_id"]
                        assert r["recipient"] == "bob"
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def _marker() -> None:
            try:
                for i in range(50):
                    store.mark_delivered(f"seed-{i}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def _gcr() -> None:
            try:
                # GC with a future-dated cutoff so it actually deletes
                # the already-marked-delivered rows.
                store.gc(now=1e12)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=_inserter),
            threading.Thread(target=_reader),
            threading.Thread(target=_reader),
            threading.Thread(target=_marker),
        ]
        for t in threads:
            t.start()

        # Let the live load run, then trigger a GC sweep in the middle.
        import time as _time

        _time.sleep(0.5)
        gc_thread = threading.Thread(target=_gcr)
        gc_thread.start()
        _time.sleep(0.3)
        stop.set()
        for t in threads:
            t.join(timeout=10)
        gc_thread.join(timeout=10)

        assert errors == [], f"unexpected errors under concurrent load: {errors[:3]!r}"


class TestAsyncWrappersInheritLock:
    @pytest.mark.asyncio
    async def test_two_concurrent_ainserts_serialise(self, store) -> None:
        """Two awaits scheduled at the same time produce two well-
        formed rows — pre-fix this could intermittently raise."""
        await asyncio.gather(
            store.ainsert("m-A", "alice", "bob", 1.0, "A", ""),
            store.ainsert("m-B", "alice", "bob", 2.0, "B", ""),
        )
        rows = await store.aquery_for_nick("bob", limit=10)
        assert {r["msg_id"] for r in rows} == {"m-A", "m-B"}

    @pytest.mark.asyncio
    async def test_aget_during_concurrent_ainserts(self, store) -> None:
        """A lookup running while inserts are in flight returns a
        consistent answer (the row exists or not — no exception)."""
        results: list[bool] = []

        async def _spam_insert() -> None:
            for i in range(50):
                await store.ainsert(f"x-{i}", "alice", "bob", float(i), "", "")

        async def _spam_check() -> None:
            for i in range(50):
                results.append(await store.aget_by_msg_id("bob", f"x-{i}"))

        await asyncio.gather(_spam_insert(), _spam_check())
        # All checks completed (no race-induced exception); we don't
        # assert the contents because lookups MAY happen before the
        # corresponding insert finishes.
        assert len(results) == 50
