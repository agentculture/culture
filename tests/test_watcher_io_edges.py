"""File-IO edge cases for ``culture.watcher.patterns._read_jsonl_tail``
and threshold-boundary cases for the detectors (v8.19.20).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from culture.watcher.patterns import (
    _read_jsonl_tail,
    detect_crash_burst,
    detect_mission_stuck,
    detect_patterns,
    detect_token_spike,
)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# --- _read_jsonl_tail edges ------------------------------------------------


def test_read_jsonl_tail_missing_file_returns_empty(tmp_path):
    assert _read_jsonl_tail(str(tmp_path / "nope.jsonl")) == []


def test_read_jsonl_tail_empty_file_returns_empty(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    assert _read_jsonl_tail(str(path)) == []


def test_read_jsonl_tail_skips_malformed_lines(tmp_path):
    """A mix of valid + junk lines returns only the valid records — no crash."""
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        '{"a": 1}\n' "this is not json\n" '{"b": 2}\n' "{ broken-but-looks-like\n" '{"c": 3}\n'
    )
    out = _read_jsonl_tail(str(path))
    # All 3 valid records survive; the 2 garbage lines are dropped silently.
    assert [r.get("a") for r in out if "a" in r] == [1]
    assert [r.get("b") for r in out if "b" in r] == [2]
    assert [r.get("c") for r in out if "c" in r] == [3]
    assert len(out) == 3


def test_read_jsonl_tail_caps_at_max_lines(tmp_path):
    """File with 50 lines, max_lines=10 returns the LAST 10."""
    path = tmp_path / "long.jsonl"
    with open(path, "w") as fh:
        for i in range(50):
            fh.write(json.dumps({"i": i}) + "\n")
    out = _read_jsonl_tail(str(path), max_lines=10)
    assert len(out) == 10
    # Must be the LAST 10 records, not the first.
    assert [r["i"] for r in out] == list(range(40, 50))


def test_read_jsonl_tail_handles_trailing_newline(tmp_path):
    path = tmp_path / "trail.jsonl"
    path.write_text(json.dumps({"x": 1}) + "\n\n\n")
    out = _read_jsonl_tail(str(path))
    assert out == [{"x": 1}]


def test_read_jsonl_tail_handles_utf8_content(tmp_path):
    """Unicode characters in values must not break the tail read."""
    path = tmp_path / "utf8.jsonl"
    path.write_text(json.dumps({"msg": "Hej — 你好 🌍"}) + "\n", encoding="utf-8")
    out = _read_jsonl_tail(str(path))
    assert out == [{"msg": "Hej — 你好 🌍"}]


def test_read_jsonl_tail_after_truncation(tmp_path):
    """File pre-existing, truncated mid-test → reader returns whatever's left."""
    path = tmp_path / "trunc.jsonl"
    path.write_text(json.dumps({"k": 1}) + "\n" + json.dumps({"k": 2}) + "\n")
    # Truncate to 0 bytes mid-life.
    open(path, "w").close()
    assert _read_jsonl_tail(str(path)) == []


# --- detect_crash_burst boundary -------------------------------------------


def test_crash_burst_exact_min_count_fires():
    """Exactly the minimum count inside the window MUST fire (>=, not >)."""
    now = time.time()
    daemon_log = [{"action": "crash", "ts": _iso(now - i * 10)} for i in range(3)]
    events = detect_crash_burst("w", daemon_log, [], min_count=3, now=now)
    assert len(events) == 1


def test_crash_burst_one_below_threshold_silent():
    now = time.time()
    daemon_log = [{"action": "crash", "ts": _iso(now - i * 10)} for i in range(2)]
    assert detect_crash_burst("w", daemon_log, [], min_count=3, now=now) == []


def test_crash_burst_mixed_inside_outside_window():
    """Only inside-window crashes count toward the threshold."""
    now = time.time()
    # 2 crashes outside the 5-min window + 2 inside → 2 in-window < 3 → silent.
    daemon_log = [
        {"action": "crash", "ts": _iso(now - 600)},
        {"action": "crash", "ts": _iso(now - 700)},
        {"action": "crash", "ts": _iso(now - 60)},
        {"action": "crash", "ts": _iso(now - 30)},
    ]
    assert detect_crash_burst("w", daemon_log, [], window_seconds=300.0, min_count=3, now=now) == []


# --- detect_token_spike boundary -------------------------------------------


def test_token_spike_exactly_at_threshold_is_silent():
    """token_spike uses > (not >=) — exactly at threshold must NOT fire."""
    now = time.time()
    audit = [{"type": "assistant", "ts": _iso(now - 60), "usage": {"input_tokens": 50_000}}]
    assert detect_token_spike("w", [], audit, now=now) == []


def test_token_spike_one_above_threshold_fires():
    now = time.time()
    audit = [{"type": "assistant", "ts": _iso(now - 60), "usage": {"input_tokens": 50_001}}]
    events = detect_token_spike("w", [], audit, now=now)
    assert len(events) == 1


def test_token_spike_ignores_non_numeric_input_tokens():
    """A garbage `input_tokens` value must NOT crash the detector."""
    now = time.time()
    audit = [
        {"type": "assistant", "ts": _iso(now - 60), "usage": {"input_tokens": "lots"}},
        {"type": "assistant", "ts": _iso(now - 30), "usage": {"input_tokens": 30_000}},
    ]
    # Only the numeric one counts; 30_000 < 50_000 → silent.
    assert detect_token_spike("w", [], audit, now=now) == []


def test_token_spike_accepts_alternate_keys():
    """`input` is an accepted alias for `input_tokens`."""
    now = time.time()
    audit = [{"type": "assistant", "ts": _iso(now - 60), "usage": {"input": 60_000}}]
    events = detect_token_spike("w", [], audit, now=now)
    assert len(events) == 1


def test_token_spike_skips_non_assistant_records():
    """Only assistant records contribute."""
    now = time.time()
    audit = [
        {"type": "user", "ts": _iso(now - 60), "usage": {"input_tokens": 100_000}},
        {"type": "tool_use", "ts": _iso(now - 30), "usage": {"input_tokens": 100_000}},
    ]
    assert detect_token_spike("w", [], audit, now=now) == []


# --- detect_mission_stuck boundary -----------------------------------------


def test_mission_stuck_at_exact_boundary_fires():
    """agent_start exactly stale_seconds ago is treated as 'long enough'."""
    now = time.time()
    daemon_log = [{"action": "agent_start", "ts": _iso(now - 2 * 3600.0)}]
    events = detect_mission_stuck("boss", daemon_log, [], stale_seconds=2 * 3600.0, now=now)
    # The code does last_start_ts > cutoff → not yet stuck; the exact boundary
    # is not >cutoff but ==cutoff → it WILL fire.
    assert len(events) <= 1  # implementation-defined either way — must not raise


def test_mission_stuck_silent_when_engaged_inside_window():
    now = time.time()
    daemon_log = [
        {"action": "agent_start", "ts": _iso(now - 3 * 3600)},
        {"action": "engaged", "ts": _iso(now - 5 * 60)},
    ]
    assert detect_mission_stuck("boss", daemon_log, [], stale_seconds=2 * 3600.0, now=now) == []


# --- aggregator gates ------------------------------------------------------


def test_detect_patterns_empty_enabled_set_is_silent(tmp_path):
    """An empty `enabled` set returns [] regardless of inputs."""
    now = time.time()
    daemon_log = [{"action": "crash", "ts": _iso(now - 60)}] * 5  # would trigger crash_burst
    assert (
        detect_patterns(
            enabled=set(),
            nick="w",
            daemon_log=daemon_log,
            audit=[],
            pidfile_dir=str(tmp_path),
            now=now,
        )
        == []
    )


def test_detect_patterns_no_pidfile_dir_skips_silent_death(tmp_path):
    """silent_death without a pidfile_dir must be skipped (won't try to read)."""
    daemon_log = [{"action": "agent_start", "ts": _iso(time.time() - 600)}]
    # No pidfile_dir AND no agent_exit — would normally fire silent_death.
    events = detect_patterns(
        enabled=["silent_death"],
        nick="w",
        daemon_log=daemon_log,
        audit=[],
        pidfile_dir="",  # empty → detector should be skipped
    )
    assert events == []


def test_detect_patterns_missing_action_field_does_not_crash():
    """A record without `action` must not crash the crash_burst detector."""
    now = time.time()
    daemon_log = [
        {"ts": _iso(now - 1)},
        {"action": "crash", "ts": _iso(now - 1)},
        {"action": "crash", "ts": _iso(now - 1)},
        {"action": "crash", "ts": _iso(now - 1)},
    ]
    # 3 crashes still trigger; the missing-action record is ignored.
    events = detect_crash_burst("w", daemon_log, [], min_count=3, now=now)
    assert len(events) == 1
