"""Deterministic failure-pattern detectors for the watcher (v8.19.19).

Each pattern is a pure function: it reads the agent's daemon-log and
audit files (already loaded into memory) and returns zero or more
``PatternEvent`` records. The watcher service is responsible for
loading the files and for routing events to the alert sinks; this
module only knows how to match the patterns.

The MVP patterns (per handoff):

* ``silent_death`` — daemon-log shows ``agent_start`` but no
  ``agent_exit`` AND the pidfile points at a dead PID. Severity HIGH.
* ``crash_burst`` — ≥3 ``crash`` records in the last 5 minutes for
  the same nick. Severity HIGH.
* ``token_spike`` — assistant turns in the last 10 minutes summed
  exceed 50 000 input tokens for the same nick. Severity MEDIUM.
* ``perm_escalation_above_ceiling`` — a permission request landed
  where the worker's ``tool_name`` is in the boss's ceiling denylist.
  Severity HIGH.
* ``mission_stuck`` — a boss has been running ≥2 hours with no
  ``assistant`` audit record AND no ``engaged`` daemon-log record.
  Severity MEDIUM.

Patterns return a stable ``key`` (``pattern:target``) the watcher
service uses for cooldown dedupe.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


@dataclass
class PatternEvent:
    """A single firing of a pattern.

    ``key`` must be deterministic — same firing causes same key — so
    the watcher's cooldown can dedupe repeated reports of the same
    problem within a window.
    """

    pattern: str
    severity: str  # "high" | "medium" | "low"
    target: str  # nick or channel; used for alert addressing
    summary: str  # short human-readable headline
    detail: str = ""
    ts: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.pattern}:{self.target}"


@dataclass
class Alert:
    """An event ready for routing — pattern event + recipients."""

    event: PatternEvent
    recipients: dict[str, list[str]] = field(default_factory=dict)


def _read_jsonl_tail(path: str, max_lines: int = 2048) -> list[dict]:
    """Read up to ``max_lines`` JSON records from the END of a JSONL file."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            chunk = min(size, max_lines * 512)  # ~512 bytes per line worst case
            fh.seek(size - chunk)
            raw = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    records: list[dict] = []
    for ln in lines[-max_lines:]:
        try:
            records.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return records


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


# --- Individual pattern detectors ------------------------------------------


def detect_silent_death(
    nick: str,
    daemon_log: list[dict],
    audit: list[dict],
    *,
    pidfile_dir: str,
) -> list[PatternEvent]:
    """High-severity if the daemon recorded agent_start but no agent_exit
    AND the pidfile-backed PID is gone.

    Mirrors the existing silent-death watchdog (v8.19.8) but as a
    cross-process check executed by the watcher daemon, so even a
    boss daemon crash can't hide a worker death.
    """
    if not daemon_log:
        return []
    last_start = None
    last_exit = None
    for rec in daemon_log:
        action = rec.get("action", "")
        if action in ("agent_start",):
            last_start = rec
        elif action in ("agent_exit", "agent_stop"):
            last_exit = rec
    if last_start is None:
        return []
    if last_exit is not None and last_exit.get("ts", "") >= last_start.get("ts", ""):
        return []
    # Look for a still-alive PID in the pidfile.
    pidfile = os.path.join(pidfile_dir, f"agent-{nick}.pid")
    try:
        with open(pidfile, encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        # No pidfile and no exit record → silent death.
        return [
            PatternEvent(
                pattern="silent_death",
                severity="high",
                target=nick,
                summary=f"{nick} appears dead (no agent_exit, no pidfile)",
                detail=f"last start ts={last_start.get('ts','?')}",
            )
        ]
    if not _pid_is_alive(pid):
        return [
            PatternEvent(
                pattern="silent_death",
                severity="high",
                target=nick,
                summary=f"{nick} died (PID {pid} gone, no agent_exit recorded)",
                detail=f"last start ts={last_start.get('ts','?')}",
            )
        ]
    return []


def detect_crash_burst(
    nick: str,
    daemon_log: list[dict],
    audit: list[dict],
    *,
    window_seconds: float = 300.0,
    min_count: int = 3,
    now: float | None = None,
) -> list[PatternEvent]:
    """High-severity if ≥``min_count`` ``crash`` records inside ``window_seconds``."""
    if not daemon_log:
        return []
    ref = time.time() if now is None else now
    cutoff_iso = _epoch_to_iso(ref - window_seconds)
    crashes = [
        r for r in daemon_log if r.get("action") == "crash" and r.get("ts", "") >= cutoff_iso
    ]
    if len(crashes) < min_count:
        return []
    return [
        PatternEvent(
            pattern="crash_burst",
            severity="high",
            target=nick,
            summary=f"{nick} crashed {len(crashes)} times in the last {int(window_seconds)//60} min",
            detail=f"last crash detail: {crashes[-1].get('detail', {})}",
        )
    ]


def detect_token_spike(
    nick: str,
    daemon_log: list[dict],
    audit: list[dict],
    *,
    window_seconds: float = 600.0,
    input_threshold: int = 50_000,
    now: float | None = None,
) -> list[PatternEvent]:
    """Medium-severity if assistant input-token sum > ``input_threshold`` in window."""
    if not audit:
        return []
    ref = time.time() if now is None else now
    cutoff_iso = _epoch_to_iso(ref - window_seconds)
    total = 0
    for rec in audit:
        if rec.get("type") != "assistant":
            continue
        if rec.get("ts", "") < cutoff_iso:
            continue
        usage = rec.get("usage") or {}
        # Accept any of the common token-count keys backends emit.
        for k in ("input_tokens", "input", "in_tokens"):
            v = usage.get(k)
            if isinstance(v, (int, float)):
                total += int(v)
                break
    if total <= input_threshold:
        return []
    return [
        PatternEvent(
            pattern="token_spike",
            severity="medium",
            target=nick,
            summary=f"{nick} burned {total:,} input tokens in {int(window_seconds)//60} min",
            detail=f"threshold {input_threshold:,}; window {int(window_seconds)}s",
        )
    ]


def detect_perm_escalation(
    nick: str,
    *,
    pending_requests: list[dict],
    boss_ceiling: dict[str, list[str]],
) -> list[PatternEvent]:
    """High-severity if a worker queued a perm request whose tool is on the
    boss's ceiling denylist.

    ``pending_requests`` comes from ``perm-queue/`` listings.
    ``boss_ceiling[boss_nick]`` is a list of tool names denied for
    boss-level approval (must escalate to the human).
    """
    events: list[PatternEvent] = []
    for req in pending_requests:
        helper = req.get("helper_nick", "")
        if helper != nick:
            continue
        boss = req.get("boss_nick", "")
        tool = req.get("tool_name", "")
        denied = boss_ceiling.get(boss, [])
        if tool and tool in denied:
            events.append(
                PatternEvent(
                    pattern="perm_escalation_above_ceiling",
                    severity="high",
                    target=nick,
                    summary=f"{nick} requested {tool!r} — above {boss}'s grant ceiling",
                    detail=f"request_id={req.get('id','?')}",
                )
            )
    return events


def detect_mission_stuck(
    nick: str,
    daemon_log: list[dict],
    audit: list[dict],
    *,
    stale_seconds: float = 2 * 3600.0,
    now: float | None = None,
) -> list[PatternEvent]:
    """Medium-severity if a boss has no recent assistant audit AND no
    recent ``engaged`` daemon-log AND has been running ≥``stale_seconds``."""
    if not daemon_log:
        return []
    ref = time.time() if now is None else now
    cutoff_iso = _epoch_to_iso(ref - stale_seconds)
    # Must have an agent_start that's older than the staleness window
    # AND no agent_exit since.
    last_start = None
    last_exit = None
    for rec in daemon_log:
        action = rec.get("action", "")
        if action == "agent_start":
            last_start = rec
        elif action in ("agent_exit", "agent_stop"):
            last_exit = rec
    if last_start is None:
        return []
    if last_exit is not None and last_exit.get("ts", "") >= last_start.get("ts", ""):
        return []
    if last_start.get("ts", "") > cutoff_iso:
        return []  # Not running long enough yet.
    recent_engaged = any(
        r.get("action") == "engaged" and r.get("ts", "") >= cutoff_iso for r in daemon_log
    )
    recent_assistant = any(
        r.get("type") == "assistant" and r.get("ts", "") >= cutoff_iso for r in audit
    )
    if recent_engaged or recent_assistant:
        return []
    return [
        PatternEvent(
            pattern="mission_stuck",
            severity="medium",
            target=nick,
            summary=f"{nick} has been running ≥{int(stale_seconds)//3600}h with no activity",
            detail=f"started {last_start.get('ts','?')}",
        )
    ]


# --- Public detector registry ----------------------------------------------


PATTERNS: dict[str, Callable[..., list[PatternEvent]]] = {
    "silent_death": detect_silent_death,
    "crash_burst": detect_crash_burst,
    "token_spike": detect_token_spike,
    "perm_escalation_above_ceiling": detect_perm_escalation,
    "mission_stuck": detect_mission_stuck,
}


def detect_patterns(
    enabled: Iterable[str],
    *,
    nick: str,
    daemon_log: list[dict],
    audit: list[dict],
    pidfile_dir: str = "",
    pending_requests: list[dict] | None = None,
    boss_ceiling: dict[str, list[str]] | None = None,
    now: float | None = None,
) -> list[PatternEvent]:
    """Run every enabled detector against the loaded data and return events."""
    pending_requests = pending_requests or []
    boss_ceiling = boss_ceiling or {}
    results: list[PatternEvent] = []
    if "silent_death" in enabled and pidfile_dir:
        results.extend(detect_silent_death(nick, daemon_log, audit, pidfile_dir=pidfile_dir))
    if "crash_burst" in enabled:
        results.extend(detect_crash_burst(nick, daemon_log, audit, now=now))
    if "token_spike" in enabled:
        results.extend(detect_token_spike(nick, daemon_log, audit, now=now))
    if "perm_escalation_above_ceiling" in enabled:
        results.extend(
            detect_perm_escalation(
                nick,
                pending_requests=pending_requests,
                boss_ceiling=boss_ceiling,
            )
        )
    if "mission_stuck" in enabled:
        results.extend(detect_mission_stuck(nick, daemon_log, audit, now=now))
    return results


# --- Internal helpers ------------------------------------------------------


def _epoch_to_iso(epoch: float) -> str:
    """ISO timestamp suitable for string-compare against record ``ts`` fields.

    daemon-log + audit timestamps use ``datetime.now(tz).isoformat()``
    which is lexically sortable for any common timezone.
    """
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
