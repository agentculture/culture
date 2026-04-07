"""SQLite disk persistence for channel message history."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)


class HistoryStore:
    """Save and load channel message history to/from SQLite."""

    def __init__(self, data_dir: str):
        db_dir = Path(data_dir)
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_dir / "history.db"
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                nick TEXT NOT NULL,
                text TEXT NOT NULL,
                timestamp REAL NOT NULL
            )""")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_channel_ts ON history(channel, timestamp, id)"
        )
        self._conn.commit()

    def append(self, channel: str, nick: str, text: str, timestamp: float) -> None:
        """Insert a single history entry (batched — not committed per call)."""
        self._conn.execute(
            "INSERT INTO history (channel, nick, text, timestamp) VALUES (?, ?, ?, ?)",
            (channel, nick, text, timestamp),
        )

    def get_recent(self, channel: str, count: int) -> list[dict]:
        """Return the last *count* entries for a channel, in chronological order."""
        cur = self._conn.execute(
            "SELECT nick, text, timestamp FROM history "
            "WHERE channel = ? ORDER BY timestamp DESC, id DESC LIMIT ?",
            (channel, count),
        )
        rows = cur.fetchall()
        return [{"nick": r[0], "text": r[1], "timestamp": r[2]} for r in reversed(rows)]

    def search(self, channel: str, term: str) -> list[dict]:
        """Case-insensitive substring search within a channel."""
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cur = self._conn.execute(
            "SELECT nick, text, timestamp FROM history "
            "WHERE channel = ? AND text LIKE ? ESCAPE '\\' ORDER BY timestamp ASC",
            (channel, f"%{escaped}%"),
        )
        return [{"nick": r[0], "text": r[1], "timestamp": r[2]} for r in cur]

    def load_channels(self, maxlen: int) -> dict[str, deque]:
        """Load the last *maxlen* entries per channel for startup restore.

        Returns a dict mapping channel names to deques of
        ``{"nick": ..., "text": ..., "timestamp": ...}`` dicts.
        """
        cur = self._conn.execute("SELECT DISTINCT channel FROM history")
        channels: dict[str, deque] = {}
        for (channel,) in cur:
            entries = self.get_recent(channel, maxlen)
            channels[channel] = deque(entries, maxlen=maxlen)
        return channels

    def prune(self, max_age_days: int) -> int:
        """Delete entries older than *max_age_days*.  Returns rows deleted."""
        cutoff = time.time() - (max_age_days * 86400)
        cur = self._conn.execute("DELETE FROM history WHERE timestamp < ?", (cutoff,))
        self._conn.commit()
        deleted = cur.rowcount
        if deleted:
            logger.info("Pruned %d history entries older than %d days", deleted, max_age_days)
        return deleted

    def close(self) -> None:
        """Flush pending writes and close the database connection."""
        try:
            self._conn.commit()
        except sqlite3.Error:
            pass
        self._conn.close()
