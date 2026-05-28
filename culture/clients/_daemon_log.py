"""Per-agent control-plane action log.

Distinct from the agent-message audit log (``_audit.py``): this records what the
*daemon* does to manage the agent — start/stop/exit/crash/compact/handoff/etc.
One JSONL line per action at ``~/.culture/daemon-log/<nick>.jsonl``. Universal
across all backends.

Design spec: docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from culture.clients._perm_broker import culture_home

logger = logging.getLogger(__name__)


def _daemon_log_dir() -> str:
    return os.path.join(culture_home(), "daemon-log")


def daemon_log_path_for(nick: str) -> str:
    return os.path.join(_daemon_log_dir(), f"{nick}.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DaemonLog:
    """Append-only JSONL writer for a single agent's daemon actions."""

    def __init__(self, nick: str) -> None:
        if not nick:
            raise ValueError("DaemonLog requires a non-empty nick")
        self._nick = nick
        self._path = daemon_log_path_for(nick)
        self._lock = asyncio.Lock()

    @property
    def path(self) -> str:
        return self._path

    async def record(self, action: str, **detail: Any) -> None:
        """Append one action line. ``detail`` becomes the record's detail dict."""
        record = {
            "ts": _now_iso(),
            "nick": self._nick,
            "action": action,
            "detail": detail,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        async with self._lock:
            await asyncio.to_thread(self._append_line_sync, line)

    def _append_line_sync(self, line: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
        except OSError:
            logger.debug("Failed to ensure daemon-log dir for %s", self._nick, exc_info=True)
            return
        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        except OSError:
            logger.warning("Failed to open daemon log %s", self._path, exc_info=True)
            return
        try:
            handle = os.fdopen(fd, "a", encoding="utf-8")
        except OSError:
            os.close(fd)
            logger.warning("Failed to wrap daemon log fd %s", self._path, exc_info=True)
            return
        try:
            with handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            logger.warning("Failed to append to daemon log %s", self._path, exc_info=True)
