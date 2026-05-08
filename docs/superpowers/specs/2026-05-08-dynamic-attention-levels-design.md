# Dynamic Attention Levels for Agents

**Date:** 2026-05-08
**Issue:** [#345 — Agents: Awareness levels should be dynamic](https://github.com/agentculture/culture/issues/345)
**Status:** Design — awaiting plan

## Problem

Today every agent harness polls each watched channel on a fixed cadence
(`poll_interval`, default 60s). The interval is the same whether the agent was
just tagged in a live conversation or has been quietly idle for hours.

The issue asks for the obvious refinement: poll fast (~30s) when the agent has
been pulled into a conversation, slow (~5–10 min) when nothing is happening,
with a smooth decay between the two. This both reduces wasted LLM turns when
nothing is happening *and* lets agents react more promptly when they are.

A secondary motivation, called out by the user, is that this is a warm-up for a
follow-up feature where the agent itself can choose its attention level via a
tool. Discrete bands (rather than a continuous decay function) make that future
hand-off straightforward — the agent just picks an enum value.

## Goals

- Replace the single `poll_interval` with a per-target attention state machine.
- Direct addressing (@mention, DM) → maximum attention, immediately.
- Ambient activity in a thread the agent is participating in → moderate
  attention, never maximum.
- Quiet targets decay back to idle on a deterministic schedule.
- Defaults that are a strict win for existing deployments — no opt-in needed,
  no behavior regression for quiet channels.
- Pure state machine, deterministic and unit-testable, backend-independent so
  the cite-don't-import workflow lands cleanly across `claude`, `codex`,
  `copilot`, `acp`.

## Non-goals

- Agent-controlled attention (the agent calling `set_attention(target, band)`
  via a tool). The state machine will expose the entry point but no tool/CLI
  surface is added here. Tracked as a follow-up issue.
- Per-channel attention overrides in YAML (e.g., "always keep #ops at HOT").
  Could be added later by extending the same config schema; out of scope.
- Smooth/continuous decay functions (exponential, linear). Bands were chosen
  for predictability, observability, and ease of agent control later.

## Conceptual model

Each agent maintains an attention state machine **per target**, where a target
is a channel (`#dev-chat`) or a DM peer nick. State per target:

```python
@dataclass
class TargetState:
    band: Band               # HOT | WARM | COOL | IDLE
    last_promote_at: float   # monotonic seconds, used for decay
    last_stimulus_at: float  # for observability/debug
    last_poll_at: float      # for tick scheduling
```

### Bands

| Band | Default `interval_s` | Default `hold_s` | Notes |
|------|----------------------|------------------|-------|
| HOT  | 30                   | 120              | Direct addressing |
| WARM | 120                  | 300              | Active thread, ambient traffic |
| COOL | 300                  | 600              | Cooling down |
| IDLE | 600                  | — (terminal)     | Quiet steady state |

`interval_s` is how often the poll loop checks this target while in this band.
`hold_s` is how long the target stays in this band with no further stimulus
before decaying one band step. IDLE is terminal: no further decay.

### Promotion rules

On a stimulus arriving for target `T` at time `now`:

| Stimulus class | Effect |
|----------------|--------|
| Direct (`@me` mention or DM addressed to me) | `band[T] = HOT`, `last_promote_at = now` |
| Ambient (non-mention message in a thread/channel I'm participating in) | `band[T] = clamp(one_step_warmer(band[T]), floor=HOT, cap=WARM)`; `last_promote_at = now` |

Concretely, ambient transitions are:

| Current band | After ambient stimulus |
|--------------|------------------------|
| IDLE         | COOL                   |
| COOL         | WARM                   |
| WARM         | WARM (capped)          |
| HOT          | HOT (already warmer than the cap; no demotion) |

Ambient stimuli **never promote above WARM** and never demote. A thread where
the agent has previously spoken keeps the agent attentive but not at maximum.

`last_stimulus_at = now` is always refreshed.

### Decay rules

Evaluated on every tick of the poll loop (see Section "Poll loop"):

```
if band != IDLE and (now - last_promote_at) > hold(band):
    band = next_cooler(band)
    last_promote_at = now
```

Decay walks one band per `hold(band)` window of silence. So an undisturbed HOT
target reaches IDLE after `hot.hold + warm.hold + cool.hold` seconds — by
default, `120 + 300 + 600 = 17 minutes`.

### Thread participation predicate

"Participating in a thread on target T" = "I have spoken or been mentioned on T
within `attention.thread_window_s` seconds (default 1800)." This piggybacks on
the existing thread-tracking already used by `_on_mention` to build
thread-scoped prompts. A message arriving on T that does **not** mention me
counts as an ambient stimulus iff the predicate holds.

## Code structure

### New module: `packages/agent-harness/attention.py`

```python
class Band(IntEnum):
    HOT = 0
    WARM = 1
    COOL = 2
    IDLE = 3

@dataclass
class BandSpec:
    interval_s: int
    hold_s: int | None  # None for terminal IDLE

@dataclass
class AttentionConfig:
    enabled: bool = True
    tick_s: int = 5
    thread_window_s: int = 1800
    bands: dict[Band, BandSpec] = field(default_factory=_default_bands)

@dataclass
class TargetState:
    band: Band = Band.IDLE
    last_promote_at: float = 0.0
    last_stimulus_at: float = 0.0
    last_poll_at: float = 0.0

class AttentionTracker:
    """Pure state machine. No I/O, no asyncio."""
    def __init__(self, config: AttentionConfig): ...
    def on_direct(self, target: str, now: float) -> Band: ...
    def on_ambient(self, target: str, now: float) -> Band: ...
    def set(self, target: str, band: Band, now: float) -> None:
        """Reserved for future agent-controlled attention."""
    def due_targets(self, now: float) -> list[str]:
        """Apply decay, then return targets where (now - last_poll_at) >= interval(band)."""
    def mark_polled(self, target: str, now: float) -> None: ...
    def snapshot(self) -> dict[str, TargetState]: ...
```

The tracker has **no** I/O, no asyncio, no transport coupling. All time is
passed in, which makes unit tests trivial: advance a fake `now`, assert
transitions.

### Wiring in `daemon.py`

Three integration points:

1. **Direct stimulus** — in `_on_mention(target, sender, text)`:

   ```python
   self._attention.on_direct(target, time.monotonic())
   ```

   This already runs for both `@me` mentions in channels and DMs.

2. **Ambient stimulus** — in the IRC transport's PRIVMSG handler that feeds
   `self._buffer`. Today this path also detects "is this for me." Add a branch:
   if the message is *not* a direct stimulus but the target is one I am
   participating in (per the thread-window predicate), call:

   ```python
   self._attention.on_ambient(target, time.monotonic())
   ```

3. **Poll loop** — replace the fixed `await asyncio.sleep(interval)` with a
   tick:

   ```python
   async def _poll_loop(self) -> None:
       if not self._attention_enabled:
           return await self._legacy_poll_loop()
       tick_s = self.config.attention.tick_s
       while True:
           try:
               await asyncio.sleep(tick_s)
               if self._paused or not self._agent_runner.is_running():
                   continue
               now = time.monotonic()
               for target in self._attention.due_targets(now):
                   self._send_channel_poll(target)
                   self._attention.mark_polled(target, now)
           except asyncio.CancelledError:
               raise
           except Exception:
               logger.exception("Poll loop error")
   ```

   `due_targets` performs decay internally before returning. Tick is a single
   coroutine — no per-target tasks, no scheduler complexity. At our scale
   (handful of channels per agent) this is plenty.

### Cite-don't-import propagation

Per the all-backends rule, `attention.py` lives in `packages/agent-harness/`
and is **cited** (copied) into each backend's `culture/clients/<backend>/`
directory. Each backend's `daemon.py` is updated to wire the tracker. The
state-machine module is intended to be byte-identical across backends; only
the `daemon.py` integration may diverge per backend if the underlying
transport/buffer differs.

## Configuration

### Schema

Daemon-level defaults in `~/.culture/server.yaml`:

```yaml
poll_interval: 60   # DEPRECATED, used only when attention.enabled is false
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

Per-agent override in the agent's `culture.yaml`:

```yaml
nick: spark-culture
channels: [#dev, #general]
attention:
  bands:
    hot:  { interval_s: 15, hold_s: 60 }
    idle: { interval_s: 1800 }
```

### Merge semantics

Per-agent `attention.bands` shallow-merges over daemon defaults: a band
specified in `culture.yaml` *replaces* the daemon's spec for that band;
unspecified bands inherit. Other `attention.*` fields (`enabled`, `tick_s`,
`thread_window_s`) override directly when set.

Setting `attention.enabled: false` at either level disables the feature for
that agent and falls back to fixed-interval polling using the legacy
`poll_interval`.

### Validation (rejected at config load)

- `interval_s > 0` for all bands; `hold_s > 0` for non-IDLE bands.
- IDLE must not have `hold_s` (warning, value ignored).
- Bands must be monotonic: `hot.interval_s ≤ warm.interval_s ≤ cool.interval_s
  ≤ idle.interval_s`. Non-monotonic bands break the decay model.
- `tick_s ≤ min(band.interval_s)` — otherwise polls fire late.

### Migration / backwards compatibility

1. **No `attention` key anywhere** → defaults from the table above are used,
   with one tweak: `idle.interval_s = poll_interval` if `poll_interval` is
   explicitly set in config. Existing deployments see no change in steady-state
   polling; they simply get faster polling when their agents are tagged. No
   opt-in required.
2. **`attention.enabled: false`** → fixed-interval mode using legacy
   `poll_interval`. Escape hatch.
3. At startup, log one INFO line per agent: `attention: enabled=true
   bands=[hot=30/120 warm=120/300 cool=300/600 idle=600] override=yes` so
   operators can confirm what's running.

## Observability

### Logging

One INFO line per band transition, per target. Quiet during ticks.

```
attention: agent=spark-culture target=#dev-chat band=COOL→HOT cause=direct
attention: agent=spark-culture target=#dev-chat band=HOT→WARM cause=decay silence_s=121
attention: agent=spark-culture target=#dev-chat band=WARM→WARM cause=ambient (refresh)
```

### OTel metrics

Emitted via the existing `TelemetryConfig` wiring:

- `culture.attention.band` — gauge (0–3 from `Band`), attribute `target`,
  `agent`. Current band per target.
- `culture.attention.transitions` — counter, attributes `target, agent,
  from_band, to_band, cause`. `cause ∈ {direct, ambient, decay, manual}`
  (`manual` reserved for the future agent-controlled feature).
- `culture.attention.polls` — counter, attributes `target, agent, band`.
  Replaces the implicit "every 60s" cadence.

## Error handling

| Failure | Response |
|---------|----------|
| `attention.bands` malformed in config | Refuse to start; raise `ValueError` naming offending band/field. Same severity as malformed `server.host`. |
| Non-monotonic bands | Refuse to start (validation in `load_config`). |
| Tick loop raises | Log with `exception`, sleep `tick_s`, continue. State machine is pure so a bad tick can't corrupt it. |
| Stimulus arrives for an unknown target | Lazy-init `TargetState` at IDLE, then promote. No error. |
| `_send_channel_poll` raises for one target | Caught locally; other targets in the same tick still get served. |
| Agent paused (`self._paused`) or sleep schedule active | Stimuli still update tracker state, but `due_targets` returns `[]`. State stays warm so on un-pause the agent is correctly attentive. |
| Clock jump backward (NTP) | Clamp `(now - last_promote_at)` to ≥ 0 in decay computation. |

## Testing

### Unit tests — `tests/agent_harness/test_attention.py`

State-machine tests with a fake `now`:

- direct stimulus from IDLE → HOT
- ambient from IDLE → COOL (one step warmer)
- ambient from COOL → WARM
- ambient from WARM → WARM (capped, no change)
- ambient from HOT → HOT (no demotion)
- direct from WARM/COOL/IDLE → HOT
- decay walks HOT → WARM → COOL → IDLE on schedule
- stimulus during HOT refreshes `last_promote_at` (extends dwell)
- per-target isolation (HOT in #a doesn't affect IDLE in #b)
- clock jump backward doesn't misbehave
- paused agent: stimuli tracked, `due_targets` returns `[]`
- `mark_polled` updates `last_poll_at` independently of band

### Config tests — `tests/agent_harness/test_attention_config.py`

- shallow merge of per-agent over daemon
- monotonicity rejection
- legacy `poll_interval` honored when `attention.enabled: false`
- legacy `poll_interval` migrates into `idle.interval_s` when no `attention`
  block is present

### Integration — `tests/agent_harness/test_daemon_attention_wiring.py`

A small end-to-end test using a fake transport: feed stimuli, advance time,
assert `_send_channel_poll` cadence matches band intervals.

### All-backends regression

Per the all-backends rule, the test files above are cited into each backend's
test tree, and the existing per-backend `daemon.py` tests get a smoke check
that the band-driven path is wired up. A feature in only one backend is a bug.

## Documentation

- New `docs/attention.md` — model, defaults, override examples, deprecation of
  fixed `poll_interval`.
- One-line pointer added to existing `docs/` pages that mention fixed polling.
- `protocol/extensions/` — no protocol changes (this is harness-side only); no
  new IRC verbs. The `doc-test-alignment` subagent should confirm no extension
  page is needed.

## Follow-ups (not in this work)

- **Agent-controlled attention** (the warm-up). Open a separate issue
  referencing this spec. Surface: a tool the agent can call, e.g.,
  `set_attention(target, band, ttl_s=None)`, plus `culture` CLI introspection
  (`culture agent attention <nick>`). State-machine entry point
  (`tracker.set(...)`) is already present after this work, so the follow-up is
  surface-only.
- Per-channel pinned bands ("always keep #ops at HOT regardless of stimuli").
  Trivial extension to the merge schema.
