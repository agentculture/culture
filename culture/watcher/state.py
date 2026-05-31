"""Watcher cooldown + last-seen state, persisted to ``~/.culture/watcher-state.json``.

Each pattern firing carries a deterministic key (``pattern_name:target``).
The watcher records the firing's epoch timestamp; the same key won't
re-fire within its cooldown window. Survives process restarts so a
crash-loop alert won't spam every minute as the watcher reboots.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)


class WatcherState:
    """Persistent dedupe + last-seen cache for the watcher service.

    The state file is one JSON object: ``{"firings": {key: epoch}}``.
    Failures to read / write the file are logged but never fatal —
    a missing or corrupt file degrades to "first run" semantics
    (every pattern can fire once) rather than crashing the watcher.
    """

    def __init__(self, path: str):
        self.path = path
        self.firings: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                payload = json.load(fh)
            firings = payload.get("firings", {}) if isinstance(payload, dict) else {}
            self.firings = {k: float(v) for k, v in firings.items() if isinstance(v, (int, float))}
        except (OSError, ValueError) as exc:
            logger.debug("watcher state load: %s — treating as empty", exc)
            self.firings = {}

    def save(self) -> None:
        tmp = f"{self.path}.tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"firings": self.firings}, fh)
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("watcher state save failed: %s", exc)

    def last_fired(self, key: str) -> Optional[float]:
        v = self.firings.get(key)
        return float(v) if v is not None else None

    def in_cooldown(self, key: str, cooldown_seconds: float, *, now: float | None = None) -> bool:
        last = self.last_fired(key)
        if last is None:
            return False
        ref = time.time() if now is None else now
        return (ref - last) < cooldown_seconds

    def record_firing(self, key: str, *, now: float | None = None) -> None:
        self.firings[key] = time.time() if now is None else now

    def gc(self, keep_seconds: float = 7 * 24 * 3600, *, now: float | None = None) -> int:
        """Drop firings older than ``keep_seconds``. Returns count removed."""
        ref = time.time() if now is None else now
        cutoff = ref - keep_seconds
        to_drop = [k for k, ts in self.firings.items() if ts < cutoff]
        for k in to_drop:
            del self.firings[k]
        return len(to_drop)
