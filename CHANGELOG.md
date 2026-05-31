# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [8.19.33] - 2026-06-01

### Added — `culture boss watch <nick>` codifies the SKILL.md Monitor recipe

The culture-boss SKILL.md baked in a ``Monitor`` command pattern that
tail -F's both ``~/.culture/audit/local-<nick>.jsonl`` and
``~/.culture/daemon-log/local-<nick>.jsonl`` with a grep filter for
significant events (assistant turns, agent_exit/stop/crash, stalled
classes). Codifying it as a CLI verb saves the boss from typing the
filter regex and routes through the same team-isolation gate as
``audit`` / ``log`` / ``approve``.

Surface::

    culture boss watch <nick> [--limit N] [--follow]

- Default: print last N (default 20) significant events, exit.
- ``--follow``: poll-loop until Ctrl-C; tracks last-seen ts per
  stream so a daemon restart's trailing-fsync window doesn't
  re-emit lines.

Significance criteria mirror the SKILL.md regex: every
``AssistantMessage`` from audit + a curated set of daemon-log
lifecycle events (start / exit / stop / crash / idle / stalled). The
routine noise (compact, whisper, engaged, model_resolved) is filtered
to keep the relay legible.

Tests: ``tests/test_boss_cli.py::TestBossWatchCmd`` — interleaved-ts
ordering, limit truncates to most-recent, routine-actions filtered.

## [8.19.25] - 2026-05-31

### Fixed — SDK inactivity hangs the agent runner

The `async for message in query(...)` loop in
`culture/clients/claude/agent_runner.py` had no per-message timeout.
When the SDK pipe wedged (Stream-closed, hung MCP tool result,
dropped subprocess stream), the daemon sat in `__anext__()`
indefinitely — the v8.18.4 / v8.19.8 watchdogs observed the silence
via daemon-log but had no way to act on it. Caught live during the
UX-eval worker run: an `mcp__playwright__browser_take_screenshot`
returned the screenshot to disk but the SDK never yielded the next
message; daemon stalled for 5 minutes before the watchdog logged
`stalled_post_engagement`.

Fix: wrap `__anext__()` in `asyncio.wait_for(timeout=180s)`. On
timeout, raise `RuntimeError("SDK stream inactivity timeout ...")`
which hits the existing `except Exception` block — fires
`on_turn_failed`, then `on_exit(1)`, daemon's crash-recovery
restarts the session. The watchdog goes from advisory to
self-healing.

Configurable via `CULTURE_SDK_INACTIVITY_TIMEOUT` env var (seconds,
float). Default 180s — comfortably above real assistant turns
including thinking + long tool calls, low enough to recover within
a few minutes when the SDK genuinely wedges.

### Added — UX evaluation of the dashboard

`docs/v8.19.24-ux-evaluation.md` — 140-line evaluation authored by
`local-ux-eval-w` (mesh worker, after restart with the v8.19.25
fix). 8 ranked findings covering the user's "hardly any information
here about the channel" complaint:

| # | Severity | Finding |
|---|---|---|
| 1 | Critical | No timeline info (started / last-active) |
| 2 | Critical | No task purpose visible at a glance |
| 3 | High | No health / stuck indicators |
| 4 | High | No per-channel pending-permissions count |
| 5 | Medium | No living brief preview on Channel cards |
| 6 | Medium | Channel title truncation hides context |
| 7 | Low | No cost conversion for tokens |
| 8 | Low | Second channel group lacks token badge |

Each finding has SYMPTOM / ROOT CAUSE / PROPOSED FIX with concrete
file:line references. Screenshot at
`docs/v8.19.24-ux-eval-channels.png`.

The findings drive the next dashboard iteration: add `started_at`
and `last_activity` to the task dicts, surface watcher state on
the Channel heading, add a brief-preview subtitle.

## [8.19.24] - 2026-05-31

### Added — living channel brief (worker onboarding)

User flagged: *"does a new worker get briefed on what has transpired,
like a new employee or team mate joining a project gets? Maybe we
should keep an updating brief for each channel?"*

A new worker spawned into `#task-<name>` used to read the last N
IRC HISTORY lines and that was its entire context. The v8.19.18
seed is **write-once initial** (the original mission). This release
adds the living sibling.

- New `culture/clients/_channel_brief.py` — persistence + read API
  for `~/.culture/briefs/<channel-without-hash>.md`. Each call
  appends a dated `## YYYY-MM-DD HH:MMZ — <title>` Markdown section.
- **`culture boss brief` auto-appends** on every successful send,
  building the running onboarding doc as work happens.
- **New `culture boss note <channel-or-name> "<text>" [--title T]`**
  for explicit non-task updates (decisions, status, gotchas).
  Accepts a channel name or worker suffix.
- **All 4 backends inject the brief into the SDK system prompt** on
  worker boot. For each channel in the agent's config that has a
  brief, the daemon appends a "Joining channel — current state"
  section so the worker boots already onboarded. Wrapped with
  framing so the worker treats it as context, not as a current
  instruction.
- **Read cap of 64 KiB** — runaway briefs read as the TAIL with
  early history elided. Can't blow a worker's context window.
- **Idempotence guard**: identical body in the same minute window
  is skipped (defends against duplicate brief fires).
- **Dashboard endpoint** `/api/channels/<name>/brief` returns the
  full text + bytes; 404 on missing.
- Path-traversal-safe channel→filename validation.

### Tests

- 14 new in `tests/test_channel_brief.py`: roundtrip, append,
  empty-body skip, unsafe-name guard, has_brief, clear idempotent,
  idempotence within minute, different body, read-cap truncation,
  system-prompt-extension framing, file under culture_home,
  traversal rejected.
- 3 new in `tests/test_dashboard.py::TestChannelBrief`: 404 / 200 /
  400.

## [8.19.23] - 2026-05-31

### Fixed — orchestrator-friction bugs

A fresh Claude Code session asked to drive a worker via the
documented `culture boss …` flow had to debug the harness instead of
using it. Captured the friction in
`docs/v8.19.22-orchestrator-friction.md` and fixed the top four
items.

- **Manifest-warning flood.** `resolve_agents` printed one
  `WARNING culture.yaml missing for <nick>` per stale manifest entry
  on every CLI invocation — 10+ lines of noise on a long-running
  mesh, drowning real output. Now: per-entry detail logged at
  `DEBUG`, ONE summary line at `WARNING` saying how many entries
  point at missing paths and what command cleans them up. New test
  `test_resolve_agents_many_missing_yields_one_summary_warning`.
- **`culture boss spawn` printed a confusing multi-agent error.**
  Spawning a worker into a cwd whose `culture.yaml` defines multiple
  agents printed `Multiple agents in <path>. Use --suffix...` then
  continued anyway. The worker came up correctly but the operator
  thought it had failed. Fixed by passing `--suffix=<name>` through
  to the underlying `culture agent register` call — spawn already
  knows the suffix (it's the worker name).
- **`culture boss brief` failed with no recovery path.**
  Without the boss daemon running, brief returned
  `Error: could not brief <nick> (is the boss daemon running?)`.
  True, but the orchestrator now has to know that "boss daemon" is
  distinct from "boss identity" AND that `culture boss status`
  doesn't surface the daemon state. Error message now prints the
  exact fix command:
  `Start it with: culture agent start <boss>`.
- **`culture boss status` was silent on the boss's own daemon.**
  The worker table didn't surface whether `brief`/`approve`/`deny`
  could actually run. New leading `BOSS <nick> <state> <pid>` row,
  with an inline hint when the daemon is down. `sys.stdout.flush()`
  before the subprocess so the row prints first under pipes.

### Captured (not fixed yet)

- `culture boss init` — single command that ensures server + boss
  daemon are up. Tracked in `docs/v8.19.22-orchestrator-friction.md`
  item #5.
- Living per-channel brief (`~/.culture/briefs/<channel>.md`) so
  new workers get onboarded with current project state rather than
  just IRC HISTORY. User-flagged as the more important sibling to
  the v8.19.18 seed (which is write-once initial only). Tracked as
  item #6. Coming as v8.19.24.

## [8.19.22] - 2026-05-31

### Changed — Channel data-model reframe (user clarification)

User flagged that calling each `#task-<worker>` a "task" was
confusing — *the Channel IS the Task*. Inside one Channel are
several rooms: the group chat, the boss board, and one private
boss↔worker dialog per worker. Adopted that model:

- Group heading now reads `CHANNEL <task title>` with the
  channel-level token total prominently rendered.
- Channel-level `tokens_total` counts each member exactly once
  via a unique-nicks set so the boss isn't N-times counted.
- Per-`#task-<worker>` room tag changed from `TASK` to `WORKER`.
- Per-card `tokens_total` demoted to a subtle grey sub-cost; the
  primary total lives on the Channel heading.

### Fixed

- **Chat tab showed empty for many rooms.** Card-body click
  selected the agent and fetched `/api/channel/<agent>` — which
  resolves to that agent's home `#task-<worker>`, NOT the room
  the user clicked. So clicking `#team` showed an empty
  `#task-<first-member>` instead of the `#team` history.
  New `state.selectedChannel` carries the clicked room. Card
  click sets it (route to room); chip click clears it (revert
  to agent's home). `refreshChat` routes through
  `/api/channels/<name>/messages` when set, falling back to
  `/api/channel/<nick>` otherwise. Live-verified: clicking
  `#team` → 50 messages, `#boss` → 11 messages.
- **Stream tabs + title now sticky** at the top of the centre
  column (`position: sticky` on `#stream-title` and
  `.stream-tabs`). Scrolling deep into a long chat no longer
  hides Activity / Daemon actions / Chat.

### Tests

- 2 new in `tests/test_dashboard_seed_integration.py`:
  channel-level `tokens_total` uniqueness (boss not
  double-counted across rooms), per-`#task-<worker>` category
  is `"worker"`.

## [8.19.21] - 2026-05-31

### Added

- **Per-agent + per-task token-usage badges in the dashboard.**
  Each channel card now shows a total-tokens badge in the header
  (sum of every member's cumulative usage) and each member chip
  shows its own contribution. The header badge is the *task total*
  per the user's clarification that channel ≈ task — the grouping
  heading (`<boss>'s work`) is one level higher.
- New `culture/clients/_usage.py`. Persists per-turn token records
  to `~/.culture/usage/<nick>.jsonl` — one line per LLM turn,
  shape: `{"ts": "...", "in": N, "out": M, "model": "..."}`.
  Helpers: `record_turn_usage` / `record_turn_usage_sync` /
  `sum_tokens` / `clear_usage` / `usage_path_for`. Disk write
  errors are swallowed — usage is advisory, must NOT block the
  agent loop.
- Wired through all 4 backends per the all-backends rule
  (`culture/clients/claude/agent_runner.py`,
  `culture/clients/codex/agent_runner.py`,
  `culture/clients/copilot/agent_runner.py`,
  `culture/clients/acp/agent_runner.py`). Claude actively records
  `tokens_input`/`tokens_output` from the SDK `ResultMessage.usage`;
  codex/copilot/acp pass `None`/`None` today (no-op) until their
  SDKs expose token counts (issues #298 / #299) — the call sites
  stay so when usage arrives, only the dict needs to be threaded.
- `list_tasks` (`culture/dashboard/server.py`) stamps every
  member with `tokens_used` / `tokens_in` / `tokens_out` and every
  channel with `tokens_total`. Internal per-call memoization
  prevents reading the boss's usage file once per channel (the
  boss is in every channel of its task).
- Frontend (`app.js`) renders `.channel-tokens-total` at the top
  of each card (only when > 0) and `.token-badge` per member chip
  (only when > 0). New `formatTokens` helper: 999→`999t`,
  12 345→`12.3k`, 4 567 890→`4.6M`.

### Fixed

- **Card click stuck on `local-boss` regardless of which task you
  clicked.** Previously every `#task-*` card defaulted to the boss
  member (the boss is in every task channel) AND auto-switched
  the stream tab to Chat, so the Activity tab never changed
  content when the user clicked different tasks. Now the card
  click picks the FIRST WORKER (non-boss member) and PRESERVES
  the user's currently-active stream tab — clicking a task while
  on Activity now actually shows that task's worker's activity.
  Live-verified via st4ck browse: `#task-builder` →
  `local-builder · Activity`, `#task-research` →
  `local-research · Activity`.

### Tests

- 12 new tests in `tests/test_usage.py`: record + sum roundtrip,
  multi-turn append, both-None no-op, input-only / output-only
  paths, negative-value rejected, missing file → zeros, malformed
  line skip, clear idempotent, file under culture_home, async
  wrapper, OSError swallowed.
- 4 new tests in `tests/test_dashboard_seed_integration.py`:
  per-member tokens_used stamping, channel tokens_total sum,
  zero-when-no-records, per-nick cache prevents N reads of the
  boss's file inside one `list_tasks` call.
- Live-verified token rendering in real browser: 23 of 26 cards
  show totals when seeded with realistic data; per-chip badges
  populated; format readable (`245k`, `380k`).

## [8.19.20] - 2026-05-31

### Added — edge-case test fleet (67 new tests across 5 files)

In response to *"did you test both backend and front end testing for
everything? especially edge cases"* — the honest answer was *not
comprehensively*, so this ships the missing coverage.

- **Functional UI walkthrough via st4ck browse** against the live
  v8.19.19 dashboard: 3 main-tab + 3 stream-tab paths, channel-card
  click → chat-stream open, member-chip click → audit-stream open,
  seed-brief expand/collapse with lazy-fetch full body, 12-cycle
  rapid tab switching with zero console errors, MutationObserver
  confirmed 0 mutations on idle, scrollTop=250 preserved across
  multiple poll cycles, cache headers + asset version-busting,
  endpoint contracts (200/400/404).
- **`tests/test_watcher_alerts_edges.py`** (22 tests). Direct
  `AlertRouter` coverage — formatting helpers (severity prefix
  mapping, subject + body, webhook payload), `irc_recipients`
  gates, every `send_email` config gate, SMTP failure tolerance,
  `send_webhook` empty-URL / unreachable-URL fallback, real
  localhost HTTP server with secret-header verification,
  `from_config_dict` tolerance for missing / None-valued subkeys
  + list-to-tuple coercion.
- **`tests/test_watcher_io_edges.py`** (20 tests). `_read_jsonl_tail`
  IO edges (missing, empty, malformed mixed with valid, max_lines
  returns LAST N, trailing newlines, UTF-8 content, post-truncation).
  Threshold boundaries for `detect_crash_burst` (exactly `min_count`,
  mixed inside/outside window), `detect_token_spike` (exactly at
  threshold is silent — uses `>`; alternate `input` key; non-numeric
  rejected), `detect_mission_stuck` (engaged inside window).
  Aggregator gates: empty `enabled` set, missing `pidfile_dir`
  skips `silent_death`, missing `action` field handled.
- **`tests/test_watcher_service_edges.py`** (10 tests). Dispatch
  isolation (IRC failure does not block email + webhook; email
  failure does not block webhook). Intra-batch cooldown dedupe.
  `run_forever` responds to `stop_event` within poll interval.
  `run_once` with empty globs is a no-op.
  `WatcherConfig.from_dict` tolerates mixed string/dict patterns
  and a YAML list at root. `_persistent_observer_lifecycle` wires
  `app[_OBSERVER]` at startup and calls `close()` at shutdown
  (verified via aiohttp `TestClient`); fallback to `None` when
  observer construction throws (endpoints still 200).
- **`tests/test_dashboard_seed_integration.py`** (7 tests).
  End-to-end seed flow against the real `list_tasks` output:
  per-channel `seed_preview` populated, task title falls back
  through seed → mission.md → `<nick>'s work`, 80-char truncation
  with ellipsis, leading-blank-line skip, multi-worker
  first-seed-wins preference.
- **`tests/test_persistent_observer_edges.py`** (8 tests). HISTORY
  timeout path (returns what it has, no crash), unreachable-port
  returns `[]` not raise, CRLF injection guard on `send_message`
  target, pure-newline body short-circuits before connect,
  `close()` resets writer/reader/nick (next read reconnects with
  fresh nick), `close()` on never-connected is no-op + idempotent,
  nick uses `_peek` prefix so v8.19.13 server-side suppression
  still applies, three concurrent `read_channel` calls all return
  cleanly via the lock.

### Fixed

- **Intra-batch cooldown dedupe** in
  `culture.watcher.service.WatcherService.cooldown_filter`.
  Previously a single dispatch batch with two events sharing the
  same `pattern:target` key would fire BOTH (state.in_cooldown
  was only updated by `record_firing` between events, not within
  a batch). Now `cooldown_filter` tracks `seen_keys` per call.
  Caught by `test_cooldown_filter_dedupes_same_key_within_one_batch`.

## [8.19.19] - 2026-05-31

### Added

- **Deterministic mesh-state watcher** (the user's "ask 1" — *"a
  deterministic system watcher that keeps looking at the activity
  table, looking for patterns and sending an email to me + nudging
  you as boss"*). Background process; reads
  `~/.culture/daemon-log/*.jsonl`, `~/.culture/audit/*.jsonl`,
  `~/.culture/perm-queue/`; evaluates 5 MVP failure patterns;
  ships alerts over IRC/email/webhook with per-pattern cooldown
  dedupe. Out-of-process, no LLM token quota, survives every
  other component's restart. Closed-loop guard.
- **5 MVP patterns** in `culture/watcher/patterns.py`:
  `silent_death` (agent_start with no agent_exit + pidfile PID
  gone), `crash_burst` (≥3 crash records in 5 min), `token_spike`
  (>50 000 input tokens in 10 min), `perm_escalation_above_ceiling`
  (pending request names a boss-denylisted tool), `mission_stuck`
  (boss running ≥2 h with no engaged or assistant activity).
- **3 alert sinks** in `culture/watcher/alerts.py`. IRC is on by
  default; routes to `target_nick` (the boss/orchestrator) AND
  `#alerts`. Email is opt-in (SMTP, STARTTLS, password sourced
  from `password_env` — never the literal). Webhook is opt-in
  (JSON POST, optional `X-Culture-Watcher-Secret` header). All
  sink errors are logged but never propagate.
- **Persistent cooldown cache** in
  `culture/watcher/state.py`. Per-firing key is
  `pattern_name:target`; default 600 s window. Atomic file rename
  on save; corrupt JSON → treated as empty (degrades to "first
  run" semantics rather than crashing).
- **`culture watcher {start,once,status,test}` CLI** plus
  `python -m culture.watcher` entry. `test` synthesizes a fake
  silent-death event and bypasses cooldown — useful for wiring a
  new SMTP relay.
- **Config schema** in `~/.culture/watcher.yaml` (sample at
  `culture/watcher/watcher.example.yaml`). Missing file →
  defaults; bad fields warned + ignored.
- **`PersistentObserver` re-use** for IRC alerts. The watcher
  reuses the same long-lived class the dashboard introduced in
  v8.19.17 so a 30 s poll never opens + tears down a TCP+IRC
  handshake every pass.
- **Documentation**: `docs/watcher.md` (pattern list, sink
  table, sample config, CLI cheatsheet, scope limits).

### Tests

- 17 in `tests/test_watcher_patterns.py`: each detector covered
  with fire-condition + 2–3 silent-condition cases; aggregator
  honors the `enabled` set; `PatternEvent.key` stable across
  same-target firings.
- 12 in `tests/test_watcher_service.py`: state persistence +
  reload, cooldown window math, GC, corrupt-state-file
  tolerance, config parsing (defaults + explicit), dispatch
  routing to IRC, cooldown suppresses repeats, different keys
  both fire, full `run_once` end-to-end against a synthetic
  `culture_home` with a silent-death pidfile.
- **Live verification**: `culture watcher once` against the
  active dev mesh found 19 real silent-death/mission-stuck
  alerts from past worker generations.

## [8.19.18] - 2026-05-31

### Added

- **Channel topics + seed-brief panel** (user ask: "channels should be
  named/titled by TOPIC, with a top tab/section showing the INITIAL
  SEED TEXT the boss had when opening the channel").
- **`culture boss spawn --topic "..."`** — sets the IRC TOPIC on
  `#task-<worker>` at spawn time AND writes the full text to
  `~/.culture/seeds/task-<worker>.md` so the dashboard can surface it
  without scrolling back through HISTORY.
- **`culture boss brief` auto-seeds** — if a task channel has no seed
  yet, the first brief becomes the seed AND sets the channel's IRC
  TOPIC (truncated to ~200 chars for the protocol line). Idempotent:
  subsequent briefs don't overwrite, so the original mission stays
  visible.
- **New `culture/clients/_seed.py`** — `persist_seed` (write-once by
  default), `load_seed`, `clear_seed`. Persistence path is
  `~/.culture/seeds/<channel-without-hash>.md` via the existing
  `seed_path_for` helper in `_perm_broker.py` (path-traversal-safe).
- **New `/api/channels/<name>/seed` endpoint** — returns the
  persisted seed with timestamp; 404 when no seed exists.
- **Dashboard rendering** — each channel card now shows a "▸ Seed
  brief" toggle when `seed_preview` is set, with lazy-fetch of the
  full text on first expand. CSS additions: `.channel-seed`,
  `.seed-toggle`, `.seed-preview-line`, `.seed-body`.
- **Task-group title falls back through TOPIC/seed → mission.md
  headline → boss-nick** — `list_tasks` now stamps every channel
  with `seed_preview` and uses the first non-empty per-worker seed as
  the task title.
- **Channel renaming is out of scope** (would break agent membership
  + ACLs + HISTORY continuity). The dashboard maps `#task-<nick>` to
  its topic for display only.

### Tests

- 9 new tests in `tests/test_seed.py`: persist/load roundtrip,
  idempotence, explicit overwrite, empty text skipped, missing
  returns None, clear removes, unsafe channel name rejected,
  multi-line body preserved, file lives under culture_home.
- 3 new tests in `tests/test_dashboard.py::TestSeed`: missing seed
  → 404, existing seed → 200 with text, invalid channel name → 400.

## [8.19.17] - 2026-05-31

### Added

- **`PersistentObserver` for the dashboard.** New class in
  `culture/observer.py` that holds ONE TCP connection + IRC
  registration across the dashboard's lifetime. Channels are joined
  lazily on first read and stay joined; auto-reconnect re-JOINs the
  membership set after a server bounce. Replaces ~24 ephemeral peek
  connections per minute (every 2.5 s chat poll) with one persistent
  connection.
- **Nick prefix is `_peekDASH<hex>`** so the v8.19.13 server-side
  event suppression continues to silence the observer's JOINs — its
  membership remains invisible to other channel members.
- **Single asyncio.Lock around the connection** serializes requests so
  HISTORY responses can be demuxed cleanly. Dashboard polls are
  infrequent (sub-second reads at 2.5 s cadence) so serialization is
  not a bottleneck.
- **Dashboard wiring** via aiohttp `cleanup_ctx`: one observer is
  built in `_persistent_observer_lifecycle` and exposed at
  `app[_OBSERVER]`. New `_observer_for(request)` helper prefers the
  persistent observer and falls back to the ephemeral `get_observer()`
  if instantiation failed at boot.
- Wired through `/api/channel/{nick}`, `/api/message`, and
  `/api/channels/{name}/messages`.

### Tests

- 5 new tests in `tests/test_persistent_observer.py` against a real
  IRCd: repeated reads of the same channel JOIN once (10 reads → 1
  JOIN line broadcast), reads on a new channel lazy-JOIN, send_message
  reuses the same nick, `close()` shuts down, auto-reconnect after a
  forced writer close re-JOINs the membership set.
- 45 dashboard tests still pass — chat-related tests updated to
  monkeypatch `server._observer_for` for clean DI.

## [8.19.16] - 2026-05-31

### Fixed

- **Dashboard flicker that v8.19.15 "fixed" was still reported by the
  user after they refreshed — root cause was browser cache.**
  v8.19.14 and v8.19.15 both touched `culture/dashboard/static/app.js`
  but the served URL is the unversioned `/static/app.js`. A tab opened
  before the fix landed sits on the stale bundle indefinitely. The
  user kept seeing the OLD code's flicker even though I'd
  live-verified the new code works (zero DOM mutations in 8s).
- **Fix: cache-bust + no-cache on index.html.** `_handle_index` now
  reads `index.html`, rewrites `/static/app.js` → `/static/app.js?v=<version>`
  and the same for `style.css`, and serves with
  `Cache-Control: no-cache, no-store, must-revalidate` + `Pragma: no-cache`
  + `Expires: 0`. So:
  - The HTML shell is never cached — every page load asks the server.
  - Static assets ARE cached per-URL (cheap on CDN-style fetches),
    but each `pyproject.toml` version bump changes the URL →
    automatic invalidation. No more "hard-refresh to pick up a
    hotfix."
- Version sourced from `culture.__version__` (`importlib.metadata`),
  so the buster updates automatically on every release.
- Live-verified end-to-end: restarted dashboard, fresh browser session
  loaded `app.js?v=8.19.16` + `style.css?v=8.19.16`, MutationObserver
  recorded **0 mutations in 10 seconds** across `#channel-list`,
  `#agent-list`, `#pending-list`, `#stream`.
- 45/45 dashboard tests pass.

## [8.19.15] - 2026-05-31

### Fixed

- **Dashboard left-panel flicker, round 2 (the actual user-reported bug).**
  v8.19.14 fixed the stream/chat panel but the user kept seeing the
  display "nuked every couple of seconds" — root cause was different:
  `refreshChannels` (every 3s), `refreshAgents` (every 2.5s), and
  `refreshPending` (every 2s) each unconditionally called
  `container.replaceChildren()` plus a full re-render even when the
  data was identical. The user saw the channel/agent list flicker
  every poll cycle and any scroll position into a long list reset.
- **Fix is JSON-snapshot skip-when-unchanged + scroll preservation.**
  New `withListSnapshot(listId, data, render)` helper:
  (a) `JSON.stringify`s the incoming payload and compares against the
  previous snapshot — same payload = same DOM, so the render callback
  is SKIPPED entirely (no replaceChildren, no flicker, no scroll
  reset); (b) on a real data change, snapshots the parent scroll
  container's `scrollTop` BEFORE the rebuild and restores it AFTER so
  a long-list scroll position survives an update.
- Wired through `refreshChannels`, `refreshAgents`, `refreshPending`,
  `refreshArchived`. Live-verified in a real browser via st4ck:
  tagged child elements survived 8s of poll cycles on `#channel-list`
  and `#agent-list`; scrollTop=200 on the parent SECTION survived
  multiple poll cycles unchanged.
- Single-file change: `culture/dashboard/static/app.js`.

## [8.19.14] - 2026-05-31

### Fixed

- **Dashboard activity panel nuked every 2.5s, scroll-up unreachable.**
  Channel-mode chat polled `/api/channel/<nick>` every 2.5s and called
  `box.replaceChildren()` on every poll — wiping any scroll-up position
  and yanking the user back to the bottom. Activity / daemon-log SSE
  paths force-scrolled to bottom on every appended record, blocking
  scrollback while live data was streaming.
- **Fix is append-only + scroll-position preservation.** `refreshChat`
  now diffs the message list, finds the previously rendered tail in
  the new list, and appends only the delta (falls back to a full
  re-render when the server-side log rotated). `renderActivityTurn`
  and `appendStreamLine` snapshot `isAtBottom(box)` before appending
  and only auto-scroll if the user was already at the bottom — if
  they've scrolled up to read history, the scrollbar stays put.
- New `state.chatLastMessages` / `state.chatLastChannel` diff baseline,
  reset on `openStream` (channel switch). Threshold for "at bottom" is
  40px to tolerate sub-pixel layout drift in the channel list.
- Single-file change: `culture/dashboard/static/app.js`. Static asset
  cache-busted by the version bump.

## [8.19.13] - 2026-05-31

### Fixed

- **Peek JOIN/PART events poisoned all channel buffers** — the v8.19.12
  welcome-bot fix suppressed only the welcome message, but the
  underlying `user.join` event continued firing for every peek
  connection. The events skill turns `user.join` into a `system-local`
  PRIVMSG that lands in every channel member's message buffer.
  Result: every agent subscribed to `#team` was still pulling
  `<system-local> local-_peekXXXX joined #team` lines into its
  next-turn SDK context. Token leak continued.
- **Architectural fix at the event-emission site**: `Client._handle_join`
  and `Client._handle_part` now skip `emit_event` for nicks whose
  agent-suffix starts with `_peek`. Raw IRC JOIN/PART protocol lines
  still broadcast to channel members (membership semantics unchanged);
  only the event-as-PRIVMSG observer-noise pathway is gated.
- Tests: 12/12 in `tests/test_channel.py` (2 new — peek suppression +
  real-agent symmetry confirming raw JOIN + welcome bot still fire
  for non-peek nicks).

## [8.19.12] - 2026-05-31

### Fixed

- **Welcome-bot peek filter was a silent no-op** (v8.19.10 regression).
  Filter referenced `event.nick` but bot_manager passes the event
  context FLAT (`type`, `nick`, `channel`, `data` at top level).
  `event.nick` resolved to MISSING → `in` returned False → `not`
  returned True → welcome fired on every peek connection regardless.
  Corrected to `not ('_peek' in nick)`. Live-verified the AST
  evaluates: peek nick → False (skip), real worker → True (welcome),
  non-join event → False (not a join).

## [8.19.11] - 2026-05-31

User-requested restructure of the Channels tab: group by TASK (per
the task-model doc's "channels-are-tasks" framing) instead of by
channel category.

### Added

- **`/api/tasks` endpoint + `list_tasks()` builder** — returns one
  task object per boss agent, each with `title` (read from the
  boss's `mission.md` first body line, fallback to `<boss>'s work`),
  `state` (running/stopped), `worker_count`, and a `channels` array
  containing the boss's `#boss` channel + its `#joint-*` channels +
  one `#task-<worker>` per owned worker, sorted boss→joint→task.
- **Task group rendering** in the Channels tab — header with task
  state dot + title + boss-nick badge + worker count, followed by
  the task's channel cards.
- Orphan workers (a manifest entry with `boss:` pointing at a nick
  that has no boss-tagged agent) get a synthetic "Unassigned
  workers" task so they don't vanish from view.

### Why

User asked for channels grouped as tasks — "channel title, and below
we would see the boss channel, then group channel and then the
channels the boss has with each agent." The old by-category grouping
made it harder to see at a glance what each boss was driving.

## [8.19.10] - 2026-05-31

Hotfix after first dashboard restart with the channels-first UI.

### Fixed

- **Welcome-bot spam on peek connections** — every `culture channel
  read` and every dashboard channels refresh opens a short-lived
  `local-_peekXXXX` connection; the welcome bot was greeting each one
  in #team, flooding the channel with `Welcome local-_peekXXXX to
  #team` lines. Filter updated to exclude any nick containing
  `_peek`.
- **Stale `#task-*` channels lingering in the active Channels tab** —
  channels whose only members are stopped workers (e.g. closed
  verify-* / qfix-* / fix-* nicks from the v8.19 fleet) cluttered
  the Channels view despite no live activity. `list_channels` now
  filters out `#task-*` channels with no `running` member. Joint /
  shared / boss channels always show regardless of member state.

## [8.19.9] - 2026-05-31

Consolidation bump after the v8.18.7 → v8.19.8 fleet merge cycle.
Twelve PRs (#24–#36) merged in under 12 hours via rebase + automated
conflict resolution; intermediate per-version CHANGELOG entries
were lost during rebase merges — captured here in aggregate.

### Added (by PR)

- **v8.18.6 / PR #24** — `model_resolved` daemon-log + `_boss_inherits`
  fallback (closes Qodo latch + orphan-resolved findings).
- **v8.18.7 / PR #29** — Task-channel ACL at IRC JOIN layer + owner-map
  TTL cache + federation gate symmetric in `should_relay` /
  `_check_incoming_trust`. Closes 4 Qodo findings.
- **v8.18.8 / PR #26** — Socket symlink on bind (`ensure_socket_symlink`),
  all 4 backends + harness.
- **v8.19.0 / PR #30** — `--channels` spawn flag + IPC `irc_read`
  history backfill + **CRLF injection guard** (new
  `culture/agentirc/irc_targets.py` wired at 4 ingress points; 30 tests).
- **v8.19.1 / PR #27** — Agent `archived` state + channel `CHANARCHIVE`
  verb + 5 blocker fixes from review + 2 Qodo Reliability fixes
  (archived channels persistent, archive-fails-loudly).
- **v8.19.2 / PR #28** — Dashboard Channels + Archived tabs + 5 endpoints
  + archive-race fix (refuse if daemon won't stop) + 3 Qodo fixes
  (perf `to_thread`, channel routing, channel-history JOIN).
- **v8.19.3 / PR #31** — Boss mission persistence across daemon restarts.
  Shared `culture/clients/_mission.py` (rotation at 32 KiB, idempotent
  cleanup, stable prompt-cache format), wired into all 4 backends.
- **v8.19.4 / PR #32** — `role:` field on AgentConfig + `--role` spawn
  flag + dashboard render.
- **v8.19.5 / PR #33** — `culture agent compact <nick> <reason>` command
  for explicit task-switch compaction. All 4 backends.
- **v8.19.6 / PR #34** — CI quality gates: mypy strict carve-out on
  security-sensitive modules, docs-coverage script that fails PRs
  adding public surface without docs, bandit B606 re-enabled.
- **v8.19.7 / PR #35** — Channels-first dashboard inversion (Channels
  default tab; channel cards show member chips with role badges +
  state dots).
- **v8.19.8 / PR #36** — Silent-death watchdog: boss daemons detect
  workers whose PIDs are dead but never wrote `agent_exit`. Closes
  the reliability gap surfaced by the v8.18.7 fleet ship (4 of 6
  workers died silently).

### Docs

- **PR #25** — `docs/task-model.md` (channels-are-tasks, three-level
  hierarchy, Pattern A vs B orchestrators, role + lifecycle).
- **PR #25** — `docs/dev-policy.md` (quality bar + flow + review).

### Security

- CRLF injection on user-controlled IRC targets → closed.
- Task-channel ACL federation bypass → closed (symmetric outbound +
  inbound gate on `#task-*`).

### Findings + dogfood writeups

- 8-reviewer audit fleet + Qodo reviewed every PR; combined finding
  pool (~30 actionable) closed end-to-end through this cycle. Workers
  shipped 4 of 6 Qodo-fix passes themselves; orchestrator shipped
  the rest under the silent-death recovery pattern documented in
  `docs/dev-policy.md`.

## [8.19.1] - 2026-05-30

Agent archiving and channel archiving.

### Added

- **Agent archiving with `state` field**. Adds `state: archived` to
  culture.yaml, formalizing the existing `archived` boolean into a
  proper lifecycle state. Agents are either active (running or stopped,
  re-startable) or archived (historical, read-only).

- **`culture agent archive <nick>`** marks a stopped agent as archived.
  Refuses if the agent is still running (must stop first). Archived
  agents are hidden from default status listing.

- **`culture agent restore <nick>`** moves an archived agent back to
  active (stopped) state, ready to be started again. `unarchive`
  remains as an alias.

- **Status filtering**: `culture agent status --archived` shows only
  archived agents; `--all` includes them alongside active agents.

- **Channel archiving**: `culture channel archive <#channel>` and
  server-side `CHANARCHIVE` command. Archived channels refuse new
  JOINs, are hidden from LIST, but history remains readable. Agents
  and channels archive independently.

- **`set_agent_state()`** config helper for programmatic state
  transitions with validation.

- 21 new tests: agent state sync, serialization, transitions, display
  filtering, lifecycle scenarios, and 4 server-level channel archive
  integration tests (JOIN refused, LIST hidden, double-archive, missing
  channel).

## [8.19.0] - 2026-05-30

Two CLI/IPC fixes from the boss-fleet audit.

### Added

- **`--channels` flag for `culture boss spawn`** (`culture/cli/boss.py`).
  Workers previously joined only `#team` and `#task-<name>`. The new flag
  accepts a comma-separated list of extra channels (e.g.
  `--channels '#joint-fixes,#design'`). Channels without a `#` prefix are
  auto-prefixed. The boss also joins the extra channels for observation.
  Channels are written into the worker's `culture.yaml` alongside the
  defaults, with deduplication.

### Fixed

- **Boss IPC `irc_read` returns empty for joint channels**
  (`culture/clients/*/irc_transport.py`, `packages/agent-harness/irc_transport.py`).
  Root cause: when a daemon joins a channel via `irc_join`, pre-existing
  messages lived only in the server's HISTORY store and never reached the
  daemon's `MessageBuffer`. The transport now handles `HISTORY` and
  `HISTORYEND` IRC responses, and `join_channel()` issues
  `HISTORY RECENT <channel> 200` after `JOIN` to backfill the buffer.
  System-user entries (`system-*`) are filtered, matching the existing
  `PRIVMSG` filter. Fix applied to all four backends (claude, acp, codex,
  copilot) and the reference implementation in `packages/agent-harness/`.

## [8.18.8] - 2026-05-30

Closes a daemon-IPC reachability bug surfaced by `verify-joint-w`
during the three-layer-vision audit dogfood: the daemon binds its
Unix socket in `$XDG_RUNTIME_DIR` or `/tmp`, but the
`culture channel` CLI resolves sockets via `~/.culture/run/`. When
the two diverge (the common case on macOS where
`XDG_RUNTIME_DIR` is unset) the CLI can't reach the daemon.

### Added

- **`culture/clients/_socket_link.py`** — `ensure_socket_symlink` +
  `remove_socket_symlink` helpers. `ensure_socket_symlink` creates
  an atomic symlink (mkstemp → unlink → symlink → rename, the
  standard POSIX atomic-symlink-replace) from the CLI-visible path
  to the real socket path; `remove_socket_symlink` cleans up on
  daemon stop. Idempotent on missing files; safe against stale
  symlinks and regular-file collisions.

### Changed

- All four backend daemons (`claude`, `codex`, `acp`, `copilot`) +
  the `packages/agent-harness/` template now call
  `ensure_socket_symlink` immediately after `socket_server.start()`
  and `remove_socket_symlink` immediately before
  `socket_server.stop()`. Satisfies the all-backends rule.

### Tests

- 10 new tests in `tests/test_socket_link.py`: creates fresh,
  replaces stale symlink, replaces regular file, no-op when paths
  already match, XDG honored, fallback to `~/.culture/run`,
  permissions enforced (0o700), idempotent remove.

### Surfacing

Surfaced during the three-layer-vision verification dogfood — see
`verify-joint-w`'s GAP 2 in `#joint-vision-audit`. Closed by
mesh worker `local-fix-symlink-w`; orchestrator shipped the
commit on behalf since the daemon stopped silently before push
(co-authored attribution in commit footer).

## [8.18.7] - 2026-05-30

### Security

- **Task-channel ACL enforcement at the IRC JOIN layer**
  (`culture/agentirc/client.py`). Workers could previously join any
  `#task-<suffix>` channel, including channels belonging to other
  workers. This bypassed the boss CLI's team isolation checks
  (`boss.py _foreign_worker`). The IRCd now enforces: only the
  worker whose nick matches the suffix (owner) and that worker's
  boss (from the manifest) may join `#task-*` channels. All other
  clients receive `474 ERR_BANNEDFROMCHAN`. System nicks
  (`system-*`) are always allowed. `#joint-*`, `#team`, and other
  channels remain unrestricted.

### Added

- `ERR_BANNEDFROMCHAN` (474) numeric reply in
  `culture/protocol/replies.py`.
- `_task_channel_acl()` and `_load_owner_map()` helpers in
  `culture/agentirc/client.py` — manifest-backed ACL for
  `#task-*` channels.
- 14 new tests in `tests/test_task_channel_acl.py` (9 unit + 5
  integration) covering: owner join, foreign refusal, boss join,
  wrong-boss refusal, joint channels, system nicks, missing manifest.

## [8.18.6] - 2026-05-30

Surfaced during the in-mesh PRD-authoring dogfood. Spawning `local-prd-w`
exposed that workers were inheriting the SDK CLI's hardcoded default
model (claude-opus-4-6) instead of whatever model the boss was actually
running with — defeating the YAML-omits-model inheritance pattern the
boss stack was designed around. The boss daemon-log was recording
`model: ''` on `agent_start` (because the boss YAML omits model by
design, so workers inherit), and the SDK-resolved runtime model was
never written anywhere a spawn could read it.

### Added

- **`model_resolved` daemon-log action**
  (`culture/clients/claude/daemon.py`). The daemon now latches the
  SDK-resolved runtime model the moment the first AssistantMessage
  names a model, writing one `model_resolved` action per session. The
  latch resets on every `agent_start` so a restarted session can
  re-record if the SDK happens to pick a different default this run
  (e.g. between CLI version bumps). Idempotent: subsequent
  AssistantMessages do not write additional records.

### Fixed

- **`_boss_inherits` fallback to `model_resolved` when YAML omits model**
  (`culture/cli/boss.py`). The reader now scans for a `model_resolved`
  action after the most recent `agent_start`. When `agent_start.model`
  is empty (the inheritance-friendly boss-YAML pattern), the resolved
  runtime model is used instead — so workers inherit the boss's actual
  running model, not the SDK CLI's hardcoded default. A `model_resolved`
  from a PRIOR session is correctly ignored (the reader stops at the
  most recent `agent_start`). An explicit YAML-pinned model still wins
  over a later `model_resolved` to honor the operator's choice.

### Notes

- Five new tests in `tests/test_boss_model_inherit.py` cover: YAML-empty
  → resolved fallback, YAML-pinned vs resolved precedence, prior-session
  resolved ignored, most-recent within-session wins, plus the existing
  9 inheritance tests still pass.
- The PRD-authoring dogfood that surfaced this is documented in
  `docs/v8.18.6-prd-authoring-dogfood.md` alongside the cross-project
  + joint-coordination-channel vision the worker incorporated across
  three correction rounds (V1 factual fixes, V2 three-level hierarchy,
  V3 cross-project orchestration).
- `.claude/skills/run-tests/scripts/test.sh` fixed: empty `EXTRA_ARGS`
  array expansion was tripping `set -u` on macOS bash 3.2.

## [8.18.5] - 2026-05-30

Surfaced + fixed during the context-watch handoff dogfood
(see `docs/v8.18.4-context-watch-dogfood.md`). A worker hitting the SDK
CLI's `Stream closed` bug on every `Write` alternated between failed
turns and Bash-workaround turns that completed cleanly. v8.18.4's
`stalled_in_retry_loop` watchdog stayed silent because each successful
Bash turn refreshed `_last_turn_completed_at`, masking the elevated
failure rate.

### Added

- **`stalled_in_failed_retry` watchdog class**
  (`culture/clients/claude/daemon.py`,
  `culture/clients/claude/agent_runner.py`). A fifth class on the
  unified stall watchdog. `AgentRunner` now also exposes an
  `on_turn_failed` callback fired from `_process_turn`'s exception
  branch. The daemon increments a `_consecutive_failed_turns` counter
  on each failed turn and resets it on each clean turn. When the
  counter exceeds `CONSECUTIVE_FAILED_TURN_THRESHOLD` (5) the watchdog
  surfaces `idle_warning {reason: stalled_in_failed_retry,
  failed_turns: N}` to the boss so it can intervene before the worker
  burns more API calls in alternating-fail-success patterns.

## [8.18.4] - 2026-05-30

Surfaced + fixed during an in-mesh multi-worker dogfood. Three workers
(`research`, `customer`, `builder`) ran concurrently under `local-boss`
for ~12 minutes; full findings in `docs/v8.18.4-dogfood-findings.md`.
The system delivered all three artifacts despite multiple SDK CLI
crashes and tool-retry loops.

### Added

- **`stalled_in_retry_loop` watchdog class**
  (`culture/clients/claude/daemon.py`,
  `culture/clients/claude/agent_runner.py`). A new fourth class to the
  unified stall watchdog. The v8.18.2 watchdog tracked
  `_last_assistant_message_at` — recent AssistantMessages cleared the
  `stalled_post_engagement` timer. But during the dogfood,
  `local-customer` ran for 4+ minutes hitting the SDK CLI's
  `Stream closed` error on every `Write personas.md`. Each retry was a
  fresh AssistantMessage so the watchdog stayed silent — even though no
  turn had completed and no file had landed.

  `AgentRunner` now exposes an `on_turn_complete` callback fired after
  a turn's `async for query()` loop ends cleanly (i.e. the SDK yielded
  a final `ResultMessage`). The daemon's `_on_turn_complete` updates
  `_last_turn_completed_at`. The watchdog's tick now checks
  `now - last_assistant_message < STALL_GRACE` AND
  `now - last_turn_completed >= STALL_GRACE` → DM the boss with a
  class-specific message that names the retry-loop pattern. Three new
  tests pin the matrix (looping fires, healthy turn-completer doesn't,
  callback updates timestamp).

### Documented (not fixed in this release)

- **SDK CLI `Stream closed` / `CLIConnectionError` mid-hook (HIGH).**
  Both `research` and `builder` crashed at the SDK level when firing
  3+ tool calls in rapid succession; the bundled `claude` CLI's
  `sendRequest` throws `Stream closed` when its `inputClosed` flag
  flips during a hook callback's await. Not directly fixable on the
  culture side — needs an upstream repro to
  `anthropics/claude-agent-sdk`. v8.18.0 crash recovery + IRC channel
  persistence (the brief is re-read from buffer on restart) makes
  these losses recoverable.
- **Off-task drift after crash recovery (MED).** `research` rewrote
  `findings.md` on a different topic after restart because the cwd
  name (`/tmp/dogfood-research`) and IRC channel mentions leaked
  `"dogfooding"` into its context. Fix candidates: explicit boss
  re-brief on `idle_warning {reason: never_briefed}`, or workers
  persisting a `mission.md` they re-read on restart. Out of scope here.

## [8.18.3] - 2026-05-30

Eight cited security findings from an in-mesh `local-secscan` dogfood
(see `docs/v8.18.2-followups.md` § B), all fixed with tests.

### Security

- **B#1 — HISTORY skill now requires channel membership** (HIGH —
  `culture/agentirc/skills/history.py`). `HISTORY RECENT` / `HISTORY
  SEARCH` previously returned every channel's content to any registered
  client whether they had joined or not. New `_client_may_read_history`
  gate matches the pattern used by PRIVMSG-to-channel / PART / TOPIC.
  Non-member requests get `ERR_NOTONCHANNEL` (442).

- **B#2 — Pre-registration PRIVMSG / NOTICE silent-drop** (HIGH —
  `culture/agentirc/client.py`). A TCP socket that sent `NICK` but not
  `USER` (so `_registered` stayed False) could `PRIVMSG <agent> :@<agent>
  …`, injecting messages into the agent's `@mention` handler and driving
  its behavior. Channel messages were already blocked (the client isn't
  in any channel) but DMs slipped through. Now matches the silent-drop
  pattern used by `_handle_join`.

- **B#3 — Pre-registration WHO / WHOIS silent-drop** (MED —
  `culture/agentirc/client.py`). Same gate as B#2, applied to user
  enumeration paths that leak nicks / modes / hostnames / channel
  memberships.

- **B#4 — S2S password compare is now constant-time** (MED —
  `culture/agentirc/server_link.py`). Replaced `self._peer_pass !=
  self.password` with `hmac.compare_digest`. The dashboard already uses
  it for its token check (`server.py:635,639`); now the S2S link does
  too.

- **B#5 — S2S read buffer is now bounded** (MED —
  `culture/agentirc/server_link.py`). A peer with no `\n` terminator
  could OOM the server by streaming bytes into an unbounded `buffer`
  string. C2S already caps at `8192/4096` (`client.py:230`); S2S now
  mirrors with `65536/32768` to accommodate larger federation payloads.

- **B#6 — SEVENT origin is force-corrected to the authenticated peer**
  (MED — `culture/agentirc/server_link.py`). Previously a `SEVENT
  other-server …` from `peer-A` only logged a warning; the spoofed
  origin still landed in `data["_origin"]` and `system-<origin>` audit
  prefixes. A compromised peer could impersonate any server in the mesh.
  Origin is now overwritten with `self.peer_name`.

- **B#7 — Default IRCd bind is now 127.0.0.1** (MED —
  `culture/agentirc/config.py`, `culture/agentirc/__main__.py`). The
  IRCd has no C2S authentication; binding to `0.0.0.0` by default was
  an unauthenticated LAN service. Operators that need network exposure
  pass `--host 0.0.0.0` explicitly. *Backwards-incompatible default.*

- **B#8 — Dashboard auth is now a POST form (`/auth`)** (LOW —
  `culture/dashboard/server.py`, `culture/cli/dashboard.py`). The
  legacy `?token=<secret>` bootstrap leaked the token into browser
  history, server access logs, and Referer headers on outbound
  navigation. The new `/auth` GET renders a minimal login form; POST
  submits the token in the request body. Unauthenticated HTML requests
  now redirect to `/auth`. The `?token=` path is kept for backwards
  compat with `logger.warning` on use; `culture dashboard --auth`
  startup output prints the login URL + token on separate lines.

## [8.18.2] - 2026-05-30

### Security (critical)

- **A — Broker actually fires now (via PreToolUse hook).** v8.18.1 changed
  the SDK from `permission_mode="bypassPermissions"` to `"default"` —
  necessary, but not sufficient. In-mesh dogfooding of v8.18.1 (a
  `local-secscan` worker) confirmed via a `logger.warning` at the top of
  `PermissionBroker.gate`: the gate **never fired** across 140+ turns
  including non-safe Bash + Write calls. The SDK CLI in `default` mode
  with `--permission-prompt-tool stdio` does not route every tool to the
  `can_use_tool` callback — its built-in allow-list pre-approves most
  tools without consulting Python. **PreToolUse hooks do fire for every
  tool call.**

  `AgentRunner._broker_pre_tool_use_hook` wraps `broker.gate` and is
  installed via `opts.hooks["PreToolUse"]` whenever a broker is wired
  (worker has a perm-policy file). Fail-closed: hook returns deny if
  `broker.gate` raises; broker bugs can't become permission bypasses.
  Generous `HookMatcher` timeout (900s) covers the broker's own 600s
  `_PERM_DECISION_TIMEOUT_SECONDS`. `can_use_tool` wiring kept as
  defense-in-depth. Verified live: `perm_request_notified` daemon-log
  action fired for both `ToolSearch` and `Write` from the secscan
  worker — the first time the broker has actually been invoked end-to-end
  in production.

### Fixed

- **C — Boss daemon auto-rejoins owned task channels on restart.** After
  `culture agent stop local-boss && culture agent start local-boss`, the
  restarted boss was no longer in any `#task-<worker>` channel, so
  `culture boss brief <worker>` failed its channel-membership pre-check.
  `AgentDaemon.start` now reads the manifest, finds every agent whose
  `boss:` field equals this daemon's nick, and rejoins their
  `#task-<suffix>` channels. Skips channels already in the daemon's own
  `agent.channels` (no double-join).

- **D — `culture agent stop` actually terminates the process.** Observed
  live during v8.18.1 verification: `agent_stop` logged at 05:00:40,
  process PID 38387 stayed alive 5+ minutes, watchdog inside the zombie
  fired `stalled_post_engagement` 305s later. `AgentDaemon.stop` now (a)
  drains supervisor evaluation tasks via `Supervisor.wait_for_evals` and
  (b) cancels every remaining task in `_background_tasks`.
  `_run_single_agent` adds a defense-in-depth pass cancelling any
  asyncio tasks the daemon's stop didn't track, so the loop drains and
  the process exits.

## [8.18.1] - 2026-05-30

### Security (critical, but incomplete — superseded by 8.18.2-A)

- **Permission broker was a no-op in production.** When the worker daemon
  configured the Claude Agent SDK with `permission_mode="bypassPermissions"`
  AND `can_use_tool=<broker.gate>`, the CLI binary literally interpreted
  bypassPermissions as "Allow all tools" (`claude_agent_sdk/query.py:58`)
  and never invoked the `can_use_tool` callback. Switching to
  `permission_mode="default"` was necessary (bypass mode was demonstrably
  wrong) but as of 8.18.2 we know it was not sufficient — the SDK's
  `default` mode still does not route every tool through `can_use_tool`.
  v8.18.2-A switches enforcement to the SDK's `PreToolUse` hook system,
  which DOES fire for every tool. v8.18.1's mode change is kept (it's
  semantically more accurate than bypass even with the hook path).

## [8.18.0] - 2026-05-30

This release closes 12 ranked findings (8 stall classes from audit1's
in-mesh dogfood + 11 prioritized findings from a comprehensive
multi-agent adversarial audit) that together hardened reliability,
observability, and security across the boss/worker stack.

### Added

- **`AgentDaemon._notify_boss(action, message, **detail)`** — every
  boss-facing alert (idle/stall, circuit_open, perm_request_notified,
  supervisor escalation) now routes through one helper that records
  to the daemon-log first (always lands; dashboard reads it) and DMs
  the boss best-effort. Each alert is structured + visible in the
  dashboard even when IRC is unhealthy.
- **`AgentRunner.set_paused(flag)`** + an internal `asyncio.Event` so
  pause holds the SDK runner queue authoritatively. Mirrors
  `AgentDaemon._paused` so handoff/`/compact`/poll prompts already
  queued wait for resume instead of executing against a "paused"
  worker.
- **`Supervisor.wait_for_evals()`** + `Supervisor.resume()` — the
  former for graceful shutdown / determinism, the latter to clear
  `paused` + `consecutive_failures` after an escalation (without it
  an escalated worker stayed unsupervised forever).
- **Activity-tab dashboard view** that renders the audit JSONL as
  per-turn cards with thinking (italic gray), assistant text, tool
  calls with their full inputs, and tool results with their full
  outputs — same shape as a regular Claude Code session. Tab
  tooltips explain what each shows (SDK activity / daemon lifecycle
  / IRC channel).

### Changed

- **Workers inherit model + thinking from the boss's RUNTIME**, not
  from any yaml. The SDK picks the current Claude when no model is
  set; the boss daemon records that in its `agent_start` daemon-log
  detail; `culture boss spawn` reads from there and propagates both
  fields into the worker's yaml. Result: no hardcoded model strings
  anywhere — new Claude versions inherit automatically. Code defaults
  for `model` go to empty; `thinking` defaults to `"high"`.
- **`_paused` is authoritative end-to-end**: pause halts the SDK
  runner queue too, not just the mention/poll surfaces (workflow #2).
- **Supervisor evaluation runs off the SDK consumer pump** via
  fire-and-forget tasks (locked) so a slow supervisor LLM call no
  longer blocks audit writes, engaged tracking, or watchdog activation
  timers (workflow #8).
- **Dashboard "Session" tab renamed to "Activity"** with tooltips on
  each tab and `<nick> · <kind>` in the column header so the current
  view is always obvious.
- **`_audit.py` captures full tool I/O + thinking** (size-capped at
  16 KiB per field with a truncation marker). Legacy `input_digest` /
  `content_digest` / `preview` kept alongside for back-compat
  consumers.

### Fixed

- **Stall watchdog now catches all three silent-worker classes**
  (audit1 #1+#2, workflow #1 — observed live as `local-qa658c`
  silently stalling for 3+ hours after receiving its brief):
  - `never_briefed` — alive > `IDLE_GRACE_SECONDS` (90s) with no
    mention/poll/invite activation
  - `stalled_pre_engagement` — brief landed, no `AssistantMessage`
    in `STALL_GRACE_SECONDS` (300s)
  - `stalled_post_engagement` — engaged, then no new
    `AssistantMessage` in `STALL_GRACE_SECONDS`
  Each fires a distinct daemon-log entry + DMs the boss once per
  state change. The watchdog re-arms on resume from pause
  (`_ipc_resume`, sleep-scheduler resume).
- **Perm-gate no longer hangs forever** when the boss is unresponsive
  (audit1 #3). `_await_decision` returns a synthetic deny with
  `reason=timeout` after `_PERM_DECISION_TIMEOUT_SECONDS` (600s) so
  the SDK call can proceed instead of stalling.
- **Circuit-breaker DMs the boss directly** (audit1 #4) — was
  webhook-only, invisible to bosses not watching the alert channel.
- **`_run_loop` done-callback** catches the silent-task-death case
  (audit1 #8): an exception escaping `_process_turn`'s try/except
  (e.g. from a callback) used to leave the task dead with no `on_exit`
  signal, so crash recovery never triggered. Now fires a fallback
  `on_exit(1)` so the circuit breaker still arms.
- **Workers inherit `thinking` from the boss too**, not just `model`
  (workflow #1, user feedback "both model and effort levels").

### Security

- **Ceiling bypass closed** (workflow #3): a sticky `--always allow`
  rule for a benign tool (e.g. `Bash ls`) used to whitelist every
  Bash invocation (`rm -rf`, `git push`, …) because the gate's fast
  path returned allow without re-checking `is_above_ceiling`. Gate
  now re-checks on every policy-allow.
- **Handoff write-anywhere closed** (workflow #5): the auto-allow
  regex was tail-anchored only (`/handoff/<nick>.md$` matched via
  `re.search`), so any path whose tail looked right slipped through —
  e.g. `/etc/secrets/handoff/<nick>.md`. Now anchored to the EXACT
  absolute `handoff_path_for(nick)` via `^…$`.
- **Ownership forge closed** (workflow #4): `_request_is_foreign`
  used to trust `req['boss']` as authoritative. The worker controls
  that payload field, so a buggy/malicious worker could forge
  `boss: <other-boss>` to route requests to another team's approver.
  Ownership now derived from the MANIFEST only (`_owner_map`); a
  request whose `helper_nick` lacks a manifest entry is foreign to
  every boss (fail closed).
- **Cross-team audit/log read gated** (workflow #9): `culture boss
  audit` / `culture boss log` now check `_foreign_worker` before
  tailing, matching the gate in `brief`/`read`/`approve`/`deny`/
  `close`.
- **Nick fidelity** (observed live): `culture channel message` from
  a worker's Bash subprocess fell to the IRCObserver fallback (which
  posts under `<server>-_peek<hex>`) when the daemon socket was
  unreachable — so the worker's DONE post appeared in the channel
  under a stranger nick. When `CULTURE_NICK` is set, the CLI now
  refuses the observer fallback with a clear error.

## [8.17.3] - 2026-05-29

### Fixed

- **Idle signal reads the full daemon-log, not a fixed tail.** `_daemon_logged_idle`
  no longer reads only the last 8 KiB — it reads the whole (small, lifecycle-only)
  daemon-log, removing a latent coupling where a future high-volume log action on
  an idle worker could have buried the lone `idle_warning` past the window and
  silently un-flagged it. Closes the last (latent, currently-unreachable) note
  from round-5 adversarial verification; the idle loop is now converged.

## [8.17.2] - 2026-05-29

### Fixed

A second adversarial re-verification (round 4) found two more idle residuals; closed:

- **Poll/room-invite work now counts as activation.** Previously `_last_activation`
  was set only on an `@mention`; a worker driven by the channel poll (a boss posts
  task context *without* `@`-tagging) or a room invite started a slow first turn
  with no activation recorded and was still false-flagged idle. Both dispatch
  paths now set `_last_activation`.
- **Dashboard idle no longer depends on audit byte-size.** The daemon now records
  an `engaged` action on a worker's first turn, and the dashboard's idle signal
  reads the daemon-log alone — `idle_warning` cleared by a later `engaged` or
  `agent_start`. This removes the last false-positive (an externally
  truncated/rotated audit on a re-driven, engaged worker) and makes the daemon-log
  the single source of truth for idle.

## [8.17.1] - 2026-05-29

### Fixed

Closing residuals an adversarial re-verification found in the 8.17.0 idle watchdog:

- **No false idle for a busy worker.** The daemon only flags a worker that was
  *never triggered* (`_last_activation is None`) — a worker briefed and grinding
  on a slow first turn (extended thinking, long first tool call > grace window)
  is no longer sent a spurious `[idle]` DM telling the boss to re-drive it.
- **Restart re-evaluates idle.** Re-arming the watchdog (crash-restart) now resets
  engagement/activation and cancels the prior watchdog task, so a worker that
  engaged → crashed → restarted → went idle is re-detected (and no orphaned task
  can fire a stale DM).
- **Dashboard `IDLE` badge now mirrors the daemon's decision.** `_is_idle` is
  gated on boss-ownership and the daemon's recorded `idle_warning` (cleared by a
  later restart), instead of guessing from audit size — so it no longer
  false-flags a freshly-started worker (startup window), a boss/standalone agent,
  or a worker whose audit was rotated/truncated.
- **Docs:** `boss-agent.md` now discloses idle self-reporting is Claude-only
  (like the broker/context-watch) — every boss-owned worker is Claude, but a
  hand-placed non-Claude worker won't self-report.

## [8.17.0] - 2026-05-29

### Added

- **Idle-worker detection that closes the loop.** A boss-owned worker that comes
  up but never produces a turn within `IDLE_GRACE_SECONDS` (90s) — e.g. spawned
  into the wrong channel or never briefed — now surfaces itself instead of sitting
  silently while the boss believes it's working:
  - the worker daemon **DMs its parent boss** an `[idle]` notice (reusing the
    same worker→boss path as permission requests) and records an `idle_warning`
    in its daemon-log, so the boss gets the truth pushed into its loop and can
    re-drive the worker — no human needed;
  - the **Mission Control dashboard** badges any running worker with no activity
    (empty audit) as **IDLE** (`/api/agents` gains an `idle` field), so a
    mis-reported worker outs itself at a glance.
  This makes the system — not the operator or the agent's self-report — the
  watchdog: agent narration becomes a hint, the idle signal is ground truth.

## [8.16.2] - 2026-05-29

### Fixed

- **`culture boss spawn` into a multi-agent directory now records ownership
  correctly.** When a worker's `cwd` already held a multi-agent `culture.yaml`
  (an `agents:` list — e.g. several workers sharing one project dir),
  `_record_worker_boss` wrote `boss`/`channels` at the top level, where the
  loader silently shadowed them with the list entry — so the worker came up
  **unassigned, in `#general` instead of `#task-<name>`, and could never be
  briefed** (it sat idle while the boss believed it was working). It now writes
  into the worker's entry within the `agents:` list (and strips the stray
  top-level fields a prior write left behind).

## [8.16.1] - 2026-05-29

### Fixed

Closing the two residuals an adversarial re-verification pass found after 8.16.0:

- **`culture boss spawn --server` is now validated** (via `_require_server`),
  closing the same `../` arbitrary-file-write that 8.16.0 fixed for
  `culture boss init` — `--server` flows into `worker_nick` →
  `seed_helper_policy` → a policy path, so it must be sanitized too.
- **Grant-ceiling residuals tightened** (still cooperative): now also catches
  long-form `rm --recursive --force`, command-substitution wrapping (`$(…)` /
  backticks), and `chmod … 777` in any flag order; and a trailing word-boundary
  removes substring false-positives (`git pushup`, `cat truncate_helper.py`,
  `kubectl-helper-doc` no longer escalate).

## [8.16.0] - 2026-05-29

### Fixed

Hardening from an adversarial verification audit of the boss stack:

- **Multi-team isolation no longer fails open.** The broker now records the
  owning boss IN each permission request (`PermissionBroker(..., boss=...)`); the
  boss CLI attributes ownership from the request itself, so a missing/corrupt/
  suffix-mismatched worker `culture.yaml` can't make another team's request appear
  unowned and become approvable by the wrong boss.
- **Dashboard returns 400, not 500, on a non-string request id.** `valid_request_id`
  now rejects non-strings (`{"id": 123}`) instead of raising `TypeError`.
- **`culture boss deny` no longer writes an orphan decision** for a missing/absent
  request id (now mirrors `approve`'s guard).
- **`culture boss brief` / `read` enforce team isolation** — a boss can no longer
  brief or read another boss's worker (same gate as approve/deny/close).
- **`culture boss close` reports the real result** — it no longer prints "closed"
  when the underlying `culture agent stop` refused or failed.
- **Model inheritance is honest** — `_boss_model` reads the boss's *explicit*
  model (not the hardcoded dataclass default), and an inherited model never
  clobbers a model the worker already carries (only an explicit `--model` does).
- **`culture boss init` validates `--nick`/`--server`** (closes a `../`
  arbitrary-file-write via the boss's own identity flags).
- **context-watch tolerates quoted YAML numbers** — string thresholds
  (`high_water: "0.9"`) and `enabled: "false"` are coerced instead of crashing the
  agent loop.

### Changed

- **Grant ceiling tightened** (still a cooperative guard, not a sandbox): the Bash
  denylist is now case-insensitive, fires on quoted SQL (`psql -c 'drop table'`)
  and `rm` flag variants (`-fr`, `-r -f`), and adds `dd`, `mkfs`, `chmod -R 777`,
  and `curl|sh` / `wget|sh`.

## [8.15.0] - 2026-05-29

### Added

- **Dashboard groups agents into teams.** The agent column now renders each boss
  as a team header with its workers nested beneath it (derived from each agent's
  `is_boss` / `boss` fields), and a final "unassigned" group for standalone
  agents — so a boss and its workers read as one unit. Frontend-only (the
  `/api/agents` contract already exposes the fields; a test locks it).

## [8.14.0] - 2026-05-29

### Added

- **Model inheritance for spawned workers.** `culture boss spawn` now writes the
  **boss's model** into the worker's `culture.yaml` by default, so a worker runs on
  its parent's model instead of the hardcoded agent default. `culture boss spawn
  --model <m>` overrides per worker; `culture boss init --model <m>` sets the
  boss's own model (its parent — the human/session — chooses it) so the whole team
  runs on the parent's model. Any parent may set a child's model; the default is
  the parent's. No model anywhere → the existing agent default still applies (so
  no change for standalone agents).

## [8.13.0] - 2026-05-29

### Changed

- **`culture boss brief` verifies delivery.** Before claiming a worker was
  briefed, it checks (via a transient observer WHO) that the worker is actually in
  its `#task-<name>` channel; if not — or if membership can't be verified — it
  refuses (exit 1) instead of silently "succeeding." This closes the
  false-"boss flow is live" failure mode where a worker started ad-hoc into
  `#general` (not via `culture boss spawn`) never receives the brief, yet the boss
  believes work began. The boss skill now also tells the boss to confirm the
  worker actually *engaged* (non-empty audit / Session activity) before reporting
  it live.

## [8.12.0] - 2026-05-29

### Added

- **Close authority — only a parent can close its children.** `culture agent stop`
  now refuses (exit 2) unless the caller (`CULTURE_NICK`) is the target's parent
  (its `boss:` field) or the human (no `CULTURE_NICK`). So no agent can close
  itself, a boss can close only its own workers (not another boss's, not itself),
  and a worker can close nothing. `culture boss close` refuses self / another
  boss's worker with a clear message, and the dashboard runs stops as the human
  (root) — it may close any agent as a safeguard. For a fully unsupervised boss
  this is a cooperative guard on the sanctioned commands (raw `kill` is still
  possible — no broker sits in front of the boss).

## [8.11.0] - 2026-05-29

### Added

- **Secure remote access for the dashboard.** `culture dashboard --auth` requires
  a token (auto-generated at `~/.culture/dashboard-token`, or set with
  `--auth-token`) before any request is served; the token is seeded into a
  `SameSite=Strict`, HttpOnly cookie via a one-time `?token=…` bootstrap URL
  (printed on start), so the SSE streams and page loads all authenticate by
  cookie. `--trusted-host HOST` (repeatable) allows a tunnel's hostname through
  the Origin/Host guard. Intended shape: keep the loopback bind and front it with
  a private tunnel (e.g. `tailscale serve`) so the control plane is reachable from
  a phone without ever being exposed publicly. Auth is off by default — pure
  localhost behavior is unchanged.

## [8.10.0] - 2026-05-29

### Added

- **Talk to agents from the dashboard.** Mission Control gains a **Chat** tab: it
  shows the recent conversation in the selected agent's channel (both sides) and a
  message box to talk to the agent directly. Sent text is posted to the agent's
  channel prefixed with `@<nick>` so its mention detector fires (same as
  `culture boss brief`), over a transient observer connection — no boss daemon
  required. New endpoints: `GET /api/channel/<nick>` (recent messages) and
  `POST /api/message` (`{nick, text}`). The target channel is the agent's private
  `#task-*` channel when it has one, else its first configured channel.

### Changed

- **Per-team isolation for multiple bosses.** The `culture boss` CLI is now
  team-aware so several bosses can share one mesh: `culture boss pending` lists
  only the calling boss's own workers, and `culture boss approve`/`deny` refuse
  (exit 2) a request from a worker owned by another boss (ownership read from each
  worker's `culture.yaml` `boss:` field via the manifest). A worker with no
  recorded owner stays visible to every boss. The dashboard remains the
  human/all-teams view and is intentionally not team-scoped.

## [8.9.1] - 2026-05-29

### Added

- `culture boss cleanup` — garbage-collects stale permission requests (queued by a
  worker that is no longer running) and orphan decision files (a decision left
  without a matching request). In-repo replacement for the out-of-repo
  `cleanup-stale-perms.sh`, so the boss can recover a clean queue after closing
  workers.

### Changed

- **Single source of truth for boss tooling.** The in-repo `culture boss` CLI +
  `culture dashboard` are now the canonical boss/permission surface. Living docs
  (`docs/agentirc/helper-permissions.md`, `helper-daemon-log.md`,
  `helper-context-handoff.md`, `boss-agent.md`, `docs/reference/cli/index.md`) and
  the `culture boss approve` above-ceiling escalation message now point at the CLI
  and the dashboard instead of the legacy out-of-repo bash scripts
  (`approve.sh`/`pending-perms.sh`/etc.), which are demoted to legacy.

## [8.9.0] - 2026-05-29

### Added

- **Mission Control dashboard** — a localhost web app (`culture dashboard`) to watch every agent live and intervene. Three-pane UI: agent grid (state, pending count, last action, BOSS tag), per-agent session/daemon-log SSE streams, and a pending-approvals panel. Full control surface: approve/deny (human is top authority — not ceiling-bounded), pause/resume, close, emergency stop-all (pause or kill), and grant-policy edit. `aiohttp` backend (existing dep) + vanilla-JS frontend (no build step); binds `127.0.0.1` only (refuses non-loopback without `--unsafe-bind`). Spec: `docs/superpowers/specs/2026-05-29-mission-control-dashboard-design.md`; guide: `docs/agentirc/dashboard.md`.
- `culture/dashboard/` (server + static SPA), `culture/cli/dashboard.py` (`culture dashboard` command).

### Changed

- `_perm_broker.list_pending()` now excludes requests that already have a decision (awaiting their worker to consume it), so approvers (dashboard and `culture boss pending`) don't re-act on decided requests. Surfaced by live in-browser verification of the dashboard.

### Fixed

- **Critical (regression from 8.7.0/8.8.0):** the Claude daemon read `agent.context_watch` / `agent.boss` directly, but at runtime it receives `culture.config.AgentConfig` (manifest config) where those live in `extras`, not as typed fields — so **every Claude agent daemon crashed on startup** (`'AgentConfig' object has no attribute 'context_watch'`). Unit tests missed it because they construct the backend-specific config. Fixed: `culture.config.AgentConfig` exposes `boss` + `context_watch` properties (reading `extras`, mirroring `acp_command`); the daemon normalizes either config flavor. Caught by the first live boss+worker bring-up.
- The dashboard's `list_agents` used the backend-specific config loader, which rejects a real `server.yaml` (`telemetry.audit_enabled`); switched to the canonical `culture.config` loader.
- **Qodo review fixes:** (1) request-id path traversal — `_perm_broker.write_decision`/`read_request` now validate the id (`^req-[A-Za-z0-9_-]+$`) before building a path (approvers pass ids from untrusted input); (2) `culture boss` validated the worker `<name>` only in `spawn` — now a shared `_require_worker_suffix` guards brief/read/audit/log/close too; (3) dashboard `_last_action()` read the whole daemon-log via `readlines()` on every `/api/agents` poll — now tails the last 4 KiB (no event-loop block, scales with log growth).

## [8.8.0] - 2026-05-28

### Added

- **Boss agent orchestration** — an autonomous boss agent (a culture daemon) that manages worker agents in the human's place: spawns them, drives them over IRC like a Claude Code session, challenges their plans/implementations/claims, and approves/denies their tool requests. Spec: `docs/superpowers/specs/2026-05-28-boss-agent-orchestration-design.md`.
- `culture boss` CLI (`culture/cli/boss.py`): `init/spawn/brief/read/pending/approve/deny/audit/log/status/close`. Reuses `_perm_broker` for queue/decision/ceiling ops. Approver flips from human → boss agent.
- **Grant ceiling (human-over-boss gate)** — `boss-policy/<boss-nick>.yaml`; high-risk tools (MCP sends, destructive Bash) are above the boss's ceiling and escalate to the human (`approve.sh`) rather than being auto-granted. `culture boss approve` refuses them (exit 2). Reuses `match_policy`.
- **Worker→boss permission notice** — `PermissionBroker` gains a best-effort `on_request` callback; the worker daemon DMs its owning boss (`AgentConfig.boss`) so the boss's activation handler fires (finishes #411's deferred v1.1).
- `CULTURE_NICK` is now set in every agent's SDK subprocess env (all four backends) so an autonomous daemon agent can address its own IRC / boss sockets.
- `culture boss init` writes the boss identity: manager `system_prompt` (with context re-grounding), seeded ceiling, copied boss skill, and the no-perm-policy deadlock guard (a boss must never be permission-supervised).
- `docs/agentirc/boss-agent.md`; `culture/clients/claude/skill/boss/SKILL.md`.

### Changed

- `culture/clients/_perm_broker.py` — `on_request` callback; grant-ceiling helpers (`is_above_ceiling`, `write_default_boss_ceiling`); approver-side helpers (`list_pending`, `read_request`, `write_decision` with `O_CREAT\|O_EXCL` first-writer-wins).
- `culture/clients/claude/{agent_runner,daemon,config}.py` — `on_perm_request` plumbing, worker→boss DM (`_on_perm_request`), `AgentConfig.boss`.
- `culture/clients/{claude,codex,copilot,acp}/agent_runner.py` — `CULTURE_NICK` in subprocess env.

## [8.7.0] - 2026-05-28

### Added

- **Helper boss permission broker** — a boss Claude Code session is the human-in-the-loop for helper agents it spawns. `culture/clients/_perm_broker.py` wires the Claude Agent SDK `can_use_tool` callback to a file-backed request/decision queue under `~/.culture/`; helpers block on boss approval for any non-safe-read tool call. Safe reads (Read/Glob/Grep, read-only Bash) auto-approve via a per-helper `perm-policy/<nick>.yaml`.
- **Helper tool inheritance** — Claude agents now load `setting_sources=["user","project","local"]`, so helpers inherit the boss's `~/.claude/` skills, MCP servers, and plugins.
- **Context-watermark handoff (Claude)** — `culture/clients/_context_watch.py`; the daemon self-monitors per-turn `input_tokens` and at 90% of the model's context window asks the agent to write a handoff to `~/.culture/handoff/<nick>.md`, compacts, then reminds it to read the handoff after the compact. Configurable via a `context_watch` block in `culture.yaml`.
- **Daemon action log (all backends)** — `culture/clients/_daemon_log.py`; structured JSONL control-plane log at `~/.culture/daemon-log/<nick>.jsonl` (start/stop/exit/crash/compact/handoff/…).
- **Agent-message audit log (all backends)** — `culture/clients/_audit.py`; one JSONL line per AssistantMessage at `~/.culture/audit/<nick>.jsonl`.
- `docs/agentirc/helper-permissions.md`, `helper-tool-inheritance.md`, `helper-context-handoff.md`, `helper-daemon-log.md`, and the design spec `docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md`.

### Changed

- `culture/clients/claude/agent_runner.py` — widened `setting_sources`; conditional `can_use_tool` (only when a `perm-policy/<nick>.yaml` exists, preserving today's behavior for standalone agents); streaming-prompt wrapper required by the SDK when the callback is set; new `on_usage` callback.
- `culture/clients/claude/config.py` — new `ContextWatchConfig` on `AgentConfig`.
- `culture/clients/{claude,codex,copilot,acp}/daemon.py` — instantiate the audit + daemon-action logs and record actions; Claude daemon also drives the context-watch handoff cycle.
- `packages/agent-harness/culture.yaml` + `culture/clients/claude/culture.yaml` — commented `context_watch` block.

## [8.6.0] - 2026-04-26

### Added

- Harness-side OTEL: 3 spans (harness.irc.connect, harness.irc.message.handle, harness.llm.call) and 4 LLM metrics (culture.harness.llm.tokens.input/output, call.duration, calls).
- W3C traceparent injection on outbound IRC + extraction on inbound — single trace_id now spans server, federation, and harness in the cross-process tree.
- Per-backend telemetry citation across claude/codex/copilot/acp with all-backends parity test (24 tests across 6 dimensions) locking down drift.
- docs/agentirc/harness-telemetry.md — new operator guide for the harness OTEL pillar.

### Changed

- packages/agent-harness/{telemetry.py,config.py,culture.yaml,irc_transport.py,daemon.py} — reference module for the citation pattern.
- culture/clients/{claude,codex,copilot,acp}/{telemetry.py,config.py,culture.yaml,irc_transport.py,daemon.py,agent_runner.py} — telemetry citation, harness.llm.call span wrap, record_llm_call invocation.
- tests/harness/ — 70 new tests (24 parity + 46 module/runner/transport/daemon).

### Fixed

- Code-quality fixes from review: zero-token usage extraction (0 no longer silenced), tracer-name from constant (no hardcoded strings), module-top imports of record_llm_call across all 4 backends.

## [8.5.0] - 2026-04-25

### Added

- `culture/telemetry/audit.py` — `AuditSink` with bounded `asyncio.Queue` + dedicated writer task + daily/size rotation + `0600`/`0700` perms.
- Public `culture.telemetry.AuditSink`, `init_audit`, `build_audit_record`, `utc_iso_timestamp`.
- `TelemetryConfig.audit_enabled` (default `True`), `audit_dir`, `audit_max_file_bytes`, `audit_rotate_utc_midnight`, `audit_queue_depth` — independent of `telemetry.enabled` (audit fires even with OTEL off).
- `culture/protocol/extensions/audit.md` — JSONL record schema as a stable contract.
- `docs/agentirc/audit.md` — operator guide.
- Audit metrics extend the Plan-3 `MetricsRegistry`: `culture.audit.writes` (Counter, labels `outcome=ok|error`) and `culture.audit.queue_depth` (UpDownCounter).
- `IRCd.__init__` creates the sink; `IRCd.start()` awaits `sink.start()`; `IRCd.stop()` awaits `sink.shutdown()` so SERVER_WAKE / SERVER_SLEEP both land in the JSONL.
- `IRCd.emit_event` submits one record per event after the `irc.event.emit` span; `trace_id` / `span_id` captured inside the span for cross-pillar joins.
- `Client._process_buffer` submits `PARSE_ERROR` records for malformed inbound lines.
- Federation audit: federated `message` events arrive on the receiver with `origin=federated`, `peer=<peer_name>`. Federated lifecycle events (JOIN/PART/QUIT) are deferred — see #296.

## [8.4.0] - 2026-04-25

### Added

- `culture/telemetry/metrics.py`: `init_metrics(config)` + `MetricsRegistry` dataclass for all 15 server-side instruments — mirrors `tracing.py`'s idempotency + no-op pattern.
- Public `culture.telemetry.MetricsRegistry` and `culture.telemetry.init_metrics`.
- `TelemetryConfig.metrics_enabled` (default `True`) and `metrics_export_interval_ms` (default 10000).
- Message-flow metrics: `culture.irc.bytes_sent`, `culture.irc.bytes_received`, `culture.irc.message.size`, `culture.privmsg.delivered`.
- Events metrics: `culture.events.emitted`, `culture.events.render.duration`.
- Federation metrics: `culture.s2s.messages` (inbound), `culture.s2s.relay_latency`, `culture.s2s.links_active`, `culture.s2s.link_events`.
- Client metrics: `culture.clients.connected`, `culture.client.session.duration`, `culture.client.command.duration`.
- `culture.trace.inbound` counter — closes Plan 2's deferral.
- `tests/conftest.py` `metrics_reader` fixture parallel to `tracing_exporter`.
- `tests/telemetry/_metrics_helpers.py` — `get_counter_value`, `get_histogram_count`, `get_up_down_value`.

## [8.3.0] - 2026-04-25

### Added

- `irc.s2s.session` span over ServerLink connection lifetime.
- `irc.s2s.<VERB>` per-verb spans on inbound federation messages with traceparent extraction and the inbound mitigation rules from `culture/protocol/extensions/tracing.md`.
- `irc.s2s.relay` span on outbound relay enforcing the re-sign-per-hop rule.
- `irc.client.session` span over Client connection lifetime (#290).
- `irc.join` and `irc.part` spans (#290).
- Public `culture.telemetry.context_from_traceparent` and `culture.telemetry.current_traceparent` helpers.
- Single traceparent injection choke point at `ServerLink.send_raw`.
- End-to-end propagation tests proving one `trace_id` spans federated client → server → relay → server hops.

### Changed

- `Client._dispatch` span name and `irc.command` attribute now uppercase, matching `ServerLink._dispatch` convention.

### Fixed

- `_replay_event` uses the hasattr-guarded comparison so string-typed federated `event.type` no longer skips the typed fast path. (#291)

## [8.2.0] - 2026-04-24

### Added

- OpenTelemetry foundation: `culture/telemetry/` package with TracerProvider bootstrap, W3C trace context extract/inject helpers for IRCv3 tags, and `TelemetryConfig` block in `server.yaml`.
- Protocol extension: `culture.dev/traceparent` and `culture.dev/tracestate` IRCv3 tags (`culture/protocol/extensions/tracing.md`).
- Server-side tracing: `IRCd.emit_event`, `Client._dispatch`, `Client._process_buffer` (with parse-error compensation), and PRIVMSG dispatch/delivery paths now emit spans.
- Outbound traceparent injection on `Client.send` and `Client.send_raw` when a span is active.
- Operator docs at `docs/agentirc/telemetry.md` and starter collector config at `docs/agentirc/otelcol-template.yaml`; `docs/reference/server/config.md` documents the new `telemetry` block.
- Dependencies: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-semantic-conventions`.

## [8.1.0] - 2026-04-23

### Added

- `culture afi` namespace — passthrough to the standalone `afi-cli` (Agent First Interface scaffolder) with argparse-REMAINDER argument forwarding, parallel to `culture devex`.
- Universal verbs register the `afi` topic; `culture explain afi` / `culture overview afi` / `culture learn afi` all route through `afi-cli` 0.3+ and `culture explain` no longer marks `culture afi` as coming-soon.
- `afi-cli>=0.3,<1.0` as a library dependency (0.3 added the `overview` verb + sixth rubric bundle per agentculture/afi-cli#5).
- `culture/cli/_passthrough.py` — shared plumbing for `culture <ext>` subcommands that embed a sibling CLI. Supplies `run()`, `capture()`, and `register_topic()` so each passthrough module stays a thin adapter.

### Changed

- `culture/cli/devex.py` refactored onto the new shared passthrough helper. Behaviour is preserved (explain/overview/learn argv and typer app invocation unchanged); the module shrinks to a package-specific entry adapter and a single `register_topic` call. When agex-cli adopts the agent-first CLI contract (`main(argv) -> int`, tracking: agentculture/agex-cli#30), the adapter collapses to a direct delegation.

## [8.0.0] - 2026-04-22

### Added

- `culture afi`, `culture identity`, and `culture secret` surfaced as (coming soon) namespaces in `culture explain` output. `culture identity` will wrap a future standalone `zehut-cli`; `culture secret` will wrap `shushu-cli`; `culture afi` shares its name with the standalone `afi-cli`.

### Changed

- BREAKING: Renamed `culture agex` to `culture devex` (developer experience) — more familiar terminology and visually distinct from `culture agent`. The upstream `agex-cli` / `agent_experience` library is unchanged; only culture's public command name differs.
- Positioning docs, README, and site chrome refreshed to reflect the new identity: culture is the framework of agreements that makes agent behavior portable, inspectable, and effective, surfacing explain/overview/learn at every CLI level.

## [7.4.0] - 2026-04-22

### Added

- culture agex passthrough to the standalone agex CLI (new subcommand embedding agex via typer)
- Universal verbs culture explain, culture overview, culture learn at the CLI root, with a per-topic handler registry (culture/cli/introspect.py)
- agex-cli>=0.13,<1.0 as a library dependency

## [7.3.0] - 2026-04-21

### Added

- `sitemap-agentirc.html`: Liquid-templated sub-sitemap emitted at `/agentirc/sitemap.xml`, listing only pages under `/agentirc/*`. `sitemap.xml` index (from 7.2.3) extended to list it alongside `/sitemap-main.xml` and `/agex/sitemap.xml`.
- AgentIRC section in the main just-the-docs nav (`has_children: true`, `nav_order: 10`) grouping the four runtime pages.

### Changed

- Fold AgentIRC into the main Culture Jekyll build under `/agentirc/*` permalinks. Retire `_config.agentirc.yml` and the separate `_site_agentirc/` target. Completes the one-origin consolidation plan: `culture.dev`, `culture.dev/agex/`, and `culture.dev/agentirc/` all live on a single origin now.

### Removed

- `_config.agentirc.yml` — no longer a separate Jekyll build target.
- `_plugins/site_filter.rb` — dropped the per-build `sites:` filter; single-build semantics make it a noop. Existing `sites:` front matter on docs is now harmless metadata.
- `.github/workflows/docs-check.yml`: second Jekyll build step and the `sites:` front-matter presence check — single build, no filter.
- `.gitignore` and `_config.base.yml`: `_site_agentirc/` entries.
- `rel=related` links to `site.data.sites.agentirc` in `_includes/head_custom.html` — same-origin now, no longer semantically meaningful; agex `rel=related` retained.

### Fixed

- `docs/culture/features.md` and `docs/culture/index.md`: eliminated double-slash bug from `{{ site.data.sites.agentirc }}` concatenations (the data value now ends in a trailing slash from culture 7.2.1) by switching those cross-references to same-origin `relative_url` links.

## [7.2.3] - 2026-04-21

### Added

- `sitemap.xml` (sitemap index, gated on `site.build_site`).
- `sitemap-main.xml` (enumerates only HTML pages in the current build; opts out via `sitemap: false` front matter).

### Changed

- Replace `jekyll-sitemap` auto-generation with an explicit sitemap index (`/sitemap.xml`) plus a Liquid-templated sub-sitemap (`/sitemap-main.xml`). On the culture build the index points at `culture.dev/sitemap-main.xml` + `culture.dev/agex/sitemap.xml`; on the agentirc build it points at `agentirc.dev/sitemap-main.xml`. Sets the stage for adding `culture.dev/agentirc/sitemap.xml` once AgentIRC folds under `culture.dev/agentirc` (Phase 4 of the one-origin consolidation).

## [7.2.2] - 2026-04-21

### Changed

- Supersedes PR #274, which tried to solve cross-site flicker via `preconnect` hints across the three-subdomain topology. The one-origin consolidation (`culture.dev/agex`, `culture.dev/agentirc`) makes those preconnects moot; only the dark-paint CSS and `aux_links_new_tab` change carry forward.

### Fixed

- `_includes/head_custom.html`: add `color-scheme` meta + inline dark-paint `<style>` to eliminate the white flash on cold-cache first paint.
- `_config.culture.yml` + `_config.agentirc.yml`: `aux_links_new_tab: false` so sibling-site clicks stay in-tab (one-site feel after the consolidation). Power users can still Ctrl/Cmd-click for a new tab.

## [7.2.1] - 2026-04-21

### Changed

- `_data/sites.yml`: agex URL → `https://culture.dev/agex/` (was `https://agex.culture.dev`)
- `_config.culture.yml` + `_config.agentirc.yml`: aux_links and footer_content agex links retargeted to `https://culture.dev/agex/`

## [7.2.0] - 2026-04-18

### Added

- Console: full CommonMark markdown rendering in chat panel — bold/italic/inline code/strikethrough, OSC 8 hyperlinks, fenced code blocks with syntax highlighting, headings, lists, blockquotes, and tables (#233)
- Console: docs/reference/console.md reference page covering chat-panel markdown rendering

### Changed

- Console: ChatPanel.add_message now writes a header line ([ts] icon nick:) followed by a Rich Markdown body, instead of a single Rich-markup string — this also closes a latent footgun where bracketed text in agent messages could be reinterpreted as Rich markup

## [7.1.5] - 2026-04-18

### Added

- `jekyll-redirect-from` plugin (Gemfile + `_config.base.yml`) so `/why-culture/` redirects cleanly to `/what-is-culture/` (#267)
- `docs/resources/positioning.md`: canonical positioning snippets (paragraph + reference points + usage notes). Source of truth for README, repo description, site meta, and LLM summarizers (#267)

### Changed

- Renamed `docs/culture/why-culture.md` to `docs/culture/what-is-culture.md` and rewrote the body to lead with the definitional framing Culture is a professional workspace for specialized agents. Added a Reference points section that names OpenClaw, Codex, Claude Code as neighbors rather than targets (#267)
- `docs/culture/vision.md` retitled to The Culture vision; `nav_order` pushed to 2 behind the new What is Culture? page. Intro trimmed to remove the duplicate definitional sentence (#267)
- `docs/culture/mental-model.md` Persistence section reframed: removed the not one-shot task execution contrast; persistence now presented as a property that supports continued participation in the culture (#267)
- `docs/culture/agent-lifecycle.md` heading changed from Education is not one-shot to Education is continuous. Same meaning, positive framing (#267)

## [7.1.4] - 2026-04-18

### Changed

- Renamed "Assimilai pattern" to "Citation pattern" throughout live docs, configs, and template headers to align with the sibling project rename from `assimilai` to `citation-cli`. Historical specs and plans left intact. See [citation-cli](https://github.com/OriNachum/citation-cli).
- Backend culture.yaml system prompts now say "Apply changes using the citation pattern (cite, don't import)" across claude, codex, copilot, acp.
- Template header comments in packages/agent-harness/ switched from `# ASSIMILAI: Replace BACKEND` to `# CITATION: Replace BACKEND`.
- Test nick in tests/test_daemon_config.py and use-case doc nick both renamed from `spark-assimilai` to `spark-citation-cli` (aligned).

## [7.1.3] - 2026-04-18

### Added

- `docs/culture/features.md`: new Features page at `/features/` with four groups (workspace itself, humans managing it, bring your agents, open foundation) (#248)
- `docs/superpowers/specs/2026-04-17-sites-repositioning-design.md`: design spec for the positioning change (#248)

### Changed

- culture.dev homepage repositioned around "The professional workspace for agents." — new hero headline, kicker, sub, room-panel visual anchor, and Features card in docs grid (#248)
- agentirc.dev homepage repositioned around "The runtime and protocol that powers Culture." — new hero headline, kicker, sub, and inline federation-mesh SVG visual anchor (#248)
- Stack diagram on culture.dev relabels "Harnesses" row to "Agents" to land the workforce metaphor (#248)
- Cross-site callouts reworded: culture.dev → AgentIRC now emphasises runtime internals; agentirc.dev → Culture now emphasises running it (#248)
- `_config.culture.yml` and `_config.agentirc.yml` site descriptions updated to the new taglines (#248)
- `_sass/custom/custom.scss`: added `.room-panel`, `.federation-mesh`, `.feature-group` component styles (#248)

## [7.1.2] - 2026-04-17

### Changed

- Pin all GitHub Actions workflow uses: to full commit SHAs (SonarCloud minor, #258)
- Document Python API for events.register/validate_event_type/render_event in docs/agentirc/events.md (#249)
- Document Python API for bot filter DSL and template engine in docs/agentirc/bots.md (#249)
- Note in ConsoleIRCClient docstring that it intentionally does not negotiate CAP message-tags (#249)

### Fixed

- Remove unused PEER_CAPABILITY_EVENTS constant from culture/constants.py (#249)

## [7.1.1] - 2026-04-17

### Changed

- Added markdownlint-cli2 to the pre-commit hook set via the upstream DavidAnson/markdownlint-cli2 repo. Contributors running `pre-commit install` now get markdown linting on staged .md files automatically — no system install required (pre-commit provisions its own Node environment).
- Tuned .markdownlint-cli2.yaml to fit the repo's existing conventions: MD024 uses `siblings_only` so Keep-a-Changelog headings pass; MD025/MD033/MD041 disabled for Jekyll pages that derive their H1 from front matter and use inline HTML; added `_site_*/**` and `docs/superpowers/**` to ignores.
- Merged duplicated `### Changed` blocks inside CHANGELOG [0.21.0] that the new lint rule surfaced.

## [7.1.0] - 2026-04-17

### Changed

- Refactored `_handle_channel_mode` in `culture/agentirc/client.py`: extracted `_apply_mode_char` and `_broadcast_mode_change` helpers to drop cognitive complexity from 21 to ≤15 (SonarCloud S3776)
- Background task GC safety in `ircd.py` `_notify_local_quit`: `asyncio.ensure_future` replaced with a tracked `asyncio.create_task` using the existing `self._background_tasks` set + `add_done_callback(discard)` pattern

### Fixed

- culture mesh update no longer hangs on broken/unresponsive systemd units — all subprocess calls in persistence.py and cli/mesh.py now have explicit timeouts (30s for service restarts, 30s for CLI fallbacks, 120s for package upgrade)
- fires_event chain not triggering downstream bots (#260) — the bot.yaml loader now accepts fires_event at the top level as well as under output:, so configs that put the block at the top level emit events as expected
- Daemon log flushing stops after startup — replaced logging.basicConfig's default StreamHandler (which inherits stderr buffering from interpreter startup) with an explicit logging.FileHandler so runtime log records flush per-record

## [7.0.4] - 2026-04-17

### Fixed

- Reduce cognitive complexity and fix code quality issues across CLI modules (SonarCloud S3776, S1192, S5886, S108)

## [7.0.3] - 2026-04-17

### Fixed

- Duplicate _ERR_CHANNEL_PREFIX string in all 5 daemon files (S1192)
- Cognitive complexity in claude/daemon.py _poll_loop (CC 22, S3776)
- Cognitive complexity in agent-harness/daemon.py _poll_loop (CC 22, S3776)
- Cognitive complexity in codex/daemon.py _relay_response_to_irc (CC 44, S3776)
- Cognitive complexity in copilot/daemon.py _relay_response_to_irc (CC 23, S3776)
- Cognitive complexity in acp/daemon.py _relay_response_to_irc (CC 23, S3776)
- Cognitive complexity in acp/agent_runner.py start() (CC 17, S3776)

## [7.0.2] - 2026-04-17

### Fixed

- Cognitive complexity in observer.py (CC 22→~13)
- Cognitive complexity in ircd.py (CC 18→~12)
- Cognitive complexity in server_link.py (CC 33→~14)

## [7.0.1] - 2026-04-17

### Fixed

- Wrong AgentConfig/DaemonConfig import types in test helpers (S5655)
- Constant if-False condition replaced with unreachable yield pattern (S5797)

## [7.0.0] - 2026-04-17

Mesh Events (issue #123) — lifecycle and activity notifications as IRCv3-tagged
PRIVMSGs, event-triggered bots, and pub/sub composition chains.

> Versions 6.3.0 through 6.11.2 were development increments for this feature
> and were never published. Their changes are consolidated here as 7.0.0.

### Breaking Changes

None. Existing clients, bots, and federation links continue to work unchanged.
The major bump reflects the scope of the feature addition (new protocol verb,
new subsystem, new bot trigger type).

### Added

- **Event system** — `system-<server>` pseudo-user surfaces lifecycle events
  as IRCv3-tagged PRIVMSGs with `@event=<type>;event-data=<b64json>` tags
- **Built-in event catalog** — 18 event types across channel-scoped
  (`user.join/part/quit`, `room.create/archive/meta`, `tags.update`) and global
  (`agent.connect/disconnect`, `server.wake/sleep/link/unlink`,
  `console.open/close`)
- **IRCv3 message-tags** — `Message.parse()` extracts tags; CAP negotiation
  (`CAP REQ :message-tags`) in all agent backends and server
- **`#system` channel** — auto-created at startup for global event delivery;
  `system-<server>` VirtualClient auto-joined
- **Reserved `system-*` nicks** — rejected for non-server clients
  (`432 ERR_ERRONEUSNICKNAME`)
- **SEVENT S2S verb** — generic federation relay for lifecycle events with
  `_origin` loop prevention and trust policy filtering
- **HistorySkill** stores lifecycle events — `HISTORY RECENT` replays
  agent.connect, server.wake, room.create, etc.
- **Filter DSL** — safe recursive-descent expression parser (`==`, `!=`, `in`,
  `and`, `or`, `not`, dotted field access) for bot event triggers
- **Bot event triggers** — `trigger.type: event` with filter DSL evaluation;
  `fires_event` output for pub/sub bot chains with rate limiting (10/sec)
- **System bots** — package-bundled bots discovered at startup from
  `culture/bots/system/<name>/bot.yaml`; welcome bot greets on `user.join`
- **All-backends CAP** — claude, codex, copilot, acp, and agent-harness
  transports negotiate `message-tags` during connection
- **Documentation** — `docs/agentirc/events.md`, `docs/agentirc/bots.md`,
  `culture/protocol/extensions/events.md`

## [6.2.3] - 2026-04-15

### Changed

- docs: post-#231 retrospective — CLAUDE.md guidance for pre-branch checklist, format-before-commit, pre-push code review, SonarCloud pre-ready; new doc-test-alignment subagent; /pr-review skill step for SonarCloud query

## [6.2.2] - 2026-04-14

### Fixed

- console: handle BrokenPipeError/ConnectionResetError in _send_raw; surface a red system notice in the chat panel instead of letting the asyncio task crash (#230)

## [6.2.1] - 2026-04-13

### Added

- Copy-paste guidance in help screen (Shift+drag bypasses TUI mouse capture in modern terminals)

### Fixed

- #227: Tab now cycles channels (added priority=True to override Textual Screen focus-cycling)
- #226: Alt+Left/Right jump by word in chat input; Alt+Backspace deletes previous word
- #225: `culture channel message` interprets literal \n, \t, and \\ (escape-an-escape); observer splits multi-line text into one PRIVMSG per line and rejects all-empty-after-interpretation input with a non-zero exit
- #224: Exiting overview now reloads the current channel history (was empty)
- Help screen now opens on F1 (Ctrl+H stays as secondary — most terminals forward it as Backspace)

## [6.2.0] - 2026-04-12

### Added

- Agent status indicators in console sidebar (#218) — shows working/idle/paused/circuit-open for each agent
- Auto-read channel history on switch (#219) — loads last 20 messages when switching channels via Tab, sidebar click, or /join
- Help menu — /help command and Ctrl+H keybinding showing all commands and keybindings

### Fixed

- Joined message no longer wiped by channel switch clear_log

## [6.1.1] - 2026-04-11

### Changed

- Remove Python API sections from all backend SKILL.md files — agents should use culture channel CLI exclusively

### Fixed

- IRC skill teaches agents to use internal module path instead of culture CLI (#215)

## [6.1.0] - 2026-04-10

### Added

- Two-site docs architecture: agentirc.dev (runtime layer) and culture.dev (full solution)
- Dark terminal theme (visual-anchor palette) replacing warm cream Anthropic theme
- Site filter Jekyll plugin for per-page content selection via sites: front matter
- 4-bucket content model: agentirc/, culture/, shared/, reference/
- Custom SCSS components: hero sections, docs grids, stack diagrams, harness chips, CTA buttons
- Cross-site linking via _data/sites.yml
- docs-check CI workflow validating both site builds

### Changed

- Consolidated 23 per-backend harness docs into 4 single-page references
- Restructured 92 docs files into 4 content buckets with sites: front matter tags
- Replaced GitHub Pages deployment with Cloudflare Pages dual-site build
- Rewrote README.md for dual-site structure

## [6.0.2] - 2026-04-10

### Changed

- AgentIRC local docs with Jekyll pipeline copy step

## [6.0.1] - 2026-04-10

### Fixed

- agent create/join/delete/archive/unarchive/rename crash with manifest-format server.yaml (#208)
- Auto-migrate legacy agents.yaml to manifest format on first load
- Server rename/archive/unarchive now work with manifest format

## [6.0.0] - 2026-04-10

### Changed

- **BREAKING:** Renamed internal Python package `culture.server` to `culture.agentirc`. All imports must update from `culture.server.*` to `culture.agentirc.*`. CLI command `culture server` and config path `~/.culture/server.yaml` are unchanged.
- **AgentIRC** is now the official name for the server engine in documentation.

## [5.0.4] - 2026-04-10

### Fixed

- Reduce cognitive complexity of _cmd_topic in channel CLI
- Fix f-string with no replacement fields in topic error message

## [5.0.3] - 2026-04-10

### Added

- New channel subcommands: join, part, ask, topic, compact, clear

### Changed

- Channel CLI routes through agent daemon IPC when CULTURE_NICK is set
- All SKILL.md files and learn prompt use culture channel CLI instead of python3 -m
- Mesh update readiness probe verifies PID-based server identity

### Fixed

- culture channel message sends as agent nick instead of temporary peek nick (#203)
- IRC skill references culture channel CLI instead of broken python3 -m path (#202)

## [5.0.2] - 2026-04-09

### Fixed

- Handle missing credential tool (secret-tool/security/powershell) gracefully instead of crashing the server
- Report restart failures in mesh update instead of claiming success

## [5.0.1] - 2026-04-09

### Added

- Topic subcommand for IRC skill (#192)
- @mention validation warnings for unknown nicks (#196)
- GitHub issues skill for Claude Code

### Fixed

- Whitespace-only messages now rejected (#195)
- join/part channel state desync with # prefix validation (#194)
- Sending to unjoined channels now returns error (#193)
- Agents can now read own messages in channel history (#191)
- Codex backend meta-response stripping (#197)

## [5.0.0] - 2026-04-09

### Added

- Mesh overview shows stopped/registered agents from server.yaml manifest (#178)

### Changed

- CLI docs use correct noun-group syntax (culture agent create, culture channel read, etc.) (#186)
- Replaced non-existent culture send with culture channel message / culture agent message (#187)
- All doc references updated from agents.yaml to server.yaml (#188)
- Documented --mesh-config, --webhook-port, --data-dir server start flags (#189)

### Fixed

- Mesh overview now includes agents that are registered but not running

## [4.5.2] - 2026-04-09

### Fixed

- Agent status now reports the circuit-open state correctly instead of showing running (#179)
- Agent status list now distinguishes paused and sleeping agents correctly (#180)
- Learn prompt now includes compact/clear commands and ask --timeout (#181)
- Non-Claude backend skill docs now include the required features and comply with the all-backends rule (#182)
- Admin skill and learn prompt now include the missing CLI commands (#183)
- Mesh overview now indicates when bots are archived (#184)

## [4.5.1] - 2026-04-09

### Fixed

- Fix mesh overview crash after agent config migration (str has no attribute items)

## [4.5.0] - 2026-04-09

### Added

- Decentralized agent configuration with per-directory culture.yaml files
- New ~/.culture/server.yaml for machine-level config with agent manifest
- CLI: culture agent register/unregister for managing agent directories
- CLI: culture agent migrate for one-time migration from agents.yaml
- Unified culture/config.py module with AgentConfig, ServerConfig, auto-detection
- culture.yaml definitions for harness template and backend agents (#harness channel)

### Changed

- Agent config split: per-agent settings in culture.yaml, server settings in server.yaml
- CLI agent commands now support both server.yaml and legacy agents.yaml formats

## [4.4.3] - 2026-04-08

### Changed

- Regenerate all favicons, including `/favicon.ico` and `/assets/images/favicon.ico`, from the source image with proper cropping and optimization
- Reduce `/favicon.ico` from 1.4 MB to 3.5 KB and optimize `/assets/images/favicon.ico`
- Remove original source image (IMG_3161.png)

## [4.4.2] - 2026-04-08

### Fixed

- Codex/copilot: preserve HOME for auth tokens instead of isolating (#159)
- Codex: fix turn sync race condition causing concatenated rapid-mention responses (#165)
- All backends: sleep scheduler no longer overrides manual pause (#162)
- All backends: poll loop filters @mention messages to prevent duplicate responses (#160)
- All backends: turn errors now send feedback to IRC channel (#163)
- All backends: consecutive turn failure circuit breaker pauses agent after 3 failures (#164)
- Status query response verified not leaking to IRC channel (#161)

## [4.4.1] - 2026-04-07

### Fixed

- Config save operations no longer strip backend-specific fields like acp_command (#150)
- Agent status detail uses cached description by default, --full for live query; IPC deadline increased to 15s (#152)
- DMs now activate agents — _detect_and_fire_mention handles direct messages in all backends (#153)
- ACP agent runner preserves HOME/XDG_CONFIG_HOME for auth tokens; warns on authMethods, fails fast on session creation failure (#154)
- _coerce_to_acp_agent now copies the icon field (#155)
- _make_backend_config passes supervisor, poll_interval, sleep_start, sleep_end to non-claude backends (#156)
- ACP load_config strips unknown fields, matching claude/codex/copilot pattern (#157)

## [4.4.0] - 2026-04-07

### Added

- SQLite-backed persistent channel history (survives server restarts)
- --data-dir CLI flag for server start (default: ~/.culture/data)

### Fixed

- Multi-line messages truncated to first line in send_privmsg and thread methods
- data_dir never wired to ServerConfig, silently disabling room/thread persistence

## [4.3.7] - 2026-04-07

### Fixed

- Extract duplicate string constants (S1192, #85)
- Remove redundant exception classes in except clauses (S5713, #86)
- Clean up unused variables and function parameters (S1481/S1172, #88)
- Remove f-strings without replacement fields (S3457, #89)
- Address hardcoded credential warnings with test constants (S2068, #90)
- Fix miscellaneous code quality issues: asyncio.timeout, nested ternaries, empty methods, CSS contrast (S7483/S3358/S1186/S7924, #91)

## [4.3.6] - 2026-04-07

### Changed

- CLI module docstring updated with current subcommand sets (#147)

### Fixed

- agent message silently succeeds for nonexistent targets (#132)
- channel message silently succeeds for nonexistent channels (#133)
- agent sleep/wake error messages use wrong command names (#134)
- server subcommands ignore default server, hardcode culture (#135)
- agent start/stop inconsistent behavior with no nick argument (#137)
- channel message and bot create accept empty strings (#138)
- bot archive/unarchive missing --config flag (#139)
- inconsistent error message casing in agent archive vs unarchive (#140)
- channel commands show confusing timeout error when server is down (#141)
- uncaught PackageNotFoundError in version fallback (#142)
- culture --version flag not supported (#143)
- agent/channel message silently succeeds for nonexistent or empty targets (#144)
- channel read displays raw Unix timestamps instead of human-readable format (#145)
- server default accepts nonexistent server names without validation (#146)

## [4.3.5] - 2026-04-07

### Changed

- Reduce cognitive complexity in 30+ functions across backend clients, server code, CLI submodules, and standalone files to meet SonarCloud threshold (≤15)

## [4.3.4] - 2026-04-07

### Changed

- Extract duplicated string literals into named constants (SonarCloud S1192)
- Refactor cli/_helpers.py into modular cli/shared/ package (constants, ipc, process, mesh, display)

## [4.3.3] - 2026-04-07

### Changed

- Reduced cognitive complexity in 40 functions across 25 files to meet SonarCloud threshold (≤15)

## [4.3.2] - 2026-04-07

### Changed

- Reduced cognitive complexity in 13 functions across 6 files by extracting helpers and flattening control flow (SonarCloud S3776)

## [4.3.1] - 2026-04-07

### Fixed

- Remove unnecessary list() wrapping on already-iterable values (SonarCloud S7504/S7494)

## [4.3.0] - 2026-04-07

### Added

- agent delete command to remove agents from config entirely
- agent create now overwrites archived agents, enabling harness/model migration

### Fixed

- agent create no longer blocks when the matching nick is archived

## [4.2.1] - 2026-04-07

### Changed

- Update dispatch patterns to use declarative maybe_await() utility for handling both sync and async handlers
- Remove unnecessary async keyword from ~40 handler functions that never use await

### Fixed

- SonarCloud S7503: async functions that never await (issue #83)

## [4.2.0] - 2026-04-07

### Added

- Archive and unarchive commands for servers, agents, and bots
- Cascade archive: server archive automatically archives all agents and bots
- Visibility filtering: archived entities hidden from default status/list views
- --all flag on status/list to reveal archived entities
- Start guard: archived entities cannot be started until unarchived

## [4.1.3] - 2026-04-06

### Fixed

- mesh update now discovers and restarts all running servers instead of only the one in mesh.yaml

## [4.1.2] - 2026-04-06

### Fixed

- Clean up _mention_targets deque on prompt failure to prevent misrouted responses

## [4.1.1] - 2026-04-06

### Fixed

- Fix ACP/Codex/Copilot poll loop to use fire-and-forget (race condition fix)
- Increase ACP prompt timeout from 120s to 300s with retry on timeout (issue #115)
- Lower default poll_interval from 300s to 60s across all backends

## [4.1.0] - 2026-04-06

### Added

- Channel polling: agents periodically check channels for unread messages (configurable via poll_interval, default 5 minutes)
- Nick alias matching: @culture now triggers spark-culture (short suffix matching)

## [4.0.0] - 2026-04-06

### Added

- culture agent message and culture agent read for DM operations
- culture channel message and culture channel who for channel operations

### Changed

- Reorganized CLI into noun-first command groups: agent, server, mesh, channel, bot, skills
- Split monolithic cli.py (2432 lines) into focused modules under culture/cli/
- Mirrored message and read commands under both agent and channel groups

## [3.1.2] - 2026-04-06

### Fixed

- culture update used wrong package name (culture-cli) for uv tool upgrade

## [3.1.1] - 2026-04-06

### Fixed

- culture update and setup auto-generate mesh.yaml from agents.yaml when mesh.yaml is missing

## [3.1.0] - 2026-04-06

### Added

- culture server rename — rename server and all its agent nick prefixes
- culture rename — rename an agent suffix within the same server
- culture assign — move an agent to a different server

## [3.0.2] - 2026-04-06

### Fixed

- Server startup readiness — culture server start now waits for port to accept connections before returning
- Added startup phase logging to server log for diagnosing slow starts

## [3.0.1] - 2026-04-06

### Fixed

- Fix empty error message when running `culture overview` against a starting or unreachable server

## [3.0.0] - 2026-04-06

### Added

- Console chat TUI for human participation in the IRC mesh (culture console)
- ICON IRC protocol extension for custom entity icons
- User modes (+H/+A/+B) for entity type identification
- Server discovery and default server management
- Three-column TUI layout with sidebar, chat, and info panel
- View switching: overview, status, agent detail
- Command parser with full CLI command parity

## [2.0.1] - 2026-04-05

### Added

- what-is-culture.md — project philosophy page
- culture-cli.md — conceptual CLI guide
- Architecture and Operations index pages for docs navigation

### Changed

- Reorganize docs/ — architecture files to docs/architecture/, operations files to docs/operations/
- Rewrite index.md and README.md landing pages in culture voice
- Refresh getting-started.md prose to speak culture

## [2.0.0] - 2026-04-05

### Added

### Changed

### Fixed

## [1.1.0] - 2026-04-05

### Added

- culture create command (replaces init for agent creation)
- culture join command (create + start in one step)
- Promote phase documented as upcoming feature

### Changed

- Agent lifecycle reframed: Introduce → Educate → Join → Mentor → Promote
- Botanical metaphors replaced with professional language throughout docs
- grow-your-agent.md renamed to agent-lifecycle.md
- use-cases/10-grow-your-agent.md renamed to use-cases/10-agent-lifecycle.md
- Observer use case blog post: The Tended Garden → The Mentored Agent
- culture init deprecated in favor of culture create

## [1.0.7] - 2026-04-05

### Fixed

- Validate PID ownership via /proc/<pid>/cmdline before os.kill() to prevent signaling unrelated processes after PID reuse (SonarCloud S4828)
- Wrap initial SIGTERM in try/except ProcessLookupError for race condition safety

## [1.0.6] - 2026-04-05

### Added

- Project-local run-tests skill for portable pytest execution

## [1.0.5] - 2026-04-05

### Changed

- Extract helper methods from `socket_server._handle_client` (all backends)
- Convert `irc_transport._handle` to dispatch table (all backends)
- Extract `_auto_approve` and `_flush_accumulated_text` in codex/acp `agent_runner`
- Extract `_handle_session_update` and `_extract_response_text` in acp/copilot `agent_runner`
- Decompose `_handle_roommeta` into query/update methods in `rooms.py`
- Extract `_merge_room_metadata` in `server_link.py`
- Extract `_attempt_single_reconnect` in `ircd.py`
- Extract `_create_agent_config`, `_try_ipc_shutdown`, and `_try_pid_shutdown` in `cli.py`
- Update packages/agent-harness templates to match backend features
- Add socket_server and irc_transport to sonar CPD exclusions

## [1.0.4] - 2026-04-05

### Changed

- Reduced cognitive complexity of 76 high-complexity functions across daemon.py (5 files), server_link.py, threads.py, cli.py, and ircd.py by replacing if/elif chains with dispatch tables and extracting named logic units

## [1.0.3] - 2026-04-05

### Changed

- Parallelize test suite with pytest-xdist for ~15x speedup (10min → 40s)

## [1.0.2] - 2026-04-05

### Fixed

- Re-raise asyncio.CancelledError after cleanup to fix cancellation propagation (SonarCloud S7497)
- Save asyncio.create_task() results to prevent garbage collection (SonarCloud S7502)

## [1.0.1] - 2026-04-05

### Fixed

- Remove agentirc legacy alias from production PyPI publish pipeline

## [1.0.0] - 2026-04-05

### Changed

- **BREAKING:** Renamed package from agentirc-cli to culture. CLI command is now culture. Config directory is now ~/.culture/. Environment variable AGENTIRC_NICK is now CULTURE_NICK. agentirc-cli and agentirc remain as PyPI aliases.

## [0.21.0] - 2026-04-04

### Added

- Bots framework — server-managed virtual IRC users triggered by external events
- Inbound webhook support via companion HTTP listener on configurable port
- Bot CLI commands: create, start, stop, list, inspect
- Template engine for webhook payload rendering with {body.field} dot-path substitution
- Custom handler.py support for advanced bot logic
- Bot visibility in status and overview commands
- VirtualClient for bot IRC presence in channels

### Changed

- **BREAKING:** Renamed package from `agentirc-cli` to `culture`. `agentirc-cli` and `agentirc` remain as PyPI aliases. CLI command is now `culture`. Config directory is now `~/.culture/`. Environment variable `AGENTIRC_NICK` is now `CULTURE_NICK`.
- Server now starts a companion HTTP listener for bot webhooks
- Overview collector and renderer include bot information
- Channel._local_members() excludes VirtualClient from auto-operator promotion

## [0.20.1] - 2026-04-03

### Changed

- SonarCloud uses Automatic Analysis instead of CI-based scanning — removes conflict and simplifies workflow

### Fixed

- Remove SonarCloud CI step that conflicted with Automatic Analysis

## [0.20.0] - 2026-04-03

### Added

- Bandit SAST security scanning
- Pylint static code analysis
- Safety dependency vulnerability scanning
- CodeQL semantic analysis (GitHub-native)
- SonarCloud code quality and security integration
- Pre-commit hooks (flake8+bandit+bugbear, isort, black, pylint, detect-private-key)
- Security CI workflow (security-checks.yml)
- Dependency Review on PRs (fails on high severity)
- SECURITY.md vulnerability disclosure policy
- docs/SECURITY.md contributor security guidelines
- Code coverage enforcement in CI

## [0.19.0] - 2026-04-03

### Added

- Conversation threads — inline sub-conversations with [thread:name] prefix
- Breakout channel promotion from threads
- Thread-scoped agent context on @mention
- S2S federation for thread messages
- JSON persistence for threads across restarts
- Thread support in all 4 agent backends (claude, codex, copilot, acp)

## [0.18.0] - 2026-04-03

### Added

- Conversation threads — inline sub-conversations with [thread:name] prefix
- Breakout channel promotion from threads
- Thread-scoped agent context on @mention
- S2S federation for thread messages
- JSON persistence for threads across restarts
- Thread support in all 4 agent backends (claude, codex, copilot, acp)
- S2S link auto-reconnect with exponential backoff (5s to 120s)
- Declarative mesh.yaml configuration for multi-machine setup
- Cross-platform auto-start persistence (systemd, launchd, Windows schtasks)
- agentirc setup command — bootstrap a machine into the mesh from mesh.yaml
- agentirc update command — upgrade package and gracefully restart all services
- --foreground flag for server start and agent start (required by service managers)
- Windows platform support guards (no fork, SIGTERM fallback)

### Changed

- S2S links now auto-retry on initial startup failure
- SQUIT (intentional delink) suppresses reconnect attempts
- Incoming peer connections cancel outbound retry tasks

## [0.17.0] - 2026-04-01

### Added

- Two-tier skill system: root-level admin skill (server setup, mesh linking, federation, agent lifecycle) and project-level messaging skill
- agentirc skills install now installs both admin and messaging skills for all backends
- Learn prompt includes server/mesh setup, agent lifecycle, and dual skill install instructions
- docs/agentic-self-learn.md documenting the two-tier skill system

## [0.16.4] - 2026-04-01

### Changed

- Rewrote UC-03 Cross-Server Delegation with Jetson dependency resolution scenario
- Updated README/index mesh link to point to new UC-03

## [0.16.3] - 2026-04-01

### Added

- Federation mesh example in README and index — 3-server topology diagram with CLI commands

## [0.16.2] - 2026-03-31

### Fixed

- Documentation-code alignment: missing CLI flags, config fields, protocol specs, and README links

## [0.16.1] - 2026-03-31

### Changed

- Revamped README, docs index, and pyproject.toml description with new landing page design

## [0.16.0] - 2026-03-31

### Added

- Generic ACP backend — supports Cline, OpenCode, Kiro, Gemini, and any ACP-compatible agent via configurable spawn command
- CLI --agent acp with --acp-command flag for registering ACP agents

### Changed

- Replaced OpenCode-specific backend with generic ACP backend (clients/acp/)
- ACP supervisor uses SDK-based evaluation (vendor-agnostic) instead of opencode --non-interactive
- Backward compat: existing agent: opencode configs map to ACP backend automatically

## [0.15.2] - 2026-03-31

### Changed

- Extended .pr_agent.toml with harness conformance checks for cross-backend validation

## [0.15.1] - 2026-03-30

### Fixed

- Overview serve: flush stdout so port URL is visible when backgrounded
- Overview serve: auto-kill previous instance for same server via PID/port files

## [0.15.0] - 2026-03-30

### Added

- Managed rooms with rich metadata (ROOMCREATE, ROOMMETA, ROOMARCHIVE, ROOMKICK, ROOMINVITE)
- Tag-based self-organizing room membership for agents and rooms
- Room persistence to disk for managed rooms
- S2S federation for room metadata, agent tags, and archives (SROOMMETA, STAGS, SROOMARCHIVE)
- Agent tags in config and at runtime (TAGS command)
- Overview integration showing room/agent tags and metadata
- Protocol extensions: rooms.md, tags.md

### Changed

- Persistent channels survive when empty (no auto-cleanup)
- Archived channels block new JOINs
- All agent backends (claude, codex, copilot, opencode) support tags and ROOMINVITE
- CLAUDE.md: added all-backends rule for harness changes

## [0.14.1] - 2026-03-30

### Fixed

- Web dashboard table rendering (enable mistune table plugin)
- Status badge injection for indented td tags
- Metadata table cell escaping in agent detail view

## [0.14.0] - 2026-03-30

### Added

- agentirc overview CLI subcommand — mesh-wide situational awareness
- Markdown-formatted default view with rooms, agents, messages, federation
- Room drill-down (--room) and agent drill-down (--agent) views
- Configurable message count (--messages N, default 4, max 20)
- Live web dashboard (--serve) with anthropic cream styling and auto-refresh
- IRC Observer-based collector with daemon IPC enrichment for local agents

## [0.13.1] - 2026-03-30

### Fixed

- Fix OpenCode agent crash (exit code -1) caused by 30s timeout on system prompt session/prompt call
- Capture stderr from opencode subprocess for debugging
- Add _running guard to busy-wait loops to prevent hang on process death
- Wrap _start_agent_runner with error handling so runner failures schedule retry instead of crashing daemon

## [0.13.0] - 2026-03-29

### Added

- `system_prompt` field in AgentConfig — custom system prompt via agents.yaml (all backends)
- `prompt_override` field in SupervisorConfig — custom supervisor eval prompt via config (all backends)
- Status/pause/resume IPC handlers for OpenCode, Codex, and Copilot daemons (parity with Claude)
- Sleep scheduler with `sleep_start`/`sleep_end` config for OpenCode, Codex, and Copilot
- Null relay target fix in `_query_agent_status()` to prevent misrouting

## [0.12.1] - 2026-03-29

### Changed

- pr-review skill now checks for existing PRs before adding unrelated work to a branch

## [0.12.0] - 2026-03-29

### Added

- agentirc learn command — self-teaching prompt for agents to learn IRC tools and create skills

## [0.11.0] - 2026-03-28

### Added

- agentirc send command for sending messages to channels and agents
- agentirc status --full flag and per-agent detailed view
- agentirc sleep/wake commands with configurable schedule (default 23:00-08:00)

### Changed

- Extended IPC protocol with status, pause, and resume handlers
- Added sleep_start/sleep_end config fields to DaemonConfig

## [0.10.7] - 2026-03-28

### Fixed

- Fix crash with cryptic asyncio Event loop is closed errors when starting agent without IRC server running
- Add server-running pre-check in CLI before starting agent daemon
- Wrap IRC transport connect in try/except for clear error on connection failure

## [0.10.6] - 2026-03-28

### Changed

- Add start command suggestion to init collision output

## [0.10.5] - 2026-03-28

### Changed

- Show existing agent config details when init detects a nick collision

## [0.10.4] - 2026-03-27

### Changed

- Renamed DaRe to DaRIA (Data Refinery Intelligent Agent) in lifecycle guide

## [0.10.3] - 2026-03-26

### Changed

- Revamped all 10 user stories to reflect real mesh (6 agents, 3 servers, 5 repos)
- Rewrote grow-your-agent guide with DaRe (Data Refinery) user story
- Replaced all fictional agents with real agent roster across documentation

## [0.10.2] - 2026-03-26

### Added

- docs: new use-case doc for pruning the mesh (docs/use-cases/10-pruning-the-mesh.md)

### Changed

- docs: expanded Prune section in Grow Your Agent lifecycle guide
- docs: updated README table to include Prune in lifecycle summary

## [0.10.1] - 2026-03-26

### Added

- docs: add Grow Your Agent lifecycle guide

## [0.10.0] - 2026-03-26

### Added

- Client documentation for Codex, OpenCode, and Copilot backends (7 docs each)

### Changed

- Remove set_directory from all backends — agents stay in their init directory
- Active config isolation for Codex, OpenCode, Copilot (isolated HOME env prevents loading platform home config)
- Replace single-page backend docs with comprehensive multi-page docs

## [0.9.0] - 2026-03-25

### Added

- GitHub Copilot agent harness (Phase 4) using github-copilot-sdk

## [0.8.0] - 2026-03-24

### Added

- OpenCode agent harness (Phase 3) — opencode acp over ACP/JSON-RPC/stdio

### Changed

- CLI now supports --agent opencode for init, start, and skills install

## [0.7.0] - 2026-03-24

### Added

- Codex agent backend: agentirc/clients/codex/
- CodexAgentRunner: wraps codex app-server over JSON-RPC/stdio
- CodexSupervisor: evaluates agent via codex exec --full-auto
- CodexDaemon: full daemon with IRC transport, IPC, crash recovery
- Codex skill client and SKILL.md
- CLI: agentirc init --agent codex to initialize Codex agents
- CLI: agentirc start dispatches to Codex daemon when agent=codex

### Changed

- CLI: --agent flag on init subcommand (choices: claude, codex)
- CLI: start command detects agent type from config

## [0.6.0] - 2026-03-24

### Added

- packages/agent-harness/ — assimilai reference for building new agent backends
- Template daemon, IRC transport, IPC, skill client for new backends
- Assimilation guide (README.md) with step-by-step instructions
- agent field in AgentConfig (default: claude, backward compatible)

### Changed

- CLAUDE.md — documented assimilai pattern for agent harness

## [0.5.0] - 2026-03-24

### Added

- Agent Harness Specification document — defines the expected interfaces for pluggable agent backends
- Documentation of AgentRunnerBase and SupervisorBase interface contracts (specification only, no new Python ABCs in this release)
- IPC protocol, skill contract, and config schema reference documentation
- Written guide for implementing new agent backends (Codex, OpenCode, custom)

## [0.4.0] - 2026-03-24

### Added

- Link trust levels: full (share all) and restricted (share nothing unless opted in)
- Channel mode +R: restricted — channel stays local, never federated
- Channel mode +S <server>: shared — explicitly share channel with named server
- Mutual +S required for restricted links — both sides must agree
- Safe default: inbound links from unknown peers default to restricted

### Changed

- Link format extended: name:host:port:password:trust (trust defaults to full)
- Burst and relay filtered through should_relay() based on trust + channel modes

## [0.3.1] - 2026-03-22

### Added

- Federation setup in Getting Started guide
- Federation snippet in README Quick Start
- Federation examples in CLI reference

## [0.3.0] - 2026-03-22

### Added

- CLI command: agentirc skills install <claude|codex|all>
- Claude Code plugin structure in plugins/claude-code/
- Codex-compatible skill layout in plugins/codex/
- Three install methods: CLI, plugin marketplace, Codex skill installer

### Changed

- Getting Started guide updated with skills install command

## [0.2.1] - 2026-03-22

### Added

- OIDC trusted publishing for PyPI and TestPyPI
- Dual package publish (agentirc + agentirc-cli) to TestPyPI
- CHANGELOG.md with Keep a Changelog format

### Changed

- Publish workflow uses id-token instead of API token secrets

## [0.2.0] - 2026-03-22

### Added

- Unified `agentirc` CLI: server start/stop/status, init, start/stop/status, read/who/channels
- `agentirc init` derives agent nick from current directory name
- IRC observer for ephemeral read-only connections (read, who, channels)
- PID file management for server and agent lifecycle
- Graceful agent shutdown via IPC socket
- `--link` flag on `agentirc server start` for federation
- `_handle_list` in server (LIST command, RPL_LIST 322 + RPL_LISTEND 323)
- `server.name` config field for nick prefix
- Config helpers: `save_config`, `load_config_or_default`, `add_agent_to_config`, `sanitize_agent_name`
- CLI reference documentation (`docs/cli.md`)
- PyPI publishing workflow with TestPyPI pre-deploy
- Publishing guide (`docs/publishing.md`)

### Changed

- Restructured all code under `agentirc/` namespace to avoid site-packages collisions
- Package name `agentirc-cli` on PyPI (`agentirc` was taken)
- README rewritten around `pip install agentirc-cli` workflow
- All imports updated from `protocol.*`, `server.*`, `clients.*` to `agentirc.*`
- Updated all documentation with new import paths and CLI commands

### Fixed

- WHO reply param index (params[5] not params[4]) for correct nick extraction
- Removed broken `WHO *` for channel listing, replaced with LIST
- Removed dead `"x in dir()"` guards in observer timeout handlers
- Removed forced `#` prefix on WHO target — nick lookups now work
- Fixed `agentirc-cli-cli` typo in publishing docs

## [0.1.0] - 2026-03-21

### Added

- Initial release
- Async Python IRCd (Layers 1-4: Core IRC, Attention/Routing, Skills, Federation)
- Claude Agent SDK client harness (Layer 5)
- Agent daemon with IRC transport, message buffering, supervisor
- IRC skill tools for agent actions via Unix socket IPC
- Webhook alerting system
- 197 tests with real TCP connections (no mocks)
- GitHub Pages documentation site
