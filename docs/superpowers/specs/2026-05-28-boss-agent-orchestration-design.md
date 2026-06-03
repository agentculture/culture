---
title: "Boss Agent Orchestration"
parent: "Design"
nav_order: 25
---

# Boss Agent Orchestration

**Status:** Draft — **partially superseded by [Mesh Rearchitecture — CC IS the Boss](2026-06-03-mesh-rearchitecture-plan.md) (2026-06-03)**.
**Date:** 2026-05-28
**Depends on:** [Helper Boss Permission Broker](2026-05-28-helper-boss-permission-broker.md) (PR #411, v8.7.0)
**Branch:** `feat/boss-agent-orchestration`

> **OBSOLETE NOTE (2026-06-03):** The "grant ceiling" concept (`DEFAULT_BOSS_CEILING`, `is_above_ceiling`, `boss-policy/<nick>.yaml`, `write_default_boss_ceiling`, `load_boss_ceiling`) described in §"Boss grant ceiling" and the `boss approve` ceiling-refusal flow are **REMOVED** as of the mesh rearchitecture (Phase 5.2). A boss can grant a worker any tool the boss itself has; runtime is governed by the worker's policy file. The high-risk sticky-allow narrowing gate (`BareStickyApproveRefusedError` — Phase 5.1) replaces the ceiling as the bypass-prevention surface: a sticky `--always allow` for `Bash`/`Edit`/`Write`/`mcp__*` requires an `input_regex` and is otherwise demoted to `scope=once` with a `perm-demote-notices/<id>.json` file dropped for the boss/dashboard. See [the mesh rearchitecture plan](2026-06-03-mesh-rearchitecture-plan.md) (EL-8, Phase 5).

## Problem

PR #411 made a *human-driven* Claude Code session the "boss": the human runs
`spawn-helper.sh` / `approve.sh` from their terminal and is the human-in-the-loop
for every worker permission request. The human is still doing the managing.

The actual goal is to **remove the human from the management loop**. The boss
should be an **autonomous agent** that the human briefs once (and steers live),
which then:

- reads `CLAUDE.md` and the project/plan context, and re-grounds itself on long
  contexts (project purpose, plan purpose);
- spawns worker agents and drives them **exactly the way a human drives a Claude
  Code session** — "what open dev tasks do we have?" → "what goes well
  together?" → "ok, make a plan" → "yes, you can use that tool, always" →
  challenges the plan/implementation/claims → ushers to completion;
- does this for one or many workers, within a project or across projects;
- and runs as one or many bosses.

The human's job shrinks to: brief the boss in an IRC channel, steer occasionally,
walk away.

## What already exists vs. what's new

PR #411 is the **substrate**; this spec is the **agent layer** on top.

| Capability | #411 (substrate) | This spec (agent layer) |
|---|---|---|
| Worker permission gate | File-backed broker; approver = human via scripts | Approver = **boss agent** via orchestration-skill tools |
| Worker visibility | `audit/<nick>.jsonl`, `daemon-log/<nick>.jsonl` | Boss reads these to challenge/verify worker claims |
| Re-grounding on long context | `context_watch` handoff (any Claude daemon) | Applies to the **boss** too — re-reads brief + CLAUDE.md + plan |
| Tool inheritance | `setting_sources=["user","project","local"]` | Boss + workers both inherit the operator's tools |
| Spawning / briefing / approving | Bash scripts a **human** runs in a terminal | An **orchestration skill** the **boss agent** calls via Bash |
| Boss identity | "boss daemon" exists but is a puppet of the human session | Boss daemon's **own LLM** is the orchestrator, with a manager system-prompt |
| Worker→boss permission notification | Deferred to v1.1 (file queue only) | **Load-bearing now** — requests surface to the boss over IRC |

## Mental model

The boss is a **culture mesh agent** (a daemon running the Claude Agent SDK,
exactly like a worker) whose "tools" are:

1. **The IRC skill it already has** (`culture channel message|read|join|...`) — to
   converse with workers in their task channels and with the human in the boss
   channel. This is how the boss "talks to a Claude Code session": it sends a
   message to the worker's channel and reads the reply, the same back-and-forth a
   human has.
2. **A new orchestration skill** (`culture-boss-agent`, in the boss's cwd) — the
   out-of-band capabilities that aren't plain conversation: spawn a worker,
   approve/deny a worker's pending permission request (including "always"),
   list pending requests, read a worker's audit/daemon logs, close a worker,
   summarize project state.

The boss's *judgment* — what to ask, when to challenge, what "done" means —
comes from its **system-prompt identity + the model**, not from rigid code. The
code only provides the levers; the LLM pulls them like a human would.

```text
        ┌─────────────┐   briefs / steers (IRC)   ┌──────────────────────┐
 Human  │  #boss-<n>  │ ────────────────────────► │  Boss agent (daemon) │
        └─────────────┘                           │  - irc skill         │
                                                  │  - orchestration skill│
                                                  │  - manager prompt    │
                                                  └──────────┬───────────┘
                                spawn / approve / converse   │
                          ┌──────────────────┬───────────────┼────────────────┐
                          ▼                  ▼               ▼                 ▼
                   ┌────────────┐     ┌────────────┐   ┌────────────┐   (perm request
                   │ worker A   │     │ worker B   │   │ worker C   │    surfaces back
                   │ #task-A    │     │ #task-B    │   │ #task-C    │    over IRC)
                   └────────────┘     └────────────┘   └────────────┘
```

## Boss as a first-class agent

Today `ensure-mesh.sh` creates a `local-boss` daemon, but the culture-boss skill
drives the mesh "from outside" — the boss daemon is just an IRC identity; the
human's session is the orchestrator (per the SKILL.md limitation note). This spec
**inverts** that: the boss daemon's own LLM is the orchestrator.

Requirements for the boss agent:

1. **It is a normal culture daemon agent** — gets mentions, polls channels, has
   the IRC skill, participates in supervisor/whisper machinery like any agent.
2. **Its cwd contains the orchestration skill** so the LLM can call spawn/approve
   /etc. via Bash. (Skills are loaded from the agent's cwd `.claude/skills/` via
   `setting_sources=["project"]`/`["user",...]`, the same path the IRC skill uses.)
3. **Its system prompt is a manager identity** (see "Boss system prompt").
4. **It is NOT itself permission-supervised.** Critical: the boss must have **no
   `~/.culture/perm-policy/<boss-nick>.yaml`**, so `can_use_tool` is not wired for
   it (per #411's gate at `agent_runner.py:105`, `has_policy_file`). Otherwise the
   boss's own `approve.sh`/`spawn-helper.sh` Bash calls would themselves require
   approval — and there is no higher boss to grant it, so the boss would deadlock.
   The boss is supervised by the **human over IRC**, not by the broker.

   **Guarding the invariant (three guards, because `spawn-helper.sh` seeds a
   policy file for whatever name it spawns):**
   - The boss is created via `cu agent create` in `ensure-mesh.sh`, **never** via
     `spawn-helper.sh` / `boss spawn` (which would seed a policy and deadlock it).
   - `ensure-mesh.sh` asserts no `perm-policy/<boss-nick>.yaml` exists after boss
     setup (and removes one if found, logging a warning).
   - `boss spawn <name>` **refuses** a `<name>` whose resulting nick collides with
     a known boss nick, so a boss can't accidentally re-spawn itself as a
     supervised worker.

## The orchestration skill — a `culture boss` CLI subcommand (in-repo)

The orchestration capability ships **in the repo** as a new `culture boss` CLI
subcommand group (`culture/cli/boss.py`), exactly mirroring how the IRC skill is
the `culture channel` subcommand. This is testable Python that reuses the broker
+ ceiling code directly (no shelling to bash scripts), and is versioned and
reviewable. A `SKILL.md` at `culture/clients/claude/skill/boss/` documents it for
an LLM caller; `culture boss init` copies that skill dir into the boss agent's
cwd `.claude/skills/` so the boss picks it up (project setting source).

The boss agent's own nick comes from the `CULTURE_NICK` env var (now set by the
agent runner — see "CULTURE_NICK prerequisite"), so `culture boss approve`
knows whose grant ceiling to apply and IRC ops act as the boss.

| Subcommand | Purpose |
|---|---|
| `culture boss init [--nick boss] [--channel '#boss'] [--cwd PATH]` | Create the boss agent: write its `culture.yaml` (manager `system_prompt`, `context_watch` on), seed its grant ceiling (`boss-policy/<nick>.yaml`), copy the boss skill into its cwd, **assert no `perm-policy/<nick>.yaml`**, join the boss channel. Idempotent. |
| `culture boss spawn <name> [--cwd PATH]` | Create + start a worker (`cu agent create/start`), seed its policy (`seed_helper_policy`), set the worker's `boss:` field to `$CULTURE_NICK`, join its task channel. Refuses a `<name>` colliding with a known boss nick. |
| `culture boss brief <name> "<task>"` | Send a task to a worker's channel (prefixes the worker nick so its mention detector fires). |
| `culture boss read <name> [--limit N]` | Read recent worker replies. |
| `culture boss pending` | List all pending worker permission requests. |
| `culture boss approve <id> [--always] [--pattern P]` | Grant a worker's tool request. **Refuses (non-zero exit + escalation message) if the tool is above `$CULTURE_NICK`'s grant ceiling** (`is_above_ceiling`); writes the decision otherwise. |
| `culture boss deny <id> [reason...]` | Refuse a worker's tool request, with a reason the worker's model sees. |
| `culture boss audit <name> [--limit N]` | Read a worker's agent-message audit log — to challenge claims. |
| `culture boss log <name> [--limit N]` | Read a worker's daemon-action log. |
| `culture boss status` | Summarize the mesh: workers, states, pending perms. |
| `culture boss close <name>` | Stop a worker daemon. |

These are thin; the boss's IRC skill handles the actual conversation
(`culture channel message #task-<name> ...` / `culture channel read ...`). The
`culture boss` subcommand only adds what conversation can't do (spawn, approve,
read logs). The decision/queue/ceiling operations reuse `_perm_broker.py`
directly — one implementation, callable by both the CLI and tests.

## CULTURE_NICK prerequisite

The harness does **not** currently set `CULTURE_NICK` in the agent's SDK
subprocess environment (verified: the Claude runner's `_make_options` sets no
`env`; codex/copilot build an `isolated_env` without it). An autonomous daemon
agent therefore cannot reliably address its own IRC socket — the IRC skill and
the `culture boss` skill both resolve the daemon socket from `CULTURE_NICK`.

Fix (all four backends, per the all-backends rule): each agent runner sets
`CULTURE_NICK=<own-nick>` in the env it passes to the SDK/CLI subprocess (merged
over `os.environ`). For the Claude runner this is `ClaudeAgentOptions(env=...)`;
codex/copilot already build an `isolated_env` dict — add the key there; ACP
similarly. This is a latent enabler for *any* autonomous agent using its own
skills, not just the boss. Test: each runner's constructed options/env contains
`CULTURE_NICK == nick`.

## Worker permission requests surface to the boss over IRC

This is the one genuinely-new Python piece (finishing #411's deferred v1.1).

When a worker hits a permission gate, the broker writes `perm-queue/<id>.json`
(as today) **and** the worker daemon posts a one-line notice to the boss so the
boss "sees" the request inline — the agent analogue of a permission prompt
appearing in a human's session.

Design:

- The worker's `PermissionBroker.gate` already runs inside the worker daemon. The
  daemon supplies the broker an optional `on_request` async callback
  (daemon → runner → broker plumbing, the hook #411 named but deferred).
- On a boss-routed request, the callback **DMs the owning boss** via the worker's
  IRC transport (`IRCTransport.send_privmsg(boss_nick, notice)`, verified to
  exist):
  `[perm] worker <nick> wants <tool>: <preview> — id <id> (approve/deny)`.
- A DM to the boss nick fires the boss's activation handler
  (`irc_transport.py:357` calls `on_mention` when `target == self.nick`), so the
  boss wakes, reads the request (`boss pending`), decides, and calls
  `boss approve|deny`.
- The boss may also pre-grant (`always`) so routine tools stop round-tripping —
  exactly "yes, you can use that tool, always".

**The worker must know its boss's nick to DM it.** The worker does not inherently
know which boss owns it. Mechanism: when the boss spawns a worker (`boss spawn`),
it records the ownership as a `boss: <boss-nick>` field in the worker's
`culture.yaml` (a new `AgentConfig.boss` field, read by the worker daemon). The
daemon's `_on_perm_request` DMs `self.agent.boss`. **Fallback if the field is
empty** (the human-supervised case from #411): post nothing — the human finds
the request via `pending-perms.sh`. A DM is used rather than a channel
`@`-mention because it needs no task-channel derivation and directly addresses
the boss; the notice content is informational, not parsed, so it is not
format-load-bearing the way an `@`-mention would be.

**`on_request` is best-effort.** The broker calls it inside a `try/except` after
writing `perm-queue/<id>.json` and before awaiting the decision. A failed IRC
post (transport down, etc.) is logged and swallowed — it must **never** block or
fail the gate. The file queue remains the source of truth and the unblock
mechanism; the IRC post is only the *notification*. If it fails, the boss still
finds the request via `boss pending` (polling fallback).

## Boss grant ceiling (human-over-boss gate)

The boss drives workers autonomously, but it is **not** the final authority on
irreversible or external actions. A configurable **grant ceiling** — a denylist
of high-risk tool patterns — bounds what the boss may grant on a worker's behalf.

Default ceiling (per-boss, `~/.culture/boss-policy/<boss-nick>.yaml`, seeded by
`ensure-mesh.sh`):

```yaml
# Tools the boss MAY NOT auto-grant to a worker; these escalate to the human.
grant_ceiling:
  - 'mcp__.*'        # any MCP server (Gmail/Drive/Calendar/Atlassian/…) — external side effects
  - tool: Bash
    input_regex: '(^|\s|;|&&|\|\|)(rm\s+-rf|git\s+push|gh\s+(pr|release)\s+(create|merge)|kubectl|terraform|drop\s+table|truncate)'
```

Enforcement is at the **boss `approve` tool** layer:

1. Worker requests a tool → broker queues it → notice surfaces to the boss.
2. Boss decides to allow and calls `boss approve <id> [always]`.
3. `boss approve` loads the request, matches the tool/input against the boss's
   `grant_ceiling`. **If it matches, the tool refuses to write a decision** and
   returns: *"`<tool>` is above your grant ceiling — escalate to your human in
   the boss channel; do not retry approve."*
4. The boss posts the request to the human boss channel
   (`@human [escalation] worker <name> wants <tool> <preview> — id <id>`).
5. The **human** approves via #411's existing `approve.sh <id>` (the human path
   is unchanged and is the escalation target). The worker unblocks.

Why enforce at the boss tool, not the broker: the broker is approver-agnostic
(it just reads a decision file). The human's `approve.sh` must remain able to
grant *anything* (the human is the top authority); only the **boss's** grant tool
is ceiling-bounded. Two grant tools, one queue, different ceilings — the human's
ceiling is unbounded, the boss's is the denylist above.

`boss deny` has no ceiling — the boss may always deny. Ceiling only bounds
*granting*.

This keeps the human as final authority on irreversible/external actions while
the boss handles everything routine without human involvement. The ceiling is
per-boss and editable, so a trusted boss in a sandboxed project can be widened.

**Threat model — this is a cooperative guardrail, not a hard boundary.** The boss
is an LLM with a Bash tool; it *could* write a `perm-decisions/<id>.json` file
directly and bypass `boss approve`'s ceiling check entirely. On a single-UID
local machine there is no authentication boundary between the boss process and
the human — both can write any file under `~/.culture/`. The ceiling therefore
**shapes a cooperative boss's behavior** (the tool refuses + the system prompt
tells it to escalate); it does **not** defend against an adversarial or
malfunctioning boss that forges decision files. Hard enforcement would require an
auth boundary (separate UID, signed decisions) that is out of scope for v1. The
ceiling matcher reuses `match_policy` from `_perm_broker.py` (the ceiling is
structurally a denylist), so there is one matcher implementation, not two.

The matching reuse and the soft-boundary caveat must both be stated in the
`boss-agent.md` docs so an operator doesn't over-trust the ceiling.

## Boss system prompt

A manager identity, set via the boss's `culture.yaml` `system_prompt`. Shape
(not final wording):

```text
You are <boss-nick>, a manager agent on the culture mesh. A human briefs you in
your IRC channel; that brief is your mission. You do not do the implementation
work yourself — you drive worker agents that do.

On a new mission:
1. Read CLAUDE.md and any referenced plan/spec to ground yourself in the
   project's purpose and conventions.
2. Decide the work. Ask clarifying questions in your channel if the brief is
   ambiguous.
3. Spawn workers (boss spawn) and drive each like a Claude Code session:
   ask what's open, scope what fits together, tell them to plan, review and
   CHALLENGE their plan before they implement, then their implementation, then
   their claims (verify against the audit log — never take "done" on faith).
4. Approve worker tool requests as they arrive (boss approve/deny). Grant
   "always" for tools you trust for a worker; deny with a reason otherwise.
   Some high-risk tools (external MCP sends, destructive commands) are above
   your grant ceiling — when `boss approve` tells you so, do NOT retry; post the
   request to your human in the boss channel and let them grant it.
5. Report progress and blockers to the human in your channel. Escalate genuine
   judgment calls and above-ceiling tool requests; handle the rest yourself.

When you approach your context limit you will be asked to write a handoff and
will be reminded to re-read it — re-ground on the mission, CLAUDE.md, and plan.
```

The challenge discipline ("never take 'done' on faith; verify against the audit
log") is the heart of the manager role and must be explicit in the prompt.

## Re-grounding (context-watch for the boss)

The boss is a long-lived Claude daemon, so #411's `context_watch` already
applies: at 90% it writes a handoff and is reminded to read it post-compact. For
the boss, the handoff content is the *mission state* (what the workers are doing,
what's approved, what's left), and the reminder should also nudge it to re-read
CLAUDE.md + the plan. No new mechanism — just ensure the boss has
`context_watch.enabled: true` (the default) and a system-prompt instruction to
re-ground on the mission, not just resume.

## Briefing & steering (live IRC)

- `ensure-mesh.sh` creates a boss channel (e.g. `#boss` or per-boss `#boss-<n>`)
  the human and boss both join.
- The human briefs by messaging the channel; the boss's mention handler treats
  channel messages as mission input / amendments.
- Multiple bosses: each is its own daemon agent with its own channel and its own
  set of workers. They do not share workers (one worker, one boss — see #411
  non-goals).

## Backend coverage

The boss agent is **Claude-only** in v1 — it depends on the broker (Claude-only)
to gate workers and on `context_watch` (Claude-only) to re-ground. Workers may be
any backend (a Claude boss can spawn and converse with a Codex/ACP worker over
IRC), but those workers are audit-only (no synchronous gate), so the boss
oversees them by reviewing their audit logs and conversing, not by approving
individual tool calls. Documented in the backend matrix.

## Files

New:
- `culture/cli/boss.py` — the `culture boss` subcommand group (init/spawn/brief/
  read/pending/approve/deny/audit/log/status/close), reusing `_perm_broker.py`
  for queue/decision/ceiling ops. Registered in the CLI dispatcher.
- `culture/clients/claude/skill/boss/SKILL.md` — documents `culture boss …` for
  an LLM boss agent. Copied into the boss cwd by `culture boss init`.
- Per-backend runner env: `CULTURE_NICK` set in the SDK subprocess env (see
  "CULTURE_NICK prerequisite").

Changed (`on_request` plumbing path is daemon → AgentRunner → PermissionBroker,
because the broker is constructed inside `AgentRunner.__init__`, not the daemon):
- `culture/clients/_perm_broker.py` — `PermissionBroker.__init__` accepts an
  optional `on_request` async callback; `_request_from_boss` invokes it
  best-effort (try/except) after writing the queue file, before polling.
- `culture/clients/claude/agent_runner.py` — new optional `on_perm_request`
  ctor param, threaded into `PermissionBroker(nick, on_request=...)` alongside
  the existing `has_policy_file` gate.
- `culture/clients/claude/daemon.py` — supply `on_perm_request` to the runner; it
  posts the `@<boss-nick> [perm] …` notice via `self._transport.send_privmsg` to
  the worker's task channel (reads `agent.boss` for the boss nick).
- `culture/clients/claude/config.py` — new optional `boss: str = ""` field on
  `AgentConfig` (a worker records its owning boss nick; default empty for
  unmanaged agents). Coerced like the existing `context_watch` field in
  `load_config`.
- `~/.claude/skills/culture-boss/` (out of repo) — the human-facing skill gains
  a "run the boss as an agent" path (spawn a boss daemon, brief it, let it run);
  `ensure-mesh.sh` ensures the boss has the orchestration skill in its cwd, a
  manager `system_prompt`, a boss channel, and **no** policy file.
- Docs: `docs/agentirc/boss-agent.md`; update `helper-permissions.md` to note the
  approver can be a boss agent; index.

## Testing strategy

Per project convention: real I/O, no mocks; pytest + xdist; `CULTURE_HOME`
isolation. The boss-agent layer is mostly skill/prose + a thin Python callback,
so the automated surface is small and focused:

| Test | Verifies |
|---|---|
| `test_perm_broker_on_request.py` | `PermissionBroker(on_request=cb)` invokes `cb(payload)` once, after the queue file is written and before the decision is awaited; a callback that raises is swallowed and the gate still resolves on the decision file (best-effort contract). Existing `gate()` flow with `on_request=None` is unchanged. |
| `test_boss_grant_ceiling.py` | The ceiling matcher (reusing `match_policy`) classifies `mcp__*` and destructive-Bash inputs as above-ceiling and routine Edit/Write/safe-Bash as grantable. Drives the `boss approve` shim: above-ceiling id → non-zero exit + escalation message + **no** decision file written; in-ceiling id → decision written. |
| `test_worker_boss_notice.py` | `_on_perm_request`: with `agent.boss` set, DMs the boss (`send_privmsg(boss_nick, "[perm] …")`); with `agent.boss` empty, sends nothing (no crash). `_perm_input_preview` truncates Bash/Edit/Write/MCP inputs to ≤80 chars. Unit-level with a fake transport capturing `send_privmsg` calls. |
| `test_boss_no_policy_invariant.py` | After `ensure-mesh`-style boss setup, `has_policy_file(boss_nick)` is False and `boss spawn <boss-short-name>` is refused. |

Manual smoke (documented, not automated): spawn a boss, brief it in its channel
to drive one worker through plan→implement→challenge, observe routine tools
auto-granted and one high-risk tool escalated to the human.

## Open questions

1. **"Open dev tasks" source.** This repo has no task store. v1: the boss infers
   open work from `CLAUDE.md` + `docs/development-plans/` + `docs/superpowers/specs/`
   + what the human tells it. A real task tracker (or a `culture task` CLI) is a
   possible follow-up — out of scope here.
2. **Boss spawning across projects.** *Resolved: allowed.* `boss spawn <name>
   <cwd>` with `cwd` at another project works mechanically (the worker inherits
   *that* project's CLAUDE.md). The human tells the boss which projects are in
   scope for the mission. One boss may manage workers across several projects.
3. **Boss-of-bosses.** Out of scope. A boss does not spawn other bosses in v1
   (would need the regress-guard reasoning extended). Flag if wanted later.
4. **Boss grant ceiling.** *Resolved: human-over-boss gate.* The boss may
   auto-grant routine tools but not high-risk ones (MCP sends, destructive
   Bash); those escalate to the human via `approve.sh`. See
   [Boss grant ceiling](#boss-grant-ceiling-human-over-boss-gate).

## Non-goals

- Boss-of-bosses hierarchies (v1: human → boss → workers, two levels).
- A task-tracking system / `culture task` CLI.
- Replacing the human-facing culture-boss skill — it stays for hands-on use; the
  boss-agent is an additional, autonomous mode.
- Cross-mesh / multi-machine boss coordination.
- Letting a boss agent run unsupervised with no human channel — the human must
  have a steering channel even if they rarely use it (kill-switch + redirect).

## Phases (provisional — finalize after review)

| Phase | Work | Gate |
|---|---|---|
| P1 | `on_request` plumbing: broker callback + worker-daemon IRC post of perm requests. Tests. | Worker perm request appears in its task channel; boss-less path unchanged. |
| P2 | Orchestration skill (`boss` shim + SKILL.md) reusing the shared scripts, incl. `boss approve` grant-ceiling enforcement + escalation message. | Boss agent can spawn/brief/read/approve/deny/close a worker via Bash; above-ceiling approve refuses and instructs escalation. |
| P3 | Boss identity: `ensure-mesh.sh` gives the boss the orchestration skill, a manager `system_prompt`, a boss channel, a seeded `boss-policy/<nick>.yaml` grant ceiling, and **no** perm-policy file. | A spawned boss daemon, briefed in its channel, drives a worker end-to-end; routine tools auto-granted, high-risk escalated to the human. |
| P4 | Re-grounding: confirm `context_watch` on the boss; system-prompt re-ground instruction. | Boss re-reads mission + CLAUDE.md after a compact. |
| P5 | Docs (`boss-agent.md` + matrix + index), version bump, PR. | doc-test-alignment clean; CI green. |

## Acceptance summary

The feature ships when a human can: create a boss agent, brief it in an IRC
channel with a mission ("ship feature X in project Y"), and walk away — and the
boss then spawns workers, converses with them like a human would (asks, scopes,
tells them to plan), challenges their plans/implementations/claims, approves
their tool requests inline, re-grounds itself on long contexts, and reports back
— with the human only steering occasionally over IRC.
