---
name: think
type: command
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
  (origin = devague); guildmaster re-broadcasts it to the AgentCulture mesh — it
  originates in devague, not guildmaster (the supplier post steward→guildmaster cutover).
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
| `new "<announcement>"` | Start a frame from the announcement (the first move). Seeds an auto-confirmed `announcement` claim. |
| `capture --kind <kind> "<text>"` | Record + classify a claim. `--origin llm` lands it as `proposed`. |
| `interrogate <id> --honesty "…"` | Attach an honesty condition (what must be true). Also `--hard-question`, `--risk`, `--contradicts`, `--blocking`. |
| `confirm <id> [<id>…]` / `reject <id> [<id>…]` | Resolve one or more claims (`c*`) / honesty conditions (`h*`) in one **transactional** call. **User-only decision.** Also `confirm --from-review <file>` to apply an edited review artifact. |
| `review` | List every **proposed** (unconfirmed) claim + honesty condition with ids (`--json` too); writes a non-authoritative artifact to `.devague/reviews/<slug>.md`. Un-gated; never mutates. |
| `question "<text>"` | Record / list / `--resolve` a pending user decision as durable working state in `.devague/questions/<slug>.md`. |
| `park "<text>" --kind <kind>` | Move uncertainty into first-class open vagueness instead of forcing an answer. |
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
  answer. Blocking vagueness holds back convergence — by design.

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

d new "Devague ships a documented spec contract"
d capture --kind audience "devague + the assisting LLM"
d capture --kind after_state "a vague idea becomes a buildable, pressure-tested spec"
d capture --kind why_it_matters "specs converge on evidence, not vibes"
d capture --kind boundary "not a full PRD generator; no fixed wizard"
d capture --kind success_signal "a frame exports only after the gate passes"

# Pressure-test a claim, then let the USER confirm the condition:
d interrogate c1 --honesty "the contract round-trips: save -> load -> identical frame"
# ...user reviews and runs: d confirm h1

# Park a genuine unknown instead of guessing:
d park "exact JSON schema versioning policy" --kind unknown_nonblocking

d status        # what's left + the next move
d converge      # gate; resolve any listed gaps
d export        # writes docs/specs/<slug>.md once converged
```

The exported spec-md is a buildable artifact.

## After export — commit, then hand off

Once `export` writes the spec **and the user has reviewed it**, close the
idea→spec leg cleanly before moving on:

1. **Commit the spec.** Commit the exported `docs/specs/<slug>.md` (along with
   the `.devague/<slug>.json` frame state and any review artifact under
   `docs/reviews/`) so the converged frame is durable in history, not just on
   disk. Use a focused message, e.g. `git commit -m "spec: <slug> (devague
   /think)"`. The frame and the spec are the evidence trail for every confirmed
   claim — keep them together. (Per the repo's standing convention this normally
   becomes a branch + PR via the `cicd` skill; commit-only is fine when the user
   asks for it.)
2. **Hand off to `/spec-to-plan`.** The forward leg is the sibling skill:
   `devague plan new --frame <slug>` seeds a plan from the converged frame and
   works it forward into a buildable plan (it can equally feed
   `superpowers:writing-plans` or a normal implementation PR).

Don't pause for a "what next?" menu after a reviewed export — the standing flow
is **commit, then `/spec-to-plan`**.

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, where the
devague agent maintains it alongside the tool it operates (dogfooding). It is the
*inverse* of the other skills under `.claude/skills/`, which devague vendors
**from** guildmaster. When this skill is ready, guildmaster pulls it **from** devague and
re-broadcasts it to the rest of the AgentCulture mesh. The `cite, don't import`
policy still holds: downstream repos copy it, they don't symlink or depend on it.
See `docs/skill-sources.md`.
