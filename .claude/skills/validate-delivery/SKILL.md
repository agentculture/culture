---
name: validate-delivery
description: >
  Run the confirmed plan's behavioral tests agent-side after
  assign-to-workforce merges its waves and before summarize-delivery closes
  the loop, then file what was found — evidence for what passed, behavioral
  deltas for what the run added, amended, or removed — as first-class,
  record-only entries via the devague CLI. Never runs the tests inside the
  CLI (issue #20); never suppresses a failing or partial outcome. Use when
  the user says "validate delivery", "run behavioral tests", "check what
  actually behaves", "file evidence", "record a behavioral delta", or after
  assign-to-workforce merges (or fails to merge) a plan's waves and before
  summarize-delivery runs. Authored and maintained in agentculture/devague
  (origin = devague); guildmaster pulls this skill from here and broadcasts
  it to the AgentCulture mesh — it is NOT vendored from guildmaster like the
  inbound skills here.
type: command
---

# validate-delivery — run behavioral tests, file evidence and deltas

The skill is named **`validate-delivery`**; it is the **execution-to-evidence
leg** of the devague method — the *seventh* leg in flow order (the *eighth*
origin skill, chronologically), sitting between the two closing execution
skills:

```text
scope -> think -> challenge -> spec-to-plan -> assign-to-workforce ->
deviate -> validate-delivery -> summarize-delivery
```

Where `/assign-to-workforce` fans out a converged plan's waves and
`/summarize-delivery` closes the loop afterward, `/validate-delivery` runs
**after waves merge and before the delivery summary is written**. It is the
gap that used to be filled by memory: the confirmed plan's claims are
obligations, and until this skill existed nothing forced the run to check
whether the merged code actually behaves as claimed before the summary
asserted it did.

## When to invoke

Run this skill once a wave (or the whole plan) has merged and there is
behavior to check against a claim or an approved deviation — always before
`/summarize-delivery`, never as a substitute for it. It is not gated on a
complete run: a partial or failed fan-out is still worth validating for
whatever did merge.

## The method

1. **Identify the obligations.** Read the plan's confirmed claims (via the
   frame) and any approved `/deviate` records to see what the run promised —
   an announcement, an after-state, a success signal, an acceptance
   criterion. Each one that has a behavioral test backing it is an
   obligation this leg checks.
2. **Locate the behavioral tests.** Consuming repos identify behavioral
   tests one of two ways (either is valid; pick whichever the repo already
   uses, and say which one in the filed evidence):
   - a **pytest marker**, e.g. `@pytest.mark.behavioral` — run with
     `pytest -m behavioral`;
   - a **dedicated folder**, e.g. `tests/behavioral/` or
     `behavioral-tests/` — run that path directly.
3. **Run the tests agent-side.** The agent (not the devague CLI) executes
   the behavioral test suite, or the specific tests relevant to the
   obligations in scope. This is read-only against the codebase — it does
   not modify code to make a test pass.
4. **File evidence for every obligation checked.** For each obligation, file
   an evidence record naming the obligation met, the test that asserted the
   behavior, and the outcome — `pass` or `fail`. A failing outcome is filed
   exactly like a passing one; it is never omitted or reworded into
   something softer.
5. **File behavioral deltas for what changed.** When the run's actual
   behavior added, amended, or removed a behavior relative to the plan, file
   a delta record — `added` / `amended` / `removed` — with provenance back
   to the claim or approved deviation that motivated it, and forward to the
   evidence record(s) that back it.
6. **Report faithfully.** Summarize what was validated, what passed, what
   failed, and what could not be checked at all (no behavioral test exists
   for that obligation yet). An unmet obligation is unmet — it is reported
   as such, not folded into a passing tally or left out of the report.
7. **Hand off to `/summarize-delivery`.** The filed evidence and deltas feed
   directly into `devague summary`'s Delivery Claims table: evidence
   strength (coverage / fidelity / execution / sensitivity) is the
   confidence vocabulary there, and any approved lapse on a claim caps its
   confidence the same way it always has.

## The CLI surface this skill drives

**Record-only.** The devague CLI never runs a test itself (issue
[#20](https://github.com/agentculture/devague/issues/20)) — it only records
what the agent already ran and found. The exact verb shapes below are
minimal placeholders while the underlying schema lands in a parallel task;
treat the verb names as stable and the flags as illustrative, and reconcile
against `devague explain <move>` once that task merges.

| Move | What it records |
|------|------------------|
| `devague oblige <cN> --seam "<seam>" --behavior "<behavior>"` | Files a behavioral obligation against a claim, naming the seam to test and the behavior to assert (snapshots the claim text at filing). |
| `devague evidence --obligation <oN> --test "<ref>" --behavior "<asserted>" --contract "<claim text>" --type <type> --strength <level> --basis "<basis>" --outcome pass\|fail [--run-commit <sha> --run-timestamp <ts>]` | Files an evidence record: obligation met by this test, asserting this behavior, outcome pass or fail (a run reference is required at execution strength and above). `llm`-origin filings land `proposed`; the human adjudicates. |
| `devague delta --kind added\|amended\|removed --behavior "<what changed>" --caused-by <cN\|dN> [--evidence <eN> ...]` | Files a behavioral delta: provenance back to the claim/deviation it diverges from (`--caused-by`), forward to the evidence that backs it. |
| `devague summary [--pr] [--json]` | Reads the filed evidence and deltas back into the Delivery Claims table (`/summarize-delivery`'s starting point). |

`--origin llm` on `oblige` / `evidence` / `delta` lands the record `proposed`
— exactly the same anti-fabrication contract as `deviate` and `lapse`: an
agent's own filing never self-confirms, and only the human's `--confirm` /
`--reject` moves a proposed record forward. A `user`-origin filing
auto-approves, mirroring `deviate` and `lapse`.

## Hard rules (do not violate)

- **The CLI never runs tests.** `devague oblige` / `evidence` / `delta` are
  record-only moves — they take the agent's already-obtained result and
  file it. Running the suite is the agent's job, agent-side, exactly like
  `/summarize-delivery`'s read-only verification step (issue #20).
- **Unmet is unmet.** A failing or unchecked obligation is filed and
  reported as failing or unchecked — never smoothed into "mostly passing" or
  silently dropped from the report. This is the direct fix for the
  motivating failure below: findings discovered only by reading data after
  the fact, never by a test failing loudly in the record.
- **A partial or failed run is still a valid input.** There is no
  completion precondition — validate whatever merged, report the rest as
  not yet checkable.
- **`llm`-origin filings stay proposed until the user confirms.** Same
  anti-fabrication contract as every other origin vocabulary in this
  method — an agent's own proposal never self-confirms.
- **Provenance both ways.** Every evidence record ties back to an obligation
  (a claim or an approved deviation); every delta ties back to what it
  diverges from and forward to the evidence that backs it. An untraceable
  evidence or delta record is not filed.
- **This is not a new gate.** Like `/deviate`, `/validate-delivery` does not
  add a fourth standing human gate — it produces the record `/summarize-
  delivery` and the final PR review consume; the three gates (spec,
  implementation split plan, final PR) are unchanged.
- File the record the moment the thing happens, never at closeout — written
  late is written flattering (issue 97).

## Worked example

Wave 2 of a plan merged the `export --format widget-md` verb. The plan's
confirmed `success_signal` claim `c9` said "round-tripping a widget through
`export` and back loses no fields." A behavioral test exists for it,
marked `@pytest.mark.behavioral`, plus two more behavioral tests for
adjacent claims — one of which fails.

```bash
# 1. Identify the obligation (echoes its id, e.g. o1)
devague oblige c9 --seam "widget export round-trip" \
  --behavior "round-tripping a widget loses no fields"

# 2. Locate and run the behavioral tests agent-side (read-only)
pytest -m behavioral -q
# -> tests/behavioral/test_widget_export.py::test_round_trip PASSED
# -> tests/behavioral/test_widget_export.py::test_empty_field_rendering FAILED

# 3. File evidence for each outcome — the failure included, not smoothed over
devague evidence --obligation o1 \
  --test tests/behavioral/test_widget_export.py::test_round_trip \
  --behavior "asserts an exported-then-reimported widget compares equal field by field" \
  --contract "round-tripping a widget loses no fields" \
  --type automated --strength execution \
  --basis "behavioral test ran green at the named commit" \
  --outcome pass --run-commit abc1234 --run-timestamp 2026-08-31T12:00:00
devague evidence --obligation o2 \
  --test tests/behavioral/test_widget_export.py::test_empty_field_rendering \
  --behavior "asserts an absent widget field renders as an empty line" \
  --contract "an absent field renders honestly, never as filler" \
  --type automated --strength execution \
  --basis "behavioral test ran red at the named commit" \
  --outcome fail --run-commit abc1234 --run-timestamp 2026-08-31T12:00:00

# 4. File a delta if the failure reveals a real behavioral divergence
devague delta --kind amended \
  --behavior "empty widget fields render as garbled text, not an empty line" \
  --caused-by c11 --evidence e2

# 5. Report faithfully: c9 is validated; c11's claimed behavior is unmet —
#    say so plainly, hand it to /summarize-delivery as Remaining Work, not
#    as a passing claim.
```

`/summarize-delivery` then reads these back — `c9`'s Delivery Claims row
cites evidence `e1` at `high` confidence (a passing behavioral test); `c11`'s
row is `unverified` or explicitly failing, never rounded up.

## After validating — hand off to /summarize-delivery

Once every obligation in scope has an evidence record (or is reported as not
yet checkable) and any behavioral deltas are filed, this leg is done — there
is nothing separate to export, the filed records already live in devague
state. Continue with the sibling **`/summarize-delivery`** skill: its
Delivery Claims table reads the evidence and deltas filed here directly
(`devague summary`), so the confidence a claim carries in the final delivery
artifact traces back to a test that actually ran, not to memory. Don't stop
at "tests ran" — the standing flow is **file the evidence, then
`/summarize-delivery`**.

## The motivating record

The Reasoning Degradation Ledger (`devague lapse`, issue
[`agentculture/devague#97`](https://github.com/agentculture/devague/issues/97))
exists because of this, cited verbatim: "Four graders failed in that
cycle... Every one was found by reading data afterwards; none by a test
failing." That gap — a corrections record reconstructed only at the end,
from memory, because nothing forced a behavioral check to run and be filed
along the way — is exactly what `/validate-delivery` closes for the
*execution* side, the same way `/challenge` closes it for the *spec* side.

The design itself traces to issue
[`agentculture/devague#107`](https://github.com/agentculture/devague/issues/107),
"Suggestion: behavioral validation and a derived current spec," which
proposed behavior as the primary contract, four evidence types, a strength
ladder, and the current spec as a projection of a behavior ledger rather
than a hand-maintained document. This skill is the method-only front door to
that idea: it does not implement the full ledger or the derived-spec
projection — it establishes where in the flow behavioral checking happens,
what gets filed, and how the failure mode #97 documented gets closed instead
of rediscovered.

## Before and after this leg

```text
Previous leg: deviate
Next leg: summarize-delivery
```

After every successful, non-exempt move, the CLI prints one `next: <recommended
move>` line to stderr — follow it, or run `devague status` when unsure what
comes next. The evidence and deltas filed here also feed `devague today`'s
read-only projection of current behavior into the committed
`docs/current-spec.md`.

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, the
*eighth* in the outbound family after `/scope`, `/think`, `/challenge`,
`/spec-to-plan`, `/assign-to-workforce`, `/deviate`, and
`/summarize-delivery`, covering the execution-to-evidence leg that runs
after a plan's waves merge and before the delivery summary is written.
guildmaster pulls it from here and broadcasts it to the AgentCulture mesh;
because devague is upstream, it is **never re-vendored back** from
guildmaster's re-broadcast copy. The `cite, don't import` policy still
holds: downstream repos copy it, they don't symlink or depend on it. See
`docs/skill-sources.md`.
