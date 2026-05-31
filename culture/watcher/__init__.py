"""Deterministic mesh-state watcher (v8.19.19).

A background process that polls the mesh's authoritative state files
(``~/.culture/daemon-log/*.jsonl``, ``~/.culture/audit/*.jsonl``,
``~/.culture/perm-queue/``) and fires deterministic alerts when known
failure patterns trigger — silent worker death, crash bursts, runaway
token spikes, permission escalations above the boss's grant ceiling,
mission-stuck bosses. Alerts route over IRC (always), and over
email or webhook when opted-in via ``~/.culture/watcher.yaml``.

The closed-loop guard. The mesh is not always reliable; the orchestrator
should not have to babysit the dashboard to notice problems. The watcher
runs out-of-process, never holds a token quota, and survives every other
component restart.
"""

from culture.watcher.alerts import AlertRouter
from culture.watcher.patterns import (
    PATTERNS,
    Alert,
    PatternEvent,
    detect_patterns,
)
from culture.watcher.service import WatcherConfig, WatcherService, load_config
from culture.watcher.state import WatcherState

__all__ = [
    "Alert",
    "AlertRouter",
    "PATTERNS",
    "PatternEvent",
    "WatcherConfig",
    "WatcherService",
    "WatcherState",
    "detect_patterns",
    "load_config",
]
