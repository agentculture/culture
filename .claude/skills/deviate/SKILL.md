---
name: deviate
description: >
  Stop an in-flight assign-to-workforce run the moment execution must diverge
  from the confirmed plan, get explicit human approval for the divergence, and
  record it as a first-class, append-only deviation record via `devague
  deviate` before resuming — never fold a deviation silently into drift after
  the fact. Use when the user says "deviate from the plan", "we need to change
  the plan mid-run", "record a deviation", "this isn't matching the plan
  anymore", or when a task agent discovers the confirmed plan no longer
  matches reality partway through a workforce run. Authored and maintained in
  agentculture/devague (origin = devague); guildmaster pulls this skill from
  here and broadcasts it to the AgentCulture mesh — it is NOT vendored from
  guildmaster like the inbound skills here.
type: command
---

# deviate — record an approved mid-run departure from the confirmed plan

The skill is named **`deviate`**; it is the **execution-time leg** of the
devague method — the *sixth* leg, sitting between the two execution skills:

```text
scope -> think -> spec-to-plan -> assign-to-workforce -> deviate -> summarize-delivery
```

Where `/assign-to-workforce` fans out a converged plan's waves and
`/summarize-delivery` closes the loop afterward, `/deviate` runs **during**
the fan-out, at the exact moment reality stops matching the plan the human
approved at gate 2 (the implementation split plan). It is not a new standing
gate — it is the human owner of gate 2 **amending** the approved split
mid-flight, scoped to the one deviation in front of them.

The plan the user confirmed is a contract. Task agents hit constraints,
discover a dependency was wrong, or find the acceptance criteria no longer
make sense once code is in front of them. Until this skill existed, that
reality either silently reshaped what got built (undocumented drift) or the
run stalled with no recorded path forward. `/deviate` is that path: the
departure is named, approved, and recorded **before** anyone keeps building
against it.

## The method

1. **STOP the run.** The moment a task agent (or the main agent) discovers
   the confirmed plan no longer matches what needs to happen, halt — do not
   keep implementing against the stale contract and do not let the fan-out
   continue past this task.
2. **Present what, why, and what it affects.** Lay out, for the human:
   - **what** is diverging — the specific change from the confirmed task(s);
   - **why** — the constraint or discovery that forced it;
   - **what it affects** — the plan item ref(s) involved (`--task`), any
     other task ids or coverage targets it touches (`--affects`), and which
     acceptance criteria are no longer accurate.
3. **Get explicit human approval.** This is the one non-negotiable step. A
   deviation is not real until a human says yes to *this specific* departure
   — not a standing blanket permission, not an inference from silence.
4. **Record it via `devague deviate`.** Once approved, record the deviation
   as a first-class ledger entry (never edit the plan itself — the plan
   stays the untouched historical contract; see `devague/delivery.py`). An
   `--origin llm` record lands `proposed` and still needs the user's
   `--confirm` — recording is not the same as approval landing as final.
5. **Adjust the affected task briefs.** Update the working instructions the
   task agent(s) are building against so they reflect the approved
   departure, not the stale plan text.
6. **Resume.** Only after the record exists (and, for an `llm`-origin record,
   only after it is confirmed) does the fan-out continue.

Deviations are never silently folded into drift after the fact — by the time
`/summarize-delivery` runs, every departure from the plan already has a
`dN` record with a reason, an approval, and an optional classification. The
delivery summary quotes these records; it does not reconstruct drift from
memory.

## The shipped CLI surface

This skill invokes the CLI directly and stays self-contained (if `devague`
isn't on your PATH: `uv tool install devague`). Deviation state persists
under `.devague/deliveries/<plan-slug>.json` — a peer of the plan store, keyed
by the plan slug, and never touches the plan JSON itself.

| Move | What it does |
|------|---------------|
| `devague deviate "<what>" --task <tN> --reason "<text>"` | Record a deviation against plan item `<tN>`. User-origin (the default) auto-approves. |
| `devague deviate "<what>" --task <tN> --reason "<text>" --affects <ref> [<ref> ...]` | Same, also naming every other plan item ref or coverage target the deviation touches (repeatable). |
| `devague deviate "<what>" --task <tN> --reason "<text>" --classification acceptable\|risky\|needs-follow-up` | Same, tagging the deviation with the classification the drift-entry contract consumes downstream. |
| `devague deviate "<what>" --task <tN> --reason "<text>" --origin llm` | An LLM-proposed record; lands `proposed`, not `approved`. |
| `devague deviate --confirm <dN>` | User-only: approve a `proposed` deviation. |
| `devague deviate --reject <dN>` | User-only: reject a `proposed` (or any) deviation. |
| `devague deviate --list [--json]` | Read every recorded deviation back (also the default action with no positional/flag). |
| `devague deviate ... --plan <slug>` | Target a plan other than the current one. |

`--reason` is required on every record — omitting it is refused with a hint.
`--task` naming the plan item ref is likewise required.

## Hard rules (do not violate)

- **Never record a deviation the human did not approve.** `devague deviate`
  without `--origin llm` auto-approves the instant it is run — so a
  user-origin record IS the approval. Only run it after the human has said
  yes to this specific departure, never preemptively "to be safe."
- **Never continue past a refused approval.** If the human does not approve,
  the run stays stopped on that task. Do not record the deviation anyway, do
  not quietly implement the diverging approach, and do not advance the wave.
- **LLM-origin records stay proposed until the user confirms.** `--origin
  llm` lands `proposed`; only `devague deviate --confirm <dN>` (user-only)
  makes it `approved`. Same anti-fabrication contract as every other origin
  in the method — an agent's own proposal never self-confirms.
- **Deviations are never silently folded into drift after the fact.** Every
  departure from the confirmed plan gets a `dN` record at the moment it
  happens — not reconstructed from memory when `/summarize-delivery` runs
  later.
- **The CLI stays non-orchestrating (issue #20).** `devague deviate` records
  a decision the human already made; it does not spawn agents, gate merges,
  mark tasks done, or make the approval decision itself. Recording is
  deterministic — no LLM calls inside the CLI.
- **This is not a fourth standing gate.** `/assign-to-workforce`'s three
  human gates are the exported spec, the implementation split plan, and the
  final PR. `/deviate` does not add a fourth — it is the human owner of gate
  2 amending the approved split for one scoped, in-flight decision.

## Worked example

Mid-fan-out on task `t4`, the task agent discovers the acceptance criteria
assumed a helper that task `t2` never actually shipped:

```bash
# 1. STOP — the main agent halts t4's worktree before more code lands
#    against a criterion that can't be met as written.

# 2. Present what/why/what-it-affects to the human:
#    what:    t4's acceptance criterion 2 assumes a `--json` flag on a
#             helper that t2 shipped without one
#    why:     t2's scope was cut to land its wave on time
#    affects: t4 (this task), and coverage target c9 (the criterion in
#             question)

# --- HUMAN: approves dropping the --json assumption from t4's criterion ---

# 3. Record the approved deviation
devague deviate "drop the --json assumption from t4's acceptance criterion" \
  --task t4 --reason "t2 shipped its helper without --json to land its wave on time" \
  --affects t2 --affects c9 --classification acceptable

# 4. Adjust t4's working brief to match what was approved, then resume the
#    task agent against the corrected instruction.

# Read the ledger back at any point:
devague deviate --list
devague deviate --list --json
```

If the agent had proposed the record itself (`--origin llm`), the same
record would land `proposed` and need an explicit
`devague deviate --confirm d1` from the user before `/summarize-delivery`
could cite it as approved.

## After recording — resume, then hand off to /summarize-delivery

Once the affected task briefs are adjusted and the fan-out resumes, nothing
further is needed from this skill — the record already lives in the delivery
store. When the run reaches `/summarize-delivery`, that skill's Drift From
Plan and Mid-work Decisions sections quote these records by their `dN` id
instead of reconstructing drift from memory, so the connective tissue between
the confirmed plan and the delivery summary is the ledger this skill wrote,
not anyone's recollection of the run.

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, the
*sixth* in the outbound family after `/scope`, `/think`, `/spec-to-plan`,
`/assign-to-workforce`, and `/summarize-delivery`, covering the execution-time
leg that runs inside an `/assign-to-workforce` fan-out. guildmaster pulls it
from here and broadcasts it to the AgentCulture mesh; because devague is
upstream, it is **never re-vendored back** from guildmaster's re-broadcast
copy. The `cite, don't import` policy still holds: downstream repos copy it,
they don't symlink or depend on it. See `docs/skill-sources.md`.
