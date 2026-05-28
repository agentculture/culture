"""Tests for the pure context-watermark logic."""

from __future__ import annotations

import pytest

from culture.clients._context_watch import (
    ContextWatchState,
    WatchAction,
    context_window_for,
    evaluate,
    fraction,
    mark_reminder_pending,
    take_reminder,
)


class TestContextWindowFor:
    def test_opus_default_200k(self):
        assert context_window_for("claude-opus-4-6") == 200_000

    def test_opus_1m_marker(self):
        assert context_window_for("claude-opus-4-7[1m]") == 1_000_000

    def test_sonnet_200k(self):
        assert context_window_for("claude-sonnet-4-6") == 200_000

    def test_haiku_200k(self):
        assert context_window_for("claude-haiku-4-5-20251001") == 200_000

    def test_unknown_defaults_200k(self):
        assert context_window_for("some-future-model") == 200_000

    def test_empty_defaults_200k(self):
        assert context_window_for("") == 200_000


class TestEvaluate:
    def test_below_high_water_none(self):
        st = ContextWatchState()
        # 100K of 200K = 50% — at low_water boundary, not high.
        assert evaluate(st, 100_000, "claude-opus-4-6") is WatchAction.NONE
        assert st.handoff_latched is False

    def test_at_high_water_triggers_handoff(self):
        st = ContextWatchState()
        # 185K of 200K = 92.5% >= 90%.
        assert evaluate(st, 185_000, "claude-opus-4-6") is WatchAction.WRITE_HANDOFF
        assert st.handoff_latched is True

    def test_latched_does_not_refire(self):
        st = ContextWatchState()
        assert evaluate(st, 190_000, "claude-opus-4-6") is WatchAction.WRITE_HANDOFF
        # Still above threshold, latched → no re-fire.
        assert evaluate(st, 192_000, "claude-opus-4-6") is WatchAction.NONE

    def test_latch_resets_below_low_water_signals_reminder(self):
        st = ContextWatchState()
        evaluate(st, 190_000, "claude-opus-4-6")  # latched (handoff written)
        # Drop below 50% (e.g. after compact) → reminder is now due.
        assert evaluate(st, 50_000, "claude-opus-4-6") is WatchAction.REMINDER_DUE
        assert st.handoff_latched is False
        # Re-fills → fires handoff again.
        assert evaluate(st, 190_000, "claude-opus-4-6") is WatchAction.WRITE_HANDOFF

    def test_low_usage_without_prior_latch_is_none(self):
        st = ContextWatchState()
        # Never latched — a low reading is just NONE (no spurious reminder).
        assert evaluate(st, 50_000, "claude-opus-4-6") is WatchAction.NONE

    def test_disabled_never_fires(self):
        st = ContextWatchState(enabled=False)
        assert evaluate(st, 199_000, "claude-opus-4-6") is WatchAction.NONE

    def test_none_tokens_no_fire(self):
        st = ContextWatchState()
        assert evaluate(st, None, "claude-opus-4-6") is WatchAction.NONE

    def test_zero_tokens_no_fire(self):
        st = ContextWatchState()
        assert evaluate(st, 0, "claude-opus-4-6") is WatchAction.NONE

    def test_1m_model_threshold(self):
        st = ContextWatchState()
        # 185K of 1M = 18.5% — well below high-water for a 1M model.
        assert evaluate(st, 185_000, "claude-opus-4-7[1m]") is WatchAction.NONE
        # 920K of 1M = 92%.
        assert evaluate(st, 920_000, "claude-opus-4-7[1m]") is WatchAction.WRITE_HANDOFF


class TestFraction:
    def test_fraction_basic(self):
        assert fraction(100_000, "claude-opus-4-6") == pytest.approx(0.5)

    def test_fraction_none(self):
        assert fraction(None, "claude-opus-4-6") is None

    def test_fraction_zero(self):
        assert fraction(0, "claude-opus-4-6") is None


class TestReminder:
    def test_reminder_roundtrip(self):
        st = ContextWatchState()
        assert take_reminder(st) is False
        mark_reminder_pending(st)
        assert take_reminder(st) is True
        # Consumed — second take returns False.
        assert take_reminder(st) is False
