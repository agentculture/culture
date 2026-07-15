---
name: summarize-delivery
description: >
  Close the loop after an assign-to-workforce run by turning what actually
  happened into an accountability artifact — planned versus actual delivery,
  mid-work decisions, plan drift, evidence-backed delivery claims, and remaining
  work. The plan the user confirmed is the contract; this skill records where
  execution obeyed it, where it changed, and what is genuinely safe to claim as
  delivered. Runs on complete, partial, AND failed runs — failure is reported
  faithfully, never smoothed over. Use when the user says "summarize delivery",
  "delivery summary", "wrap up", "close the loop", "what did we actually ship",
  or "plan versus actual", or after assign-to-workforce merges (or fails to
  merge) a plan's waves. Authored and maintained in agentculture/devague
  (origin = devague); guildmaster pulls this skill from here and broadcasts it
  to the AgentCulture mesh — it is NOT vendored from guildmaster like the
  inbound skills here.
type: command
---

# summarize-delivery — turn a workforce run into an accountability artifact

The skill is named **`summarize-delivery`**; it is the **delivery-side closure
leg** that closes the devague method's six-leg flow:

```text
scope -> think -> spec-to-plan -> assign-to-workforce -> deviate -> summarize-delivery
```

It runs *after* the sibling **`/assign-to-workforce`** skill has executed (or
attempted to execute) a converged plan — and after any **`/deviate`** records
that run produced, since `/deviate` slots in **between**
`/assign-to-workforce` and this skill, recording approved mid-run departures
the moment they happen during the fan-out. Where `/assign-to-workforce` takes
the plan to delivery, this skill *summarizes* that delivery: it separates
what was **planned** from what was **actually delivered**, records the
**mid-work decisions** and **plan drift** the execution produced — quoting
`/deviate`'s approved `dN` records as the recorded ground truth wherever they
cover an item — states **delivery claims** with evidence and confidence, and
names the **remaining work**.

The plan the user confirmed is a **contract, not a fiction**. Agents make
mid-work decisions, hit constraints, cut scope, and produce delivery claims
that were never in the plan. Until now the flow had no wrap-up leg to record
any of it. This skill is that leg: the summary records where execution obeyed
the contract, where it changed, and what is actually safe to claim as
delivered.

It is **not** a polite progress report and **not** a restatement of the plan.
It is an accountability artifact — auditable by a human who never watched the
run.

## The three readers

Write for exactly these three, and no one who was present for the execution:

1. **The operator (main agent)** closing a workforce run — assembling the
   ground truth of what merged, what failed, and what still needs doing.
2. **The human owner at the final PR gate** — who uses the artifact as the
   review map: every delivery claim traces to a plan task or an explicit drift
   entry, so the plan-versus-actual comparison is mechanical, not rhetorical.
3. **Any later reader** who needs to know what actually shipped without
   replaying the execution transcript. The artifact is committed and
   self-contained; nothing in it depends on unrecorded memory of the run.

## Method-only — no script, no CLI verb

This is a **method-only** skill (v1), modelled on `/scope` at its birth: a
`SKILL.md` with an output template and no entry-point script, no new CLI verb.
The deterministic devague CLI surface is **unchanged** — Devague never spawns
agents, marks tasks done, gates merges (`/assign-to-workforce` owns the TDD
gate), or mutates delivery state. The CLI stays deterministic and
non-orchestrating (issue
[#20](https://github.com/agentculture/devague/issues/20)); the summary is
agent-side work. A future devague *delivery engine* — a third structural peer
with a delivery store and a deterministic no-overclaim gate — is parked as
follow-up, deferred until dogfooding the method shows machine state is needed.

Because there is no script, this skill invokes the read-only devague moves
directly (if `devague` isn't on your PATH: `uv tool install devague`).

## The method

1. **Establish the planned-work baseline — verbatim, starting from the
   skeleton.** Run `devague summary [--plan <slug>] [--json]` first. It
   renders the eight-section template pre-filled, verbatim, from the plan's
   tasks, the plan's live source frame, and the delivery (deviation) store —
   with `<fill: ...>` placeholders standing in for everything
   execution-dependent (run status, per-task delivery status, evidence,
   delivery claims). Continue filling in that skeleton rather than retyping
   it from scratch — fewer transcription errors, same verbatim-baseline rule.
   **Fall back to hand-assembly** when `devague summary` isn't available at
   all — the verb is missing (an older devague predates the command) or the
   underlying state it needs can't be read (no current plan, or a delivery
   record written by a newer devague) — by reconstructing the baseline
   read-only via `` `devague plan show [--json]` `` and
   `` `devague plan waves --json` ``, the same enriched payload
   `/assign-to-workforce` fans out, keyed by task id with each task's
   `summary`, `instruction`, `acceptance_criteria`, and `covers`. If no plan
   state exists at all, degrade further still (see "Degrading when there is
   no plan state"). **Whichever path was taken, say so in the artifact's
   `baseline:` line** — one of `` `devague summary skeleton` ``,
   `` `devague plan (hand-assembled)` ``, or
   `` `git+PR history (no plan state)` ``. Quote task ids and summaries
   **verbatim** into the Planned Work section either way — mirroring the
   verbatim-brief rule in `/assign-to-workforce`. Drift is then measured
   against the contract the user confirmed, never against a paraphrase.
2. **Establish actual delivery.** Reconcile the baseline against what merged:
   the merged branches, commits, and PRs of the run. **Every** plan task must
   be accounted for as **delivered / partial / dropped / blocked**, keyed by
   task id — 100 %, no silent omissions.
3. **Record mid-work decisions.** Every constraint discovered, scope cut, or
   choice made during execution that was *not* in the plan.
4. **Classify drift against the plan.** Any plan task whose delivery differs
   from its contract becomes a drift entry — exhaustive relative to the plan,
   never silently normalized. Each entry names the plan item it diverges from,
   the reason, and **exactly one** classification: `acceptable` / `risky` /
   `needs-follow-up`.
5. **Gather evidence, read-only.** You may run read-only verification — the
   test suite, the linters, `git log` — to substantiate a claim *before* you
   write it. Verification never mutates code or state. A claim you cannot
   verify stays `unverified`.
6. **State delivery claims with confidence + evidence.** Each claim carries a
   confidence level (`high` / `medium` / `low` / `unverified`) and at least one
   **resolvable** evidence pointer, or an explicit `unverified` marker. A claim
   without evidence is `unverified` — never asserted as done.
7. **Name the remaining work.** What is incomplete, deferred, or newly
   discovered — including any failure and its cause.
8. **Write the artifact and commit it.** Fill the eight-section template into a
   committed, durable file at `docs/deliveries/<created-date>-<slug>.md` — the
   structural peer of `docs/specs/` and `docs/plans/`. Reads of plan and frame
   state stay read-only; producing the artifact leaves `.devague/` byte-
   identical.

## The eight-section template

Fill this in verbatim — all eight sections, in this order. Every section stays
writable for a **partial or failed** run; nothing here requires all waves
merged, and there is no completion precondition (a run that merged zero waves
is still a valid input).

```markdown
# Delivery Summary — <plan title>

plan: `<slug>` · run: `<complete | partial | failed>` · date: `<created-date>`
baseline: `<devague summary skeleton | devague plan (hand-assembled) | git+PR history (no plan state)>`

## Intent

<One paragraph: what this run set out to deliver, and the plan it executed.>

## Planned Work

<The plan's tasks, quoted VERBATIM from the `devague summary` skeleton (or,
when hand-assembling, `devague plan waves --json`) — task id and summary,
never paraphrased. When no plan state exists, quote the git/PR baseline
instead and SAY SO here.>

- `t1` — <task summary, verbatim>
- `t2` — <task summary, verbatim>
- ...

## Actual Delivery

<Every plan task accounted for — 100 %, keyed by task id — as one of
delivered / partial / dropped / blocked.>

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | <what merged> |
| `t2` | partial | <what merged, what is missing> |
| `t3` | blocked | <why it could not proceed> |

## Mid-work Decisions

<Constraints discovered, scope cuts, and choices made during execution that
were not in the plan. When a delivery store exists for this plan, quote each
approved deviation by its `dN` id — the record is the decision, consumed here,
not re-litigated. A decision no record covers is still captured directly. One
per bullet.>

- `d2` — <what deviated, from the record> — <the record's reason>
- <decision not covered by any deviation record> — <why it was made>

## Drift From Plan

<Every plan task whose delivery differs from its contract. Exhaustive relative
to the plan. When a delivery store exists, an entry covered by an approved
deviation names the plan item as `` `tN` (`dN`) `` and inherits that record's
reason and classification verbatim. Drift NOT covered by any record is still
recorded here exhaustively, exactly as before — never silently normalized. If
nothing drifted, state "no drift" and back it with the task-by-task accounting
above.>

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t2` (`d2`) | <quoted from the `d2` record's reason> | needs-follow-up |
| `t3` | <what changed vs. the confirmed task — no record covers this> | risky |

## Evidence

<The read-only checks run to substantiate the claims below: test node ids that
ran, lint results, `git log` ranges, PR/issue numbers. This section is what
makes every claim resolvable.>

- tests: `<pytest node id>` — <pass | fail>
- lint: `<command>` — <result>
- commits: `<sha>..<sha>`
- PRs / issues: <#NN, ...>

## Delivery Claims

<Each claim: the assertion, a confidence level, and a resolvable evidence
pointer — or an explicit `unverified` marker. No claim is asserted as done
without evidence.>

| Claim | Confidence | Evidence |
|-------|------------|----------|
| <what was delivered> | high | commit `<sha>` / file `<path>` |
| <what was delivered> | medium | PR `#<n>` · test `<node id>` |
| <what is asserted but not checked> | unverified | (no evidence — not claimed done) |

## Remaining Work / Follow-up

<What is incomplete, deferred, blocked, or newly discovered — including any
failure and its cause. Every partial/dropped/blocked task from Actual Delivery
reappears here with its next step.>

- <remaining item> — <next step / owner>
```

### Delivery Claims — the row contract

Every Delivery Claims row carries three fields:

- **Claim** — the specific thing asserted as delivered.
- **Confidence** — exactly one of `high` / `medium` / `low` / `unverified`.
- **Evidence** — at least one **resolvable** pointer, or the explicit
  `unverified` marker. A resolvable pointer is one a reader can follow: a
  **commit SHA** that exists, a **file path** that is present, a **PR or issue
  number** that is real, or a **test node id** that ran. "It works" is not
  evidence.

### Drift From Plan — the entry contract

Every Drift From Plan entry names three things:

- **The plan item** it diverges from (by task id, quoted from the plan).
- **The reason** for the divergence.
- **Exactly one** classification: `acceptable` (a defensible deviation),
  `risky` (may cause a problem; flag it), or `needs-follow-up` (leaves work
  the plan assumed done).

When a delivery store exists and an approved `devague deviate` record covers
the item, cite it by its `dN` id and inherit its reason and classification
verbatim — the record is the recorded ground truth, not something this skill
re-derives. An item no record covers still gets an entry here, worked out the
same way as before.

## Degrading when there is no plan state

The summary is producible from **durable** inputs — plan state, git history, PR
links, test output — and never depends on unrecorded memory of the run. There
are three tiers, most to least mechanical, and the artifact's `baseline:` line
names which one applied:

1. **`devague summary` skeleton** — the plan (and, if any, its delivery
   record) can both be read; the render is fully mechanical.
   `baseline: devague summary skeleton`.
2. **Hand-assembly** — `devague summary` isn't available (the verb is missing,
   or the state it needs can't be read) but the plan itself can still be read:
   reconstruct the baseline read-only via `devague plan show [--json]` and
   `devague plan waves --json`. `baseline: devague plan (hand-assembled)`.
3. **Git + PR history** — no devague plan state exists at all: degrade to git
   history and PR history as the planned-work baseline.
   `baseline: git+PR history (no plan state)`.

**Whichever tier applied, SAY SO in the artifact** (in the `baseline:` line
and the Planned Work section). The delivery summary is still a valid
accountability artifact either way; it just names its baseline honestly
instead of pretending a more mechanical path was used.

## The only devague moves this skill uses — all read-only

This skill **never mutates devague state**. The complete set of devague moves
it documents is read-only:

| Move | What it reads |
|------|---------------|
| `devague summary [--pr] [--json]` | The eight-section delivery-summary skeleton (or condensed `--pr` skeleton), pre-filled verbatim from the plan's tasks, its live source frame, and the delivery (deviation) store — the primary planned-work baseline (Method step 1). |
| `devague deviate --list [--json]` | Every recorded deviation, read back by `dN` id — the source Drift From Plan and Mid-work Decisions quote. Recording or confirming a deviation is `/deviate`'s job, never this skill's. |
| `devague plan show [--json]` | The plan's tasks, acceptance criteria, dependencies — the hand-assembly fallback when `devague summary` (or the state it needs) is unavailable. |
| `devague plan waves --json` | The wave batches + per-task `summary` / `instruction` / `acceptance_criteria` / `covers`, keyed by id — the hand-assembly fallback's verbatim planned-work baseline. |
| `devague scope --list [--json]` | Recorded scope-exploration findings, if the frame carried any. |
| `devague show [--json]` | A frame / plan rendered for reading. |
| `devague status [--json]` | Where a frame stands (read-only next-move helper). |

No other devague command appears in this method. Producing a delivery summary
leaves `.devague/` byte-identical (issue
[#20](https://github.com/agentculture/devague/issues/20)).

## Hard rules (do not violate)

These are the point of the method — a delivery summary must be trustworthy.

- **No overclaiming.** A delivery claim without evidence is marked `unverified`,
  never asserted as done. The `unverified` marker survives into the committed
  artifact; no downstream step upgrades a claim's confidence without new
  evidence.
- **Partial and failed runs are valid inputs.** There is no completion
  precondition — a run that merged zero waves is still a valid input. Failure
  is reported faithfully: it appears under Drift and Remaining Work with its
  cause, and no delivery claim says "done". No section ever becomes unwritable.
- **Verification is read-only.** You may run the test suite, the linters, and
  `git log` to substantiate a claim before writing it. Verification **never**
  mutates code or state. A claim you cannot verify stays `unverified`.
- **No devague state mutation.** The only devague moves this skill uses are the
  read-only `summary`, `deviate --list`, `plan show`, `plan waves`,
  `scope --list`, `show`, and `status` (see the table above). `deviate --list`
  is read-only — recording or confirming a deviation belongs to `/deviate`,
  never this skill. Never run a mutating devague command, and never run
  `devague plan` inside a task worktree to "mark a task done" — that is
  `/assign-to-workforce`'s boundary too (#20).
- **Account for 100 % of plan tasks.** Every plan task appears in Actual
  Delivery as delivered / partial / dropped / blocked — no silent omissions.
  Both the task count and the claim-evidence coverage are checkable by
  inspecting the artifact alone, with no hidden context.
- **Quote the plan verbatim.** Planned Work quotes task ids and summaries
  verbatim from the `devague summary` skeleton (or `devague plan waves --json`
  when hand-assembling). Drift is measured against the confirmed contract, not
  a reworded version of it.

## Worked example

A **partial** run: three tasks were planned; `t1` merged cleanly, `t2` merged
after an approved mid-run deviation, and `t3` failed its post-merge TDD gate
and was reverted. Every section is still writable — nothing about the failure
makes the artifact unproducible.

```bash
# 1. Establish the planned-work baseline — read-only, quoted verbatim.
devague summary                # -> pre-filled eight-section skeleton, keyed by
                               #     task id, `<fill: ...>` placeholders for
                               #     everything execution-dependent
devague deviate --list         # -> d1 (approved, acceptable): t2 dropped an
                               #     assumed flag mid-run

# 2. Establish actual delivery — read-only reconciliation.
git log --oneline main~4..main   # what merged
gh pr view 71                    # the run's PR (evidence pointers)
uv run pytest tests/test_export.py::test_widget_round_trips -q  # verify a claim
```

The resulting `docs/deliveries/2026-07-09-widget-export.md`:

```markdown
# Delivery Summary — widget export

plan: `widget-export` · run: `partial` · date: `2026-07-09`
baseline: `devague summary skeleton`

## Intent

Ship the `widget export` command as three independent tasks fanned out by
/assign-to-workforce.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — add the `export` verb to the CLI chassis
- `t2` — implement the widget-md renderer
- `t3` — wire the convergence gate into `export`

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `export` verb registered; merged in PR `#71` |
| `t2` | delivered | renderer added at `devague/render/widget_md.py` |
| `t3` | blocked | post-merge tests failed; merge reverted |

## Mid-work Decisions

- `d1` — dropped the `--verbose` flag from `t2`'s CLI surface — the plan
  assumed it, but the renderer never needed a verbose mode (recorded via
  `/deviate`, approved)
- t2 also renders an absent field as an empty line rather than filler — no
  deviation record covers this; captured here directly, matching the
  no-fabrication rule the plan assumed but did not spell out.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t2` (`d1`) | dropped the `--verbose` flag — the plan assumed it, but the renderer never needed a verbose mode | acceptable |
| `t3` | gate raised on an unconverged plan; reverted, not delivered | needs-follow-up |

## Evidence

- tests: `tests/test_export.py::test_widget_round_trips` — pass
- tests: `tests/test_export.py::test_gate_blocks_unconverged` — fail
- commits: `main~4..main`
- PRs: `#71`

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| the `export` verb ships and round-trips | high | test `tests/test_export.py::test_widget_round_trips` · PR `#71` |
| the widget-md renderer exists | high | file `devague/render/widget_md.py` |
| the convergence gate blocks unconverged exports | unverified | t3 reverted — not claimed done |

## Remaining Work / Follow-up

- `t3` — fix the gate wiring so `test_gate_blocks_unconverged` passes, then
  re-run the TDD merge gate. Blocking for the feature's completeness.
```

Note what the failure did: it flowed into Actual Delivery (`blocked`), Drift
(`needs-follow-up`), Delivery Claims (`unverified`), and Remaining Work — and
no claim overstated it. That is the whole point.

## After the summary — the final PR gate

The delivery summary is the review map for `/assign-to-workforce`'s third human
gate (the final PR). Commit `docs/deliveries/<created-date>-<slug>.md` alongside
the run's work so the reviewer can audit planned-versus-actual without replaying
the transcript. This skill does not open the PR or mutate any state — it
produces the artifact the human reviews.

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, the
*fifth* in the outbound family after `/scope`, `/think`, `/spec-to-plan`, and
`/assign-to-workforce`, covering the delivery-side closure leg after a plan is
executed. guildmaster pulls it from here and broadcasts it to the AgentCulture
mesh; because devague is upstream, it is **never re-vendored back** from
guildmaster's re-broadcast copy. The `cite, don't import` policy still holds:
downstream repos copy it, they don't symlink or depend on it. See
`docs/skill-sources.md`.
