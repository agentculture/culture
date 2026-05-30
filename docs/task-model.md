# The culture task model

How culture represents **tasks**, **agents**, and **the orchestrator
that drives them**. Codified 2026-05-30 after the three-layer-vision
verification dogfood and the design discussion that followed.

This is a product/design doc — the implementation lives in
`culture/cli/boss.py`, `culture/agentirc/`, `culture/clients/`,
and the dashboard at `culture/dashboard/`. The doc is the canonical
shape; the code is the realization.

## TL;DR

```
Human                                                          ← business level
  │  briefs the orchestrator in chat
  ▼
Claude Code session (or in-mesh boss-agent)                    ← orchestrator
  │  spawns + drives via culture boss CLI
  ▼
Agents in N projects, each with a role, in shared channels     ← workers
```

- A **task** = a channel + a goal + a set of agents with roles.
- An **agent** = a persistent identity (active / stopped / archived).
- An **orchestrator** = either a Claude Code session driving from
  outside the mesh (Pattern A), or an autonomous in-mesh
  boss-agent (Pattern B).
- **st4ck MCP** composes above this for long-running task artifacts
  (specs, tests, dev tasks). Culture provides the live
  agent + channel + role layer; st4ck provides the persistent
  artifact layer for tasks that outgrow a single conversation.

## Tasks

A **task** lives in a channel. The channel IS the task envelope.

### Task = channel + goal + agents

| Field | Where it lives | Set by |
|---|---|---|
| Channel | IRC channel (`#task-<agent>` or `#joint-<name>`) | Orchestrator (auto on spawn, or explicit `irc_join`) |
| Goal | Channel topic / first brief / `culture channel goal` *(planned post-v8.19.x)* | Orchestrator |
| Agents | Channel membership | Orchestrator (spawns + joins) |
| Roles | `role:` field on each agent's `culture.yaml` *(planned post-v8.19.x)* | Orchestrator (spawn-time + editable) |
| State | `active` / `archived` | Orchestrator (`culture channel archive`, v8.19.1) |

The orchestrator owns the channel. Agents are tenants.

> **Status note** (added during PR #25 review): some of the CLI
> commands and config fields below are **not yet implemented** as
> of v8.19.x. The doc captures the target shape; rows are
> labeled *(planned post-v8.19.x)* where the actual code hasn't
> landed yet. The shipped baseline is:
>
> - **v8.19.1 (PR #27)** — agent + channel archiving (`culture
>   channel archive`, `culture agent archive`, `culture agent
>   restore`, `culture agent unarchive`).
> - **v8.19.0 (PR #416)** — `--channels` flag on `culture boss
>   spawn`.
> - **v8.18.6 (PR #24)** — model inheritance via
>   `model_resolved` daemon-log fallback.
>
> Deferred to follow-ups: `role:` field, `culture channel goal`,
> `culture agent compact`, `--ephemeral` / `--persistent` spawn
> flags, in-mesh boss-agent autonomous mission persistence
> (`fix-pattern-b`, blocked on all-backends propagation).

### Three task flavors

1. **Short-lived** (hours, single shot): "write the PRD",
   "audit security finding X". One or few agents, joint channel
   if multi-worker, archive when goal met.

2. **Long-lived** (days–weeks): "ship feature release X". Workers
   come and go (some "relieved" when their part is done). The
   joint channel persists through the release; sub-tasks within
   it are tracked in st4ck MCP if present, or as nested briefs
   in channel history.

3. **Cross-project very-long** (weeks–months): "st4ck QA feature
   development with ori + plenty as dogfood targets". The joint
   channel is **permanent** for the duration of the initiative
   (months). Agents per project (qa-ori, qa-plenty, dev-stack-1,
   etc.) persist across many sub-tasks within the initiative.
   Each sub-task (a specific bug, a specific feature PR) is
   tracked in st4ck MCP; the channel hosts the live
   coordination.

### Joint channels (`#joint-<name>`)

A `#joint-<name>` channel is the **multi-worker task envelope**.
Workers from different projects + bosses can join. Channel-layer
ACLs (v8.18.7, PR #415) gate `#task-<nick>` channels by owner +
boss, but joint channels are open to any registered agent — by
design, because the cross-project collaboration model requires it.

### Per-agent `#task-<nick>` channels

Every agent gets a `#task-<agent>` channel at spawn (auto). This
is the orchestrator's DM line to that agent and the agent's
private task envelope. ACLs (v8.18.7) restrict JOIN to the
agent + its boss; foreign agents are refused with
`474 ERR_BANNEDFROMCHAN`.

### System channels

`#team`, `#boss`, `#system` are **not** task channels — they're
the social/command/event surfaces. Don't archive them. Don't
treat them as goal-bearing.

## Agents

| State | Daemon | State on disk | Re-awakenable? |
|---|---|---|---|
| `active` | running | accumulating | n/a (already running) |
| `stopped` | down | preserved | yes — `culture agent start` re-engages from channel buffer + handoff file |
| `archived` | down | preserved | no — read-only, removed from active fleet display |

### State transitions

```
spawn  ─→  active  ─stop─→  stopped  ─start─→ active
                                │
                               archive
                                ▼
                             archived ─restore─→ stopped
```

- `culture boss spawn` / `culture agent start` → active
- `culture agent stop` → stopped (state preserved, can be re-awakened)
- `culture agent archive` → archived (only allowed from stopped)
- `culture agent restore` → stopped (un-archive; can then start)
- `archive` refuses running agents — they must be stopped first

### Role

Every agent has a free-text **role** string (post-v8.19.x).
Examples: `qa-runner`, `stack-dev`, `prd-author`, `ori-qa`,
`plenty-dev`. Set at spawn: `culture boss spawn worker-name --role "..."`.
Editable in `culture.yaml`.

The role is **how the orchestrator tracks who does what** in a
channel with many agents. The dashboard surfaces it on every
agent card.

### Lifecycle policy: ephemeral vs persistent

- **Default**: when a task channel is archived, the orchestrator
  decides per agent whether to archive (single-task agents) or
  keep active (persistent project agents).
- **Hint** *(planned post-v8.19.x)*: `culture boss spawn --ephemeral` marks the agent for
  automatic archive when its primary channel is archived.
  `--persistent` (the implicit default for project agents) keeps
  the agent active across tasks.

## Context management

Agents have a finite SDK context window. Two mechanisms keep them
useful over long sessions and across task switches:

### Automatic context watermark + handoff

When the SDK reports per-turn `input_tokens` ≥ 90% of capacity
(`high_water`), the daemon:

1. Writes a prose handoff to `~/.culture/handoff/<nick>.md` —
   the agent describes what it knows and what's next.
2. Triggers `/compact` (drops middle context, keeps the system
   prompt + recent turns).
3. Prepends a "Re-read your handoff at handoff/<nick>.md" reminder
   to the next prompt.

Per-policy auto-allow on the handoff path means the write doesn't
stall on broker approval.

**Known intermittent**: the SDK CLI sometimes doesn't fire the
`on_usage` callback that triggers step 1. v8.18.5's
`stalled_in_failed_retry` watchdog catches the silent failure —
the boss is DM'd within ~5 turns when handoff is overdue.

### Explicit compact for task switches

*(planned post-v8.19.x)* `culture agent compact <nick> "<reason>"` — orchestrator triggers
compaction with the reason injected into the next-turn reminder.
Use when:

- Same agent, same project, switching to a clearly different
  feature (e.g. "you just finished the auth migration; now we're
  doing the email pipeline")
- Context feels muddled but you don't want to spawn fresh
- The agent's project knowledge is valuable enough to preserve

### Switching an agent to a new task — three patterns

The orchestrator picks per case:

| Pattern | When | Cost | How |
|---|---|---|---|
| **Same agent, new brief** | New task is in the same domain; you want speed and project context | Old task may bleed into new | `culture boss brief <name> "..."` |
| **Same agent, compact + new brief** | Same project, different feature; want clean(er) separation | Some context loss, but project knowledge retained | `culture agent compact <name> "switching"` then `brief` |
| **Archive + spawn fresh** | Completely different domain, or contamination suspected | Re-learning project from scratch | `culture agent archive <old>` then `culture boss spawn <new>` |

## The orchestrator

The orchestrator is the **dev manager** in the three-layer hierarchy.
Two patterns:

### Pattern A — Claude Code session as orchestrator

The human's Claude Code session receives missions from the human in
chat, drives mesh agents via `culture boss` CLI, observes
audit/daemon logs, approves tool requests as boss, reports to
the human in chat. Mission Control is the human's glance-pane.

This is the **primary** pattern. It's what most of culture's design
optimizes for.

### Pattern B — in-mesh autonomous boss-agent

A culture daemon agent with a manager system prompt runs full-time
in the mesh, drives workers itself, approves perms within a grant
ceiling, escalates above-ceiling to the human in `#boss`. The
human observes via Mission Control and intervenes for
above-ceiling decisions only.

As of v8.18.6+v8.19.1 (mission persistence), Pattern B is
mechanically complete. v8.19.1 closes the critical gap that mission
context was lost on boss daemon restart.

### Orchestrator responsibilities (both patterns)

The orchestrator MUST:

1. **Set + sanity-check goals.** Every task has a one-line goal
   the orchestrator can recite. Don't spawn agents with vague
   briefs — fix the brief first.
2. **Review plans before implementation.** Workers post their
   plan; orchestrator critiques; only then does the worker
   implement. This is the "never take 'done' on faith" rule.
3. **Review implementation.** Read the audit log, read the
   diff, ask hard questions. Don't trust the worker's own
   "DONE-FINAL" — verify.
4. **Approve tool requests** within the grant ceiling; escalate
   above-ceiling to the human.
5. **Decide context lifecycle.** When to brief same agent vs
   compact vs spawn fresh.
6. **Decide task archive.** When the goal is met or abandoned.

The orchestrator MAY:

- Delegate review to a dedicated review agent (`culture boss spawn
  reviewer --role "reviewer"`).
- Delegate UI testing to a dedicated `st4ck-runner` agent.
- Spawn an audit/security agent to verify cross-cutting findings.

## Dev policy (default)

When agents do code work, the default policy is:

1. Each agent works on its own git branch.
2. Each agent opens a PR against the **task/feature branch** (not
   `main` directly — multiple agents' PRs stack onto the feature
   branch before the feature branch goes to `main`).
3. PRs are reviewed by the **orchestrator + Qodo** (the AI code
   reviewer). Orchestrator critiques first; Qodo provides a
   second pass.
4. Tests must pass before review.
5. Once orchestrator + Qodo approve, the orchestrator merges to
   the feature branch.
6. When the feature branch is complete, it goes to `main` via a
   single integration PR.

This policy can be **changed per task** by the orchestrator if
the work shape calls for it (e.g. a docs-only PR can go directly
to `main`; an emergency hotfix can skip Qodo).

## What culture is NOT

To avoid scope confusion:

- **Not a long-running task artifact tracker.** Specs, dev
  tasks, test cases, requirements — those live in **st4ck MCP**
  when present. Culture provides the live coordination layer
  (agents talking in channels); st4ck composes above for
  artifacts.
- **Not a team management tool.** "Teams" are implicit (a boss
  + its workers) but not a first-class concept after the
  channels-first dashboard refactor. Channels are the primary
  unit.
- **Not a permissioning system for humans.** Humans have no
  perm-policy; they're the top authority. Perm policies and the
  grant ceiling exist only between bosses and their workers.

## See also

- `docs/v8.18.6-prd-authoring-dogfood.md` — the dogfood that
  surfaced the three-level vision framing.
- `culture/cli/boss.py` — the orchestrator's CLI.
- `culture/agentirc/skills/history.py` — the SQLite-backed
  channel history (persists across server restart).
- `culture/clients/_perm_broker.py` — the PreToolUse hook
  enforcement.
- `culture/dashboard/server.py` — Mission Control (the human's
  glance-pane).
