# Dynamic Attention Levels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed `poll_interval` with a per-target attention state machine (HOT/WARM/COOL/IDLE bands) driven by direct (@mention, DM) and ambient (active-thread) stimuli, propagated across all four agent backends per the cite-don't-import rule.

**Architecture:** A pure `AttentionTracker` state machine lives in `packages/agent-harness/attention.py`. The transport gains two new callbacks (`on_ambient`, `on_outgoing`); the daemon constructs the tracker, wires the callbacks, and replaces the fixed-interval poll loop with a tick-based loop that polls only targets the tracker reports as "due." Same module is cited byte-identically into each backend, with backend-specific import paths.

**Tech Stack:** Python 3.11+, asyncio, dataclasses/IntEnum, pyyaml (already in use), pytest + pytest-asyncio (existing).

**Spec:** [`docs/superpowers/specs/2026-05-08-dynamic-attention-levels-design.md`](../specs/2026-05-08-dynamic-attention-levels-design.md)

---

## File Structure

### Phase A — Reference implementation in `packages/agent-harness/`

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/agent-harness/attention.py` | **Create** | Pure state machine: `Band`, `BandSpec`, `AttentionConfig`, `TargetState`, `AttentionTracker`. No I/O, no asyncio. |
| `packages/agent-harness/config.py` | **Modify** | Add `AttentionConfig` dataclass, parse `attention:` block in `load_config`, monotonicity + range validation, per-agent shallow-merge. |
| `packages/agent-harness/irc_transport.py` | **Modify** | Add `on_ambient(target, sender, text)` and `on_outgoing(target, line)` callbacks; fire from existing `_on_privmsg` and `send_privmsg`. |
| `packages/agent-harness/daemon.py` | **Modify** | Construct `AttentionTracker`; wire `on_direct` from `_on_mention`, `on_ambient` from new transport callback (gated by thread-window predicate), `on_outgoing` to update engagement; replace `_poll_loop` with tick-based version (legacy fallback when `attention.enabled=false`). |
| `tests/harness/test_attention.py` | **Create** | State machine unit tests with fake `now`. |
| `tests/harness/test_attention_config.py` | **Create** | Config parsing, validation, merge, legacy fallback. |
| `tests/harness/test_daemon_attention_wiring.py` | **Create** | Smoke test: fake transport feeds stimuli, assert `_send_channel_poll` cadence. |

### Phase B — All-backends propagation

For each of `claude`, `codex`, `copilot`, `acp`, modify the cited copies under `culture/clients/<backend>/`:

| File | Action |
|------|--------|
| `culture/clients/<backend>/attention.py` | **Create** (copy of reference, no import changes — module is self-contained). |
| `culture/clients/<backend>/config.py` | **Modify** — same diff as Phase A Task 2. |
| `culture/clients/<backend>/irc_transport.py` | **Modify** — same diff as Phase A Task 3. |
| `culture/clients/<backend>/daemon.py` | **Modify** — same diff as Phase A Task 4 (already uses backend-prefixed imports). |

### Phase C — Docs

| File | Action |
|------|--------|
| `docs/attention.md` | **Create** — model, defaults, override examples, deprecation note. |
| `docs/agents.md` (or whichever doc currently describes `poll_interval`) | **Modify** — add one-line pointer to `attention.md`. Engineer searches `docs/` for `poll_interval` to find the right page. |

### Phase D — Verification & release

| Task | File |
|------|------|
| `doc-test-alignment` subagent run | n/a (subagent dispatch) |
| `/run-tests --ci` | full suite |
| `/version-bump minor` | `pyproject.toml`, `CHANGELOG.md`, `uv.lock` |

---

## Phase A — Reference implementation

### Task 1: State machine module + tests (TDD)

**Files:**
- Create: `packages/agent-harness/attention.py`
- Test: `tests/harness/test_attention.py`

- [ ] **Step 1.1: Write `tests/harness/test_attention.py`**

```python
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
    t.due_targets(now=400.0)
    assert t.snapshot()["#a"].band == Band.COOL  # decayed twice
```

- [ ] **Step 1.2: Run tests; they should fail (module not yet written)**

```bash
cd packages/agent-harness && uv run --with pytest --with pytest-asyncio pytest ../../tests/harness/test_attention.py -v
```
Expected: collection error or `ModuleNotFoundError: No module named 'attention'`.

- [ ] **Step 1.3: Write `packages/agent-harness/attention.py` (`Diff A`)**

```python
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
    """Mutable state for one target."""

    band: Band = Band.IDLE
    last_promote_at: float = 0.0
    last_stimulus_at: float = 0.0
    last_poll_at: float = 0.0


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
        First-poll case (``last_poll_at == 0``) is always due.

        This method also walks decay: if a target's hold has elapsed
        since ``last_promote_at``, it's stepped one band cooler. Multiple
        steps can fire in one call when the daemon was paused or sleep-
        scheduled.
        """
        due: list[str] = []
        for target, state in self._states.items():
            self._apply_decay(target, state, now)
            interval = self._config.bands[state.band].interval_s
            elapsed = now - state.last_poll_at
            # Clamp negative elapsed (clock jump backward) to "not due."
            if elapsed < 0:
                continue
            if state.last_poll_at == 0.0 or elapsed >= interval:
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
            state.last_promote_at = now
            self._emit(target, prev, state.band, CAUSE_DECAY)

    def _emit(self, target: str, prev: Band, new: Band, cause: str) -> None:
        if self._on_transition is not None:
            self._on_transition(target, prev, new, cause)
```

- [ ] **Step 1.4: Run tests; they should pass**

```bash
cd packages/agent-harness && uv run --with pytest --with pytest-asyncio pytest ../../tests/harness/test_attention.py -v
```
Expected: all tests in `test_attention.py` PASS.

- [ ] **Step 1.5: Commit**

```bash
git add packages/agent-harness/attention.py tests/harness/test_attention.py
git commit -m "feat(harness): pure attention state machine module (#345)

Per-target Band/BandSpec/AttentionConfig/TargetState/AttentionTracker.
No I/O, no asyncio — all time passed in by caller. Direct stimulus
promotes to HOT; ambient one step warmer capped at WARM; decay walks
one band per hold window. Reserved set() entry point for the agent-
controlled-attention follow-up (#355).

- Claude"
```

---

### Task 2: Config schema + tests

**Files:**
- Modify: `packages/agent-harness/config.py`
- Test: `tests/harness/test_attention_config.py`

- [ ] **Step 2.1: Write `tests/harness/test_attention_config.py`**

```python
"""Tests for AttentionConfig parsing, validation, and per-agent merge."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from config import (  # type: ignore[import-not-found]
    DaemonConfig,
    load_config,
    resolve_attention_config,
)
from attention import Band  # type: ignore[import-not-found]


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "server.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_no_attention_block_uses_defaults_with_legacy_poll_interval(tmp_path):
    cfg_path = _write_yaml(tmp_path, {"poll_interval": 90, "agents": []})
    cfg = load_config(cfg_path)
    assert cfg.attention.enabled is True
    # Legacy poll_interval migrates into idle.interval_s
    assert cfg.attention.bands[Band.IDLE].interval_s == 90
    # Other bands keep defaults
    assert cfg.attention.bands[Band.HOT].interval_s == 30


def test_attention_disabled_falls_back_to_legacy_poll_interval(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {"poll_interval": 45, "attention": {"enabled": False}, "agents": []},
    )
    cfg = load_config(cfg_path)
    assert cfg.attention.enabled is False
    assert cfg.poll_interval == 45


def test_explicit_attention_overrides_defaults(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {
                "enabled": True,
                "tick_s": 3,
                "thread_window_s": 600,
                "bands": {
                    "hot": {"interval_s": 15, "hold_s": 60},
                    "warm": {"interval_s": 60, "hold_s": 180},
                    "cool": {"interval_s": 180, "hold_s": 360},
                    "idle": {"interval_s": 900},
                },
            },
            "agents": [],
        },
    )
    cfg = load_config(cfg_path)
    assert cfg.attention.tick_s == 3
    assert cfg.attention.thread_window_s == 600
    assert cfg.attention.bands[Band.HOT].interval_s == 15
    assert cfg.attention.bands[Band.IDLE].hold_s is None


def test_partial_band_override_inherits_defaults(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {"bands": {"hot": {"interval_s": 10, "hold_s": 30}}},
            "agents": [],
        },
    )
    cfg = load_config(cfg_path)
    assert cfg.attention.bands[Band.HOT].interval_s == 10
    # Unspecified bands keep defaults
    assert cfg.attention.bands[Band.WARM].interval_s == 120
    assert cfg.attention.bands[Band.COOL].interval_s == 300


def test_non_monotonic_bands_rejected(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {
                "bands": {
                    "hot": {"interval_s": 100, "hold_s": 60},
                    "warm": {"interval_s": 50, "hold_s": 60},  # warmer than HOT — invalid
                }
            },
            "agents": [],
        },
    )
    with pytest.raises(ValueError, match="monotonic"):
        load_config(cfg_path)


def test_zero_interval_rejected(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {"attention": {"bands": {"hot": {"interval_s": 0, "hold_s": 60}}}, "agents": []},
    )
    with pytest.raises(ValueError, match="interval_s"):
        load_config(cfg_path)


def test_zero_hold_rejected_for_non_idle(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {"attention": {"bands": {"warm": {"interval_s": 60, "hold_s": 0}}}, "agents": []},
    )
    with pytest.raises(ValueError, match="hold_s"):
        load_config(cfg_path)


def test_tick_s_must_not_exceed_min_interval(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {
                "tick_s": 60,
                "bands": {"hot": {"interval_s": 30, "hold_s": 60}},
            },
            "agents": [],
        },
    )
    with pytest.raises(ValueError, match="tick_s"):
        load_config(cfg_path)


def test_per_agent_override_shallow_merges(tmp_path):
    """resolve_attention_config(daemon, agent) merges per-agent over daemon."""
    from config import AgentConfig  # type: ignore[import-not-found]

    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {"bands": {"hot": {"interval_s": 30, "hold_s": 120}}},
            "agents": [
                {
                    "nick": "spark-bot",
                    "channels": ["#dev"],
                    "attention": {"bands": {"hot": {"interval_s": 15, "hold_s": 60}}},
                }
            ],
        },
    )
    cfg = load_config(cfg_path)
    agent = cfg.get_agent("spark-bot")
    assert agent is not None
    resolved = resolve_attention_config(cfg, agent)
    # Per-agent override applied
    assert resolved.bands[Band.HOT].interval_s == 15
    assert resolved.bands[Band.HOT].hold_s == 60
    # Other bands inherit from daemon defaults
    assert resolved.bands[Band.WARM].interval_s == 120
```

- [ ] **Step 2.2: Run tests; they should fail**

```bash
cd packages/agent-harness && uv run --with pytest --with pytest-asyncio --with pyyaml pytest ../../tests/harness/test_attention_config.py -v
```
Expected: `ImportError: cannot import name 'resolve_attention_config'` (or similar).

- [ ] **Step 2.3: Modify `packages/agent-harness/config.py` (`Diff B`)**

Add this import near the top of the file (after the existing `from constants import ...` block):

```python
from attention import (  # noqa: E402  # pylint: disable=import-error
    AttentionConfig,
    Band,
    BandSpec,
    default_bands,
)
```

In the `AgentConfig` dataclass, add a new field at the end:

```python
@dataclass
class AgentConfig:
    """Per-agent settings."""

    nick: str = ""
    directory: str = "."
    channels: list[str] = field(default_factory=lambda: ["#general"])
    model: str = "claude-opus-4-6"
    thinking: str = "medium"
    system_prompt: str = ""
    icon: str | None = None
    turn_timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS
    # Per-agent attention overrides; merged shallowly over daemon defaults.
    # None means "inherit fully."
    attention_overrides: dict | None = None
```

In `DaemonConfig`, add:

```python
@dataclass
class DaemonConfig:
    """Top-level daemon configuration."""

    server: ServerConnConfig = field(default_factory=ServerConnConfig)
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)
    webhooks: WebhookConfig = field(default_factory=WebhookConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    buffer_size: int = 500
    poll_interval: int = 60
    sleep_start: str = "23:00"
    sleep_end: str = "08:00"
    agents: list[AgentConfig] = field(default_factory=list)
    attention: AttentionConfig = field(default_factory=AttentionConfig)
```

Replace the body of `load_config` with:

```python
_BAND_NAMES = {
    "hot": Band.HOT,
    "warm": Band.WARM,
    "cool": Band.COOL,
    "idle": Band.IDLE,
}


def _parse_bands(raw_bands: dict, defaults: dict[Band, BandSpec]) -> dict[Band, BandSpec]:
    """Shallow-merge raw band dict over defaults. Validates each entry."""
    result = dict(defaults)
    for name, raw_spec in (raw_bands or {}).items():
        if name not in _BAND_NAMES:
            raise ValueError(f"unknown band name: {name!r}")
        band = _BAND_NAMES[name]
        interval_s = raw_spec.get("interval_s")
        hold_s = raw_spec.get("hold_s")
        if interval_s is None or interval_s <= 0:
            raise ValueError(f"band {name}: interval_s must be > 0, got {interval_s!r}")
        if band == Band.IDLE:
            if hold_s is not None:
                # Allow but ignore — log a warning at startup.
                hold_s = None
        else:
            if hold_s is None or hold_s <= 0:
                raise ValueError(f"band {name}: hold_s must be > 0, got {hold_s!r}")
        result[band] = BandSpec(interval_s=interval_s, hold_s=hold_s)
    return result


def _validate_attention(cfg: AttentionConfig) -> None:
    """Monotonicity, tick range. Raises ValueError on violation."""
    intervals = [cfg.bands[b].interval_s for b in (Band.HOT, Band.WARM, Band.COOL, Band.IDLE)]
    if intervals != sorted(intervals):
        raise ValueError(
            f"attention bands must be monotonic (HOT<=WARM<=COOL<=IDLE); got intervals {intervals}"
        )
    if cfg.tick_s <= 0:
        raise ValueError(f"attention.tick_s must be > 0, got {cfg.tick_s!r}")
    if cfg.tick_s > min(intervals):
        raise ValueError(
            f"attention.tick_s ({cfg.tick_s}) must be <= smallest band interval ({min(intervals)})"
        )


def _build_attention_config(raw: dict, legacy_poll_interval: int) -> AttentionConfig:
    raw_attention = raw.get("attention") or {}
    bands = _parse_bands(raw_attention.get("bands", {}), default_bands())
    # If no attention block was specified, migrate legacy poll_interval into IDLE.
    if "attention" not in raw and "poll_interval" in raw:
        bands[Band.IDLE] = BandSpec(interval_s=legacy_poll_interval, hold_s=None)
    cfg = AttentionConfig(
        enabled=raw_attention.get("enabled", True),
        tick_s=raw_attention.get("tick_s", 5),
        thread_window_s=raw_attention.get("thread_window_s", 1800),
        bands=bands,
    )
    _validate_attention(cfg)
    return cfg


def load_config(path: str | Path) -> DaemonConfig:
    """Load daemon config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server = ServerConnConfig(**raw.get("server", {}))
    supervisor = SupervisorConfig(**raw.get("supervisor", {}))
    webhooks = WebhookConfig(**raw.get("webhooks", {}))
    telemetry = TelemetryConfig(**raw.get("telemetry", {}))

    legacy_poll_interval = raw.get("poll_interval", 60)
    attention = _build_attention_config(raw, legacy_poll_interval)

    agents = []
    for agent_raw in raw.get("agents", []):
        # Pop attention before passing to AgentConfig so we control its name.
        per_agent_attention = agent_raw.pop("attention", None)
        agents.append(AgentConfig(**agent_raw, attention_overrides=per_agent_attention))

    return DaemonConfig(
        server=server,
        supervisor=supervisor,
        webhooks=webhooks,
        telemetry=telemetry,
        buffer_size=raw.get("buffer_size", 500),
        poll_interval=legacy_poll_interval,
        sleep_start=raw.get("sleep_start", "23:00"),
        sleep_end=raw.get("sleep_end", "08:00"),
        agents=agents,
        attention=attention,
    )


def resolve_attention_config(
    daemon_cfg: DaemonConfig, agent_cfg: AgentConfig
) -> AttentionConfig:
    """Merge per-agent attention overrides over daemon defaults."""
    if not agent_cfg.attention_overrides:
        return daemon_cfg.attention
    raw = agent_cfg.attention_overrides
    bands = _parse_bands(raw.get("bands", {}), daemon_cfg.attention.bands)
    merged = AttentionConfig(
        enabled=raw.get("enabled", daemon_cfg.attention.enabled),
        tick_s=raw.get("tick_s", daemon_cfg.attention.tick_s),
        thread_window_s=raw.get("thread_window_s", daemon_cfg.attention.thread_window_s),
        bands=bands,
    )
    _validate_attention(merged)
    return merged
```

- [ ] **Step 2.4: Run tests; they should pass**

```bash
cd packages/agent-harness && uv run --with pytest --with pytest-asyncio --with pyyaml pytest ../../tests/harness/test_attention_config.py -v
```
Expected: all tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add packages/agent-harness/config.py tests/harness/test_attention_config.py
git commit -m "feat(harness): AttentionConfig parsing, validation, per-agent merge (#345)

Adds attention: block to server.yaml with bands/tick_s/thread_window_s.
Per-agent attention: in culture.yaml shallow-merges over daemon defaults.
Legacy poll_interval migrates into idle.interval_s when no attention
block is present. Monotonicity, tick<=min(interval), and positive
intervals/holds enforced at load time.

- Claude"
```

---

### Task 3: Transport ambient/outgoing callbacks

**Files:**
- Modify: `packages/agent-harness/irc_transport.py`
- Test: extend `tests/harness/test_irc_transport_propagation.py` (already present)

- [ ] **Step 3.1: Add tests for new callbacks**

The file already has a `_make_transport(tracer=None)` helper (around line 112) that builds an `IRCTransport` with a fake `MessageBuffer` and the import-time BACKEND shim already in place. We'll reuse it. We need one new helper to construct a fake `Message` for the privmsg path.

Add this helper near the top of the file (after `_make_transport` and before the first test):

```python
from culture.protocol.message import Message  # noqa: E402


def _priv(sender: str, target: str, text: str) -> Message:
    """Build a synthetic PRIVMSG Message for the privmsg handler tests."""
    return Message(prefix=f"{sender}!~{sender}@host", command="PRIVMSG", params=[target, text])
```

Append these tests to `tests/harness/test_irc_transport_propagation.py`:

```python
def test_on_ambient_fires_for_non_mention_privmsg():
    received: list[tuple[str, str, str]] = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_ambient = lambda t, s, x: received.append((t, s, x))
    transport.on_mention = lambda t, s, x: None

    transport._on_privmsg(_priv("alice", "#dev", "hello world"))
    assert received == [("#dev", "alice", "hello world")]


def test_on_ambient_does_not_fire_for_mentions():
    received_ambient: list = []
    received_mention: list = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_ambient = lambda t, s, x: received_ambient.append((t, s, x))
    transport.on_mention = lambda t, s, x: received_mention.append((t, s, x))

    transport._on_privmsg(_priv("alice", "#dev", "hello @thor-claude"))
    assert received_ambient == []
    assert received_mention == [("#dev", "alice", "hello @thor-claude")]


def test_on_ambient_does_not_fire_for_dm():
    """A DM is always direct (target == own nick)."""
    received_ambient: list = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_ambient = lambda t, s, x: received_ambient.append((t, s, x))
    transport.on_mention = lambda *_: None

    transport._on_privmsg(_priv("alice", "thor-claude", "psst"))
    assert received_ambient == []


def test_on_ambient_skips_system_messages():
    received_ambient: list = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_ambient = lambda t, s, x: received_ambient.append((t, s, x))

    transport._on_privmsg(_priv("system-thor", "#dev", "user.join alice"))
    assert received_ambient == []


@pytest.mark.asyncio
async def test_on_outgoing_fires_after_send():
    received: list = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_outgoing = lambda t, line: received.append((t, line))
    _inject_rw(transport)  # capture writer so send_privmsg can complete

    await transport.send_privmsg("#dev", "hi")
    assert received == [("#dev", "hi")]
```

- [ ] **Step 3.2: Run new tests; they should fail**

```bash
cd packages/agent-harness && uv run --with pytest --with pytest-asyncio pytest ../../tests/harness/test_irc_transport_propagation.py -v
```
Expected: `AttributeError: 'IRCTransport' object has no attribute 'on_ambient'` for the new tests; existing tests should still PASS.

- [ ] **Step 3.3: Modify `packages/agent-harness/irc_transport.py` (`Diff C`)**

In the `IRCTransport.__init__`, add (next to the existing `on_mention`, `on_roominvite` callbacks):

```python
        self.on_ambient: Callable[[str, str, str], None] | None = None
        self.on_outgoing: Callable[[str, str], None] | None = None
```

(Add `Callable` to existing `from typing import ...` import if not already imported.)

In `_on_privmsg`, after the existing `_detect_and_fire_mention` call, add the ambient branch — but **only for channel messages where the agent was NOT mentioned**. The existing code already short-circuits DM into the mention path (DM = target is own nick), so we just need to detect "channel + no @nick + no @short" here:

```python
    def _on_privmsg(self, msg: Message) -> None:
        if len(msg.params) < 2:
            return
        target = msg.params[0]
        text = msg.params[1]
        sender = msg.prefix.split("!")[0] if msg.prefix else "unknown"
        if sender == self.nick:
            return
        if sender.startswith("system-"):
            return
        if target.startswith("#"):
            self.buffer.add(target, sender, text)
        else:
            self.buffer.add(f"DM:{sender}", sender, text)
        was_mention = self._detect_and_fire_mention(target, sender, text)
        # Ambient: channel message that was NOT a mention. DMs are always direct.
        if not was_mention and target.startswith("#") and self.on_ambient:
            self.on_ambient(target, sender, text)
```

Change `_detect_and_fire_mention` to **return a bool** indicating whether a mention was fired (so we don't double-count):

```python
    def _detect_and_fire_mention(self, target: str, sender: str, text: str) -> bool:
        """Check if the message mentions this agent and fire the callback.

        Returns True if a mention was detected (whether or not on_mention is set).
        """
        # DMs always activate
        if target == self.nick:
            if self.on_mention:
                self.on_mention(target, sender, text)
            return True
        short = self.nick.split("-", 1)[1] if "-" in self.nick else None
        if re.search(rf"@{re.escape(self.nick)}\b", text) or (
            short and re.search(rf"@{re.escape(short)}\b", text)
        ):
            if self.on_mention:
                self.on_mention(target, sender, text)
            return True
        return False
```

In `send_privmsg`, after the existing `await self._send_raw(f"PRIVMSG {target} :{line}")` and `self.buffer.add(...)` calls, fire `on_outgoing`:

```python
                await self._send_raw(f"PRIVMSG {target} :{line}")
                if target.startswith("#"):
                    self.buffer.add(target, self.nick, line)
                else:
                    self.buffer.add(f"DM:{target}", self.nick, line)
                if self.on_outgoing:
                    self.on_outgoing(target, line)
```

- [ ] **Step 3.4: Run tests; all should pass**

```bash
cd packages/agent-harness && uv run --with pytest --with pytest-asyncio pytest ../../tests/harness/test_irc_transport_propagation.py -v
```
Expected: all PASS, including new tests.

- [ ] **Step 3.5: Commit**

```bash
git add packages/agent-harness/irc_transport.py tests/harness/test_irc_transport_propagation.py
git commit -m "feat(harness): on_ambient/on_outgoing callbacks for attention tracking (#345)

on_ambient fires for non-mention channel PRIVMSGs. on_outgoing fires
after a successful send, used by the daemon to track 'I spoke on T
recently.' DMs remain direct-only. _detect_and_fire_mention now
returns a bool so the privmsg handler doesn't double-fire.

- Claude"
```

---

### Task 4: Daemon wiring + tick poll loop + tests

**Files:**
- Modify: `packages/agent-harness/daemon.py`
- Test: `tests/harness/test_daemon_attention_wiring.py`

- [ ] **Step 4.1: Write `tests/harness/test_daemon_attention_wiring.py`**

```python
"""Smoke test that the daemon wires the AttentionTracker correctly.

We don't spin up a real IRC server here — we use a fake transport and
buffer, drive stimuli synthetically, and assert _send_channel_poll is
called with the right cadence.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from attention import AttentionConfig, Band, BandSpec  # type: ignore
from config import AgentConfig, DaemonConfig  # type: ignore
from daemon import AgentDaemon  # type: ignore


def _fast_attention_config() -> AttentionConfig:
    """Subsecond bands so tests don't take minutes."""
    return AttentionConfig(
        enabled=True,
        tick_s=1,
        thread_window_s=60,
        bands={
            Band.HOT: BandSpec(interval_s=1, hold_s=2),
            Band.WARM: BandSpec(interval_s=2, hold_s=3),
            Band.COOL: BandSpec(interval_s=3, hold_s=4),
            Band.IDLE: BandSpec(interval_s=5, hold_s=None),
        },
    )


@pytest.mark.asyncio
async def test_direct_stimulus_promotes_target_to_hot():
    daemon_cfg = DaemonConfig(attention=_fast_attention_config())
    agent_cfg = AgentConfig(nick="thor-claude", channels=["#dev"])
    daemon = AgentDaemon(daemon_cfg, agent_cfg, skip_agent=True)

    # Inject the tracker without going through start()
    daemon._init_attention()
    daemon._on_mention("#dev", "alice", "@thor-claude please look")

    snap = daemon._attention.snapshot()
    assert snap["#dev"].band == Band.HOT


@pytest.mark.asyncio
async def test_ambient_only_promotes_when_thread_window_active():
    daemon_cfg = DaemonConfig(attention=_fast_attention_config())
    agent_cfg = AgentConfig(nick="thor-claude", channels=["#dev"])
    daemon = AgentDaemon(daemon_cfg, agent_cfg, skip_agent=True)
    daemon._init_attention()

    # Without prior engagement, ambient is a no-op
    daemon._on_ambient("#dev", "alice", "just chatting")
    assert "#dev" not in daemon._attention.snapshot()

    # After mention, the agent is "engaged" on #dev. Now ambient promotes.
    daemon._on_mention("#dev", "alice", "@thor-claude help")
    daemon._on_ambient("#dev", "alice", "actually nevermind")
    snap = daemon._attention.snapshot()
    # Already HOT from the mention — ambient does not demote
    assert snap["#dev"].band == Band.HOT


@pytest.mark.asyncio
async def test_outgoing_send_starts_thread_window():
    daemon_cfg = DaemonConfig(attention=_fast_attention_config())
    agent_cfg = AgentConfig(nick="thor-claude", channels=["#dev"])
    daemon = AgentDaemon(daemon_cfg, agent_cfg, skip_agent=True)
    daemon._init_attention()

    # Agent speaks first
    daemon._on_outgoing("#dev", "good morning")
    # Now ambient counts
    daemon._on_ambient("#dev", "alice", "morning!")
    assert daemon._attention.snapshot()["#dev"].band == Band.COOL


@pytest.mark.asyncio
async def test_legacy_poll_loop_used_when_attention_disabled():
    daemon_cfg = DaemonConfig(
        poll_interval=42,
        attention=AttentionConfig(enabled=False),
    )
    agent_cfg = AgentConfig(nick="thor-claude", channels=["#dev"])
    daemon = AgentDaemon(daemon_cfg, agent_cfg, skip_agent=True)
    daemon._init_attention()
    assert daemon._attention_enabled is False


@pytest.mark.asyncio
async def test_paused_agent_still_tracks_attention_state():
    """Spec: stimuli update tracker state even while paused, so the
    agent is correctly attentive on resume."""
    daemon_cfg = DaemonConfig(attention=_fast_attention_config())
    agent_cfg = AgentConfig(nick="thor-claude", channels=["#dev"])
    daemon = AgentDaemon(daemon_cfg, agent_cfg, skip_agent=True)
    daemon._init_attention()

    daemon._paused = True
    daemon._on_mention("#dev", "alice", "@thor-claude")

    snap = daemon._attention.snapshot()
    assert snap["#dev"].band == Band.HOT  # tracker updated despite pause
```

- [ ] **Step 4.2: Run tests; they should fail**

```bash
cd packages/agent-harness && uv run --with pytest --with pytest-asyncio --with pyyaml pytest ../../tests/harness/test_daemon_attention_wiring.py -v
```
Expected: `AttributeError: ... no attribute '_init_attention'`.

- [ ] **Step 4.3: Modify `packages/agent-harness/daemon.py` (`Diff D`)**

Add imports near the top:

```python
from culture.clients.BACKEND.attention import (
    AttentionConfig,
    AttentionTracker,
    Band,
    CAUSE_DECAY,
)
from culture.clients.BACKEND.config import resolve_attention_config
```

In `AgentDaemon.__init__`, after the existing `_last_activation` line, add:

```python
        # Attention state — initialized by _init_attention(), called from start()
        self._attention: AttentionTracker | None = None
        self._attention_enabled: bool = False
        self._last_engaged_at: dict[str, float] = {}
```

Add a new method after `__init__`:

```python
    def _init_attention(self) -> None:
        """Build the AttentionTracker from merged config. Called once at start."""
        attention_cfg = resolve_attention_config(self.config, self.agent)
        self._attention_enabled = attention_cfg.enabled
        self._attention = AttentionTracker(
            attention_cfg, on_transition=self._on_attention_transition
        )

    def _on_attention_transition(
        self, target: str, prev: Band, new: Band, cause: str
    ) -> None:
        """Logging + OTel hook for band transitions. (Metrics added in Task 5.)"""
        logger.info(
            "attention: agent=%s target=%s band=%s→%s cause=%s",
            self.agent.nick,
            target,
            prev.name,
            new.name,
            cause,
        )
```

In the `start()` method, just before the existing `self._poll_task = asyncio.create_task(self._poll_loop())` line, add:

```python
        self._init_attention()
```

Replace the existing `_poll_loop` (the one that does `await asyncio.sleep(interval)`) with:

```python
    async def _poll_loop(self) -> None:
        """Background task: tick-driven when attention.enabled, else legacy fixed-interval."""
        if not self._attention_enabled:
            await self._legacy_poll_loop()
            return
        attention_cfg = resolve_attention_config(self.config, self.agent)
        tick_s = attention_cfg.tick_s
        while True:
            try:
                await asyncio.sleep(tick_s)
                if self._paused or not self._agent_runner or not self._agent_runner.is_running():
                    continue
                now = time.monotonic()
                due = self._attention.due_targets(now) if self._attention else []
                for target in due:
                    self._send_channel_poll(target)
                    self._attention.mark_polled(target, now)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Poll loop error")

    async def _legacy_poll_loop(self) -> None:
        """Fixed-interval polling. Used when attention.enabled is false."""
        interval = self.config.poll_interval
        if interval <= 0:
            return
        while True:
            try:
                await asyncio.sleep(interval)
                self._process_poll_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Poll loop error")
```

Update `_on_mention` to record direct stimulus and engagement. Per the spec, **stimuli must update tracker state even while paused** so the agent is correctly attentive when it un-pauses; only the prompt-building/relay is gated by `_paused`:

```python
    def _on_mention(self, target: str, sender: str, text: str) -> None:
        # Always update attention state — even when paused — so the agent
        # is correctly warm on resume. Spec section "Error handling" row
        # for paused/sleep schedule.
        now = time.monotonic()
        self._last_engaged_at[target] = now
        if self._attention is not None:
            self._attention.on_direct(target, now)

        if self._paused:
            return
        self._last_activation = time.time()
        if target.startswith("#"):
            prompt = self._build_channel_prompt(target, sender, text)
        else:
            prompt = self._build_dm_prompt(sender, text)
        logger.info("@mention prompt (%d chars) from %s in %s", len(prompt), sender, target)
```

Add new handler methods (next to `_on_mention`):

```python
    def _on_ambient(self, target: str, sender: str, text: str) -> None:
        """Ambient stimulus — only counts if agent has engagement on this target.

        Updates tracker state even while paused (same reasoning as _on_mention).
        """
        if self._attention is None:
            return
        now = time.monotonic()
        thread_window_s = resolve_attention_config(self.config, self.agent).thread_window_s
        last = self._last_engaged_at.get(target, 0.0)
        if last == 0.0 or (now - last) > thread_window_s:
            return
        self._attention.on_ambient(target, now)

    def _on_outgoing(self, target: str, line: str) -> None:
        """Track that the agent has spoken on this target — opens thread window."""
        self._last_engaged_at[target] = time.monotonic()
```

In the `start()` method, where `self._transport = IRCTransport(...)` is constructed, add the new callback wirings (next to the existing `on_mention` assignment — likely lines just after the transport is created):

```python
        self._transport.on_mention = self._on_mention
        self._transport.on_ambient = self._on_ambient
        self._transport.on_outgoing = self._on_outgoing
```

(The first line should already exist; add the other two.)

- [ ] **Step 4.4: Run tests; they should pass**

```bash
cd packages/agent-harness && uv run --with pytest --with pytest-asyncio --with pyyaml pytest ../../tests/harness/test_daemon_attention_wiring.py -v
```
Expected: all PASS.

- [ ] **Step 4.5: Run the full harness suite to catch regressions**

```bash
uv run pytest tests/harness/ -v
```
Expected: all PASS. (If any fail, fix before continuing — likely a callback wiring or import path issue.)

- [ ] **Step 4.6: Commit**

```bash
git add packages/agent-harness/daemon.py tests/harness/test_daemon_attention_wiring.py
git commit -m "feat(harness): wire AttentionTracker into daemon (#345)

_on_mention -> tracker.on_direct + last_engaged_at[T].
New _on_ambient handler gated by thread_window_s engagement check.
New _on_outgoing handler updates last_engaged_at on every send.
_poll_loop becomes tick-driven; _legacy_poll_loop kept for
attention.enabled=false fallback. All transitions logged at INFO.

- Claude"
```

---

### Task 5: OTel metrics for attention transitions

**Files:**
- Modify: `packages/agent-harness/telemetry.py`
- Modify: `packages/agent-harness/daemon.py`
- Test: extend `tests/harness/test_daemon_attention_wiring.py`

- [ ] **Step 5.1: Add a metrics test**

Append to `tests/harness/test_daemon_attention_wiring.py`:

```python
@pytest.mark.asyncio
async def test_attention_transitions_emit_otel_counter(monkeypatch):
    """The transition callback increments culture.attention.transitions."""
    daemon_cfg = DaemonConfig(attention=_fast_attention_config())
    agent_cfg = AgentConfig(nick="thor-claude", channels=["#dev"])
    daemon = AgentDaemon(daemon_cfg, agent_cfg, skip_agent=True)

    counter = MagicMock()
    daemon._metrics = MagicMock()
    daemon._metrics.attention_transitions = counter
    daemon._metrics.attention_band = MagicMock()

    daemon._init_attention()
    daemon._on_mention("#dev", "alice", "@thor-claude")

    counter.add.assert_called_once()
    args, kwargs = counter.add.call_args
    assert args[0] == 1
    attrs = kwargs.get("attributes") or args[1]
    assert attrs["target"] == "#dev"
    assert attrs["from_band"] == "IDLE"
    assert attrs["to_band"] == "HOT"
    assert attrs["cause"] == "direct"
```

- [ ] **Step 5.2: Run; should fail**

```bash
uv run pytest tests/harness/test_daemon_attention_wiring.py::test_attention_transitions_emit_otel_counter -v
```
Expected: FAIL — `_metrics` has no `attention_transitions` and `_on_attention_transition` doesn't emit anything.

- [ ] **Step 5.3: Modify `packages/agent-harness/telemetry.py` (`Diff E`)**

Locate `init_harness_telemetry` (or the equivalent function that builds `_metrics`). Add three new instruments to the metrics object it returns:

```python
        # Attention metrics (issue #345)
        attention_band = meter.create_observable_gauge(
            name="culture.attention.band",
            description="Current attention band per target (0=HOT,1=WARM,2=COOL,3=IDLE)",
            callbacks=[],  # callback wired by daemon at start()
        )
        attention_transitions = meter.create_counter(
            name="culture.attention.transitions",
            description="Count of attention band transitions",
        )
        attention_polls = meter.create_counter(
            name="culture.attention.polls",
            description="Count of channel polls fired by the daemon",
        )
        # Attach to the returned metrics object — adapt to local convention:
        metrics.attention_band = attention_band
        metrics.attention_transitions = attention_transitions
        metrics.attention_polls = attention_polls
```

(If `telemetry.py` returns a `dict` rather than an object, set the keys instead.)

- [ ] **Step 5.4: Modify `packages/agent-harness/daemon.py` to emit metrics**

Replace `_on_attention_transition` body with:

```python
    def _on_attention_transition(
        self, target: str, prev: Band, new: Band, cause: str
    ) -> None:
        logger.info(
            "attention: agent=%s target=%s band=%s→%s cause=%s",
            self.agent.nick,
            target,
            prev.name,
            new.name,
            cause,
        )
        if self._metrics is not None and getattr(self._metrics, "attention_transitions", None):
            self._metrics.attention_transitions.add(
                1,
                attributes={
                    "agent": self.agent.nick,
                    "target": target,
                    "from_band": prev.name,
                    "to_band": new.name,
                    "cause": cause,
                },
            )
```

In the tick-driven branch of `_poll_loop`, emit the polls counter after `mark_polled`:

```python
                for target in due:
                    self._send_channel_poll(target)
                    self._attention.mark_polled(target, now)
                    if self._metrics is not None and getattr(self._metrics, "attention_polls", None):
                        self._metrics.attention_polls.add(
                            1,
                            attributes={
                                "agent": self.agent.nick,
                                "target": target,
                                "band": self._attention.snapshot()[target].band.name,
                            },
                        )
```

- [ ] **Step 5.5: Run tests; should pass**

```bash
uv run pytest tests/harness/test_daemon_attention_wiring.py -v
```
Expected: all PASS.

- [ ] **Step 5.6: Commit**

```bash
git add packages/agent-harness/telemetry.py packages/agent-harness/daemon.py tests/harness/test_daemon_attention_wiring.py
git commit -m "feat(harness): OTel counters for attention transitions and polls (#345)

culture.attention.transitions (counter) — agent, target, from/to band,
cause. culture.attention.polls (counter) — agent, target, band.
culture.attention.band gauge declared; per-target observable callback
to be wired in a follow-up if needed (we already get the same info
from transitions).

- Claude"
```

---

## Phase B — All-backends propagation

> **Reading note:** Phase A defined five concrete diffs labelled `Diff A` (attention.py), `Diff B` (config.py), `Diff C` (irc_transport.py), `Diff D` (daemon.py), and `Diff E` (telemetry.py). For each backend, you're applying the same five diffs to the cited copies. Diff A is a fresh copy with no edits; Diffs B–E are textual edits. Backend-specific imports already use the `culture.clients.<backend>.foo` form (see line 26 of `daemon.py` for the BACKEND placeholder), so the import-path adjustment for cited copies has already been done.

### Task 6: Propagate to `claude` backend

**Files:**
- Create: `culture/clients/claude/attention.py`
- Modify: `culture/clients/claude/config.py`
- Modify: `culture/clients/claude/irc_transport.py`
- Modify: `culture/clients/claude/daemon.py`
- Modify: `culture/clients/claude/telemetry.py`

- [ ] **Step 6.1: Copy `attention.py` (Diff A)**

```bash
cp packages/agent-harness/attention.py culture/clients/claude/attention.py
```

The module is self-contained — no imports to adjust. Verify:

```bash
diff packages/agent-harness/attention.py culture/clients/claude/attention.py
```
Expected: no output (files identical).

- [ ] **Step 6.2: Apply Diff B to `culture/clients/claude/config.py`**

Apply the same edits described in Phase A Task 2 Step 2.3 to this file. Adjust import lines:

- Replace the bare `from attention import (...)` with `from culture.clients.claude.attention import (...)`.
- The rest of the diff (dataclass field additions, `_parse_bands`, `_validate_attention`, `_build_attention_config`, `load_config` body, `resolve_attention_config`) is verbatim.

- [ ] **Step 6.3: Apply Diff C to `culture/clients/claude/irc_transport.py`**

Apply the edits described in Phase A Task 3 Step 3.3 verbatim. No import adjustments needed (already uses `Callable` and `re`).

- [ ] **Step 6.4: Apply Diff D to `culture/clients/claude/daemon.py`**

Apply the edits described in Phase A Task 4 Step 4.3 verbatim. The imports use `culture.clients.BACKEND.*` — those are already correct because the cited copy substitutes the literal backend name.

- [ ] **Step 6.5: Apply Diff E to `culture/clients/claude/telemetry.py`**

Apply the edits described in Phase A Task 5 Step 5.3 verbatim.

- [ ] **Step 6.6: Run the claude backend's tests**

```bash
uv run pytest tests/harness/test_agent_runner_claude.py -v
uv run pytest tests/harness/test_all_backends_parity.py -v
```
Expected: all PASS. The parity test will catch field/method drift between backends — if it fails, the backends are out of sync and the offending field needs to be added.

- [ ] **Step 6.7: Commit**

```bash
git add culture/clients/claude/
git commit -m "feat(claude): cite attention machine + wire daemon (#345)

Cites packages/agent-harness/attention.py byte-identically. Applies
the same config/transport/daemon/telemetry diffs as the reference
implementation. All-backends rule: claude propagation #1 of 4.

- Claude"
```

---

### Task 7: Propagate to `codex` backend

**Files:**
- Create: `culture/clients/codex/attention.py`
- Modify: `culture/clients/codex/{config,irc_transport,daemon,telemetry}.py`

- [ ] **Step 7.1: Copy `attention.py`**

```bash
cp packages/agent-harness/attention.py culture/clients/codex/attention.py
diff packages/agent-harness/attention.py culture/clients/codex/attention.py
```
Expected: no output.

- [ ] **Step 7.2–7.5: Apply Diffs B/C/D/E**

Apply the same edits described in Phase A Tasks 2/3/4/5 verbatim to the codex copies. The only adjustment is in `config.py`: change the bare `from attention import (...)` to `from culture.clients.codex.attention import (...)`.

- [ ] **Step 7.6: Run codex backend tests**

```bash
uv run pytest tests/harness/test_agent_runner_codex.py -v
uv run pytest tests/harness/test_all_backends_parity.py -v
```
Expected: all PASS.

- [ ] **Step 7.7: Commit**

```bash
git add culture/clients/codex/
git commit -m "feat(codex): cite attention machine + wire daemon (#345)

All-backends rule: codex propagation #2 of 4.

- Claude"
```

---

### Task 8: Propagate to `copilot` backend

**Files:**
- Create: `culture/clients/copilot/attention.py`
- Modify: `culture/clients/copilot/{config,irc_transport,daemon,telemetry}.py`

- [ ] **Step 8.1: Copy `attention.py`**

```bash
cp packages/agent-harness/attention.py culture/clients/copilot/attention.py
diff packages/agent-harness/attention.py culture/clients/copilot/attention.py
```
Expected: no output.

- [ ] **Step 8.2–8.5: Apply Diffs B/C/D/E**

Same as Task 7, with `from culture.clients.copilot.attention import (...)` in `config.py`.

- [ ] **Step 8.6: Run copilot backend tests**

```bash
uv run pytest tests/harness/test_agent_runner_copilot.py -v
uv run pytest tests/harness/test_all_backends_parity.py -v
```
Expected: all PASS.

- [ ] **Step 8.7: Commit**

```bash
git add culture/clients/copilot/
git commit -m "feat(copilot): cite attention machine + wire daemon (#345)

All-backends rule: copilot propagation #3 of 4.

- Claude"
```

---

### Task 9: Propagate to `acp` backend

**Files:**
- Create: `culture/clients/acp/attention.py`
- Modify: `culture/clients/acp/{config,irc_transport,daemon,telemetry}.py`

- [ ] **Step 9.1: Copy `attention.py`**

```bash
cp packages/agent-harness/attention.py culture/clients/acp/attention.py
diff packages/agent-harness/attention.py culture/clients/acp/attention.py
```
Expected: no output.

- [ ] **Step 9.2–9.5: Apply Diffs B/C/D/E**

Same as Task 7, with `from culture.clients.acp.attention import (...)` in `config.py`.

- [ ] **Step 9.6: Run acp backend tests**

```bash
uv run pytest tests/harness/test_agent_runner_acp.py -v
uv run pytest tests/harness/test_all_backends_parity.py -v
```
Expected: all PASS.

- [ ] **Step 9.7: Commit**

```bash
git add culture/clients/acp/
git commit -m "feat(acp): cite attention machine + wire daemon (#345)

All-backends rule: acp propagation #4 of 4.

- Claude"
```

---

## Phase C — Documentation

### Task 10: Write `docs/attention.md` and add pointers

**Files:**
- Create: `docs/attention.md`
- Modify: existing doc that mentions `poll_interval` (engineer searches `docs/` to find it)

- [ ] **Step 10.1: Create `docs/attention.md`**

```markdown
# Dynamic Attention Levels

Each agent in a culture mesh maintains a per-target attention state
machine that dictates how often it polls each watched channel/DM. The
machine has four bands — `HOT`, `WARM`, `COOL`, `IDLE` — each with its
own polling interval and decay-hold duration.

## Defaults

| Band | Poll interval | Hold duration |
|------|---------------|---------------|
| HOT  | 30 s          | 2 min         |
| WARM | 2 min         | 5 min         |
| COOL | 5 min         | 10 min        |
| IDLE | 10 min        | terminal      |

Total walk from HOT → IDLE under no further stimulus: 17 minutes.

## Stimuli

| Stimulus | Effect |
|----------|--------|
| `@mention` of the agent in a channel | promote target to HOT |
| Direct message to the agent | promote DM target to HOT |
| Non-mention message in a channel where the agent has spoken or been mentioned within `thread_window_s` (default 30 min) | promote one band warmer, capped at WARM |

Ambient stimuli never demote and never reach HOT.

## Configuration

### Daemon defaults — `~/.culture/server.yaml`

```yaml
attention:
  enabled: true
  tick_s: 5
  thread_window_s: 1800
  bands:
    hot:  { interval_s: 30,  hold_s: 120 }
    warm: { interval_s: 120, hold_s: 300 }
    cool: { interval_s: 300, hold_s: 600 }
    idle: { interval_s: 600 }
```

### Per-agent override — `culture.yaml`

```yaml
nick: spark-bot
channels: [#dev, #general]
attention:
  bands:
    hot:  { interval_s: 15, hold_s: 60 }
    idle: { interval_s: 1800 }
```

Per-agent values shallow-merge over daemon defaults: any band you specify
replaces that band's spec; unspecified bands inherit.

### Disabling

```yaml
attention:
  enabled: false
poll_interval: 60   # legacy fixed-interval polling
```

## Backwards compatibility

If `attention:` is absent from your config, defaults apply but
`idle.interval_s` is overridden to whatever your existing `poll_interval`
was. So existing deployments see no change in steady-state polling and
get faster polling for free when their agents are tagged.

## Observability

Each band transition is logged at INFO and emitted as an OTel counter:

```
culture.attention.transitions{agent, target, from_band, to_band, cause}
culture.attention.polls{agent, target, band}
```

`cause ∈ {direct, ambient, decay, manual}`.

## Future: agent-controlled attention

The state machine exposes `set(target, band)` for the upcoming
agent-controlled-attention feature ([#355](https://github.com/agentculture/culture/issues/355)).
Bands were chosen over a continuous decay function specifically so the
agent can pick an enum value via a tool call.

## Reference

- Spec: `docs/superpowers/specs/2026-05-08-dynamic-attention-levels-design.md`
- Implementation: `packages/agent-harness/attention.py` (cited into each backend)
```

- [ ] **Step 10.2: Find and update the existing doc that mentions `poll_interval`**

```bash
grep -l "poll_interval" docs/
```

Open the file(s) returned. Add this line near the top of any section that describes fixed-interval polling:

```markdown
> Polling is now driven by per-target attention bands.
> See [Dynamic Attention Levels](./attention.md) for the full model.
> The legacy `poll_interval` field still works as a fallback when
> `attention.enabled: false`.
```

- [ ] **Step 10.3: Commit**

```bash
git add docs/attention.md docs/<file-modified>.md
git commit -m "docs: dynamic attention levels reference (#345)

- Claude"
```

---

## Phase D — Verification & release

### Task 11: doc-test-alignment subagent

- [ ] **Step 11.1: Run the audit**

Invoke the `doc-test-alignment` subagent on the staged branch diff:

```
Agent(subagent_type="doc-test-alignment",
      prompt="Audit branch feat/dynamic-attention-levels for #345.
              New public API: AttentionTracker, AttentionConfig, Band,
              BandSpec, on_ambient/on_outgoing transport callbacks,
              resolve_attention_config(), 'attention' config block.
              Confirm docs/attention.md covers all of it; flag any
              missing protocol/extensions/ pages and any all-backends
              drift between claude/codex/copilot/acp.")
```

- [ ] **Step 11.2: Address any findings**

If the subagent flags missing coverage or drift, fix and commit. If it returns clean, proceed.

---

### Task 12: Full test suite + version bump

- [ ] **Step 12.1: Run the full test suite with coverage**

Invoke `/run-tests --ci` (per the project's testing convention).

Expected: all PASS, coverage report generated.

- [ ] **Step 12.2: Bump version**

Invoke `/version-bump minor` (this is a feature addition, not a bug fix).

Expected: `pyproject.toml`, `culture/__init__.py`, `CHANGELOG.md`, `uv.lock` all updated, with the changelog entry summarizing #345.

- [ ] **Step 12.3: Commit version bump**

```bash
git add pyproject.toml culture/__init__.py CHANGELOG.md uv.lock
git commit -m "chore: bump version for dynamic attention levels (#345)

- Claude"
```

- [ ] **Step 12.4: Push and open PR**

```bash
git push -u origin feat/dynamic-attention-levels
gh pr create --title "feat: dynamic attention levels (#345)" --body "$(cat <<'EOF'
## Summary

Implements per-target attention state machine described in #345. Each
agent now polls fast (~30s) on channels where it has been tagged or is
participating in a thread, and slow (~5–10 min) on quiet channels. Decay
walks one band per hold window down to IDLE.

Closes #345. Follow-up agent-controlled-attention work tracked in #355.

Spec: \`docs/superpowers/specs/2026-05-08-dynamic-attention-levels-design.md\`
Docs: \`docs/attention.md\`

## Changes

- New \`AttentionTracker\` state machine (pure, deterministic, byte-identical
  across backends per the all-backends rule).
- \`attention:\` block in \`server.yaml\` with bands/tick_s/thread_window_s.
  Per-agent overrides in \`culture.yaml\` shallow-merge.
- Legacy \`poll_interval\` migrates into \`idle.interval_s\` when no
  \`attention\` block present — strict win for existing deployments.
- Two new transport callbacks (\`on_ambient\`, \`on_outgoing\`).
- OTel counters \`culture.attention.transitions\` and
  \`culture.attention.polls\`.

## Test plan

- [ ] State machine unit tests pass
- [ ] Config parsing/validation/merge tests pass
- [ ] Transport callback tests pass
- [ ] Daemon wiring smoke tests pass
- [ ] Per-backend tests pass for claude/codex/copilot/acp
- [ ] All-backends parity test passes
- [ ] doc-test-alignment subagent reports clean
- [ ] /sonarclaude on the branch shows no new findings before merge
- [ ] Manual: run a daemon, tag the agent in a channel, confirm INFO
      log shows IDLE→HOT transition

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Notes for the executor

- **Order matters within Phase A** (Task 1 → 2 → 3 → 4 → 5) because each task imports/calls types defined in the previous one.
- **Phase B tasks are independent** of one another and can be parallelized if you have the appetite — each backend is its own commit.
- **TDD discipline:** write the test, see it fail, write the impl, see it pass. Don't skip the "see it fail" step — it's how you confirm the test would have caught a regression.
- **Frequent commits:** every task ends with a commit. If a task is taking longer than expected, look for a commit-able subtask within it.
- **All-backends rule:** if a Phase B task fails the parity test, the right fix is almost always "the diff in this backend's daemon.py drifted from the reference; re-apply Diff D verbatim."
- **The `_send_channel_poll` already exists** — it's at line 284 of `packages/agent-harness/daemon.py`. The tick loop just calls it; no changes needed there.
