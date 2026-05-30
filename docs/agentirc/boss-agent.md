---
layout: default
title: Boss Agent Orchestration
parent: AgentIRC
nav_order: 97
---

# Boss Agent Orchestration

A **boss agent** is an autonomous culture daemon that manages worker agents in
your place. You brief it once in an IRC channel; it reads `CLAUDE.md` and the
plan, spawns workers, drives them like you'd drive a Claude Code session —
asking, scoping, telling them to plan, **challenging** their work and claims —
and approves or denies their tool requests, bounded by a grant ceiling. One or
many bosses, within a project or across several.

Design spec: `docs/superpowers/specs/2026-05-28-boss-agent-orchestration-design.md`
Builds on the [Helper Permission Broker](helper-permissions.md).

## The model

The boss is a normal culture mesh agent whose tools are:

- the **IRC skill** (`culture channel …`) — to converse with workers and with you;
- the **boss skill** (`culture boss …`) — the out-of-band operations conversation
  can't do: spawn a worker, approve/deny its tool requests, read its logs.

Its manager behavior comes from a system-prompt identity, not rigid code — it
exercises judgment about what to ask, when to challenge, and what "done" means.

```text
   you ──brief/steer (IRC #boss)──►  boss agent (daemon)
                                       │  culture boss spawn / approve / …
                  ┌────────────────────┼────────────────────┐
                  ▼                     ▼                     ▼
              worker A              worker B              worker C
              #task-A               #task-B               #task-C
                  └── perm request DM ──► boss (approve/deny, bounded by ceiling)
```

## Quick start

```bash
culture boss init --nick boss --channel '#boss'   # create the boss identity
culture agent start local-boss                     # start the boss daemon
# then, in #boss, brief it:  "@local-boss ship feature X in project Y"
```

The boss takes it from there: spawns workers, drives them, approves their routine
tool calls, escalates the risky ones to you.

## `culture boss` commands (used by the boss agent)

| Command | Purpose |
|---|---|
| `culture boss init [--nick boss] [--channel '#boss'] [--cwd PATH]` | Create the boss identity: manager `system_prompt`, seeded grant ceiling, copied boss skill, **no perm-policy** (deadlock guard), boss channel. Idempotent. |
| `culture boss spawn <name> [--cwd PATH] [--channels "#ch1,#ch2"]` | Create + start a worker under this boss; seed its policy; record `boss:` in its `culture.yaml`; join its task channel (and any `--channels`). The boss also joins extra channels for observation. Refuses a nick colliding with a boss. |
| `culture boss brief <name> "<task>"` | Send a task to the worker's channel. |
| `culture boss read <name> [--limit N]` | Read the worker's recent replies. |
| `culture boss pending` | List pending worker permission requests. |
| `culture boss approve <id> [--always] [--pattern P]` | Grant a request. Refuses (exit 2 + escalation message) if the tool is above the boss's grant ceiling. |
| `culture boss deny <id> [reason...]` | Deny; the reason is returned to the worker's model. |
| `culture boss audit <name> [--limit N]` | The worker's agent-message log — to verify/challenge claims. |
| `culture boss log <name> [--limit N]` | The worker's daemon-action log. |
| `culture boss status` | Workers + pending-perm count. |
| `culture boss close <name>` | Stop a worker daemon. |

The boss's own nick comes from `CULTURE_NICK`, which the agent runner now sets in
every daemon agent's subprocess environment (so an autonomous agent can address
its own IRC/boss sockets).

## The grant ceiling (you stay the final authority on risk)

The boss can `--always`-grant routine tools (Edit/Write/Bash) freely, but a
denylist of high-risk actions — external MCP sends (Gmail/Drive/…), destructive
Bash (`rm -rf`, `git push`, `kubectl`, …) — is **above its grant ceiling**.
`culture boss approve` refuses those (exit 2) and tells the boss to escalate; you
grant them from the [Mission Control dashboard](dashboard.md), where the human is
the top authority and can approve even above-ceiling tools.
The ceiling lives at `~/.culture/boss-policy/<boss-nick>.yaml` and is editable.

> **This is a cooperative guardrail, not a hard boundary.** The boss is an LLM
> with a Bash tool; on a single-UID machine nothing cryptographically stops it
> from writing a decision file directly. The ceiling shapes a cooperative boss's
> behavior (the tool refuses + the system prompt says to escalate); it does not
> defend against an adversarial or malfunctioning boss. Don't over-trust it.

## Model inheritance

A spawned worker runs on its **parent (boss)'s model** by default — `culture boss
spawn` writes the boss's model into the worker's `culture.yaml` unless you pass
`--model`. The boss's own model is whatever its parent (the human/session) gives
it via `culture boss init --model <model>`; set that to your own model so the
whole team runs on the parent's model. Any parent may override a child's model;
the default is simply the parent's. (If no model is set anywhere, the agent
default applies.)

## Close authority (only a parent closes its children)

Agent shutdown follows the spawn hierarchy — **no agent can close itself, and a
boss can close only its own workers**:

- The **human is root** and may close any agent (e.g. the [dashboard](dashboard.md)
  Close / emergency stop-all is a safeguard that kills anything).
- A **boss** may close its **own** workers (`culture boss close <name>`), never
  another boss's worker and never itself.
- A **worker** has no children, so it can close nothing — and nothing can close
  itself.

Enforced in `culture agent stop`: a stop is refused (exit 2) unless the caller
(`CULTURE_NICK`) is the target's parent — i.e. the target's `boss:` field — or the
caller is the human (no `CULTURE_NICK`). `culture boss close` and the dashboard
route through the same guard. (For a fully unsupervised boss this is a cooperative
guard on the sanctioned commands; a determined boss could still raw-`kill` its own
process, since no broker sits in front of it — same posture as the grant ceiling.)

## Deadlock invariant

A boss must **not** be permission-supervised — it has no
`~/.culture/perm-policy/<boss-nick>.yaml`. If it did, its own `culture boss
approve` Bash calls would themselves require approval, and there is no higher
boss to grant them → deadlock. `culture boss init` enforces this (and removes a
stray policy file if found); the boss is supervised by **you over IRC**, not by
the broker.

## Re-grounding on long missions

The boss is a long-lived agent, so the [context handoff](helper-context-handoff.md)
applies: near its context limit it writes a handoff and is reminded to read it
after compacting. Its manager system-prompt tells it to re-ground on the mission,
`CLAUDE.md`, and the plan — not just the last few messages.

## Backend support

The boss agent is **Claude-only** (it depends on the broker and context-watch,
both Claude-only). Workers may be any backend — a Claude boss can spawn and
converse with a Codex/ACP worker over IRC — but those workers are audit-only (no
synchronous tool gate), so the boss oversees them by reading their audit logs and
conversing, not by approving individual tool calls.

## Idle-worker detection

A worker that comes up but never produces a turn within ~90s (spawned into the
wrong channel, never briefed, etc.) would otherwise sit idle while you believe
it's working. The worker daemon detects "never triggered" and **DMs you an
`[idle]` notice** (and records an `idle_warning` in its daemon-log), so the truth
is pushed into your loop — re-drive or re-spawn it rather than reporting it live.
The [dashboard](dashboard.md) also badges such a worker `IDLE`. Treat a worker as
working only once you've seen real activity (`culture boss audit <name>`), never
from the assumption that spawn/brief succeeded.

A worker that *was* briefed but is still grinding on a slow first turn (extended
thinking, a long first tool call) is **not** flagged — only one that was never
triggered. **Claude-only:** like the broker and context-watch, idle self-reporting
lives in the Claude daemon. This covers every boss-owned worker because
`culture boss spawn` always creates a Claude worker; a non-Claude agent
hand-placed under a boss (audit-only) will not self-report idleness.

## Multiple teams

More than one boss can run on the same mesh, each managing its own team. Bosses
are ordinary agents with globally-unique nicks; `culture boss init --nick boss1`
and `--nick boss2` create independent identities, each with its own grant ceiling
(`boss-policy/<nick>.yaml`), cwd, and boss channel. Each worker records its owner
in its `culture.yaml` `boss:` field at spawn (the **one worker, one boss**
invariant), so a worker's permission request routes to *its* boss.

**Team-scoped approvals.** The permission queue lives in one place
(`perm-queue/`), but the boss CLI is team-aware: `culture boss pending` lists only
the calling boss's own workers, and `culture boss approve`/`deny` **refuse**
(exit 2) a request from a worker owned by another boss. A worker with no recorded
owner (legacy/standalone) stays visible to every boss rather than vanishing. The
[Mission Control dashboard](dashboard.md) is the human/all-teams view and is
**not** team-scoped — it sees and can act on every team's requests.

**Single mesh (v1).** The worker→boss permission DM addresses the boss by nick on
the same `local` server; teams live on one mesh. Cross-mesh / multi-machine boss
coordination is out of scope for v1.
