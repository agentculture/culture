from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass

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

    def read_thread(self, channel: str, thread_name: str, limit: int = 50) -> list[BufferedMessage]:
        buf = self._buffers.get(channel)
        if not buf:
            return []
        matches = [m for m in buf if m.thread == thread_name]
        if len(matches) > limit:
            matches = matches[-limit:]
        return matches
