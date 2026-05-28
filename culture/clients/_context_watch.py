"""Context-watermark handoff logic.

A long-running agent fills its context window. Before it does, the daemon asks
the agent to write a handoff for its post-compact self, triggers a compact, then
reminds it to read the handoff on next activation. This module holds the pure
decision logic; the daemon owns the I/O (sending prompts, triggering compact).

Design spec: docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Context window sizes by model family. Unknown models default to the
# conservative 200K. Extend this map as new models ship.
_CONTEXT_WINDOWS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"claude-opus-4.*1m", re.IGNORECASE), 1_000_000),
    (re.compile(r"claude-opus-4", re.IGNORECASE), 200_000),
    (re.compile(r"claude-sonnet-4", re.IGNORECASE), 200_000),
    (re.compile(r"claude-haiku-4", re.IGNORECASE), 200_000),
]
_DEFAULT_CONTEXT_WINDOW = 200_000

# 1M-context model IDs carry a separate marker; the SDK model id for the
# 1M beta is exposed as e.g. "claude-opus-4-7[1m]". Match that explicitly.
_ONE_M_MARKER = re.compile(r"\[1m\]|1m|1000k|one[-_]?m", re.IGNORECASE)


def context_window_for(model: str) -> int:
    """Resolve the context window (in tokens) for a model id."""
    if not model:
        return _DEFAULT_CONTEXT_WINDOW
    if "opus-4" in model.lower() and _ONE_M_MARKER.search(model):
        return 1_000_000
    for pattern, size in _CONTEXT_WINDOWS:
        if pattern.search(model):
            return size
    logger.warning(
        "Unknown model %r for context-watch; defaulting to %d tokens",
        model,
        _DEFAULT_CONTEXT_WINDOW,
    )
    return _DEFAULT_CONTEXT_WINDOW


class WatchAction(enum.Enum):
    """What the daemon should do after evaluating a turn's usage."""

    NONE = "none"
    WRITE_HANDOFF = "write_handoff"
    REMINDER_DUE = "reminder_due"


@dataclass
class ContextWatchState:
    """Mutable per-agent watch state.

    ``high_water`` / ``low_water`` are fractions of the context window.
    ``handoff_latched`` prevents re-firing the handoff every turn while still
    above threshold. ``reminder_pending`` signals that a post-compact reminder
    is owed on the next activation.
    """

    high_water: float = 0.90
    low_water: float = 0.50
    enabled: bool = True
    handoff_latched: bool = False
    reminder_pending: bool = False


def evaluate(state: ContextWatchState, input_tokens: int | None, model: str) -> WatchAction:
    """Decide the next watch action from a turn's input-token count.

    Called by the daemon after each ``ResultMessage``. Mutates ``state``.

    - At/above ``high_water`` and not latched: return WRITE_HANDOFF, set latch.
    - Below ``low_water`` while latched: a compact has actually dropped usage —
      reset the latch and return REMINDER_DUE so the daemon arms the
      post-compact reminder *now* (not when the compact was merely queued, which
      would let an interleaving activation consume the reminder too early).
    - Otherwise: NONE.
    """
    if not state.enabled or input_tokens is None or input_tokens <= 0:
        return WatchAction.NONE

    window = context_window_for(model)
    pct = input_tokens / window

    if pct < state.low_water:
        if state.handoff_latched:
            # Context dropped after a handoff+compact — re-arm and signal that
            # the reminder is now due.
            state.handoff_latched = False
            return WatchAction.REMINDER_DUE
        return WatchAction.NONE

    if pct >= state.high_water and not state.handoff_latched:
        state.handoff_latched = True
        return WatchAction.WRITE_HANDOFF

    return WatchAction.NONE


def fraction(input_tokens: int | None, model: str) -> float | None:
    """Return the context-fill fraction for a turn, or None if unknown."""
    if input_tokens is None or input_tokens <= 0:
        return None
    return input_tokens / context_window_for(model)


def mark_reminder_pending(state: ContextWatchState) -> None:
    """Record that a post-compact reminder is owed."""
    state.reminder_pending = True


def take_reminder(state: ContextWatchState) -> bool:
    """Consume the reminder flag. Returns True if a reminder was pending."""
    if state.reminder_pending:
        state.reminder_pending = False
        return True
    return False
