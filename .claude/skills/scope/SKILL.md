---
name: scope
description: >
  Explore the scope of a vague idea BEFORE framing it into a spec (the
  idea→scope leg; the optional opening move ahead of /think). Survey the
  surfaces the idea touches — code, docs, skills, CI, sibling repos — and seed
  the coming Announcement Frame with boundary, non-goal, and assumption claims
  that cite what was actually explored (provenance, not generic disclaimers).
  Use when the user says "explore scope", "scope this idea", "what does this
  touch", "map the scope", "scope exploration", or when an idea touches an
  existing codebase and speccing it cold would mean guessing its boundaries.
  Hand off to the sibling /think skill to build the frame. Authored and
  maintained in agentculture/devague (origin = devague); guildmaster pulls this
  skill from here and broadcasts it to the AgentCulture mesh — it is NOT
  vendored from guildmaster like the inbound skills here.
type: command
---

# scope — explore what an idea touches before you frame it

The skill is named **`scope`**; it is the **opening leg** of the devague method
— run it *before* the sibling **`/think`** skill frames an idea into a spec.
Where `/think` converges on *what* to build, `/scope` grounds *where the idea
lives*: which surfaces it touches, which it must not, and what is genuinely
unknown — so the frame starts from explored territory instead of guesses.

This comes from the sharper end-to-end method spec (devague#53): scope grounded
up front means convergence measures real coverage instead of vibes. The
exploration itself is **agent-side work** — the devague CLI stays deterministic
and never explores anything (#20).

## When to use — and when to skip

Use `/scope` when the idea touches an existing codebase or ecosystem: a feature
in a real repo, a process change across skills, anything where boundaries are
discoverable rather than invented.

**Skip it freely for small ideas.** Scope exploration is *not* a mandatory
first stage — the move-driven adaptive arc stays intact, and an idea that fits
in one announcement can go straight to `/think`. (This is a recorded non-goal
of the method: no wizard.)

## The method

1. **Enumerate candidate surfaces.** List what the idea *might* touch: source
   packages, CLI verbs, renderers, schemas, tests, docs, skills, CI workflows,
   sibling repos. `git ls-files` and the repo's `CLAUDE.md` are the usual map.
   Count the candidates — the count decides how step 2 explores them.
2. **Explore each surface read-only — inline for a handful, fanned out for a
   broader survey.** Read enough of each candidate to decide: touched, not
   touched, or unknown. Exploration never mutates anything — no edits, no
   state changes, no CLI moves — while surveying, whether you explore inline
   or fan out.
   - **4 or fewer candidate surfaces: explore them yourself, inline,
     serially**, exactly as before. Spinning up subagents to read three files
     costs more than it saves, and the no-wizard rule still applies underneath
     this: a small idea can skip scope exploration altogether (see *When to
     use — and when to skip*, above).
   - **5 or more candidate surfaces: fan out.** Dispatch one **read-only
     exploration subagent per surface** (or per tight cluster of closely
     related surfaces, e.g. "the five files under `render/`"), defaulting to a
     **smaller tier — sonnet** — for every subagent in the fan-out. Sonnet is
     the default, not a ceiling: escalate a single subagent to a stronger tier
     only when that specific surface demands it (an ambiguous design doc
     needing real synthesis, say), never as the default for the whole survey.
   - **Subagents explore and report; they never run a `devague` move.** A
     fan-out subagent's job ends at a read-only finding — touched / not
     touched / unknown, with the file, line, or command output that grounds
     the verdict — handed back to the main agent in its report. The main
     agent alone runs every `capture`, `scope`, `question`, and `park` call in
     step 4, from the subagents' reported evidence. This is the load-bearing
     rule of the whole fan-out: provenance and the anti-fabrication contract
     stay in one place (the main agent), never scattered across however many
     subagent transcripts the user never sees.
3. **Classify every finding.** Each explored surface yields one of:
   - **in scope** — the idea changes it → becomes a `requirement` or
     `assumption` claim in the frame;
   - **out of scope** — the idea must not change it → becomes a `boundary` or
     `non_goal` claim;
   - **genuinely unknown** — can't tell without a decision → becomes a `park`
     (open vagueness) or a `question` (pending user decision).
4. **Record findings on the frame, with provenance.** Start the frame with
   `devague new "<announcement>" --title "<short>"` (this is also `/think`'s
   first move) — scope entries live on the frame, so it must exist first.
   Record each explored surface as a first-class finding:
   `` `devague scope "<surface>" --finding "<text>" [--seeds <id> ...]` ``
   — text that **cites the surface explored** ("the CLI stays deterministic
   per issue 20; scope exploration is agent-side" beats "we won't overreach").
   Capture the claim it seeded first (`capture --kind ...`), then pass its id
   to `--seeds`; a finding you routed to a hard question instead (the
   "genuinely unknown" branch above) links the same way — `--seeds` accepts a
   claim id (`c*`) **or** a claim-attached hard-question id (`q*`, #84) — and
   an unknown seed id is refused with a hint. Provenance, not
   generic disclaimers: a reviewer should be able to trace every boundary claim
   back to something you read. **This move is always the main agent's, never a
   subagent's** — when step 2 fanned out, the main agent writes the finding
   from the subagent's reported evidence (still citing the actual surface, not
   "a subagent explored this"), so every seeded claim traces back to a
   concrete file, line, or command output either way.

## How findings land (the shipped surface)

This skill invokes the CLI directly and stays self-contained (if `devague`
isn't on your PATH: `uv tool install devague`).

The **primary** landing surface is the deterministic `devague scope` move,
shipped in task t3 of the committed sharper-end-to-end-method plan
(`docs/plans/2026-07-01-devague-ships-a-sharper-end-to-end-method-a-guided.md`,
devague#53). It records an explored surface + finding as first-class frame
state (`Frame.scope_entries` / `ScopeEntry`: `id` (`sN`), `surface`, `finding`,
`seeds`) — deterministic recording only, no LLM calls, no subprocess, no
filesystem exploration inside the CLI. Like `capture`, it needs a frame to
already exist, so run `devague new` first:

| Move | What it does |
|------|--------------|
| `devague scope "<surface>" --finding "<text>"` | Record a finding on the current frame. |
| `devague scope "<surface>" --finding "<text>" --seeds <id> [<id> ...]` | Record a finding, linking it to what it went on to seed — a claim id (`c*`) or a claim-attached hard-question id (`q*`, #84), so a finding routed to `interrogate --hard-question` keeps its provenance link too. An unknown seed id is refused with a hint (`run 'devague show' to see valid claim ids`). |
| `devague scope --amend <sN> --finding "<corrected>"` | Replace an entry's finding in place — same `id`, `surface`, and `seeds` (#84). Use this instead of recording a second entry that says "supersedes s18"; there is no revision trail here, unlike claim `amend`. |
| `devague scope --list [--json]` | Read every recorded entry back. |

After every successful, non-exempt move (`status` itself is exempt, since
reporting the next move is already its whole purpose) the CLI prints a
`next: <recommended move>` line to **stderr**. Follow that hint or run
`devague status` — either gets you the same recommended next move.

Boundary / non-goal / in-scope claims still land the same way they always
did, through the normal frame moves — `devague scope` documents *what surface
you explored and what you learned*, `capture` records *the claim that
followed*:

| Finding | Move |
|---------|------|
| in scope (the idea changes this) | `capture --kind requirement` / `--kind assumption` (with `--origin llm` if you proposed it) |
| out of scope (must not change) | `capture --kind boundary` / `--kind non_goal` |
| genuinely unknown, needs a user decision | `question "<text>"` (later `question --resolve <qid> --decision "<text>"`) |
| genuinely unknown, not decidable now | `park "<text>" --kind unknown_blocking\|unknown_nonblocking` |

**Which `q*` ids `--seeds` accepts.** Two independent things mint `qN` ids and
both start counting at `q1`: **claim-attached** hard questions
(`interrogate <cN> --hard-question "<text>"`) and the separate durable
`.devague/questions/<slug>.md` artifact (`devague question`). `scope --seeds`
resolves against the **claim-attached** ones only. So when you want a
"needs a user decision" finding to carry a provenance link, raise it with
`interrogate --hard-question` on the claim it hangs off and seed that id —
seeding a `devague question` id would either be refused or, worse, match an
unrelated claim-attached question with the same number.

## Hard rules (do not violate)

- **Exploration is read-only.** Surveying scope never edits files, never
  mutates frame state, never runs a mutating CLI move — true whether you
  explore inline or fan out to subagents (step 2).
- **Provenance in every seeded claim.** A scope-derived claim cites what was
  explored. If you didn't read it, don't claim it — including what a fan-out
  subagent read on your behalf: its report is the evidence, not a substitute
  for citing the surface.
- **Subagents explore; only the main agent moves.** When step 2 fans out to
  read-only exploration subagents, they never run `devague capture`, `scope`,
  `question`, or `park` — only report findings back. The main agent runs
  every one of those moves itself, from the subagents' reported evidence, so
  provenance and the anti-fabrication contract are never split across agents.
- **LLM proposals stay proposed.** Findings you capture with `--origin llm`
  land `proposed`; the user confirms. Same anti-fabrication contract as
  `/think`.
- **Don't become a wizard.** Scope exploration is optional-by-size and
  adaptive. Never block a small idea on a survey it doesn't need — and even
  a broad survey that fans out to subagents skips the fan-out entirely if the
  idea itself is small (4 or fewer candidate surfaces, step 2).
- File the record the moment the thing happens, never at closeout — written
  late is written flattering (issue 97).

## Worked example

Scoping "devague exports should carry per-item instructions" against the
devague repo itself:

```bash
# 1–2. enumerate + explore (read-only)
git ls-files devague/ | head -30       # the CLI package map
# read: devague/frame.py (claim model), devague/render/spec_md.py (renderer),
#       docs/spec-contract.md (schema contract), .claude/skills/think/SKILL.md

# 3. start the frame (also /think's first move — scope entries live on it)
devague new "devague exports carry per-item instructions" --title "per-item instructions"

# 4. capture each finding as a claim, then record the scope entry that seeded it
devague capture --origin llm --kind requirement "claims gain an optional instruction field — devague/frame.py claim model + docs/spec-contract.md schema both need a bump"
devague scope "devague/frame.py" --finding "claim model needs an optional instruction field per docs/spec-contract.md's schema contract" --seeds c2

devague capture --origin llm --kind boundary "render/spec_md.py renders instructions verbatim; absent instructions render nothing — the renderer never fabricates filler"
devague scope "render/spec_md.py" --finding "renderer must render instructions verbatim; absent instructions render nothing — never fabricate filler" --seeds c3

devague capture --origin llm --kind non_goal "no LLM calls land inside the CLI (issue 20) — instruction text is authored by the operator/user, never generated in-CLI"
devague scope "issue 20 (no LLM calls in-CLI)" --finding "instruction text is authored by the operator/user, never generated in-CLI" --seeds c4

devague question "do instructions attach to frame claims, plan tasks, or both?"
devague scope --list   # read every recorded finding back, with its seeded claim ids
```

Every claim above names the file or issue that was actually read — that is the
provenance bar. Note the order: `--seeds` needs the claim id to already exist,
so `capture` runs first and the matching `scope` call cites it back.

## After scoping — hand off to /think

The recorded scope entries and the claims captured alongside them both live on
the same frame — there is nothing separate to export. `devague scope --list`
is the durable, citable record of what was explored; a converged frame's
exported spec-md also renders a `## Scope exploration` section from the same
entries (surface, finding, and seeded claim ids), so the provenance survives
into the buildable artifact (#53 t6). When the survey is done, continue with
`/think`'s remaining moves (`interrogate`, `confirm`/`reject`, `converge`,
`export`) to take the frame the rest of the way. The user confirms
LLM-proposed claims there, as always.

## Before and after this leg

```text
Previous leg: nothing precedes
Next leg: think
```

## Provenance

This is a **first-party** skill — its origin is `agentculture/devague`, the
*fourth* authored in the outbound family (after `/think`, `/spec-to-plan`, and
`/assign-to-workforce`) but the **opening leg** of the flow, covering the
pre-frame exploration that runs before `/think`. guildmaster pulls it from here
and broadcasts it to the AgentCulture mesh; because devague is upstream, it is
**never re-vendored back** from guildmaster's re-broadcast copy. The
`cite, don't import` policy still holds: downstream repos copy it, they don't
symlink or depend on it. See `docs/skill-sources.md`.
