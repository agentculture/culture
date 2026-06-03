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

    Best-effort: filesystem errors are logged and swallowed so a
    transient I/O failure doesn't tear down the IRC handler.
    """
    record = {"kind": kind, "ts": time.time(), **payload}
    path = inbox_path(nick)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        # Tighten permissions on first write (0o600 — same-user only).
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        logger.warning("Failed to append to bridge inbox %s", path, exc_info=True)
