"""SQLite-backed per-nick DM spool for offline message delivery.

Phase 3 of the 2026-06-03 mesh-rearchitecture: when a peer DMs a boss
nick whose bridge is offline (CC closed), the IRCd writes the message
to this spool instead of returning ``ERR_NOSUCHNICK``. When the bridge
reconnects it issues an IRCv3 ``CHATHISTORY`` request to drain.

Mirrors the SQLite-connection / WAL-journal / idempotent-DDL pattern
used by ``culture/agentirc/history_store.py`` so the two stores feel
the same to anyone maintaining them.

Schema:

    CREATE TABLE IF NOT EXISTS dm_spool (
        msg_id      TEXT PRIMARY KEY,
        sender      TEXT NOT NULL,
        recipient   TEXT NOT NULL,
        ts_server   REAL NOT NULL,
        payload     TEXT NOT NULL,
        tags        TEXT NOT NULL,
        delivered_at REAL
    );

Retention (``gc()``):
- Entries with ``delivered_at IS NOT NULL`` are purged 7 days after
  delivery — they've been seen by CC; keeping them past a week serves
  only forensics, which the audit log already covers.
- Entries with ``delivered_at IS NULL`` are purged 30 days after
  ``ts_server`` — undelivered DMs older than a month are presumed
  abandoned (the boss never reconnected). An audit-log entry records
  the purge.

Filesystem hardening: the DB file is created at 0o600 and the parent
directory at 0o700 — same-user-only access (T2 threat in plan §Security
Trade-offs).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Retention thresholds in seconds (named for readability).
_DELIVERED_TTL_SECONDS = 7 * 86400  # purge delivered entries after 7 days
_UNDELIVERED_TTL_SECONDS = 30 * 86400  # drop undelivered entries after 30 days


def default_spool_path(server_name: str, culture_home_dir: str | None = None) -> str:
    """Return the canonical spool DB path: ``<culture_home>/<server>.dm-spool.db``.

    When *culture_home_dir* is None, resolve via
    ``culture.clients._perm_broker.culture_home()`` (honors
    ``CULTURE_HOME`` for test isolation; falls back to ``~/.culture``).
    """
    if culture_home_dir is None:
        from culture.clients._perm_broker import culture_home

        culture_home_dir = culture_home()
    return os.path.join(culture_home_dir, f"{server_name}.dm-spool.db")


class DmSpoolStore:
    """Per-server SQLite spool of DMs targeted at currently-offline nicks.

    Designed as a long-lived singleton attached to the IRCd; mid-run
    inserts share one connection. ``check_same_thread=False`` matches
    ``HistoryStore`` — the IRCd's asyncio executor only calls into this
    from the loop thread, but the flag keeps test fixtures (which may
    open + interrogate from a sync helper) honest.

    Concurrency (Qodo PR #50 round-3 #1): the async wrappers below
    dispatch DB statements via ``asyncio.to_thread``. Multiple awaits
    can run in parallel (an insert from a DM spool path + a drain from
    a CHATHISTORY handler + the hourly GC tick), each on a different
    worker thread that touches the SAME ``sqlite3.Connection``. SQLite
    itself is serialised internally, but Python's ``sqlite3`` does
    NOT guarantee a single ``Connection`` is safe across simultaneous
    cross-thread ``execute()`` calls — corruption / cursor-mix
    behaviour is documented. ``_lock`` is a re-entrant lock that
    every read/write method takes BEFORE touching ``_conn``. RLock
    (not plain Lock) because some methods compose other methods (none
    do today, but the RLock is a cheap forward-compatibility hedge).
    """

    def __init__(self, db_path: str | os.PathLike[str]):
        self._db_path = Path(db_path)
        db_dir = self._db_path.parent
        # Parent directory must be same-user-only. ``exist_ok=True`` is
        # idempotent; mode=0o700 only applies on first creation, so we
        # also chmod afterward to handle the upgrade case.
        db_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(db_dir, 0o700)
        except OSError:
            logger.warning("Failed to chmod 0o700 on %s", db_dir, exc_info=True)

        # See class docstring on concurrency. Initialised BEFORE the
        # connection so a failure to open does not leave the lock
        # attribute missing on a partially-constructed instance.
        self._lock = threading.RLock()

        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS dm_spool (
                msg_id TEXT PRIMARY KEY,
                sender TEXT NOT NULL,
                recipient TEXT NOT NULL,
                ts_server REAL NOT NULL,
                payload TEXT NOT NULL,
                tags TEXT NOT NULL,
                delivered_at REAL
            )""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dm_spool_recipient_ts "
            "ON dm_spool(recipient, ts_server)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dm_spool_gc " "ON dm_spool(delivered_at, ts_server)"
        )
        self._conn.commit()

        # Tighten DB file permissions to same-user-only after creation.
        try:
            os.chmod(self._db_path, 0o600)
        except OSError:
            logger.warning("Failed to chmod 0o600 on %s", self._db_path, exc_info=True)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def insert(
        self,
        msg_id: str,
        sender: str,
        recipient: str,
        ts: float,
        payload: str,
        tags: str,
    ) -> None:
        """Insert a spool entry. ``msg_id`` is the unique identifier the
        bridge will echo back in ``mark_delivered`` after CC acks.

        Idempotent under duplicate ``msg_id`` (PK conflict is silently
        ignored — the original entry stands). The IRCd generates fresh
        UUIDs per send so the only realistic collision is a bridge
        replay of the same message, which is exactly the case we want
        to be a no-op.
        """
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO dm_spool "
                    "(msg_id, sender, recipient, ts_server, payload, tags, delivered_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (msg_id, sender, recipient, ts, payload, tags),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                # Duplicate msg_id — original entry stands. No log: this is
                # an expected idempotency case.
                pass

    def mark_delivered(self, msg_id: str, now: float | None = None) -> bool:
        """Mark *msg_id* as delivered (CC acked the inbound_dm push).

        Returns True when a row was updated, False when *msg_id* was
        unknown or already delivered. Idempotent — callers re-acking a
        previously-acked message see False, not an error.
        """
        ts = now if now is not None else time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE dm_spool SET delivered_at = ? " "WHERE msg_id = ? AND delivered_at IS NULL",
                (ts, msg_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def query_for_nick(
        self,
        nick: str,
        limit: int = 100,
        include_delivered: bool = False,
    ) -> list[dict]:
        """Return spool entries addressed to *nick*, oldest first.

        By default returns only entries that have not yet been delivered
        (``delivered_at IS NULL``) — the bridge's CHATHISTORY drain only
        wants the unread tail. ``include_delivered=True`` returns
        everything (used by forensics / tests).

        Capped at *limit* entries (matches the IRCv3 chathistory
        ``CHATHISTORY=<max>`` ISUPPORT contract).
        """
        if limit <= 0:
            return []
        with self._lock:
            if include_delivered:
                cur = self._conn.execute(
                    "SELECT msg_id, sender, recipient, ts_server, payload, tags, delivered_at "
                    "FROM dm_spool WHERE recipient = ? "
                    "ORDER BY ts_server ASC, msg_id ASC LIMIT ?",
                    (nick, limit),
                )
            else:
                cur = self._conn.execute(
                    "SELECT msg_id, sender, recipient, ts_server, payload, tags, delivered_at "
                    "FROM dm_spool WHERE recipient = ? AND delivered_at IS NULL "
                    "ORDER BY ts_server ASC, msg_id ASC LIMIT ?",
                    (nick, limit),
                )
            rows = cur.fetchall()
        return [
            {
                "msg_id": row["msg_id"],
                "sender": row["sender"],
                "recipient": row["recipient"],
                "ts_server": row["ts_server"],
                "payload": row["payload"],
                "tags": row["tags"],
                "delivered_at": row["delivered_at"],
            }
            for row in rows
        ]

    def count(self) -> int:
        """Return the total number of rows in the spool. Mostly for tests."""
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM dm_spool")
            (n,) = cur.fetchone()
        return int(n)

    def get_by_msg_id(self, nick: str, msg_id: str) -> bool:
        """Return True iff a spool row exists whose ``msg_id`` matches AND
        whose ``recipient`` equals *nick*.

        Targeted O(1) lookup used by ``CHATHISTORY DELETE`` to gate the
        mark-delivered ack without paging through the recipient's whole
        spool. IDOR-safe by construction: the WHERE clause pins the
        recipient to the requesting nick, so a peer cannot mark another
        boss's row delivered nor probe for the existence of an unknown
        msg_id outside its own spool.

        The previous page-scan capped at CHATHISTORY_LIMIT_MAX (100)
        meant valid msg_ids beyond position 100 (oldest-first ts_server
        order) returned a spurious ERR_NOPRIVILEGES, leaking the spool
        indefinitely once it grew past the cap.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM dm_spool WHERE msg_id = ? AND recipient = ? LIMIT 1",
                (msg_id, nick),
            )
            return cur.fetchone() is not None

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def gc(self, now: float | None = None) -> dict[str, int]:
        """Apply the retention policy. Returns ``{"delivered": N1, "undelivered": N2}``.

        - Delivered entries with ``now - delivered_at > 7d`` are deleted.
        - Undelivered entries with ``now - ts_server > 30d`` are deleted
          and the count is logged (so the audit pillar shows abandoned
          DMs being dropped).

        Called from the IRCd's hourly maintenance task; safe to call
        from tests with an arbitrary *now*.
        """
        ts_now = now if now is not None else time.time()
        delivered_cutoff = ts_now - _DELIVERED_TTL_SECONDS
        undelivered_cutoff = ts_now - _UNDELIVERED_TTL_SECONDS

        with self._lock:
            cur_d = self._conn.execute(
                "DELETE FROM dm_spool " "WHERE delivered_at IS NOT NULL AND delivered_at < ?",
                (delivered_cutoff,),
            )
            delivered_purged = cur_d.rowcount or 0

            cur_u = self._conn.execute(
                "DELETE FROM dm_spool " "WHERE delivered_at IS NULL AND ts_server < ?",
                (undelivered_cutoff,),
            )
            undelivered_purged = cur_u.rowcount or 0
            self._conn.commit()

        if undelivered_purged:
            # Audit-log the purge of undelivered entries. The IRCd's
            # audit pillar consumes structured records; we log at
            # warning level so the dashboard surfaces the drop.
            logger.warning(
                "dm_spool gc: purged %d undelivered entries older than %d days",
                undelivered_purged,
                _UNDELIVERED_TTL_SECONDS // 86400,
            )
        if delivered_purged:
            logger.info(
                "dm_spool gc: purged %d delivered entries older than %d days",
                delivered_purged,
                _DELIVERED_TTL_SECONDS // 86400,
            )
        return {"delivered": delivered_purged, "undelivered": undelivered_purged}

    # ------------------------------------------------------------------
    # Async wrappers — Qodo PR #50 #6.
    # ------------------------------------------------------------------
    #
    # Every method above runs synchronous ``sqlite3`` I/O (open file,
    # execute, fsync on commit). Calling them directly from an
    # ``async`` handler stalls the asyncio event loop while the SQLite
    # call returns — and on a slow disk, a WAL checkpoint, or a
    # ``database is locked`` retry that can be 10s of ms per call. With
    # many connected clients that blocks every IRC connection.
    #
    # The wrappers below dispatch each DB call to the default thread
    # pool via ``asyncio.to_thread``. The sync methods remain for
    # tests, scripts, and the few callers that genuinely run on a
    # dedicated thread already.
    #
    # The connection itself was opened with
    # ``check_same_thread=False`` (see ``__init__``) so cross-thread
    # use is sanctioned by sqlite3 — but the prior comment that claimed
    # ``to_thread`` does not run multiple of these concurrently was
    # WRONG (Qodo PR #50 round-3 #1). Two awaits hitting the executor
    # simultaneously will run statements on the same connection from
    # two threads, which Python's sqlite3 does not guarantee is safe.
    # The remediation is ``self._lock`` (a ``threading.RLock``) held
    # by every sync method — including ``insert`` / ``query_for_nick``
    # / ``mark_delivered`` / ``get_by_msg_id`` / ``gc`` / ``close`` /
    # ``count``. The async wrappers inherit that protection for free
    # because they simply ``to_thread(sync_method)``. RLock (re-entrant)
    # is the cheap forward-compat hedge for any future composite method.

    async def ainsert(
        self,
        msg_id: str,
        sender: str,
        recipient: str,
        ts: float,
        payload: str,
        tags: str,
    ) -> None:
        """Async wrapper around :meth:`insert`. See class-level note."""
        import asyncio as _asyncio

        await _asyncio.to_thread(self.insert, msg_id, sender, recipient, ts, payload, tags)

    async def amark_delivered(self, msg_id: str, now: float | None = None) -> bool:
        """Async wrapper around :meth:`mark_delivered`."""
        import asyncio as _asyncio

        return await _asyncio.to_thread(self.mark_delivered, msg_id, now)

    async def aquery_for_nick(
        self,
        nick: str,
        limit: int = 100,
        include_delivered: bool = False,
    ) -> list[dict]:
        """Async wrapper around :meth:`query_for_nick`."""
        import asyncio as _asyncio

        return await _asyncio.to_thread(self.query_for_nick, nick, limit, include_delivered)

    async def aget_by_msg_id(self, nick: str, msg_id: str) -> bool:
        """Async wrapper around :meth:`get_by_msg_id`."""
        import asyncio as _asyncio

        return await _asyncio.to_thread(self.get_by_msg_id, nick, msg_id)

    async def agc(self, now: float | None = None) -> dict[str, int]:
        """Async wrapper around :meth:`gc`."""
        import asyncio as _asyncio

        return await _asyncio.to_thread(self.gc, now)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
            self._conn.close()
