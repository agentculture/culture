# Build Plan — Culture now knows when its residents are busy: every agent (claude, codex, colleague, ...) publishes a live busy/idle activity signal to the mesh, and the server aggregates it into a resource view — active residents, token spend — that operators and balancing policies can act on

slug: `culture-now-knows-when-its-residents-are-busy-ever` · status: `exported` · from frame: `culture-now-knows-when-its-residents-are-busy-ever`

> Culture now knows when its residents are busy: every agent (claude, codex, colleague, ...) publishes a live busy/idle activity signal to the mesh, and the server aggregates it into a resource view — active residents, token spend — that operators and balancing policies can act on.

## Tasks

### t1 — PRESENCE protocol extension spec: write protocol/extensions/presence.md defining the new verb — structured payload (six-state enum idle|listening|thinking|working|draining|offline, since, current-task hint, token counters), heartbeat/refresh cadence, stale-T semantics, S2S propagation notes, and explicit backward-compat statement

- covers: c9, c11, c12, h3
- acceptance:
  - doc defines the full payload schema and maps EACH of the six states to the observable harness boundary that drives it (connect, message handle, LLM call open/close, tool exec, shutdown)
  - doc explicitly states no RFC 2812 command is redefined and that vanilla clients ignoring the verb see identical behavior; all wire/identity strings stay culture.*

### t2 — Engine config schema for budgets + presence policy: per-agent token_budget with warn thresholds in culture.yaml schema; mesh-wide presence settings (stale_after, heartbeat interval) in server.yaml — warn-only semantics, with defined spend window/reset behavior

- depends on: t1
- covers: c14, h6
- acceptance:
  - config round-trips: parse, validate, defaults; budget/presence keys documented in docs/; invalid values produce actionable CultureError, not tracebacks
  - spend window/reset semantics are explicitly documented (chosen in-task, e.g. per-UTC-day) and the accounting model survives agent restart; breach computation yields a warn flag in the resource-view data model, never an enforcement action

### t3 — Hand-off brief to agentirc: IRCd-side PRESENCE verb handling, per-client presence state, S2S propagation so cross-server residents appear, and the stale-busy watchdog (presumed-hung flag after stale-T without heartbeat)

- depends on: t1
- covers: c13, h5
- acceptance:
  - brief posted to agentculture/agentirc via the communicate skill, carrying the t1 wire contract and watchdog acceptance tests: a kill -9'd busy resident is flagged within T with zero cooperation; a slow-but-alive resident heartbeating through a long LLM call is NOT flagged
  - brief names the query surface contract the culture front door will read (per-resident state, since, spend, hung-flag, server-of-origin)

### t4 — Hand-off brief to cultureagent: busy-signal emitter in the SHARED harness transport, hooked at observable boundaries only, heartbeating through long LLM calls, token counts sourced from the same registry as the culture.harness.llm.tokens.* OTel counters (state-only where the SDK exposes none)

- depends on: t1
- covers: c6, h1, h4
- acceptance:
  - brief posted to agentculture/cultureagent with the state-transition table; requires the emitter in shared code so claude/codex/colleague all emit identically and backend-parity CI stays green — no per-backend forks
  - brief requires transitions driven only by code boundaries (never model self-report) and one counting source shared with existing OTel token counters

### t5 — culture residents CLI verb: front-door command reading the live server aggregation — human table + --json, showing per-resident state, since, token spend, budget %, presumed-hung flag, server of origin

- depends on: t1, t3
- covers: c2, c7, h2, h9
- acceptance:
  - against a live server with two residents (one mid-task) the command shows exactly one busy, from a live query; includes residents connected via S2S from other servers
  - --json output validates against a documented schema consumable by server-side policies; bare 'uv tool install culture' suffices; unreachable server yields actionable nonzero exit, not a traceback

### t7 — Resource-view data endpoint for irc-lens: expose the same aggregation the CLI reads as a documented JSON endpoint on the culture side, sharing one serializer with 'culture residents --json' so there is exactly one source of truth

- depends on: t5
- covers: c21
- acceptance:
  - endpoint payload is byte-compatible with the CLI --json schema (shared serializer, verified by a test that diffs the two); endpoint contract + auth story documented in docs/ for the irc-lens consumer

### t8 — Hand-off brief to irc-lens: a dedicated residents page at chat.agentculture.org rendering the endpoint data — per-resident state, spend, budget status — behind CF Access like the rest of the console, degrading to an 'IRCd down' notice instead of a 500 when 6667 is unreachable

- depends on: t7
- covers: c21, h15
- acceptance:
  - brief posted to the irc-lens repo with the t7 endpoint contract, the CF Access requirement, and the graceful-degrade requirement (explicitly citing the known console-500-when-6667-down failure mode)

### t6 — Live-mesh verification + observe-only audit: after sibling releases land and version floors bump, run the honesty battery on the spark mesh and record a verification log

- depends on: t2, t4, t5, t7, t8
- covers: c1, h8, c3, h10, c4, h11, c5, h12, c8, h13, h14, c18, h16
- acceptance:
  - verification log records: a resident flipping busy->idle live; two-residents-one-busy showing exactly one busy; a balancing decision made from shipped data alone; the success-signal command demo against spark-agentirc/spark-colleague; the before-state evidence (no AWAY, no in-band signal, OTel-only counters as of 2026-07-07)
  - weechat regression session shows identical vanilla-client behavior; diff audit confirms no RFC 2812 command redefined and no code path where the server declines/defers/blocks work on presence or spend — budget breach warns only

## Risks

- [unknown_nonblocking] heartbeat transport + interval: piggyback on existing IRC traffic vs dedicated periodic PRESENCE refresh, and the default stale-T — decided in t1/t3 design (task t1)
- [unknown_nonblocking] budget spend window/reset semantics (per-session vs per-day vs cumulative) and where the counter persists — decided in t2 (task t2)
- [unknown_nonblocking] where the aggregation query surface physically lives (inside the agentirc IRCd process vs the culture console seam) shapes t5/t6 plumbing — settled when the t3 brief is answered (task t3)
