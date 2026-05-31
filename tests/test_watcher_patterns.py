"""Tests for ``culture.watcher.patterns`` — the 5 MVP detectors (v8.19.19)."""

import json
import time
from datetime import datetime, timezone

import pytest

from culture.watcher.patterns import (
    detect_crash_burst,
    detect_mission_stuck,
    detect_patterns,
    detect_perm_escalation,
    detect_silent_death,
    detect_token_spike,
)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# --- silent_death ----------------------------------------------------------


def test_silent_death_fires_when_pid_dead(tmp_path):
    daemon_log = [
        {"action": "agent_start", "ts": _iso(time.time() - 100), "detail": {}},
    ]
    # Write a pidfile with a PID that is almost certainly dead.
    pidfile = tmp_path / "agent-w.pid"
    pidfile.write_text("99999999")
    events = detect_silent_death("w", daemon_log, [], pidfile_dir=str(tmp_path))
    assert len(events) == 1
    assert events[0].severity == "high"
    assert events[0].target == "w"
    assert "died" in events[0].summary


def test_silent_death_skips_when_exit_recorded(tmp_path):
    daemon_log = [
        {"action": "agent_start", "ts": _iso(time.time() - 100)},
        {"action": "agent_exit", "ts": _iso(time.time() - 10)},
    ]
    assert detect_silent_death("w", daemon_log, [], pidfile_dir=str(tmp_path)) == []


def test_silent_death_skips_when_pid_alive(tmp_path):
    import os

    daemon_log = [{"action": "agent_start", "ts": _iso(time.time() - 100)}]
    pidfile = tmp_path / "agent-w.pid"
    pidfile.write_text(str(os.getpid()))  # this process is alive
    assert detect_silent_death("w", daemon_log, [], pidfile_dir=str(tmp_path)) == []


def test_silent_death_no_log_returns_empty(tmp_path):
    assert detect_silent_death("w", [], [], pidfile_dir=str(tmp_path)) == []


# --- crash_burst -----------------------------------------------------------


def test_crash_burst_fires_at_threshold():
    now = time.time()
    daemon_log = [
        {"action": "crash", "ts": _iso(now - 60), "detail": {}},
        {"action": "crash", "ts": _iso(now - 40)},
        {"action": "crash", "ts": _iso(now - 20)},
    ]
    events = detect_crash_burst("w", daemon_log, [], now=now)
    assert len(events) == 1
    assert events[0].severity == "high"
    assert "3 times" in events[0].summary


def test_crash_burst_below_threshold_silent():
    now = time.time()
    daemon_log = [
        {"action": "crash", "ts": _iso(now - 60)},
        {"action": "crash", "ts": _iso(now - 20)},
    ]
    assert detect_crash_burst("w", daemon_log, [], now=now) == []


def test_crash_burst_outside_window_silent():
    now = time.time()
    daemon_log = [
        {"action": "crash", "ts": _iso(now - 3600)},
        {"action": "crash", "ts": _iso(now - 3700)},
        {"action": "crash", "ts": _iso(now - 3800)},
    ]
    assert detect_crash_burst("w", daemon_log, [], window_seconds=300.0, now=now) == []


# --- token_spike -----------------------------------------------------------


def test_token_spike_fires_above_threshold():
    now = time.time()
    audit = [
        {"type": "assistant", "ts": _iso(now - 60), "usage": {"input_tokens": 30_000}},
        {"type": "assistant", "ts": _iso(now - 30), "usage": {"input_tokens": 25_000}},
    ]
    events = detect_token_spike("w", [], audit, now=now)
    assert len(events) == 1
    assert events[0].severity == "medium"
    assert "55,000" in events[0].summary


def test_token_spike_below_threshold_silent():
    now = time.time()
    audit = [{"type": "assistant", "ts": _iso(now - 60), "usage": {"input_tokens": 10_000}}]
    assert detect_token_spike("w", [], audit, now=now) == []


def test_token_spike_ignores_old_records():
    now = time.time()
    audit = [
        {"type": "assistant", "ts": _iso(now - 7200), "usage": {"input_tokens": 100_000}},
    ]
    assert detect_token_spike("w", [], audit, window_seconds=600.0, now=now) == []


# --- perm_escalation_above_ceiling -----------------------------------------


def test_perm_escalation_fires_on_denylisted_tool():
    pending = [
        {
            "id": "req-1",
            "helper_nick": "local-w",
            "boss_nick": "local-boss",
            "tool_name": "mcp__dangerous__send",
        }
    ]
    ceiling = {"local-boss": ["mcp__dangerous__send"]}
    events = detect_perm_escalation("local-w", pending_requests=pending, boss_ceiling=ceiling)
    assert len(events) == 1
    assert events[0].severity == "high"


def test_perm_escalation_allows_non_denylisted_tool():
    pending = [
        {"id": "req-2", "helper_nick": "local-w", "boss_nick": "local-boss", "tool_name": "Bash"}
    ]
    ceiling = {"local-boss": ["mcp__dangerous__send"]}
    assert detect_perm_escalation("local-w", pending_requests=pending, boss_ceiling=ceiling) == []


def test_perm_escalation_ignores_other_helpers():
    pending = [{"id": "x", "helper_nick": "other-w", "boss_nick": "local-boss", "tool_name": "X"}]
    ceiling = {"local-boss": ["X"]}
    assert detect_perm_escalation("local-w", pending_requests=pending, boss_ceiling=ceiling) == []


# --- mission_stuck ---------------------------------------------------------


def test_mission_stuck_fires_when_silent_long():
    now = time.time()
    # Started 3 hours ago, no engaged, no audit since.
    daemon_log = [{"action": "agent_start", "ts": _iso(now - 3 * 3600)}]
    audit: list[dict] = []
    events = detect_mission_stuck("boss", daemon_log, audit, stale_seconds=2 * 3600.0, now=now)
    assert len(events) == 1
    assert events[0].severity == "medium"


def test_mission_stuck_silent_when_recent_engaged():
    now = time.time()
    daemon_log = [
        {"action": "agent_start", "ts": _iso(now - 3 * 3600)},
        {"action": "engaged", "ts": _iso(now - 60)},
    ]
    assert detect_mission_stuck("boss", daemon_log, [], stale_seconds=2 * 3600.0, now=now) == []


def test_mission_stuck_silent_when_recent_assistant():
    now = time.time()
    daemon_log = [{"action": "agent_start", "ts": _iso(now - 3 * 3600)}]
    audit = [{"type": "assistant", "ts": _iso(now - 60), "text": "hi"}]
    assert detect_mission_stuck("boss", daemon_log, audit, stale_seconds=2 * 3600.0, now=now) == []


def test_mission_stuck_silent_when_not_long_running():
    now = time.time()
    daemon_log = [{"action": "agent_start", "ts": _iso(now - 60)}]
    assert detect_mission_stuck("boss", daemon_log, [], stale_seconds=2 * 3600.0, now=now) == []


# --- detect_patterns aggregator --------------------------------------------


def test_detect_patterns_runs_only_enabled(tmp_path):
    now = time.time()
    daemon_log = [
        {"action": "crash", "ts": _iso(now - 1)},
        {"action": "crash", "ts": _iso(now - 2)},
        {"action": "crash", "ts": _iso(now - 3)},
    ]
    # crash_burst enabled, others disabled — should only get one event class.
    events = detect_patterns(
        enabled=["crash_burst"],
        nick="w",
        daemon_log=daemon_log,
        audit=[],
        pidfile_dir=str(tmp_path),
        now=now,
    )
    assert len(events) == 1
    assert events[0].pattern == "crash_burst"


def test_pattern_event_key_is_stable():
    from culture.watcher.patterns import PatternEvent

    a = PatternEvent(pattern="silent_death", severity="high", target="w", summary="x")
    b = PatternEvent(pattern="silent_death", severity="high", target="w", summary="y")
    assert a.key == b.key == "silent_death:w"
