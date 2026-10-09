---
name: assign-to-workforce
description: >
  Fan out a converged devague plan's dependency waves to parallel agents in
  isolated git worktrees, one agent per task per wave, with TDD-gated merges
  by the main agent. Human gates: the exported spec, the implementation split
  plan (task map + per-task agent/model proposal + go/no-go), and the final PR.
  The devague CLI stays deterministic and non-orchestrating (#20) — it only
  *describes* the graph via `devague plan waves`; the operator (main agent)
  performs the fan-out. Use when the user says "assign to workforce",
  "fan out the plan", "parallel subagents", or after /spec-to-plan exports a
  plan. Authored and maintained in agentculture/devague (origin = devague);
  guildmaster pulls this skill from here and broadcasts it to the AgentCulture
  mesh — it is NOT vendored from guildmaster like the inbound skills here.
type: command
---

# assign-to-workforce — fan out a converged plan's waves to parallel agents

The skill is named **`assign-to-workforce`**; the product/CLI it reads is the
**`devague plan waves`** command. (The prior leg — turning a spec into a plan —
is the sibling **`/spec-to-plan`** skill.)

`assign-to-workforce` takes a **converged devague plan** and fans out its
dependency waves to parallel agents (subagents, teammate agents, or generalist
agents) — one agent per task per wave — each working in an **isolated git
worktree**. The main agent merges each completed worktree gated by TDD. The
human owns exactly three gates: the exported spec, the implementation split
plan, and the final PR.

The devague CLI is **never orchestrated by devague itself** — `devague plan
waves` describes the dependency graph (#20); it does not spawn agents, manage
worktrees, mark tasks done, or pick a backend. The fan-out is the *operator's*
job — this skill and the main agent perform it.

## How to run

The entry point is `scripts/assign-to-workforce.sh`. Invoke it from the
repository whose plan you are implementing (plans persist under `.devague/`
in the current directory):

```bash
bash .claude/skills/assign-to-workforce/scripts/assign-to-workforce.sh split-plan [--plan <slug>] [--write]
bash .claude/skills/assign-to-workforce/scripts/assign-to-workforce.sh waves    [--plan <slug>] [--json]
bash .claude/skills/assign-to-workforce/scripts/assign-to-workforce.sh help
```

It resolves the CLI portably — an installed `devague` on `PATH` (the normal
case), falling back to `uv run devague` when you are inside the devague
checkout, else an install hint. The `split-plan` subcommand reads
`devague plan waves --json` — the enriched payload (devague#53 t9) that
carries every active task's summary, instruction, acceptance criteria, and
covered targets keyed by task id — and renders the human-facing
implementation split plan: task map (task id, wave, summary verbatim, whether
an instruction is present, acceptance-criteria count), proposed per-task
agent + model assignment, the go/no-go question, and — last — an End state
section that is the verbatim output of `devague plan deliverables` (#70),
degrading to a one-line hint on a `devague` too old to have the verb. Adding
`--write` (issue #82) additionally persists that same content — plus an
owner/model annotation table the script reads back on the next `--write` —
to a durable file next to the exported plan-md; see *The durable split
artifact* below. The `waves` subcommand forwards to `devague plan waves`
verbatim.

### Usage

| Subcommand | What it does |
|------------|--------------|
| `split-plan [--plan S] [--write]` | Read `devague plan waves --json` and print the implementation split plan — task map (summary/instruction/acceptance-criteria count, verbatim) with per-task agent + model proposal, go/no-go, and a trailing End state section quoting `devague plan deliverables` verbatim (one-line hint on an older devague) — ready for human go/no-go review. With `--write`, also persist that content to `docs/plans/<created-date>-<slug>-split.md` (issue #82) — the durable gate-2 record; re-running overwrites the same path in place and preserves any hand-edited Owner/Model cells. |
| `waves [--plan S] [--json]` | Forward to `devague plan waves [--json]`. Read-only; lists wave batches. On a converged plan exits 0 listing the waves. |
| `help` | Print usage. |

## The full flow

The flow has three human gates and one automated TDD merge loop.

### Human gate 1 — the exported spec

The plan is seeded from a converged frame (`devague plan new --frame <slug>`).
The human reviewed and approved the spec when it was exported by the `/think`
skill. No re-approval needed here — the spec gate is already closed.

### Human gate 2 — the implementation split plan

Before any task is assigned, the main agent presents the **implementation split
plan** for human go/no-go. This is the only gate the human owns at the
implementation stage (per task, the TDD gate is the main agent's).

The split plan contains:

1. **Task map** — every task id, its one-line summary (verbatim), whether it
   carries a working instruction, its acceptance-criteria count, and the wave
   it belongs to — all read straight from `devague plan waves --json` (no
   operator paraphrasing).
2. **Per-task agent + model proposal** — for each task: the proposed agent type
   (subagent / teammate / generalist), the proposed model (e.g. a cheaper/faster
   model for a well-scoped task), and the scope justification (why this task is
   safe to delegate).
3. **Go/no-go question** — explicit human decision: "Approve this split and
   assign the plan to the workforce, or edit it first?"
4. **End state** — the verbatim output of `devague plan deliverables` (#70):
   what the plan actually produces — confirmed after-state claims, terminal
   tasks with acceptance criteria, and surviving open items. Present this to
   the human alongside the go/no-go question, not just the task map — approving
   a fan-out without seeing the world it produces is the gap this closes. On a
   `devague` too old to have the verb, this degrades to a one-line hint naming
   the minimum version instead of failing the split plan.

The human may edit any row (agent type, model, scope) before approving. The
plan is model-agnostic — devague does not pick a backend (#20).

Run `split-plan` to print the proposed table:

```bash
bash .claude/skills/assign-to-workforce/scripts/assign-to-workforce.sh split-plan
```

Do not proceed to fan-out until the human approves the split plan.

### The durable split artifact (`split-plan --write`)

Unlike the exported spec (`docs/specs/*.md`) and the exported plan
(`docs/plans/*.md`), the implementation split plan — gate 2 — survived only
in conversation before issue #82. `split-plan --write` closes that gap with
an **artifact-only** change (decision c25): the written file *is* the record;
there is no plan-schema change and no new `devague` CLI verb, so `devague
plan waves`/`show`/`deliverables` stay read-only exactly as before.

```bash
bash .claude/skills/assign-to-workforce/scripts/assign-to-workforce.sh split-plan --write
```

This writes (or overwrites) `docs/plans/<created-date>-<slug>-split.md` —
the same date-prefix convention `devague plan export` uses for the plan-md
it sits beside, derived from the plan's own `created` timestamp (via
`devague plan show --json`) rather than today's date, so re-running the
command is idempotent: it updates the same file in place instead of spawning
a dated duplicate. The file carries:

1. The dependency waves and the full per-task content (summary, instruction,
   acceptance criteria, covered targets) for every wave — quoted verbatim
   from `devague plan waves --json`, organized under one `## Wave N` heading
   per wave and one `### <task-id> — <summary>` heading per task.
2. A **Task assignments** table (`Task | Owner | Model`) — the durable form
   of the human's per-task owner/model decision (#82 ask 2). The script
   **reads this table back** from any existing file at the same path before
   regenerating: a human's edited `Owner`/`Model` cell for a given task id
   survives the next `--write`, matched by task id, rather than being
   clobbered back to the `sonnet` default. Only edit this table (or add a
   new plan/task and re-run) — don't hand-edit the wave/task sections above
   it, since those are fully regenerated every run.
3. The same End state section as plain `split-plan` — the verbatim output of
   `devague plan deliverables`, nested under its own `## End state` heading.

Present this file (or its stdout twin from plain `split-plan`) at the go/no-go
either way; `--write` is for keeping a committed record of what was actually
approved, not a replacement for the live review.

### The `waves --json` payload — the single source for every brief

`devague plan waves --json` emits `{"plan": "<slug>", "waves": [[...], ...],
"tasks": {...}}` — the ordered dependency-wave batches plus a top-level
`tasks` object keyed by task id, each entry carrying that task's full working
contract:

```json
{
  "plan": "<slug>",
  "waves": [["t1"], ["t2", "t3"]],
  "tasks": {
    "t1": {
      "summary": "<task summary>",
      "instruction": "<verbatim instruction, or \"\" if none>",
      "acceptance_criteria": ["<criterion>", "..."],
      "covers": ["<c*/h* id>", "..."]
    }
  }
}
```

This one payload is enough to build a per-subagent brief with **no external
context** — no need to also read `devague plan show --json` or the exported
plan-md. `split-plan` reads it to render the task map above; the fan-out step
below reads the same payload to build each task agent's brief. Quote
`summary`, `instruction`, `acceptance_criteria`, and `covers` **verbatim**
into every brief — never paraphrase them. (Documented identically in the
sibling `/spec-to-plan` skill, since both skills consume the same payload —
stay consistent if either changes.)

### Fan-out — one agent per task per wave in isolated worktrees

Once the human approves, the main agent fans out each wave in order:

1. **Create an isolated git worktree** for each task in the current wave,
   **inside this repo's own worktree root** — `.worktrees.<repo-name>`, a
   sibling of the repo directory:

   ```bash
   repo_root=$(git rev-parse --show-toplevel)
   wt_root="$(dirname "$repo_root")/.worktrees.$(basename "$repo_root")"
   git worktree add "$wt_root/agent-<task-id>" -b agent/<task-id>
   ```

   Never use a bare `../worktrees/` or an in-repo path. The
   `.worktrees.<repo-name>` root is mandatory for three reasons:

   - **Nobody else will delete it.** A shared `../worktrees/` in a multi-repo
     parent directory looks like anyone's scratch space; a directory named
     after *your* repo is visibly owned, so another agent or human cleaning up
     their own worktrees won't sweep away a live fan-out mid-wave.
   - **No cross-repo collision.** Task ids restart at `t1` in every repo and
     every plan, so `../worktrees/agent-t1` from two concurrent repos is the
     same path. Namespacing by repo name keeps concurrent fan-outs disjoint.
   - **The repo working tree stays clean.** An in-repo path (`.worktrees/`,
     `.claude/worktrees/`) puts N checkouts inside the tree you are about to
     commit and PR — `git add -A` sweeps them in and `git clean -fdx` destroys
     them. Outside the repo, neither can touch them.

2. **Spawn a task agent** inside that worktree (using the approved model from
   the split plan), with:
   - The task id, summary, working instruction, acceptance criteria, and
     covered targets as its brief — **quoted verbatim** from `devague plan
     waves --json` (see *The `waves --json` payload* above). No operator
     paraphrasing anywhere in this flow: the plan text *is* the contract the
     user confirmed, and a reworded brief silently drifts from it. If a task
     has no instruction (`""`), say so rather than inventing one.
   - Instruction to work **test-first** (TDD): write the failing test(s) that
     match the acceptance criteria before implementing.
   - Instruction to commit its work to the worktree branch.
   - Instruction to **report, never file**, any reasoning-degradation lapse it
     notices in its own work — a skipped check, an assumption standing in for
     a real measurement, an unverified grader, missing provenance, or another
     of the six `LAPSE_CODES` in `devague/frame.py`. The task agent names it
     in its transcript or final report; it never runs `devague lapse` itself,
     because it never runs any devague command inside its worktree (see the
     hard rule below). The **main agent** files the record (`devague lapse
     "<what>" --code <code> --origin llm`) the moment the task agent reports
     it — not deferred to closeout — since written late is written
     flattering.

3. **Same-wave tasks run in parallel** (within-wave tasks have no
   inter-task dependency; the dependency graph guarantees this). Same-file
   overlap surfaces as a merge conflict at reconcile time, not a live race —
   isolated worktrees prevent clobbering.

4. **Wait for all tasks in the wave to complete** before starting the next wave.

### TDD-gated merge — main agent, no human per task

For each completed task worktree, the main agent:

1. **Runs the task's tests before merge** (on the main branch): baseline must
   pass (or the relevant tests must be absent — the task adds them).
2. **Merges the worktree branch** into the main branch:

   ```bash
   git merge --no-ff agent/<task-id>
   ```

3. **Runs the task's tests after merge**: they must pass. If they do not, the
   merge is reverted and the task agent is given the failure output to fix.
4. **Removes the worktree** once the merge is accepted:

   ```bash
   git worktree remove "$wt_root/agent-<task-id>"
   ```

   Remove only the worktrees this run created — never `rm -rf` the
   `.worktrees.<repo-name>` root itself, and never touch another repo's
   worktree root. A concurrent fan-out may be live inside it.

The human does **not** review individual task merges. Per-task acceptance is
the main agent's responsibility — the TDD gate (tests pass before AND after
merge) plus the task's acceptance criteria. This mirrors the non-authoritative
working state pattern of the Human Review Loop (#17): per-task merge records
are uncommitted working state; the authoritative human gate is the final PR.

Advance to the next wave only after all tasks in the current wave are merged
and their tests pass.

### Human gate 3 — the final PR

Once all waves are merged and the full test suite passes, the main agent opens
a PR via the `cicd` skill (`agex pr open`). The human reviews and merges. This
is the last and only remaining human gate.

## Hand-offs: mid-run and after the final PR

Two hand-offs bracket execution — one that can fire mid-run, one that always
fires after the final PR merges:

1. **Mid-run — hand off to `/deviate`.** If a task agent (or the main agent)
   discovers the confirmed plan no longer matches reality partway through a
   wave, that is not a silent edit to this run — stop, get explicit human
   approval for the divergence, and record it via the sibling **`/deviate`**
   skill (`devague deviate`) before resuming the fan-out. This is not a fourth
   standing gate; it is the human owner of gate 2 approving an amendment to it
   in-flight.
2. **Post-merge — hand off to `/validate-delivery`, then `/summarize-delivery`.**
   Once the final PR is **merged**, close the execution loop cleanly instead of
   stopping at a green merge:
   a. **Validate delivery.** Run the sibling **`/validate-delivery`** skill —
      the execution-to-evidence leg. It runs the plan's behavioral tests
      agent-side and files what it found (obligations met, evidence, and any
      behavioral deltas) via the devague CLI; a failing or unchecked outcome is
      filed and reported exactly as such, never rounded up.
   b. **Summarize the delivery.** Run the sibling **`/summarize-delivery`**
      skill — the delivery-side closure leg. It turns the run into a committed
      accountability artifact (`docs/deliveries/<created-date>-<slug>.md`) that
      records planned-versus-actual delivery, the mid-work decisions the
      workforce made, where execution drifted from the plan, evidence-backed
      delivery claims (a claim without evidence stays `unverified`, never
      asserted as done — the strength ladder now draws on what
      `/validate-delivery` filed), and any remaining work. The `devague plan
      waves --json` payload you fanned out is the planned-work baseline it
      compares actuals against.
   c. **Both close partial and failed runs too.** Neither skill requires every
      wave to have merged — a run that shipped only some tasks, or none, still
      produces a truthful record: the failure lands under drift and remaining
      work, and no claim says done without evidence.

This is the accountability wrap-up after the three gates, not a fourth gate —
`/deviate`, `/validate-delivery`, and `/summarize-delivery` are all method-only
and record- or read-only (#20): none of them orchestrate, gate merges, or
mutate devague state beyond their own append-only records. Don't stop at "PR
merged" — the standing flow is **merge, then `/validate-delivery`, then
`/summarize-delivery`**.

## Hard rules (do not violate)

These protect the human-gate contract and the TDD guarantee.

- **Present the split plan before any fan-out.** Never spawn a task agent
  without prior human approval of the implementation split plan (gate 2). The
  split plan is the human's only implementation-stage decision.
- **One worktree per task.** Never run two tasks in the same worktree — file
  contention is managed by isolation, not by trust in the dependency graph.
  The dependency graph guarantees *logical* independence within a wave, not
  *file* disjointness. Conflicts surface at merge time.
- **All worktrees live under `.worktrees.<repo-name>`.** Every worktree this
  skill creates goes in that one repo-owned root beside the repo directory —
  never a shared `../worktrees/`, never inside the repo. Clean up only the
  worktrees you created; leave the root and anyone else's worktrees alone.
- **Tests before AND after merge — no exceptions.** The TDD gate must pass on
  both sides. A merge that makes tests pass only after (not before) means the
  baseline was already broken — fix the baseline first.
- **Human does not gate per-task merges.** The TDD contract replaces the
  human here. Do not pause for human approval between wave tasks.
- **No devague move runs inside a task worktree — not just `devague plan`.**
  `devague plan waves` is read-only scheduling metadata (#20); more broadly, a
  task agent never runs any devague command in its worktree, including
  `devague lapse`. If a task agent notices its own reasoning degraded — a
  skipped check, an assumption standing in for a real measurement, an
  unverified grader, missing provenance, or another `LAPSE_CODES` case
  (`devague/frame.py`) — it reports the degradation in its transcript or
  final report; it does not file it. The **main agent** files that record
  the moment the task agent's report surfaces it, not deferred to closeout
  (`devague lapse "<what>" --code <code> --origin llm`), the same way it
  alone runs every plan-mutating move — mirroring the `/scope` subagent
  boundary, where exploration subagents report and only the main agent runs
  a `devague` move (#79/#91). Adjudicating
  a filed lapse (`devague lapse --confirm`/`--reject`) is the same human who
  already owns gate 2/3 — no new role — typically exercised once the run
  reaches `/summarize-delivery`.
- **Three gates only.** The human's gates are: (1) the exported spec, (2) the
  implementation split plan, (3) the final PR. No silent fourth gate.
- **No LLM calls in the devague CLI.** The CLI is deterministic. This skill
  adds orchestration convention, not CLI behavior.
- File the record the moment the thing happens, never at closeout — written
  late is written flattering (issue 97).

## Output contract

The `split-plan` subcommand prints to **stdout** and exits 0 when a converged
plan is found. On error (no plan, cyclic graph) it exits non-zero with a
`hint:` line on stderr. The `waves` subcommand forwards the CLI's own output
contract (stdout, `--json` for structured output, exit 0 on success).

The trailing End state section (#70) never fails `split-plan`: on a `devague`
new enough to have `plan deliverables`, it quotes that command's stdout
verbatim under an ``End state (from `devague plan deliverables`):`` header; on
an older `devague`, it prints exactly one hint line naming the minimum
version (e.g. `hint: End state view requires devague >= 0.18.0 (devague plan deliverables)`)
and `split-plan` still exits 0.

`--write` adds exactly one line after all of the above: `wrote split
artifact: <path>` on the first run, `updated split artifact: <path>` on every
run after (issue #82). It calls one additional read-only command,
`devague plan show --json` (for the plan's `created` timestamp and title);
a failure there exits non-zero with that command's own stderr, same as a
`plan waves --json` failure.

## Worked example

Picking up after `/spec-to-plan` exported a plan for the frame `my-feature`:

```bash
a() { bash .claude/skills/assign-to-workforce/scripts/assign-to-workforce.sh "$@"; }

# 1. Inspect the waves
a waves

# 2. Present the implementation split plan for human review
a split-plan

# --- HUMAN: review the table, edit agent/model assignments if needed,
#     then say "approved" to proceed ---

# 3. Fan out wave 1 (t1, t2, t3 are independent — run in parallel).
#    All worktrees live under this repo's own root, beside the repo dir:
#    e.g. <parent>/devague -> <parent>/.worktrees.devague/
repo_root=$(git rev-parse --show-toplevel)
wt_root="$(dirname "$repo_root")/.worktrees.$(basename "$repo_root")"

git worktree add "$wt_root/agent-t1" -b agent/t1
git worktree add "$wt_root/agent-t2" -b agent/t2
git worktree add "$wt_root/agent-t3" -b agent/t3
# ... spawn task agents in each worktree, await completion ...

# 4. TDD-gated merge for each wave-1 task (no human per task)
git merge --no-ff agent/t1   # tests pass before + after
git worktree remove "$wt_root/agent-t1"
git merge --no-ff agent/t2
git worktree remove "$wt_root/agent-t2"
git merge --no-ff agent/t3
git worktree remove "$wt_root/agent-t3"

# 5. Advance to wave 2 (t4 depends on t1–t3 being merged)
git worktree add "$wt_root/agent-t4" -b agent/t4
# ... spawn, await, merge with TDD gate, remove worktree ...

# 6. Open the final PR (human gate 3)
bash .claude/skills/cicd/scripts/workflow.sh open
```

`devague plan waves --json` is the standing brief for each task agent — its
task id, summary, instruction, acceptance criteria, and the targets it covers
are all in that one payload. Quote those fields **verbatim** into each task
agent's brief; the fan-out is honest only if what the subagent builds against
is exactly what the user confirmed in the plan.

## Before and after this leg

```text
Previous leg: spec-to-plan
Next leg: deviate
```

After every successful, non-exempt move, the CLI prints one `next: <recommended
move>` line to stderr — follow it, or run `devague plan status` when unsure
what comes next.

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, where
the devague agent maintains it alongside the tools it operates (dogfooding),
next to its siblings `/think` and `/spec-to-plan`. It is the *third* skill in
that outbound family, covering the implementation leg after a plan converges.
The flow runs the *opposite* direction of the vendored guildmaster skills:
guildmaster pulls this **from** devague and broadcasts it to the rest of the
AgentCulture mesh. The `cite, don't import` policy still holds: downstream repos copy it,
they don't symlink or depend on it. See `docs/skill-sources.md`.
