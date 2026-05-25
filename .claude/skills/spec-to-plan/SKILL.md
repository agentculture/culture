---
name: spec-to-plan
type: command
description: >
  Turn a converged devague spec into a buildable plan by working forwards (the
  spec→plan leg; drives the `devague plan` CLI group). Seed a plan from a
  converged frame, add tasks that collectively cover every coverage target (the
  frame's confirmed claims + honesty conditions), give each task acceptance
  criteria and an honest dependency order, park genuine unknowns as first-class
  risks, and export a plan only once it *converges*. Use when the user says
  "spec to plan", "stp", "turn this spec into a plan", "plan this spec", "make a
  build plan", or after the /think skill exports a spec. Authored and maintained
  in agentculture/devague (origin = devague); guildmaster re-broadcasts it to the
  AgentCulture mesh — it originates in devague, not guildmaster (the supplier
  post steward→guildmaster cutover).
---

# spec-to-plan — work a converged spec forwards into a buildable plan

The skill is named **`spec-to-plan`**; the product/CLI it drives is the
**`devague plan`** command group. (The prior leg — turning a vague idea into a
spec — is the sibling **`/think`** skill.) It is the **forward** peer of the
working-backwards spec engine: where `/think` converges on *what* to build,
`/spec-to-plan` converges on *how* to build it.

A plan is seeded from a **converged frame** and tracks **tasks** against the
spec's **coverage targets**. The CLI is **deterministic and move-driven** — you
(the agent) choose the next move; the CLI tracks state and tells you what's still
missing. Run `devague plan learn` for the method and `devague plan explain
<move>` for any single move.

## How to run

The entry point is `scripts/spec-to-plan.sh`. Invoke it from the repository you
are speccing (plans persist under `.devague/` in the current directory, alongside
the frames they derive from):

```bash
bash .claude/skills/spec-to-plan/scripts/spec-to-plan.sh <move> [args...]
bash .claude/skills/spec-to-plan/scripts/spec-to-plan.sh status
```

It resolves the CLI portably — an installed `devague` on `PATH` (the normal
case), falling back to `uv run devague` inside the devague checkout, else an
install hint. Every move — including `status` — is forwarded verbatim as
`devague plan <move>`, so you can equally call the CLI directly
(`devague plan <move> …`).

### Moves

| Move | What it does |
|------|--------------|
| `new --frame <slug>` | Seed a plan from a **converged** frame. Derives the coverage targets (`c*`/`h*`) the plan must satisfy. Refuses an unconverged frame. |
| `task "<summary>"` | Add a task. `--accept "<crit>"`, `--dep <tN>`, `--covers <c*/h*>` (each repeatable); `--origin llm` lands it `proposed`. |
| `accept <tN> "<crit>"` | Add an acceptance criterion to a task. |
| `depend <tN> --on <tM>` | Record that task `tN` depends on `tM`. |
| `cover <tN> --target <c*/h*>` | Mark a task as covering a coverage target. |
| `confirm <tN>` / `reject <tN>` | Resolve a task. **User-only decision.** |
| `risk "<text>" --kind <kind>` | Record a first-class plan risk (`--task <tN>` to attach). |
| `converge` | Evaluate the gate against the **live** source frame; list remaining gaps. |
| `export` | Write the buildable plan to `docs/plans/` — only after `converge` passes. |
| `waves` | Emit deterministic dependency waves (`{plan, waves}`) — scheduling metadata only, *not* orchestration. Read-only, works on an in-progress plan; refuses a cyclic/dangling graph. Devague describes the graph; an operator decides how to run it (#20). |
| `status` | Read-only: where the plan stands + the recommended next move, re-checked against the live frame (`--json` too). |
| `show` / `list` | Render a plan / list plans (`--json` for raw state). |
| `learn` / `explain <move>` | Teach the method / explain one move. |

Risk kinds (shared with the frame engine): `unknown_nonblocking`,
`unknown_blocking`, `out_of_scope`, `follow_up`.

### `status` — the next-move verb

`status` is a first-class, **read-only** CLI verb (`devague plan status`,
internalised from this wrapper in 0.11.0 — issue
[#30](https://github.com/agentculture/devague/issues/30)). It composes
`devague plan list` + `devague plan converge` and prints where the current plan
stands, the remaining gaps, and the recommended next move derived from the first
gap. Like `converge`/`export` it re-checks the **live** source frame (so frame
drift surfaces as an error), but it never mutates state. Pass `--json` for the
structured payload (`{plan, total, ready_for_plan, blockers, warnings,
parked_items, required_next_moves}`).

```text
plan: my-feature    (1 plan total)
convergence: NOT passed — 2 gap(s):
  - coverage target c5 (boundary) has no confirmed task
  - task t2 has no acceptance criteria

recommended next move (first gap):
  cover c5: devague plan task "<summary>" --covers c5 --accept "<...>"
```

Run it whenever you're unsure what to do next.

## Hard rules (do not violate)

These are the point of the method — convergence must mean something.

- **Seed from a converged spec only.** `plan new` refuses a frame that hasn't
  converged. The plan's coverage targets *are* the spec's confirmed claims and
  honesty conditions — there is nothing honest to plan against until the spec
  converges.
- **LLM proposals stay proposed.** A task captured with `--origin llm` lands as
  `proposed`. **Never `confirm` your own proposal.** Confirmation is a user-only
  decision — surface the proposed task and let the user confirm or reject it.
- **Cover every target; criteria on every task.** The gate requires every
  coverage target to be covered by a confirmed task, and every confirmed task to
  carry at least one acceptance criterion. Don't hand-wave a task as "done-ish."
- **Keep the graph honest.** Dependencies must reference real tasks and form an
  acyclic graph; the gate rejects dangling deps and cycles.
- **Park real unknowns as risks; don't paper over them.** A genuinely unknown
  decision is an `unknown_blocking` risk — it holds back convergence, by design.
- **Converge against the live frame.** `converge`/`export` re-load the source
  frame every time. If the frame was deleted or has regressed below convergence,
  they refuse — re-converge the spec (in `/think`) first.

## Coaching toward small, file-disjoint, TDD-gated tasks

When authoring a plan that will be built via parallel execution (fanned out to
multiple agents via the downstream `/assign-to-workforce` skill), prefer the
following discipline to maximize parallelism and minimize merge friction:

### Small and crisply scoped

Each task should be **small enough for a simpler or cheaper model to build
test-first** without re-deriving the full design. If a task spans multiple files
or architectural layers, split it — narrow scope forces you to write sharp
acceptance criteria and keeps waves wide.

### File disjoint

**Prefer tasks that touch non-overlapping files.** When two same-wave tasks
modify the same file, merge collision becomes inevitable. The dependency graph
alone *does not* guarantee file disjointness — it only sequences task *content*
dependencies; same-wave tasks with overlapping file-writes must be split across
waves or given explicit dependencies.

Check `devague plan waves` output: if a wave is wide but all tasks touch
`src/core.py`, the wave is *formally* parallel but *operationally* serialized at
merge. Reorder task boundaries so wide waves operate on disjoint file sets.

### TDD acceptance criteria on every task

Every confirmed task must carry **at least one acceptance criterion**, phrased as
a testable condition (not a vague outcome). For example:

- Bad: "Implement the parser"
- Better: "Parser accepts a valid spec file and rejects malformed YAML without
  data loss"

Acceptance criteria are **the contract** between the main agent (who merges) and
the subagent (who builds). A test suite derived from these criteria validates
each task's output *before* merge, independent of model capability. This is not
optional: `devague plan converge` warns (non-blocking) when a confirmed task
lacks criteria.

### The key invariant: parallel = serial

**A plan built in parallel must yield identical results to building it serially.**
This is guaranteed only if:

1. Same-wave tasks have no inter-task dependencies (checked by `waves`).
2. Same-wave tasks touch disjoint files (you must verify; the CLI does not).
3. Each task's acceptance criteria are sharp enough that a subagent's output
   passes them independent of whether it was built in isolation or alongside
   other tasks.

The TDD gate — tests pass before *and* after the merge — is the main agent's
proof that parallelism didn't break correctness.

### How to route tasks to the workforce

Once your plan converges, `devague plan waves` emits the dependency-graph as
**scheduling metadata** (ordered batches of task IDs). This feeds directly into
the `/assign-to-workforce` skill, which:

1. Displays the plan, waves, and suggested per-task subagent/model pairing.
2. Waits for the human to approve the implementation split plan (or edit
   assignments).
3. Fans out approved waves to isolated subagent worktrees (one per task per
   wave).
4. Returns control to the main agent, which TDD-gates each merge before moving
   to the next wave.

Plan for workforce execution early: narrow task scope, write crisp acceptance
criteria, and strive for wide waves with disjoint files.

## Output contract

Results go to **stdout**, diagnostics and errors to **stderr** — a strict split
you can rely on when parsing. Pass `--json` to any move for a structured payload.
Exit code `0` on success, non-zero on user error (with a `hint:` line). Plans
live under `.devague/plans/` in the current directory; the exported plan-md lands
in `docs/plans/`.

## Worked example

Picking up after `/think` exported a spec for the frame `my-feature`:

```bash
p() { bash .claude/skills/spec-to-plan/scripts/spec-to-plan.sh "$@"; }

p new --frame my-feature        # seeds the plan + its coverage targets
p show                          # see the c*/h* targets you must cover

p task "Build the core engine" --accept "engine has a convergence gate" \
    --covers c1 --covers c3
p task "Pressure-test honesty conditions" --dep t1 --covers h1 --covers h2 \
    --accept "every honesty condition maps to a test"

# Park a genuine unknown instead of guessing:
p risk "exact rollout sequencing" --kind unknown_nonblocking

p status        # what's left + the next move
p converge      # gate; resolve any listed gaps
p export        # writes docs/plans/my-feature.md once converged
```

The exported plan-md is a buildable artifact: topologically ordered tasks, each
with acceptance criteria and the spec targets it covers. It feeds directly into
implementation (or `superpowers:writing-plans`).

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, where the
devague agent maintains it alongside the tool it operates (dogfooding), next to
its sibling `/think`. It is the *inverse* of the other skills under
`.claude/skills/`, which devague vendors **from** guildmaster. When ready,
guildmaster pulls it **from** devague and re-broadcasts it to the rest of the
AgentCulture mesh.
The `cite, don't import` policy still holds: downstream repos copy it, they don't
symlink or depend on it. See `docs/skill-sources.md`.
