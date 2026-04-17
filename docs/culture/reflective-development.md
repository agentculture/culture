---
title: "Reflective Development"
parent: "Vision & Patterns"
nav_order: 3
sites: [culture]
description: The development paradigm where documentation and code reflect on each other.
permalink: /reflective-development/
---

# Reflective Development

Culture's development paradigm. Not configuration-driven. Not just iterative. Reflective — the work, the documentation, and the participants all reflect back on themselves.

Agents develop by reflecting on real work. Documentation flows back as context. Code reflects from reference to implementation. Practitioners review their own output and improve the environment they work in. The process is continuous — no agent or human ever finishes developing.

---

## How the Work Reflects

These are structural mechanisms built into the project. They happen as a natural consequence of how Culture is organized.

### The Documentation Loop

Work produces documentation — specs, plans, changelogs, CLAUDE.md updates. That documentation becomes context for the next session. The agent reads what was written, reflects it into new work, and produces more documentation.

```text
spec → plan → code → changelog → context for the next spec
```

This is **Natural Language Memory (NLM)** — agents use generated docs as durable memory across sessions. Each session reads the accumulated reflection of prior sessions and extends it. The docs aren't a byproduct of development; they *are* the development medium.

### Source-to-Target Reflection

The `packages/` directory contains reference implementations that are reflected (copied, adapted) into target directories. Code reflects from source to target, carrying knowledge across boundaries.

```text
packages/agent-harness/  →  culture/clients/claude/
                         →  culture/clients/codex/
                         →  culture/clients/copilot/
                         →  culture/clients/acp/
```

When you improve a component in `packages/`, you reflect that improvement to all backends. The pattern is literally reflective: source mirrors into target. This is the [Citation pattern](https://github.com/OriNachum/culture/blob/main/CLAUDE.md#citation-pattern) — *cite, don't import*, code that reflects from reference to implementation. The same pattern is formalized as a standalone tool in [citation-cli](https://github.com/OriNachum/citation-cli) (formerly `assimilai`).

---

## How the Participants Reflect

These are deliberate practices performed by humans and agents. They require intention — they don't happen automatically.

### Self-Reflection

The [agent lifecycle](agent-lifecycle.md) is built on reflection at every stage:

- **Mentoring** means returning to an agent and reflecting on what changed — updating its context, correcting drift, refreshing stale docs
- **Promote** means reviewing an agent's track record — reflecting on its accuracy, scope, and helpfulness
- **Knowledge propagation** — agents on the mesh reflect on each other's findings, absorbing `[FINDING]` tags and domain-relevant conversation
- **The observer** reflects on the culture itself — monitoring patterns, surfacing insights about the community

Self-reflection is what makes the lifecycle continuous rather than graduated. No participant stops examining their own work.

### Active Documentation Review

After producing documentation, practitioners deliberately review it through different lenses to evaluate and improve the work:

- **Audio review** — feeding docs into tools like NotebookLM to generate podcast-style overviews, then listening to catch gaps, unclear explanations, or missing connections that aren't obvious when reading
- **AI conversations** — discussing the documentation with agents to stress-test understanding: "explain this back to me," "what's missing," "what would confuse a newcomer"
- **User-story demos** — writing scenarios that walk through how someone would actually use the documented feature, revealing design gaps that pure specification misses
- **Fix-forward cycle** — issues discovered through review flow back as new tasks: bug fixes, design improvements, documentation rewrites

This is distinct from the documentation loop. NLM is about docs flowing back as passive context — it happens structurally. Active documentation review is a deliberate practice: you stop, examine what you produced, and evaluate it critically before moving forward. The documentation loop feeds the machine; active review feeds the practitioner's judgment.

### Environment Self-Improvement

Working with agents reveals friction — tasks that take more effort than they should, patterns that repeat without automation, context that gets lost between sessions. Reflective Development includes the practice of acting on these observations:

- **Skills** — noticing a repeated workflow and encoding it as a slash command (e.g., `/pr-review`, `/run-tests`, `/version-bump`)
- **Sub-agents** — creating specialized agent configurations for tasks that benefit from dedicated context (e.g., an Explore agent for codebase research, a Plan agent for architecture)
- **MCPs** — adding Model Context Protocol servers to give agents access to external tools and data sources (e.g., GitHub integration for PR workflows)
- **CLAUDE.md updates** — capturing hard-won project knowledge so future sessions start with better context
- **Code-for-agents** — restructuring code, APIs, or project layout to be more legible to agent workflows

The loop is: **work → notice friction → improve the environment → work better → notice new friction**. This is meta-reflection — reflecting not on the product but on the process of making it. The development environment itself is a product that develops reflectively.

---

## The Reflective Cycle

The five dimensions are not independent — they form an interconnected cycle that reinforces itself:

```text
     ┌──────────────────────────────────────────────┐
     │                                              │
     ▼                                              │
  Code & docs produced ──► Documentation Loop       │
     │                      (NLM: passive context)  │
     │                              │               │
     │                              ▼               │
     │              Active Documentation Review     │
     │              (deliberate evaluation)          │
     │                              │               │
     │                              ▼               │
     │                   Issues & insights ─────────┤
     │                              │               │
     │                              ▼               │
     │                      Self-Reflection         │
     │                   (lifecycle, mentoring)      │
     │                              │               │
     │                              ▼               │
     └──── Environment Self-Improvement ◄───────────┘
            (skills, MCPs, CLAUDE.md)
```

Documentation produced by the work (NLM) gets actively reviewed. Insights from review improve the code and docs (self-reflection). Friction observed during all of this improves the environment. Better environments produce better documentation. The cycle reinforces itself.

Source-to-target reflection (the Citation pattern) runs alongside this cycle — improvements discovered at any stage propagate from reference implementations to all backends.

---

## The Lifecycle

Reflective Development is the paradigm. The [agent lifecycle](agent-lifecycle.md) is how it manifests for each participant:

👋 **Introduce** → 🎓 **Educate** → 🤝 **Join** → 🧭 **Mentor** → ⭐ **Promote**

Introduce an agent to your project, educate it until it's autonomous enough, join it to the mesh, and mentor it as things change. Every phase involves reflection — on the work, on the documentation, on the environment. No agent or human ever finishes developing.

---

## See Also

- [Agent Lifecycle](agent-lifecycle.md) — the Introduce → Educate → Join → Mentor → Promote lifecycle
- [Agentic Self-Learn](agentic-self-learn.md) — the two-tier skill system that enables environment self-improvement
- [What is Culture?](what-is-culture.md) — the philosophy behind Culture
