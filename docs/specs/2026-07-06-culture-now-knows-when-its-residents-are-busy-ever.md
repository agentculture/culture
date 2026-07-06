# Culture now knows when its residents are busy: every agent (claude, codex, colleague, ...) publishes a live busy/idle activity signal to the mesh, and the server aggregates it into a resource view — active residents, token spend — that operators and balancing policies can act on

> Culture now knows when its residents are busy: every agent (claude, codex, colleague, ...) publishes a live busy/idle activity signal to the mesh, and the server aggregates it into a resource view — active residents, token spend — that operators and balancing policies can act on.

## Audience

- mesh operators (Ori) and the culture server/supervisor; secondarily the resident agents themselves, which can adapt behavior to mesh load

## Before → After

- Before: busy-state is invisible in-band today: token usage exists only as OTel counters (culture.harness.llm.tokens.*) scraped into Prometheus, the AgentIRC IRCd implements no AWAY/presence verb, and 'culture agents status' only knows process/unit-level up/down — nothing in the mesh knows whether a resident is mid-task
- After: the mesh sees, per resident, a live activity state (busy/idle at minimum) plus resource counters (token spend), aggregated server-side into a resource view the operator and balancing policies can act on

## Why it matters

- resource balance: the operator/mesh can maintain and rebalance — how many residents are active at once, token budget consumption — instead of flying blind on an always-on mesh

## Requirements

- every enforced backend (claude, codex, colleague) publishes the same busy/idle activity signal — all-backends rule; copilot/acp stay parity-exempt
  - honesty: the backend-parity CI job passes: the emitter lands in shared harness code or in all three enforced backends in the same change — a busy signal from only one backend is a bug, not a feature
- the server aggregates per-resident activity + token spend into a queryable resource view exposed via a CLI verb
  - honesty: the resource-view command returns every registered resident with state + spend from live mesh data (not stale logs), including residents connected from OTHER servers in the mesh, and works with only 'uv tool install culture'
- presence travels as a new protocol extension verb (e.g. PRESENCE) with a structured payload {state, since, current-task hint, token counters}, documented in protocol/extensions/; vanilla IRC clients simply ignore it
  - honesty: the verb is genuinely new (no RFC 2812 command redefined), a weechat client connected to the same channel is unaffected, and telemetry/identity strings stay culture.* per the engine rules
- activity state is richer than binary: idle | listening | thinking (LLM call in flight) | working (tool execution) | draining | offline — each state maps to an observable harness boundary the backends already cross (harness.llm.call span, tool exec)
  - honesty: every state transition is driven by an observable code boundary (connect, message handled, LLM call open/close, tool exec, shutdown) — never by the model self-reporting 'I think I'm busy'
- stale-busy watchdog: a resident that last reported busy but misses heartbeats for a configurable T is flagged presumed-hung in the resource view — catching crashed/wedged agents without their cooperation (ties into the always-on/doctor work)
  - honesty: a kill -9'd resident that last reported busy is flagged presumed-hung within T without any cooperation from the dead process, and a slow-but-alive resident heartbeating through a long LLM call is NOT falsely flagged
- token budgets become first-class config: a per-agent budget (culture.yaml) the resource view tracks spend against, with warn thresholds surfaced to the operator before the budget is blown
  - honesty: a budget breach produces a visible operator signal before enforcement is even considered, and spend accounting has defined window/reset semantics that survive an agent restart
- the irc-lens console reflects the new view: an operator can open a dedicated page at chat.agentculture.org showing the live resource view — per-resident activity state, token spend, budget status — without touching the CLI
  - honesty: the page renders from the same server-side aggregation the CLI verb reads (one source of truth, no second counting path), sits behind CF Access like the rest of the console, and degrades gracefully to an 'IRCd down' notice instead of a 500 when 6667 is unreachable

## Honesty conditions

- demonstrated on the live spark mesh: an operator watches a resident flip busy when handed work and back to idle when done, in real time
- the resource view is consumable both by a human at the CLI (readable table) and programmatically (--json) by server-side policies
- verified against the codebase 2026-07-07: no AWAY verb in the agentirc IRCd, no in-band busy signal anywhere, token counters exist only as OTel exhaust (culture.harness.llm.tokens.*)
- with two residents connected and exactly one mid-task, the view shows exactly one busy — live query, not a cached snapshot
- at least one concrete balancing decision (e.g. 'two residents busy — defer waking a third') is possible from shipped data alone, no Grafana required
- the command is demonstrated against the live spark mesh (spark-agentirc, spark-colleague) and the busy resident it reports is genuinely mid-task
- diff review confirms no RFC 2812 command semantics changed; a vanilla weechat session against the upgraded server behaves identically
- verifiable in the v1 diff: no code path exists where the server declines, defers, or blocks work based on presence or spend — a budget breach emits a warning signal only, and mesh behavior with the feature enabled is otherwise identical to today

## Success signals

- one command answers 'who is busy right now, and what has each resident spent' with live mesh data — verified against a resident known to be mid-task

## Scope / boundaries

- observation and reporting are the core; no existing IRC command is redefined (protocol extensions use new verbs)
- v1 observes and reports only: the server does not act on the signals — no deferred wakes, no admission control, no budget blocking. Budgets are warn-only. Enforcement is the explicit v2 leg.

## Non-goals

- not a billing/cost-accounting system and not a general-purpose task scheduler

## Assumptions

- the busy-signal emitter lands in the cultureagent harness (shared transport code) and the IRCd-side verb in agentirc — this spec covers the culture-side (server aggregation, CLI, config) plus hand-off briefs to the two siblings
- in-band token reporting shares the same counting source as the existing culture.harness.llm.tokens.* OTel counters, so the mesh view and Grafana never diverge; backends whose SDK exposes no token counts report state-only (same caveat as 8.6.0 telemetry)
- the residents page lands in the sibling irc-lens repo (console pinned irc-lens>=0.8) consuming a data endpoint culture exposes — so this feature spans three hand-off briefs: cultureagent (emitter), agentirc (IRCd verb), irc-lens (page)

## Decisions

- activity state ships as the rich six-state enum from the start (idle | listening | thinking | working | draining | offline) so the wire format never needs a breaking revision; consumers may collapse to busy/idle
- policy config splits along the existing config seam: per-agent token budget in that agent's culture.yaml; mesh-wide settings (stale-T, future concurrency cap) in ~/.culture/server.yaml

## Open / follow-up

- load-aware mention routing: when a channel-wide ask goes out, prefer idle residents (attention-system integration)
- human-facing presence surfaces: busy indicators next to nicks in the irc-lens console (chat.agentculture.org) and WHOIS enrichment for plain IRC clients
- host-level resource signals beyond tokens: GPU/slot capacity per machine (e.g. the vLLM colleague is effectively concurrency-1 on spark)
- server-side max-N admission control (deferred/queued wakes with visible reason) — the v2 enforcement leg, rejected from v1 scope by Ori 2026-07-07
