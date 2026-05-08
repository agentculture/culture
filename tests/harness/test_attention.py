"""Unit tests for the AttentionTracker state machine.

Pure state machine — all `now` values are passed in, so we drive the clock
explicitly in each test. No asyncio, no real time.
"""

from __future__ import annotations

import pytest

# Bare-name import: tests run with packages/agent-harness on sys.path.
from attention import (  # type: ignore[import-not-found]
    AttentionConfig,
    AttentionTracker,
    Band,
    BandSpec,
    TargetState,
    default_bands,
)


def _cfg(**overrides) -> AttentionConfig:
    cfg = AttentionConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def test_default_bands_are_monotonic():
    bands = default_bands()
    intervals = [bands[b].interval_s for b in (Band.HOT, Band.WARM, Band.COOL, Band.IDLE)]
    assert intervals == sorted(intervals), intervals
    # Holds set on non-IDLE only
    assert bands[Band.HOT].hold_s == 120
    assert bands[Band.WARM].hold_s == 300
    assert bands[Band.COOL].hold_s == 600
    assert bands[Band.IDLE].hold_s is None


def test_unknown_target_starts_idle():
    t = AttentionTracker(_cfg())
    assert t.snapshot() == {}
    # Querying a target that hasn't been touched yet is fine
    assert t.due_targets(now=0.0) == []


def test_direct_stimulus_promotes_idle_to_hot():
    t = AttentionTracker(_cfg())
    t.on_direct("#dev", now=10.0)
    state = t.snapshot()["#dev"]
    assert state.band == Band.HOT
    assert state.last_promote_at == 10.0
    assert state.last_stimulus_at == 10.0


def test_ambient_one_step_warmer_capped_at_warm():
    t = AttentionTracker(_cfg())
    # IDLE -> COOL
    t.on_ambient("#a", now=1.0)
    assert t.snapshot()["#a"].band == Band.COOL
    # COOL -> WARM
    t.on_ambient("#a", now=2.0)
    assert t.snapshot()["#a"].band == Band.WARM
    # WARM -> WARM (capped)
    t.on_ambient("#a", now=3.0)
    assert t.snapshot()["#a"].band == Band.WARM


def test_ambient_does_not_demote_hot():
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=1.0)
    assert t.snapshot()["#a"].band == Band.HOT
    t.on_ambient("#a", now=2.0)
    assert t.snapshot()["#a"].band == Band.HOT  # no demotion


def test_direct_after_warm_jumps_to_hot():
    t = AttentionTracker(_cfg())
    t.on_ambient("#a", now=1.0)  # COOL
    t.on_ambient("#a", now=2.0)  # WARM
    t.on_direct("#a", now=3.0)
    assert t.snapshot()["#a"].band == Band.HOT


def test_decay_walks_one_band_per_hold():
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=0.0)
    # In HOT, hold_s=120 by default. Just before hold elapses: still HOT.
    t.due_targets(now=119.0)
    assert t.snapshot()["#a"].band == Band.HOT
    # After hold: HOT -> WARM
    t.due_targets(now=121.0)
    assert t.snapshot()["#a"].band == Band.WARM
    # WARM hold_s=300; +301s -> COOL
    t.due_targets(now=121.0 + 301.0)
    assert t.snapshot()["#a"].band == Band.COOL
    # COOL hold_s=600; +601s -> IDLE
    t.due_targets(now=121.0 + 301.0 + 601.0)
    assert t.snapshot()["#a"].band == Band.IDLE


def test_idle_is_terminal_no_decay():
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=0.0)
    # Drive far past total decay window
    t.due_targets(now=99999.0)
    assert t.snapshot()["#a"].band == Band.IDLE
    t.due_targets(now=999999.0)
    assert t.snapshot()["#a"].band == Band.IDLE  # stays IDLE


def test_stimulus_during_hot_extends_dwell():
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=0.0)
    # Just before HOT decay would fire
    t.on_direct("#a", now=119.0)
    # 60 seconds later (would have decayed at t=121 without refresh)
    t.due_targets(now=179.0)
    assert t.snapshot()["#a"].band == Band.HOT


def test_per_target_isolation():
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=0.0)
    t.due_targets(now=10.0)
    assert t.snapshot()["#a"].band == Band.HOT
    assert "#b" not in t.snapshot()
    t.on_ambient("#b", now=10.0)
    assert t.snapshot()["#a"].band == Band.HOT
    assert t.snapshot()["#b"].band == Band.COOL


def test_clock_jump_backward_is_clamped():
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=1000.0)
    # Clock goes backward (e.g., NTP correction)
    t.due_targets(now=500.0)
    # No promotion, no demotion, no exceptions
    assert t.snapshot()["#a"].band == Band.HOT


def test_due_targets_returns_targets_overdue_for_their_interval():
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=0.0)  # HOT, interval_s=30
    # Never been polled -> due immediately at any now
    assert t.due_targets(now=0.0) == ["#a"]
    t.mark_polled("#a", now=0.0)
    # Not due yet
    assert t.due_targets(now=10.0) == []
    # Due at 30s
    assert t.due_targets(now=30.0) == ["#a"]


def test_due_targets_applies_decay_before_returning():
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=0.0)
    t.mark_polled("#a", now=0.0)
    # 121s later: HOT should decay to WARM AND #a should be due (WARM
    # interval_s=120, last_poll at 0, so 121 >= 120).
    due = t.due_targets(now=121.0)
    assert due == ["#a"]
    assert t.snapshot()["#a"].band == Band.WARM


def test_set_band_manual_override():
    """Reserved entry point for the agent-controlled-attention follow-up (#355)."""
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=0.0)  # HOT
    t.set("#a", Band.IDLE, now=10.0)
    assert t.snapshot()["#a"].band == Band.IDLE


def test_paused_handled_by_caller_not_tracker():
    """The tracker doesn't know about pause — the daemon decides whether to
    consult `due_targets`. We just confirm `due_targets` is a pure read-and-
    update function so the daemon can skip calling it while paused."""
    t = AttentionTracker(_cfg())
    t.on_direct("#a", now=0.0)
    # Caller skips due_targets entirely while paused, then resumes.
    # When it resumes, decay catches up because due_targets handles it.
    snapshot1 = t.snapshot()
    assert snapshot1["#a"].band == Band.HOT
    # Simulating resume: caller invokes due_targets after pause window.
    # 421s = past HOT->WARM (120s) and past WARM->COOL (120 + 300 = 420s).
    t.due_targets(now=421.0)
    assert t.snapshot()["#a"].band == Band.COOL  # decayed twice
