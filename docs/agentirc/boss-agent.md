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
| `culture boss spawn <name> [--cwd PATH]` | Create + start a worker under this boss; seed its policy; record `boss:` in its `culture.yaml`; join its task channel. Refuses a nick colliding with a boss. |
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
grant them with the human `approve.sh <id>` from the [permission broker](helper-permissions.md).
The ceiling lives at `~/.culture/boss-policy/<boss-nick>.yaml` and is editable.

> **This is a cooperative guardrail, not a hard boundary.** The boss is an LLM
> with a Bash tool; on a single-UID machine nothing cryptographically stops it
> from writing a decision file directly. The ceiling shapes a cooperative boss's
> behavior (the tool refuses + the system prompt says to escalate); it does not
> defend against an adversarial or malfunctioning boss. Don't over-trust it.

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

**Single mesh (v1).** The worker→boss permission DM addresses the boss by nick on
the same `local` server; one boss and its workers live on one mesh. Cross-mesh /
multi-machine boss coordination is out of scope for v1.
