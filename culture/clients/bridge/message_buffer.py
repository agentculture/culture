from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_THREAD_PREFIX_RE = re.compile(r"^\[thread:([a-zA-Z0-9\-]+)\] ")


@dataclass
class BufferedMessage:
    nick: str
    text: str
    timestamp: float
    thread: str | None = None


class MessageBuffer:
    def __init__(self, max_per_channel: int = 500):
        self.max_per_channel = max_per_channel
        self._buffers: dict[str, deque[BufferedMessage]] = {}
        self._cursors: dict[str, int] = {}
        self._totals: dict[str, int] = {}

    def add(self, channel: str, nick: str, text: str) -> None:
        if channel not in self._buffers:
            self._buffers[channel] = deque(maxlen=self.max_per_channel)
            self._totals[channel] = 0
            self._cursors[channel] = 0
        thread = None
        m = _THREAD_PREFIX_RE.match(text)
        if m:
            thread = m.group(1)
        self._buffers[channel].append(
            BufferedMessage(nick=nick, text=text, timestamp=time.time(), thread=thread)
        )
        self._totals[channel] += 1

    def read(self, channel: str, limit: int = 50) -> list[BufferedMessage]:
        buf = self._buffers.get(channel)
        if not buf:
            return []
        total = self._totals[channel]
        cursor = self._cursors.get(channel, 0)
        new_count = total - cursor
        if new_count <= 0:
            return []
        available = list(buf)
        new_messages = available[-new_count:] if new_count <= len(available) else available
        if len(new_messages) > limit:
            new_messages = new_messages[-limit:]
        self._cursors[channel] = total
        return new_messages

    def known_nicks(self) -> set[str]:
        """Return the set of nicks seen across all buffers."""
        nicks: set[str] = set()
        for buf in self._buffers.values():
            for m in buf:
                nicks.add(m.nick)
        return nicks

    def read_thread(self, channel: str, thread_name: str, limit: int = 50) -> list[BufferedMessage]:
        buf = self._buffers.get(channel)
        if not buf:
            return []
        matches = [m for m in buf if m.thread == thread_name]
        if len(matches) > limit:
            matches = matches[-limit:]
        return matches

    # ------------------------------------------------------------------
    # Cursor persistence (Phase 2.7 of the rearchitecture plan)
    # ------------------------------------------------------------------
    #
    # Buffer contents themselves are NOT persisted — on bridge restart the
    # buffer rebuilds via ``HISTORY RECENT`` IRC replay (see
    # ``IRCTransport.join_channel``). Only the per-channel cursor is
    # serialized, so the next ``read()`` after a restart doesn't
    # re-deliver the HISTORY-replayed messages as "unread" (EL-5 lesson).
    #
    # Format: ``{"cursors": {"#chan": int, "DM:nick": int, ...},
    # "schema": 1}``. Atomic write via tempfile + ``os.replace`` (POSIX
    # atomic). Reads tolerate missing file (returns silently) and
    # malformed JSON (warn + skip; cursors start at 0).

    def save(self, path: str) -> None:
        """Atomically serialize the cursor dict to *path* as JSON.

        Only cursors are persisted. Buffer contents and per-channel
        totals are NOT (totals are derived at runtime from the buffer +
        in-memory cursors; on restart we rebuild from IRC HISTORY).
        """
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        payload = {"schema": 1, "cursors": dict(self._cursors)}
        # tempfile in the same directory so os.replace is atomic on POSIX.
        dirname = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".cursors-", suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp_path, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load(self, path: str) -> None:
        """Restore cursors from a previously ``save()``-d JSON file.

        Missing file is silent (first-run case). Malformed JSON logs a
        warning and starts cursors at 0 — better to over-deliver one
        backlog than to crash on a corrupt persistence file.

        Existing in-memory cursors are merged with persisted ones —
        persisted-side wins on conflict (the on-disk state is the
        source of truth for "what the bridge already delivered to CC").
        """
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load MessageBuffer cursors from %s: %s", path, exc)
            return
        cursors = payload.get("cursors", {}) if isinstance(payload, dict) else {}
        if not isinstance(cursors, dict):
            logger.warning("MessageBuffer cursors file %s has unexpected shape; ignoring", path)
            return
        for channel, cursor in cursors.items():
            if not isinstance(channel, str) or not isinstance(cursor, int):
                continue
            self._cursors[channel] = cursor
            # If the buffer hasn't seen the channel yet, also seed totals
            # so a subsequent ``read()`` before any new ``add()`` returns
            # nothing (the cursor refers to messages we haven't replayed
            # back into the buffer yet).
            if channel not in self._totals:
                self._totals[channel] = cursor
