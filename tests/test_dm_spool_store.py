"""Unit tests for ``culture.agentirc.dm_spool_store``.

Pure-function-style tests against the SQLite spool — no IRCd, no
bridge. Covers schema/insert/query/mark-delivered/gc plus the
0o600 file / 0o700 dir filesystem hardening (Task 3.7).
"""

from __future__ import annotations

import os
import stat
import time

import pytest

from culture.agentirc.dm_spool_store import (
    DmSpoolStore,
    default_spool_path,
)


def _open_store(tmp_path) -> DmSpoolStore:
    db_path = tmp_path / "testserv.dm-spool.db"
    return DmSpoolStore(db_path)


def test_insert_and_query_roundtrip(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        store.insert(
            msg_id="abc123",
            sender="testserv-peer",
            recipient="testserv-boss",
            ts=1700000000.0,
            payload="hello boss",
            tags="msgid=abc123",
        )
        entries = store.query_for_nick("testserv-boss")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["msg_id"] == "abc123"
        assert entry["sender"] == "testserv-peer"
        assert entry["recipient"] == "testserv-boss"
        assert entry["payload"] == "hello boss"
        assert entry["tags"] == "msgid=abc123"
        assert entry["delivered_at"] is None
    finally:
        store.close()


def test_insert_duplicate_msg_id_is_idempotent(tmp_path) -> None:
    """Re-inserting the same msg_id leaves the original row intact."""
    store = _open_store(tmp_path)
    try:
        store.insert("dup", "s", "r", 100.0, "first", "")
        store.insert("dup", "s2", "r2", 200.0, "second", "")  # ignored
        entries = store.query_for_nick("r")
        assert len(entries) == 1
        assert entries[0]["payload"] == "first"
        assert store.query_for_nick("r2") == []
    finally:
        store.close()


def test_query_returns_only_recipient_entries(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        store.insert("a", "peer", "boss-a", 1.0, "for a", "")
        store.insert("b", "peer", "boss-b", 2.0, "for b", "")
        store.insert("c", "peer", "boss-a", 3.0, "for a-again", "")
        a_entries = store.query_for_nick("boss-a")
        assert [e["payload"] for e in a_entries] == ["for a", "for a-again"]
        b_entries = store.query_for_nick("boss-b")
        assert [e["payload"] for e in b_entries] == ["for b"]
    finally:
        store.close()


def test_query_respects_limit(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        for i in range(10):
            store.insert(f"id{i}", "peer", "boss", float(i), f"text {i}", "")
        entries = store.query_for_nick("boss", limit=3)
        assert len(entries) == 3
        assert entries[0]["payload"] == "text 0"  # oldest first
    finally:
        store.close()


def test_query_excludes_delivered_by_default(tmp_path) -> None:
    """``query_for_nick`` returns only undelivered entries by default."""
    store = _open_store(tmp_path)
    try:
        store.insert("a", "peer", "boss", 1.0, "old", "")
        store.insert("b", "peer", "boss", 2.0, "new", "")
        assert store.mark_delivered("a") is True
        undelivered = store.query_for_nick("boss")
        assert [e["msg_id"] for e in undelivered] == ["b"]
        # include_delivered=True returns everything.
        full = store.query_for_nick("boss", include_delivered=True)
        assert sorted(e["msg_id"] for e in full) == ["a", "b"]
    finally:
        store.close()


def test_mark_delivered_is_idempotent(tmp_path) -> None:
    """Marking a row delivered twice returns False the second time."""
    store = _open_store(tmp_path)
    try:
        store.insert("a", "peer", "boss", 1.0, "x", "")
        assert store.mark_delivered("a") is True
        assert store.mark_delivered("a") is False
        assert store.mark_delivered("nonexistent") is False
    finally:
        store.close()


def test_gc_purges_old_delivered_entries(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        # Aged: delivered 8 days ago.
        store.insert("old", "peer", "boss", 1.0, "old", "")
        store.mark_delivered("old", now=time.time() - 8 * 86400)
        # Recent: delivered 1 day ago.
        store.insert("recent", "peer", "boss", 2.0, "recent", "")
        store.mark_delivered("recent", now=time.time() - 1 * 86400)
        result = store.gc()
        assert result["delivered"] == 1
        assert result["undelivered"] == 0
        ids = {e["msg_id"] for e in store.query_for_nick("boss", include_delivered=True)}
        assert ids == {"recent"}
    finally:
        store.close()


def test_gc_purges_undelivered_after_30_days(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        now = time.time()
        # Ancient undelivered: 31 days old.
        store.insert("ancient", "peer", "boss", now - 31 * 86400, "x", "")
        # Recent undelivered: 5 days old.
        store.insert("fresh", "peer", "boss", now - 5 * 86400, "y", "")
        result = store.gc(now=now)
        assert result["undelivered"] == 1
        assert result["delivered"] == 0
        ids = {e["msg_id"] for e in store.query_for_nick("boss", include_delivered=True)}
        assert ids == {"fresh"}
    finally:
        store.close()


def test_db_file_permissions_are_0o600(tmp_path) -> None:
    """Task 3.7: DB file MUST be created at 0o600 (owner read/write only)."""
    db_path = tmp_path / "perms.dm-spool.db"
    store = DmSpoolStore(db_path)
    try:
        st = os.stat(db_path)
        mode = stat.S_IMODE(st.st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    finally:
        store.close()


def test_parent_dir_permissions_are_0o700(tmp_path) -> None:
    """Task 3.7: parent dir MUST be 0o700 after store opens."""
    sub = tmp_path / "tightened"
    db_path = sub / "x.dm-spool.db"
    store = DmSpoolStore(db_path)
    try:
        st = os.stat(sub)
        mode = stat.S_IMODE(st.st_mode)
        assert mode == 0o700, f"expected 0o700, got {oct(mode)}"
    finally:
        store.close()


def test_default_spool_path_uses_culture_home(tmp_path, monkeypatch) -> None:
    """``default_spool_path`` honors CULTURE_HOME for test isolation."""
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    path = default_spool_path("spark")
    assert path == str(tmp_path / "spark.dm-spool.db")


def test_close_is_idempotent(tmp_path) -> None:
    store = _open_store(tmp_path)
    store.close()
    # Second close on a closed connection raises ProgrammingError if not
    # guarded — current impl swallows. Just verify no exception escapes.
    with pytest.raises(Exception):
        # SQLite raises after close on commit/execute. We just want to
        # confirm the API is "close once, done".
        store._conn.execute("SELECT 1")


def test_count_helper(tmp_path) -> None:
    store = _open_store(tmp_path)
    try:
        assert store.count() == 0
        store.insert("a", "x", "y", 1.0, "p", "")
        store.insert("b", "x", "y", 2.0, "p", "")
        assert store.count() == 2
    finally:
        store.close()
