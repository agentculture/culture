---
name: think
description: >
  Think a vague feature idea into a buildable spec by working backwards (the
  idea→spec leg; drives the `devague` CLI). Start from the announcement
  ("pretend it shipped"), capture and classify claims, interrogate them with
  honesty conditions and hard questions, park open vagueness as a first-class
  object, and export a spec only once the frame *converges*. Use when the user
  says "think this through", "spec this", "work backwards", "turn this idea into
  a spec", "announcement frame", or "devague", or when a feature request is too
  vague to build yet. Once a spec exports, hand off to the sibling /spec-to-plan
  skill to turn it into a plan. Authored and maintained in agentculture/devague
  (origin = devague); guildmaster pulls this skill from here and broadcasts it to
  the AgentCulture mesh — it is NOT vendored from guildmaster like the inbound skills
  here.
type: command
---

# think — work an idea backwards into a buildable spec

The skill is named **`think`**; the product/CLI it drives is **`devague`**. (The
forward leg — turning a converged spec into a plan — is the sibling
**`/spec-to-plan`** skill, which drives `devague plan`.)

`think` turns a vague feature idea into a buildable spec by **working
backwards**: you start from the announcement you'd make if it had already
shipped, then build an **Announcement Frame** by capturing claims, pressure
-testing them, parking what's still genuinely unknown, and only exporting once
the frame converges.

The CLI is **deterministic and move-driven** — it is *not* a wizard. There is no
fixed sequence of prompts. **You (the agent) choose the next move; the CLI just
tracks state and tells you what's still missing.** Run `devague learn` for the
canonical ten-stage arc and `devague explain <move>` for any single move.

This skill is the operator: a portable wrapper that resolves the CLI and
forwards every move verbatim — including `status`, the read-only verb that reads
the convergence gate and tells you the recommended next move.

**Explore scope first when the idea touches an existing codebase.** The sibling
**`/scope`** skill is the optional opening leg: survey the surfaces the idea
touches *before* framing, then seed the frame with `boundary` / `non_goal` /
`assumption` claims that cite what was actually explored (provenance, not
generic disclaimers). Small ideas skip it and start here — no wizard. Findings
now land on the frame itself through the shipped `devague scope` move: start
the frame with `new` first (scope entries live on it, like any other claim),
then record each explored surface with
`` `devague scope "<surface>" --finding "<text>" [--seeds <id> ...]` ``
— `--seeds` links the finding to what it went on to seed: a claim id (`c*`)
**or** a claim-attached hard-question id (`q*`, for a finding that routed to
`interrogate --hard-question` because it needed a user decision rather than a
claim). An unknown seed id is refused with a hint. Read every recorded entry
back with `` `scope --list [--json]` ``, and correct a wrong finding in place
with `` `scope --amend <sN> --finding "<corrected>"` `` instead of recording a
second entry that says "supersedes". (From the sharper end-to-end method spec,
devague#53, task t3; `q*` seeds and `--amend` from #84.)

## How to run

The entry point is `scripts/think.sh`. Invoke it from the repository you are
speccing (frames persist under `.devague/` in the current directory):

```bash
bash .claude/skills/think/scripts/think.sh <move> [args...]
bash .claude/skills/think/scripts/think.sh status
```

It resolves the CLI portably — an installed `devague` on `PATH` (the normal
case), falling back to `uv run devague` when you are inside the devague checkout.
If neither resolves it prints an install hint (`uv tool install devague`). Every
move — including `status` — is forwarded verbatim, so you can equally call the
CLI directly (`devague <move> …`) when it is installed; the wrapper exists only
for portable resolution.

### Moves

| Move | What it does |
|------|--------------|
| `new "<announcement>" [--title "<short>"]` | Start a frame from the announcement (the first move). Seeds an auto-confirmed `announcement` claim. Always pass `--title` (see *Export hygiene*). |
| `capture --kind <kind> "<text>" [--instruction "<text>"]` | Record + classify a claim. `--origin llm` lands it as `proposed`. `--instruction` attaches verbatim working guidance (how to verify/implement the claim) at creation time. |
| `amend <cN> [--text "<text>"] [--kind <kind>] [--reason "<why>"]` | Correct a claim **without** churning its id — its honesty conditions, hard questions, `instruction`, and inbound `scope --seeds` refs all survive. The superseded `(text, kind)` pair is kept on `Claim.revisions`; a **confirmed** claim flips back to `proposed` (re-confirm it). Prefer this over reject-and-recapture. |
| `interrogate <id> --honesty "…"` | Attach an honesty condition (what must be true). Also `--hard-question`, `--risk`, `--contradicts`, `--blocking`. `--instruction "<text>"` adds/updates a claim's or honesty condition's instruction — `<id>` may be a claim (`c*`) or, with `--instruction` alone, an honesty condition (`h*`). |
| `interrogate <cN> --resolve <qN> [--decision "<text>"]` | Close out that claim's blocking hard question — it stays on record (rendered `(resolved)` in the export) and stops blocking `converge`. **User-only decision**, the claim-level twin of `park --resolve`; never invoke it on your own. Mutually exclusive with the add-flags above. |
| `confirm <id> [<id>…]` / `reject <id> [<id>…]` | Resolve one or more claims (`c*`) / honesty conditions (`h*`) in one **transactional** call. **User-only decision.** Rejecting a claim cascades onto its still-live honesty conditions and unresolved hard questions (echoed as `(also rejected: h3, q1)`). Also `confirm --from-review <file>` to apply an edited review artifact. |
| `review` | List every **proposed** (unconfirmed) claim + honesty condition with ids and their instructions (`--json` too); writes a non-authoritative artifact to `.devague/reviews/<slug>.md`. Un-gated; never mutates. |
| `scope "<surface>" --finding "<text>" [--seeds <c*\|q*> ...]` / `scope --list` / `scope --amend <sN> --finding "<text>"` | Record (or list, or correct in place) a pre-frame exploration finding as first-class state (see the scope pointer above). |
| `question "<text>"` | Record / list / `--resolve` a pending user decision as durable working state in `.devague/questions/<slug>.md`. |
| `park "<text>" --kind <kind>` | Move uncertainty into first-class open vagueness instead of forcing an answer. |
| `park --resolve <vN> --decision "<text>" [--claim <cN>]` | Close out a decided parked item — stays on record with its resolution, drops out of the convergence gate; `--claim` links the deciding claim. **User-only decision**, same as `confirm`. |
| `converge` | Evaluate the gate; list remaining gaps. |
| `export` | Write the buildable spec to `docs/specs/` — only after `converge` passes. |
| `status` | Read-only: where the frame stands + the recommended next move (`--json` too). |
| `show` / `list` | Render a frame / list frames (`--json` for raw state). |
| `learn` / `explain <move>` | Teach the method / explain one move. |

Claim kinds: `announcement`, `audience`, `after_state`, `before_state`,
`why_it_matters`, `boundary`, `success_signal`, `open_question`, `non_goal`,
`requirement`, `assumption`, `decision`. Vagueness kinds: `unknown_nonblocking`,
`unknown_blocking`, `out_of_scope`, `follow_up`.

These are exactly the kinds the **shipped CLI enforces** (`CLAIM_KINDS` /
`VAGUENESS_KINDS` in `devague/frame.py`) — the skill documents the surface as
built, so every command here passes the CLI's `choices=` validation. `requirement`
is spec-affecting (needs a confirmed honesty condition); `non_goal` / `decision`
are descriptive; an unconfirmed `assumption` is a convergence *warning*, not a
blocker. The formal entity model, the `(state × origin)` vocabulary, and the
per-move input/output/transition/error contract are documented in
[`docs/spec-contract.md`](../../../docs/spec-contract.md) (issue
[#5](https://github.com/agentculture/devague/issues/5)); for the authoritative
live shape of any move, run it with `--json` (or `devague learn --json` /
`devague explain <move>`).

### Instructions — verbatim guidance, never fabricated

Claims and honesty conditions may carry an optional `instruction`: verbatim
text on how to verify or implement the item — write it yourself, don't invent
filler to satisfy the gate. Two ways to set it:

- `capture --kind <kind> "<text>" --instruction "<text>"` — at creation.
- `interrogate <c*|h*> --instruction "<text>"` — add or change one on an
  existing claim or honesty condition (this is the one case where `interrogate`
  accepts an `h*` id directly, since the instruction targets whichever item you
  name).

**Re-confirm rule.** Changing the instruction on an already-`confirmed` claim
or honesty condition flips its status back to `proposed` — the instruction is
part of what the user confirmed, so a change to it must go back through the
user. `interrogate` prints a diagnostic note (stderr) when this happens; re-run
`confirm <id>`.

**Gate warnings, not blockers.** `converge` emits two deterministic,
warning-only structural-sharpness signals — neither holds back `ready_for_spec`:

- a confirmed spec-affecting claim (`announcement`, `audience`, `after_state`,
  `before_state`, `why_it_matters`, `boundary`, `success_signal`, `requirement`)
  with no `instruction`;
- a confirmed `success_signal` claim (or claims) where none contains a
  measurable token — a numeral, `%`, or a comparator (`<`/`>`/`≤`/`≥`).

Both are pure predicates over frame state, never LLM judgment on prose — see
`devague/convergence.py`'s module docstring for the exact rules (S1/S2). A
frame can converge and export with these warnings still showing; they nudge
toward a sharper, more directly actionable spec.

### `status` — the next-move verb

`status` is a first-class, **read-only** CLI verb (`devague status`, internalised
from this wrapper in 0.11.0 — issue
[#30](https://github.com/agentculture/devague/issues/30)). It composes
`list` + `converge` and prints where the current frame stands, the remaining
gaps, and the recommended next move; it never mutates state (the
`drafting`↔`converged` transition stays in `converge`). It reports
`ready_for_spec`, lists the `blockers` and `warnings`, and shows
`required_next_moves[0]` as the recommended move. Pass `--json` for the same
fields as a structured payload (`{frame, total, ready_for_spec, blockers,
warnings, parked_items, required_next_moves}`).

```text
frame: my-feature    (1 frame total)
convergence: NOT passed — 2 gap(s):
  - missing a 'boundary' / non-goal claim
  - claim c2 has no confirmed honesty condition

recommended next move (first gap):
  devague capture --kind boundary "<text>"
```

Run it whenever you're unsure what to do next.

After every successful, non-exempt move (`status` itself is exempt, since
reporting the next move is already its whole purpose) the CLI also prints a
`next: <recommended move>` line to **stderr**. Follow that hint or run
`devague status` — either gets you the same recommended next move.

### Export hygiene — keep the artifacts lint-clean

The exported spec-md must pass the repo's markdown lint. Two gotchas found by
dogfooding (devague#53), both cheap to avoid up front and expensive after —
there is no retitle/edit move yet, so fixing them later means hand-editing
state JSON and re-exporting:

- **Always pass `--title "<short title>"` to `new`.** The title becomes the H1
  of every exported artifact (the plan inherits it too). The default is the
  full announcement — long, and if it ends with a period the H1 fails MD026
  (trailing punctuation). Keep the title short, no trailing period.
- **Backtick angle-bracket tokens.** A placeholder like `--seeds <claim-id>`
  in claim or honesty-condition text renders as inline HTML (MD033) unless
  wrapped in backticks. Write `` `--seeds <claim-id>` ``, not the bare form.
- **Lint before committing:** `markdownlint-cli2 "docs/specs/<file>.md"`.

### Pending decisions — the `question` loop

When a genuine design decision surfaces mid-frame, don't guess and don't stall:

1. `question "<the decision to make>"` — records it as durable working state.
2. Put it to the user (with concrete options where possible).
3. `question --resolve <qid> --decision "<what the user chose>"`.
4. `capture --kind decision "<the choice>"` — a user-origin capture
   auto-confirms, making the decision a first-class frame claim.

This keeps every user decision traceable from question → resolution → claim.

## Hard rules (do not violate)

These are the point of the method — convergence must mean something.

- **LLM proposals stay proposed.** A claim captured with `--origin llm`, and any
  honesty condition you (the agent) propose, lands as `proposed`. **Never
  `confirm` your own proposal.** Confirmation is a user-only decision — surface
  the proposal and let the user confirm or reject it. Proposed content must not
  silently become an authoritative requirement.
- **Honesty conditions route through the user.** Propose them freely with
  `interrogate --honesty`; the user owns whether they hold.
- **Converge, don't vibe.** `export` is gated on `converge` passing. Never claim
  the frame is ready on a hunch — run `converge` (or `status`) and resolve every
  listed gap. The gate requires confirmed `announcement` / `audience` /
  `after_state`, a `before_state` or `why_it_matters`, a `boundary`, a
  `success_signal`, a confirmed honesty condition on every spec-affecting claim,
  and no unresolved blocking vagueness or hard question.
- **Park real unknowns; don't paper over them.** If something is genuinely
  unknown, `park` it (blocking or non-blocking) rather than fabricating an
  answer. Blocking vagueness holds back convergence — by design. Once it's
  decided, close it out with `park --resolve <vN> --decision "<text>"` —
  the item stays on record with its resolution and stops blocking. Never
  leave a decided blocking park sitting forever, and never hand-edit
  `.devague` state to work around it — the resolve move is the CLI's own
  escape hatch.
- File the record the moment the thing happens, never at closeout — written
  late is written flattering (issue 97).

## Output contract

Results go to **stdout**, diagnostics and errors to **stderr** — a strict split
you can rely on when parsing. Pass `--json` to any move for a structured payload
on the same stream. Exit code `0` on success, non-zero on user error (with a
`hint:` line). Frames live under `.devague/` in the current directory.

## Worked example

A short end-to-end session (the kind you'd run to spec a feature like
[devague#5](https://github.com/agentculture/devague/issues/5)):

```bash
d() { bash .claude/skills/think/scripts/think.sh "$@"; }

d new "Devague ships a documented spec contract" --title "documented spec contract"

# Optional scope stage: record what you explored before capturing claims
d scope "devague/frame.py" --finding "the claim model devague#5 extends lives here"

d capture --kind audience "devague + the assisting LLM" \
    --instruction "check docs/spec-contract.md names devague + the LLM as readers"
d capture --kind after_state "a vague idea becomes a buildable, pressure-tested spec"
d capture --kind why_it_matters "specs converge on evidence, not vibes"
d capture --kind boundary "not a full PRD generator; no fixed wizard"
d capture --kind success_signal "a frame exports only after the gate passes"

# Pressure-test a claim, then let the USER confirm the condition:
d interrogate c1 --honesty "the contract round-trips: save -> load -> identical frame"
# ...user reviews and runs: d confirm h1

# Attach a working instruction to the now-confirmed honesty condition — this
# flips h1 back to 'proposed' (the re-confirm rule), so the user re-confirms:
d interrogate h1 --instruction "run tests/test_contract.py::test_contract_round_trips"
# ...user reviews and runs: d confirm h1

# Park a genuine unknown instead of guessing:
d park "exact JSON schema versioning policy" --kind unknown_nonblocking

d status        # what's left + the next move (warnings show with a ⚠ marker)
d converge      # gate; resolve any listed gaps (warnings never block export)
d export        # writes docs/specs/<slug>.md once converged
```

The exported spec-md is a buildable artifact.

## After export — commit, then hand off

Once `export` writes the spec **and the user has reviewed it**, close the
idea→spec leg cleanly before moving on:

1. **Commit the spec.** Commit the exported `docs/specs/<slug>.md` (along with
   the `.devague/frames/<slug>.json` frame state and any review artifact under
   `.devague/reviews/`) so the converged frame is durable in history, not just on
   disk. Use a focused message, e.g. `git commit -m "spec: <slug> (devague
   /think)"`. The frame and the spec are the evidence trail for every confirmed
   claim — keep them together. (Per the repo's standing convention this normally
   becomes a branch + PR via the `cicd` skill; commit-only is fine when the user
   asks for it.)
2. **Hand off to `/challenge`, then `/spec-to-plan`.** The next leg is the
   blind-spot pass: `/challenge` pressure-tests the exported frame before it
   becomes a plan. Once that pass clears (or lands nothing new), the forward
   leg is the sibling skill: `devague plan new --frame <slug>` seeds a plan
   from the converged frame and works it forward into a buildable plan (it can
   equally feed `superpowers:writing-plans` or a normal implementation PR).

Don't pause for a "what next?" menu after a reviewed export — the standing flow
is **commit, then `/challenge`, then `/spec-to-plan`**.

## Before and after this leg

```text
Previous leg: scope
Next leg: challenge
```

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, where the
devague agent maintains it alongside the tool it operates (dogfooding). It is the
*inverse* of the other skills under `.claude/skills/`, which devague vendors
**from** guildmaster. guildmaster pulls this skill **from** devague and
broadcasts it to the rest of the AgentCulture mesh. The `cite, don't import`
policy still holds: downstream repos copy it, they don't symlink or depend on it.
See `docs/skill-sources.md`.
