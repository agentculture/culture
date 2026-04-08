# Reflective Development — Deepening Design Spec

**Date:** 2026-04-08

## Context

The [Reflective Development Reframe](2026-04-07-reflective-development-reframe-design.md) spec
renamed "Organic Development" to "Reflective Development" and identified three
senses of the word "reflective":

1. **Self-reflection** — agents and humans examining their own work
2. **The documentation loop (NLM)** — generating docs, consuming them, growing
3. **Source-to-target reflection** — the Assimilai pattern

That spec also noted a **Future Consideration**: a dedicated
`docs/reflective-development.md` page that would expand on these senses and
give the paradigm a proper home beyond the brief README section.

This spec fulfills that intent — and expands the framework. The three original
senses captured the structural mechanisms well but missed two deliberate
practices that are equally central to how the project actually develops.

## Why Deepen

The original three senses describe things that happen *to* the work — docs flow
back as context, code reflects from source to target, agents examine each
other's output. These are important, but they don't capture what the human
practitioner does: the active, intentional reflection that drives improvement.

Two practices were missing:

- **Active documentation review** — the deliberate step of reflecting on what
  you produced, through different lenses, before moving forward
- **Environment self-improvement** — the meta-practice of observing friction in
  the development process and acting to reduce it

Both are reflective by nature. Both are central to how Culture is actually
built. They deserve to be named.

## The Five Dimensions of Reflective Development

Organized into two groups that distinguish *what reflects automatically* from
*what participants reflect on deliberately*.

### How the Work Reflects

These are structural mechanisms built into the project. They happen as a
natural consequence of how Culture is organized.

#### 1. The Documentation Loop (NLM)

Work produces documentation — specs, plans, changelogs, CLAUDE.md updates. That
documentation becomes context for the next session. The agent reads what was
written, reflects it into new work, and produces more documentation. A spec
becomes a plan becomes code becomes a changelog entry becomes context for the
next spec. This is Natural Language Memory: agents use generated docs as durable
memory across sessions.

#### 2. Source-to-Target Reflection (Assimilai)

The `packages/` directory contains reference implementations that are reflected
(copied, adapted) into target directories. Code reflects from source to target,
carrying knowledge across boundaries. When you improve a component in
`packages/`, you reflect that improvement to all backends. The pattern is
literally reflective: source mirrors into target.

### How the Participants Reflect

These are deliberate practices performed by humans and agents. They require
intention — they don't happen automatically.

#### 3. Self-Reflection

The lifecycle (Introduce → Educate → Join → Mentor → Promote) is built on
reflection. Mentoring means returning to an agent and reflecting on what
changed. Promote means reviewing an agent's track record. Agents on the mesh
reflect on each other's findings (knowledge propagation). The observer reflects
on the culture itself.

#### 4. Active Documentation Review

After producing documentation, practitioners deliberately review it through
different lenses to evaluate and improve the work:

- **Audio review** — feeding docs into NotebookLM to generate podcast-style
  overviews, then listening to catch gaps, unclear explanations, or missing
  connections that aren't obvious when reading
- **AI conversations** — discussing the documentation with agents to
  stress-test understanding: "explain this back to me," "what's missing,"
  "what would confuse a newcomer"
- **User-story demos** — writing scenarios that walk through how someone would
  actually use the documented feature, revealing design gaps
- **Fix-forward cycle** — issues discovered through review flow back as new
  tasks: bug fixes, design improvements, documentation rewrites

This is distinct from the documentation loop (dimension 1). NLM is about docs
flowing back as passive context — it happens structurally. Active documentation
review is a deliberate practice: you stop, examine what you produced, and
evaluate it critically before moving forward. The documentation loop feeds the
machine; active review feeds the practitioner's judgment.

#### 5. Environment Self-Improvement

Working with agents reveals friction — tasks that take more effort than they
should, patterns that repeat without automation, context that gets lost between
sessions. Reflective Development includes the practice of acting on these
observations:

- **Skills** — noticing a repeated workflow and encoding it as a slash command
  (e.g., `/pr-review`, `/run-tests`, `/version-bump`)
- **Sub-agents** — creating specialized agent configurations for tasks that
  benefit from dedicated context (e.g., an Explore agent for codebase research)
- **MCPs** — adding Model Context Protocol servers to give agents access to
  external tools and data sources (e.g., GitHub integration)
- **CLAUDE.md updates** — capturing hard-won project knowledge so future
  sessions start with better context
- **Code-for-agents** — restructuring code, APIs, or project layout to be more
  legible to agent workflows

The loop is: work → notice friction → improve the environment → work better →
notice new friction. This is meta-reflection — reflecting not on the product
but on the process of making it. The development environment itself is a
product that develops reflectively.

## What Changes

### New Files

| File | Purpose |
|------|---------|
| `docs/reflective-development.md` | User-facing canonical reference for the paradigm |
| `docs/superpowers/specs/2026-04-08-reflective-development-deepening-design.md` | This design spec |

### Updated Files

| File | Change |
|------|--------|
| `README.md` (lines 117–125) | Broaden the Reflective Development section to imply all five dimensions; add link to new page |
| `docs/index.md` (line 59) | Fix leftover: "Simple, organic, transparent" → "Simple, reflective, transparent" |
| `docs/index.md` (lines 113–121) | Mirror README changes with relative links |
| `docs/index.md` (lines 127–130) | Add reflective-development.md to What's Next section |
| `docs/getting-started.md` | nav_order 2 → 3 |
| `docs/agent-lifecycle.md` | nav_order 3 → 4 |
| `docs/culture-cli.md` | nav_order 4 → 5 |
| `docs/rooms.md` | nav_order 5 → 6 |
| `docs/agentic-self-learn.md` | nav_order 6 → 7 |
| `docs/use-cases-index.md` | nav_order 7 → 8 |

### Nav Order (after changes)

| Order | Page |
|-------|------|
| 0 | index.md |
| 1 | what-is-culture.md |
| 2 | **reflective-development.md** (new) |
| 3 | getting-started.md |
| 4 | agent-lifecycle.md |
| 5 | culture-cli.md |
| 6 | rooms.md |
| 7 | agentic-self-learn.md |
| 8 | use-cases-index.md |

## Relationship to Prior Spec

The [2026-04-07 Reframe spec](2026-04-07-reflective-development-reframe-design.md) is
not superseded. It remains the record of the rename from "Organic" to
"Reflective." This spec extends the framework by adding two dimensions and
creating the dedicated page that the prior spec identified as a future
consideration.

## Verification

1. **Grep scan:** Search all `.md` files for "Simple, organic" — should return
   zero hits outside historical specs/plans.
2. **Link integrity:** All new links (`reflective-development.md`) resolve to
   existing files.
3. **Nav order:** Sequence 0–8, no gaps or duplicates.
4. **Read-through:** README section implies all five dimensions without being
   heavy-handed.
