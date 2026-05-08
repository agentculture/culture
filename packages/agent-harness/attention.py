"""Per-target attention state machine for agent harnesses.

Each agent maintains a small state machine per target (channel or DM peer).
Direct stimuli (@mentions, DMs) promote the target to HOT. Ambient stimuli
(non-mention messages on a target the agent is participating in) promote
one band warmer, capped at WARM. Quiet targets decay one band per hold
window down to IDLE.

Pure module: no I/O, no asyncio. All time values are passed in by the
caller (use ``time.monotonic()`` in production). Designed to be cited
byte-identically into each backend per the all-backends rule.

See ``docs/superpowers/specs/2026-05-08-dynamic-attention-levels-design.md``
for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable


class Band(IntEnum):
    """Attention bands ordered warm-to-cold (lower int = warmer)."""

    HOT = 0
    WARM = 1
    COOL = 2
    IDLE = 3


@dataclass
class BandSpec:
    """Polling interval and decay hold for a single band."""

    interval_s: int
    hold_s: int | None  # None for IDLE (terminal — no further decay)


def default_bands() -> dict[Band, BandSpec]:
    """Defaults from the spec. Operators can override per-band in YAML."""
    return {
        Band.HOT: BandSpec(interval_s=30, hold_s=120),
        Band.WARM: BandSpec(interval_s=120, hold_s=300),
        Band.COOL: BandSpec(interval_s=300, hold_s=600),
        Band.IDLE: BandSpec(interval_s=600, hold_s=None),
    }


@dataclass
class AttentionConfig:
    """Static config for the tracker. Validated once at load time."""

    enabled: bool = True
    tick_s: int = 5
    thread_window_s: int = 1800
    bands: dict[Band, BandSpec] = field(default_factory=default_bands)


@dataclass
class TargetState:
    """Mutable state for one target.

    ``last_poll_at`` uses ``None`` to mean "never polled" so a real
    ``now=0.0`` does not collide with the sentinel.
    """

    band: Band = Band.IDLE
    last_promote_at: float = 0.0
    last_stimulus_at: float = 0.0
    last_poll_at: float | None = None


# Cause strings for transition callbacks. Kept as bare strings (not an
# enum) because they're emitted into log lines and OTel attributes.
CAUSE_DIRECT = "direct"
CAUSE_AMBIENT = "ambient"
CAUSE_DECAY = "decay"
CAUSE_MANUAL = "manual"


TransitionCallback = Callable[[str, Band, Band, str], None]
"""(target, from_band, to_band, cause) -> None"""


def _next_cooler(band: Band) -> Band:
    """Walk one step toward IDLE. IDLE returns IDLE."""
    if band == Band.IDLE:
        return Band.IDLE
    return Band(band + 1)


def _next_warmer(band: Band) -> Band:
    """Walk one step toward HOT. HOT returns HOT."""
    if band == Band.HOT:
        return Band.HOT
    return Band(band - 1)


class AttentionTracker:
    """Per-target attention state machine. Pure state, no I/O.

    All time values are passed in by the caller. The tracker never reads
    a clock itself, which makes its behaviour fully deterministic and
    trivial to unit-test.

    Concurrency: not thread-safe by itself. The agent daemon runs a
    single asyncio event loop, so all calls happen on the same thread
    and no locking is needed.
    """

    def __init__(
        self,
        config: AttentionConfig,
        on_transition: TransitionCallback | None = None,
    ):
        self._config = config
        self._states: dict[str, TargetState] = {}
        self._on_transition = on_transition

    # ------------------------------------------------------------------
    # Stimulus inputs
    # ------------------------------------------------------------------

    def on_direct(self, target: str, now: float) -> Band:
        """Direct addressing (@mention, DM). Always promotes to HOT."""
        state = self._get_or_init(target)
        prev = state.band
        state.band = Band.HOT
        state.last_promote_at = now
        state.last_stimulus_at = now
        if prev != state.band:
            self._emit(target, prev, state.band, CAUSE_DIRECT)
        return state.band

    def on_ambient(self, target: str, now: float) -> Band:
        """Non-mention message on an active target. One step warmer, capped at WARM."""
        state = self._get_or_init(target)
        prev = state.band
        candidate = _next_warmer(prev)
        # Cap promotion at WARM. If we're already HOT, stay HOT (no demotion).
        if candidate < Band.WARM:  # warmer than WARM
            candidate = Band.WARM if prev != Band.HOT else Band.HOT
        state.band = candidate
        state.last_promote_at = now
        state.last_stimulus_at = now
        if prev != state.band:
            self._emit(target, prev, state.band, CAUSE_AMBIENT)
        return state.band

    def set(self, target: str, band: Band, now: float) -> Band:
        """Manual override. Reserved for the agent-controlled-attention follow-up."""
        state = self._get_or_init(target)
        prev = state.band
        state.band = band
        state.last_promote_at = now
        state.last_stimulus_at = now
        if prev != state.band:
            self._emit(target, prev, state.band, CAUSE_MANUAL)
        return state.band

    # ------------------------------------------------------------------
    # Poll-loop driver
    # ------------------------------------------------------------------

    def due_targets(self, now: float) -> list[str]:
        """Apply decay, then return targets overdue for a poll at `now`.

        A target is "due" when ``now - last_poll_at >= interval(band)``.
        Targets that have never been polled (``last_poll_at is None``)
        are always due.

        This method also walks decay: if a target's hold has elapsed
        since ``last_promote_at``, it's stepped one band cooler. Multiple
        steps can fire in one call when the daemon was paused or sleep-
        scheduled.
        """
        due: list[str] = []
        for target, state in self._states.items():
            self._apply_decay(target, state, now)
            if state.last_poll_at is None:
                due.append(target)
                continue
            interval = self._config.bands[state.band].interval_s
            elapsed = now - state.last_poll_at
            # Clamp negative elapsed (clock jump backward) to "not due."
            if elapsed < 0:
                continue
            if elapsed >= interval:
                due.append(target)
        return due

    def mark_polled(self, target: str, now: float) -> None:
        """Record that the daemon just polled this target."""
        state = self._get_or_init(target)
        state.last_poll_at = now

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, TargetState]:
        """Return a shallow copy for tests / observability."""
        return {t: TargetState(**vars(s)) for t, s in self._states.items()}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_or_init(self, target: str) -> TargetState:
        state = self._states.get(target)
        if state is None:
            state = TargetState()
            self._states[target] = state
        return state

    def _apply_decay(self, target: str, state: TargetState, now: float) -> None:
        # Walk decay one band at a time; multiple steps per call OK.
        # Each step advances ``last_promote_at`` by the band's hold (not
        # by ``now``) so a long silence walks through every band rather
        # than stopping at the first one.
        while state.band != Band.IDLE:
            hold = self._config.bands[state.band].hold_s
            if hold is None:
                break
            elapsed = now - state.last_promote_at
            if elapsed < 0:
                # Clock jumped backward; do nothing this tick.
                return
            if elapsed <= hold:
                return
            prev = state.band
            state.band = _next_cooler(prev)
            state.last_promote_at = state.last_promote_at + hold
            self._emit(target, prev, state.band, CAUSE_DECAY)

    def _emit(self, target: str, prev: Band, new: Band, cause: str) -> None:
        if self._on_transition is not None:
            self._on_transition(target, prev, new, cause)
