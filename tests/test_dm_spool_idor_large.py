"""Regression: ``CHATHISTORY DELETE`` must scale past the page cap.

The original ``_handle_delete`` implementation page-scanned the
requesting nick's own spool via ``query_for_nick(..., limit=100)``
to enforce the IDOR guard. If the boss's spool grew beyond 100
entries the bridge's mark-delivered ack for older msg_ids would
return a spurious ``ERR_NOPRIVILEGES`` and the spool would leak
indefinitely.

The fix is ``DmSpoolStore.get_by_msg_id(nick, msg_id)`` — a
targeted O(1) lookup. The WHERE clause pins ``recipient = nick``
so it remains IDOR-safe.
"""

from __future__ import annotations

from culture.agentirc.dm_spool_store import DmSpoolStore


def test_get_by_msg_id_finds_entry_beyond_page_cap(tmp_path) -> None:
    """Insert 150 entries; the 120th must be findable via get_by_msg_id."""
    store = DmSpoolStore(tmp_path / "large.dm-spool.db")
    try:
        target_msg_id: str | None = None
        for i in range(150):
            mid = f"msg{i:04d}"
            store.insert(
                msg_id=mid,
                sender="testserv-peer",
                recipient="testserv-boss",
                ts=float(i),
                payload=f"text {i}",
                tags=f"msgid={mid}",
            )
            if i == 119:  # the 120th entry (0-indexed 119)
                target_msg_id = mid

        assert target_msg_id is not None
        # Targeted lookup must find this entry even though it's well
        # beyond position 100 in ts_server-ASC order.
        assert store.get_by_msg_id("testserv-boss", target_msg_id) is True

        # Confirm mark_delivered then succeeds (no ERR_NOPRIVILEGES would
        # have been returned by the skill, because get_by_msg_id is True).
        assert store.mark_delivered(target_msg_id) is True
    finally:
        store.close()


def test_get_by_msg_id_rejects_cross_nick_lookup(tmp_path) -> None:
    """IDOR guard: cannot find a msg_id whose recipient is another nick."""
    store = DmSpoolStore(tmp_path / "idor.dm-spool.db")
    try:
        store.insert(
            msg_id="bossmsg",
            sender="testserv-peer",
            recipient="testserv-boss",
            ts=1.0,
            payload="for boss",
            tags="",
        )
        # Boss can find it.
        assert store.get_by_msg_id("testserv-boss", "bossmsg") is True
        # Eve cannot.
        assert store.get_by_msg_id("testserv-eve", "bossmsg") is False
    finally:
        store.close()


def test_get_by_msg_id_unknown_id_returns_false(tmp_path) -> None:
    store = DmSpoolStore(tmp_path / "unknown.dm-spool.db")
    try:
        assert store.get_by_msg_id("testserv-boss", "does-not-exist") is False
    finally:
        store.close()
