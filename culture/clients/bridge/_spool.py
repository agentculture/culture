"""Bridge spool — interim placeholder for Phase 3's real DM spool.

The bridge daemon receives inbound IRC events (mentions, DMs, ROOMINVITEs)
from ``IRCTransport`` callbacks. The boss-side runtime (CC, the user-facing
Claude Code session) consumes them via IPC push. Until Phase 3 lands the
server-side ``draft/chathistory`` per-nick spool, this module persists the
payloads to ``~/.culture/bridge/inbox-<nick>.jsonl`` as a flat append-only
log — so a CC session that wasn't connected when the event arrived can
still drain the backlog on its next ``cc_session_start``.

Schema (one JSON object per line)::

    {
        "kind": "inbound_mention" | "inbound_dm" | "inbound_roominvite",
        "target": "#chan-or-nick",   # absent for roominvite
        "sender": "peer-nick",
        "text": "...",
        "ts": 1717420000.123,
        "meta": {...},               # roominvite-only
    }

This file is intentionally minimal — Phase 3 replaces it with a proper
SQLite store + IRCv3 chathistory drain protocol. Until then, the bridge
treats this as a write-only spool; reading back is the CC session's job.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


def _spool_dir() -> str:
    """Resolve the bridge spool directory under ``CULTURE_HOME``.

    Falls back to ``~/.culture/bridge`` when no override is set. Created
    on demand; permissions tightened to 0o700.
    """
    from culture.clients._perm_broker import culture_home

    path = os.path.join(culture_home(), "bridge")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError:
        logger.warning("Failed to create bridge spool dir %s", path, exc_info=True)
    return path


def inbox_path(nick: str) -> str:
    """Path to the inbox JSONL file for a given bridge nick."""
    safe = nick.replace("/", "_").replace("..", "_")
    return os.path.join(_spool_dir(), f"inbox-{safe}.jsonl")


def spool_inbound(nick: str, kind: str, **payload: Any) -> None:
    """Append a structured inbound-event record to the bridge inbox.

    ``kind`` is one of ``inbound_mention``, ``inbound_dm``,
    ``inbound_roominvite``. ``payload`` carries event-specific fields
    (``target``, ``sender``, ``text``, ``meta`` — see module docstring).

    Takes an exclusive POSIX advisory lock (``fcntl.flock``) for the
    duration of the write so a concurrent ``_ipc_inbox_drain`` (which
    runs on the IPC handler thread, while spool writes happen on the
    asyncio loop thread) cannot interleave with the append. The drain
    side takes the same lock around its read+truncate sequence — see
    ``drain_inbox`` below.

    Best-effort: filesystem errors are logged and swallowed so a
    transient I/O failure doesn't tear down the IRC handler.
    """
    record = {"kind": kind, "ts": time.time(), **payload}
    path = inbox_path(nick)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        # Tighten permissions on first write (0o600 — same-user only).
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        logger.warning("Failed to append to bridge inbox %s", path, exc_info=True)


def drain_inbox(nick: str) -> list[dict]:
    """Read every spooled entry for ``nick`` and atomically truncate.

    Holds an exclusive ``fcntl.flock`` for the entire read+truncate
    sequence — any concurrent :func:`spool_inbound` blocks at its own
    ``LOCK_EX`` until the drain releases. This closes the read-then-
    unlink window where an appended-during-read entry was lost (Qodo
    PR #50 finding #1).

    Returns the list of decoded records, oldest first. Records whose
    JSON fails to decode are skipped silently — a partial append from
    a crashed writer should not block draining the rest.

    Returns ``[]`` (no error) when the spool file does not yet exist.
    """
    path = inbox_path(nick)
    entries: list[dict] = []
    try:
        # ``r+`` so the same fd reads AND truncates under one lock.
        # Opening for write would clobber the file before we read it.
        with open(path, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                # Truncate-in-place under the lock so any writer
                # blocked at LOCK_EX appends to an empty file (and its
                # event becomes the next drain's first entry).
                fh.seek(0)
                fh.truncate()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError:
        return []
    return entries
