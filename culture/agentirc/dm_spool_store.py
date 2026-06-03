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
            for row in cur.fetchall()
        ]

    def count(self) -> int:
        """Return the total number of rows in the spool. Mostly for tests."""
        cur = self._conn.execute("SELECT COUNT(*) FROM dm_spool")
        (n,) = cur.fetchone()
        return int(n)

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
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.commit()
        except sqlite3.Error:
            pass
        self._conn.close()
