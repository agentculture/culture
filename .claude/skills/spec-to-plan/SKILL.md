---
name: spec-to-plan
description: >
  Turn a converged devague spec into a buildable plan by working forwards (the
  spec→plan leg; drives the `devague plan` CLI group). Seed a plan from a
  converged frame, add tasks that collectively cover every coverage target (the
  frame's confirmed claims + honesty conditions), give each task acceptance
  criteria and an honest dependency order, park genuine unknowns as first-class
  risks, and export a plan only once it *converges*. Use when the user says
  "spec to plan", "stp", "turn this spec into a plan", "plan this spec", "make a
  build plan", or after the /think skill exports a spec. Authored and maintained
  in agentculture/devague (origin = devague); guildmaster pulls this skill from
  here and broadcasts it to the AgentCulture mesh — it is NOT vendored from
  guildmaster like the inbound skills here.
type: command
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
| `task "<summary>"` | Add a task. `--accept "<crit>"`, `--dep <tN>`, `--covers <c*/h*>` (each repeatable), `--instruction "<text>"` (verbatim working guidance, at creation); `--origin llm` lands it `proposed`. |
| `instruct <tN> "<text>"` | Add/update a task's working instruction. Changing it on an already-`confirmed` task flips it back to `proposed` — the user re-confirms (the plan side's mirror of the frame side's `interrogate --instruction` re-confirm rule). |
| `accept <tN> "<crit>"` | Add an acceptance criterion to a task. |
| `amend <tN>` | Edit a task's summary (`--summary`) and/or replace/remove an acceptance criterion by index. May flip a `confirmed` task back to `proposed`; refuses on a rejected task. |
| `depend <tN> --on <tM>` | Record that task `tN` depends on `tM`; `--remove` cuts one edge. Both a self-dependency and an unknown task id are refused **at creation** with an actionable hint (devague#86) — the same checks `task --dep` applies. |
| `cover <tN> --target <c*/h*>` | Mark a task as covering a coverage target. Validated against the **live** source frame, exactly as `converge` derives it — so a target the frame grew after seeding can be covered straight away (devague#90). |
| `defer <target-id> --reason "<text>"` | Deliberately exclude a coverage target from this plan's gate (`--undo` reverses it). The honest way to scope a plan to a milestone: deferred targets stop blocking `converge` and render in the exported plan's **Deferred targets** section with their reason (devague#85). An `out_of_scope` *risk* does **not** excuse a target — only `defer` does. |
| `confirm <tN> [<tN>…]` / `reject <tN> [<tN>…]` | Resolve one or more tasks in one **transactional** call — all ids valid or nothing changes. **User-only decision.** Matches the frame engine's multi-id `confirm`/`reject` (parity landed for devague#86). |
| `risk "<text>" --kind <kind>` | Record a first-class plan risk (`--task <tN>` to attach). `--resolve <rN> --decision "<text>"` closes one out; `--amend <rN> --text "<text>"` corrects a risk's text in place, preserving its id, kind, task link, and resolution state (devague#84). |
| `converge` | Evaluate the gate against the **live** source frame; list remaining gaps, plus non-blocking warnings (e.g. a confirmed task with no instruction). Deferred targets are excluded from the gate. |
| `export` | Write the buildable plan to `docs/plans/` — only after `converge` passes. |
| `deliverables` | Read-only "end state" preview: the source frame's confirmed announcement/after-state/success-signal claims, every terminal task with its acceptance criteria, and the surviving open items. Never refuses — useful before convergence too. |
| `waves` | Emit deterministic dependency waves — `{plan, waves}` plus a top-level `tasks` object keyed by task id (per-task summary/instruction/acceptance criteria/covers — see *The `waves --json` payload* below) — scheduling + subagent-brief metadata only, *not* orchestration. Read-only, works on an in-progress plan; refuses a cyclic/dangling graph. Devague describes the graph; an operator decides how to run it (#20). |
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

After every successful, non-exempt move (`status` itself is exempt, since
reporting the next move is already its whole purpose) the CLI also prints a
`next: <recommended move>` line to **stderr**. Follow that hint or run
`devague plan status` — either gets you the same recommended next move.

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
- **Never fake coverage to satisfy the gate — `defer` instead.** If a target is
  deliberately out of scope (a later milestone, a separately reviewed change),
  do **not** write a task that merely *mentions* it so coverage goes green. That
  is the exact dishonesty devague#85 was filed about: a task claiming a target it
  does not deliver looks perfectly healthy to the gate. Run
  `devague plan defer <target-id> --reason "<why>"` — the target stops blocking
  `converge` and is named, with its reason, in the exported plan's **Deferred
  targets** section, so the exclusion is visible to a reviewer instead of implied
  by absence. Deferring is a scoping decision: surface it to the user, don't take
  it unilaterally.
- **Keep the graph honest.** Dependencies must reference real tasks and form an
  acyclic graph; the gate rejects dangling deps and cycles.
- **Park real unknowns as risks; don't paper over them.** A genuinely unknown
  decision is an `unknown_blocking` risk — it holds back convergence, by design.
- **Converge against the live frame.** `converge`/`export` re-load the source
  frame every time. If the frame was deleted or has regressed below convergence,
  they refuse — re-converge the spec (in `/think`) first.
- File the record the moment the thing happens, never at closeout — written
  late is written flattering (issue 97).

## Coaching toward small, file-disjoint, TDD-gated tasks

When authoring a plan that will be built via parallel execution (fanned out to
multiple agents via the downstream `/assign-to-workforce` skill), prefer the
following discipline to maximize parallelism and minimize merge friction:

### Acceptance criteria are the testable contract; instruction is the working guidance

Two fields now do two distinct jobs on every task (shipped: devague#53 t5):

- **`--accept "<criterion>"`** (repeatable) — the **testable contract**: what a
  test suite checks to prove the task done. Write each as something a cheaper
  model can be validated against test-first: name the files or modules the task
  owns, the observable behavior that proves it done, and the compatibility
  constraints ("pre-existing plans load with no error"). A criterion a subagent
  can't be validated against alone is a summary, not a contract.
- **`--instruction "<text>"`** (at `task` time) / **`instruct <tN> "<text>"`**
  (afterwards) — verbatim **working guidance** carried to the subagent: the
  approach to take, which files to touch first, anything the acceptance
  criteria don't spell out. Write it yourself; never invent filler to satisfy
  the gate. Changing it on an already-`confirmed` task flips the task back to
  `proposed` — the user re-confirms.

`devague plan converge` warns (non-blocking) when a confirmed task carries no
instruction:

```text
task t1 has no instruction — attach operator guidance with `devague plan instruct t1 "<text>"`
```

Neither field replaces the other: acceptance criteria stay the pass/fail gate;
instruction is what a subagent reads before it starts, quoted verbatim (never
paraphrased) into the brief — see *The `waves --json` payload* below.

### Text hygiene for exports

The exported plan-md must pass markdown lint. The plan's H1 inherits the
*frame's* title — set a short, period-free `--title` at `devague new` time (see
`/think`'s export-hygiene rules). And backtick angle-bracket placeholders in
task text (`` `instruct <tN>` ``, not `instruct <tN>`) — bare ones fail MD033.
There is no task-edit move yet, so fixing text after confirmation means
hand-editing state JSON.

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

### The `waves --json` payload — the subagent brief

`devague plan waves --json` keeps its original shape — `{"plan": "<slug>",
"waves": [[...], ...]}`, the ordered task-id scheduling batches — and adds a
top-level `"tasks"` object, keyed by task id, carrying each task's brief
verbatim (devague#53 t9, shipping in this same increment):

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

This is enough to build a per-subagent brief with **no external context** —
no need to also fetch `plan show --json` or the exported plan-md. Quote
`instruction` and `acceptance_criteria` verbatim into the brief; don't
paraphrase them.

### How to route tasks to the workforce

Once your plan converges, `devague plan waves` emits the dependency-graph plus
the per-task brief above as **scheduling metadata** (ordered batches of task
IDs, each with its summary/instruction/acceptance criteria/covers). This feeds
directly into the `/assign-to-workforce` skill, which:

1. Displays the plan, waves, and suggested per-task subagent/model pairing.
2. Waits for the human to approve the implementation split plan (or edit
   assignments).
3. Fans out approved waves to isolated subagent worktrees (one per task per
   wave) — each subagent's brief quotes its task's `instruction` and
   `acceptance_criteria` verbatim from the payload above.
4. Returns control to the main agent, which TDD-gates each merge before moving
   to the next wave.

Plan for workforce execution early: narrow task scope, write crisp acceptance
criteria, attach a working instruction, and strive for wide waves with
disjoint files.

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
    --covers c1 --covers c3 --instruction "implement in devague/frame.py; see docs/spec-contract.md for the schema"
p task "Pressure-test honesty conditions" --dep t1 --covers h1 --covers h2 \
    --accept "every honesty condition maps to a test"

# Add/refine an instruction after the fact — changing it on a confirmed task
# flips the task back to 'proposed' (the user re-confirms):
p instruct t2 "write tests/test_honesty.py covering each honesty condition"

# Park a genuine unknown instead of guessing:
p risk "exact rollout sequencing" --kind unknown_nonblocking

p status        # what's left + the next move
p converge      # gate; resolve any listed gaps (warnings never block export)
p export        # writes docs/plans/my-feature.md once converged
p waves --json  # scheduling metadata + the per-task subagent brief
```

The exported plan-md is a buildable artifact: topologically ordered tasks, each
with acceptance criteria and the spec targets it covers. It feeds directly into
implementation (or `superpowers:writing-plans`).

## After the plan converges — hand off to /assign-to-workforce

Once `converge` passes and `export` writes the plan-md, this leg is done.
`devague plan waves --json` emits the dependency-graph plus the per-task brief
(see above) as scheduling metadata — the single source the `/assign-to-workforce`
skill needs to fan the plan's waves out to parallel subagents, get the human's
go/no-go on the implementation split plan, and TDD-gate each merge. Continue
with `/assign-to-workforce` next; if a fan-out mid-run needs to diverge from
this plan, that is `/deviate`'s job, not a silent edit here.

## Before and after this leg

```text
Previous leg: challenge
Next leg: assign-to-workforce
```

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, where the
devague agent maintains it alongside the tool it operates (dogfooding), next to
its sibling `/think`. It is the *inverse* of the other skills under
`.claude/skills/`, which devague vendors **from** guildmaster. guildmaster
pulls it **from** devague and broadcasts it to the rest of the AgentCulture mesh.
The `cite, don't import` policy still holds: downstream repos copy it, they don't
symlink or depend on it. See `docs/skill-sources.md`.
