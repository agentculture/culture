---
name: challenge
description: >
  Run a risk-scaled blind-spot discovery pass over a converged, exported
  frame BETWEEN /think and /spec-to-plan (the seventh origin skill, third
  leg in flow order): pressure-test the spec through structured lenses,
  route every finding back through the existing deterministic moves as
  proposed-only content the human adjudicates, and on a clean pass record
  the examined lenses/surfaces and residual uncertainty — never a claim
  that there are no unknown unknowns. Use when the user says "challenge
  this spec", "blind-spot pass", "pressure-test the frame", "what are we
  missing", "unknown unknowns", or after /think exports and before
  `devague plan new`. Authored and maintained in agentculture/devague
  (origin = devague); guildmaster pulls this skill from here and broadcasts
  it to the AgentCulture mesh — it is NOT vendored from guildmaster like
  the inbound skills here.
type: command
---

# challenge — hunt the blind spots before the plan inherits them

The skill is named **`challenge`**; it is the **blind-spot discovery leg** of
the devague method — the *seventh* origin skill, sitting *third* in flow
order, between the spec leg and the plan leg:

```text
scope -> think -> challenge -> spec-to-plan -> assign-to-workforce -> deviate -> summarize-delivery
```

Before this leg existed, a frame could converge on precisely stated claims
while the original framing was still incomplete: open questions only captured
uncertainty someone had already noticed — **nothing actively hunted omitted
dimensions, hidden dependencies, or assumptions shared by everyone in the
frame, and no record existed of which surfaces were ever examined**
(issue 73's problem statement). Strictly speaking, an unknown unknown cannot
be listed directly — once articulated, it becomes a known unknown. The useful
capability is therefore to **raise the odds of discovering blind spots and
lower the cost of the surprises that remain**, not to promise their
elimination.

That is the surprise-cost rationale: an articulated blind spot becomes a
known unknown the method can manage; an unexamined one surfaces later as a
mid-run `/deviate` or a production surprise. Discovery *before* planning is
cheaper than either — a proposed claim the human rejects costs minutes; the
same gap found mid-fan-out stops a wave, and found in production it costs
whatever the blast radius costs.

This doc is written for two readers. The **operator** — the main agent — runs
the pass: sweeps the lenses, drives the deterministic CLI move by move, and
proposes findings. The **gate-owning human** adjudicates: every finding lands
`proposed`, and confirming, rejecting, or resolving it is the human exercising
the existing **spec gate** (gate 1) — challenge adds no fourth gate, mirroring
how `/deviate` amends gate 2 rather than adding one.

## When it runs

The timing is a recorded decision — quote it, don't re-derive it:

> the challenge pass runs after /think exports: challenge the converged,
> exported frame before `devague plan new`; findings reopen the frame,
> reconverge, and re-export the same dated spec file — /think stays
> self-contained (resolves q1)

— decision c17 in `docs/specs/2026-07-15-challenge-skill.md`.

Concretely: `/think` finishes its own arc (converge, export) untouched. Then
`/challenge` pressure-tests the exported spec **before** `devague plan new`
seeds a plan from it. Findings land as proposed claims, honesty conditions,
questions, or parks — which reopens the frame — the human adjudicates, the
frame **reconverges**, and `devague export` **re-exports the same dated
file** (`docs/specs/<created-date>-<slug>.md`; exports are prefixed with the
frame's creation date, so a re-export overwrites in place rather than
spawning a duplicate). That reconverge-and-re-export loop repeats until the
pass's findings are all adjudicated and the spec artifact carries them.

## Proportionality — scale the pass to the risk

The pass is **mandatory but proportional**: lightweight for ordinary work,
rigorous for high-risk work (integration option 1 from issue 73, per the
issue author — a distinct operator skill, no new CLI engine until the
workflow proves it needs one). Which work is high-risk is likewise a recorded
decision:

> the named escalation signals that deepen the pass from lightweight to
> rigorous: migrations, security-sensitive work, distributed state, hardware,
> destructive operations, other hard-to-reverse changes, concurrency hazards,
> and any surface that can lose user data (resolves q3)

— decision c19 in `docs/specs/2026-07-15-challenge-skill.md`.

If **any** escalation signal applies, run the rigorous form: every lens,
deliberate counter-evidence hunting, and cheap probes where they would settle
a real question. If none applies, a lightweight sweep — one pass over the
lenses against the exported spec, minutes not hours — satisfies the method.
Lightweight never means skipped: even the lightest pass leaves durable
records (see the hard rules).

## The lenses

Sweep the exported spec, the live frame, and the surfaces the idea touches
through these structured lenses (from issue 73):

- **adjacent systems and hidden dependencies** — what else reads, writes, or
  assumes the thing being changed;
- **unstated assumptions and missing counter-evidence** — what everyone in
  the frame believes without a claim saying so, and what was never checked
  because nobody argued the other side;
- **overlooked actors, lifecycle stages, data flows, and failure modes** —
  who and what the frame forgot: other users, upgrade/downgrade paths,
  half-completed operations;
- **security, migration, concurrency, operations, and reversibility** — the
  classic hard surfaces, each also an escalation signal when present;
- **missing observability, containment, rollback, and recovery paths** —
  when the surprise happens anyway, how it is seen, bounded, and undone;
- **cheap probes or experiments** — small, scratch-space checks that could
  expose a surprise now instead of mid-run (probes never mutate the repo or
  `.devague/` state).

## The method

1. **Confirm the entry condition.** A converged frame that `/think` has
   already exported, and no plan seeded from it yet (`devague status` shows
   where the frame stands; the exported spec-md is the artifact under
   challenge).
2. **Set the depth.** Check the idea against the c19 escalation signals
   above. Any hit → rigorous; none → lightweight. Say which you chose and
   why — the depth decision is part of the pass's record.
3. **Sweep the lenses read-only.** Read the exported spec claim by claim,
   the frame's parked vagueness and resolved questions, and the actual
   surfaces the idea touches. Nothing mutates during the sweep; the only
   mutations are the deterministic moves that record findings.
4. **Route every finding through an existing move.** Use the routing table
   below. Everything the agent proposes carries `--origin llm` and lands
   `proposed` — the pass cannot silently convert speculation into confirmed
   requirements.
5. **Let the human adjudicate.** `devague review` lists every proposal with
   ids; `devague confirm` / `devague reject` / `devague question --resolve`
   are user-only decisions. This is the existing spec gate doing its job.
6. **Reconverge and re-export.** `devague converge`, then `devague export` —
   the same dated spec file now carries the adjudicated findings and the
   pass's provenance (the exported spec renders scope entries in its
   Scope exploration section).
7. **On a clean pass, record the pass itself.** A sweep that finds nothing
   still records which lenses and surfaces were examined (`devague scope`
   entries, one per lens/surface, e.g. `challenge pass / concurrency lens:
   devague/store.py`) and what residual uncertainty remains (`park`). A bare
   "no issues found" is not an outcome this skill produces.

## Where findings land

Challenge keeps **no parallel prose-only artifact** — the frame is the
record, and every output category from issue 73 has an existing deterministic
move to land in:

| Output category (issue 73) | What it is | Landing move |
|----------------------------|------------|--------------|
| known facts | something the pass established, with provenance | `capture --kind requirement` / `--kind decision` / `--kind boundary` (`--origin llm` → lands `proposed`) |
| assumptions | beliefs the frame leaned on unstated | `capture --kind assumption --origin llm`, then pressure-test with `interrogate --honesty` / `--hard-question` / `--contradicts` |
| known unknowns / open questions | articulated uncertainty | `question "<text>"` when it needs a user decision; `park --kind unknown_nonblocking\|unknown_blocking` when not decidable now |
| unexamined surfaces | what this pass did not (or could not) look at | `devague scope "<surface>" --finding "<what was and wasn't examined, and why>"` |
| residual surprise risk | uncertainty that survives the pass | `park` on the frame while speccing; `devague plan risk --kind <kind>` once the plan exists |
| resilience measures | containment, rollback, recovery the surprise cost demands | spec-side `capture --kind requirement` / `--kind boundary`; plan-side `devague plan risk` (see below) |

Every finding names the **lens and surface** it came from (the
`challenge pass / <lens>: <surface>` convention in scope entries; provenance
citations inside claim text). That provenance bar is how the method hunts
blind spots **without encouraging speculative issue generation** — a finding
you cannot trace to something you actually read is speculation, not a
finding.

## Resilience placement — spec-side or plan-side

Where a resilience measure lands is a recorded decision:

> resilience measures land in both spec and plan by nature: spec-side as
> requirement/boundary claims when they change what to build, plan-side as
> plan risks or tasks when they change how to build it — the skill coaches
> which is which (resolves q2)

— decision c18 in `docs/specs/2026-07-15-challenge-skill.md`.

The coaching: ask *"does this change **what** ships, or **how** it gets
built?"* A rollback path the user needs, a fail-closed version check, a
containment boundary — those change the product: `capture` them spec-side as
`requirement` / `boundary` claims so the re-exported spec carries them.
A staging sequence, a merge-order constraint, an uncertainty the workforce
must build around — those change the build: land them plan-side via
`devague plan risk --kind <kind>` (blocking or nonblocking, honestly chosen)
once `/spec-to-plan` seeds the plan, where the plan's convergence gate keeps
blocking risks visible until resolved.

## Hard rules (do not violate)

- **Never conclude there are no unknown unknowns.** Not after a rigorous
  pass, not after a clean one. The only honest clean-pass output is a record
  of which lenses and surfaces were examined — `devague scope` entries — plus
  the residual uncertainty that remains — `park` — so the pass leaves durable
  provenance instead of a comforting absolute (issue 73 success criteria;
  the anti-fabrication contract in `docs/llm-guidance.md`).
- **LLM-origin findings stay proposed until the user confirms.** Every
  finding the agent proposes carries `--origin llm` and lands `proposed`;
  only the user's `confirm` makes it real. The pass must not be able to
  silently convert speculation into confirmed requirements.
- **Findings route through existing deterministic moves only.** `capture`,
  `interrogate`, `question`, `park`, `devague scope`, `devague plan risk` —
  nothing else. No parallel prose artifact, no new CLI verb, engine, or
  state model (issue 20; issue 73's stated preference). If it didn't land in
  a move, it didn't land.
- **Provenance on every finding.** Name the lens and the surface it came
  from. If you didn't read it, don't claim it — same bar as `/scope`.
- **Proportional, never skipped.** Lightweight is the floor, not an
  exemption; any c19 escalation signal makes the rigorous form mandatory.
  Skipping the pass entirely is not a depth setting.
- **Not a fourth standing gate.** The three human gates stay: exported spec,
  implementation split plan, final PR. Challenge output is adjudicated
  inside the existing spec gate — mirroring how `/deviate` amends gate 2
  rather than adding one.
- **The sweep is read-only until it routes.** Reading spec, frame, and
  surfaces never edits files or state; probes run in scratch space. The only
  mutations are the recording moves themselves.

## Worked example

Challenging the exported spec for a store-schema migration (illustrative
slug `store-schema-v3`) — "migrations" and "any surface that can lose user
data" are both c19 escalation signals, so the pass runs rigorous:

```bash
# Entry condition: /think exported docs/specs/2026-07-15-store-schema-v3.md
# and no plan exists yet. Depth: rigorous (migration + data-loss signals).

# adjacent-systems lens: an older installed devague reads the same store
devague capture --origin llm --kind assumption "older installed devague binaries refuse a v3 store via the fail-closed schema_version check in devague/store.py"
devague interrogate c9 --origin llm --honesty "a v2-reading binary pointed at a v3 store exits with the version hint, not a traceback"
devague scope "challenge pass / adjacent-systems lens: devague/store.py schema_version gate" --finding "older binaries fail closed on v3; seeded the compat assumption" --seeds c9

# failure-mode lens: the migration can die halfway
devague capture --origin llm --kind requirement "migration writes to a temp file and renames — a killed run never leaves a half-written store"

# overlooked-actors lens: needs a user decision, not a guess
devague question "do mesh agents share one store, or does each checkout own its own?"

# reversibility lens: genuinely unknown, not decidable now
devague park "whether a v3->v2 downgrade path is ever needed" --kind unknown_nonblocking

# concurrency lens found nothing — record the clean pass, not a conclusion
devague scope "challenge pass / concurrency lens: devague/store.py + delivery_store.py" --finding "single-writer CLI, no locking today; clean pass — residual risk only if two agents ever share a checkout"

# --- HUMAN adjudicates: the existing spec gate at work ---
devague review
devague confirm c9 h4 c10
devague question --resolve q1 --decision "each checkout owns its own store"

# Reconverge and re-export — the SAME dated file, now carrying the pass
devague converge
devague export

# Residual risk that changes HOW to build lands plan-side once
# /spec-to-plan seeds the plan:
devague plan new --frame store-schema-v3
devague plan risk "two agents sharing a checkout could interleave store writes mid-migration" --kind unknown_nonblocking
```

Every finding above is traceable to a lens and a surface; the clean lens is
recorded as examined rather than silently dropped; and nothing the agent
proposed became confirmed without the human's `confirm`.

## After the pass — hand off to /spec-to-plan

Once the frame reconverges and the same dated spec file is re-exported, the
pass is done — the examined surfaces, residual uncertainty, and adjudicated
findings all live in frame state and render into the spec artifact. Continue
with `/spec-to-plan` as usual: the plan seeds from the challenged frame, and
any residual surprise risk you routed plan-side lands via
`devague plan risk` as first-class plan state. If a surprise still gets
through mid-fan-out, that is `/deviate`'s job — and every approved `dN`
deviation record is evidence for what the *next* challenge pass's lenses
should look harder at.

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, the
*seventh* in the outbound family after `/scope`, `/think`, `/spec-to-plan`,
`/assign-to-workforce`, `/deviate`, and `/summarize-delivery`, sitting third
in flow order as the blind-spot discovery leg between `/think` and
`/spec-to-plan`. guildmaster pulls it from here and broadcasts it to the
AgentCulture mesh; because devague is upstream, it is **never re-vendored
back** from guildmaster's re-broadcast copy. The `cite, don't import` policy
still holds: downstream repos copy it, they don't symlink or depend on it.
See `docs/skill-sources.md`.
