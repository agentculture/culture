---
name: agent-config
description: >
  Show a Culture agent's full configuration in one read-only view: its
  system-prompt file (CLAUDE.md / AGENTS.md / GEMINI.md), the parallel
  culture.yaml, and the agent's local .claude/skills index. Use when an
  operator says "show agent <name>", "what does <agent> look like", or before
  teaching/onboarding an agent and you need to see its current kit + config.
  Backs the `guild show` verb. Vendored from guildmaster (cite-don't-import;
  guildmaster forked it from steward, which keeps an alignment-judgment
  variant); inventory only — it reports, it does not judge alignment or drift.
type: command
---

# agent-config — surface a Culture agent's config in one view

guildmaster is the mesh's skills supplier and owns the **inventory** surfaces:
"what kit + config does this agent have?" This skill answers exactly that for a
single agent, showing the three artifacts that together define it:

1. **System-prompt file** (`CLAUDE.md` / `AGENTS.md` / `GEMINI.md`) — the
   prompt-side guidance for the agent's backend. The script detects which file
   is present from a backend-fingerprint registry.
2. **`culture.yaml`** — the runtime-side config (`agents:` list with `suffix`,
   `backend`, `model`, `system_prompt`, `channels`, `tags`, `acp_command`,
   `extras`). Lives parallel to the prompt file at the project root.
3. **`.claude/skills/*/SKILL.md`** — the per-project skills the agent can
   invoke, one line each (name + truncated description).

This is the **inventory half** of the steward → guildmaster split
([issue #12](https://github.com/agentculture/guildmaster/issues/12)): it reports
the config, it does **not** interpret drift or judge alignment. The relationship
graph and the "is this agent aligned?" judgment stay with `steward overview` /
`steward doctor`.

## When to use

- Before `guild teach` / `guild onboard` — see an agent's current kit + config.
- When an operator asks "show me agent `<name>`" or "what does `<agent>` run".
- Read it, don't guess — before answering a question about what an agent does.

## How to run

One script, two ways to call it (or just run `guild show`, which wraps it):

```bash
# Path mode — point at any directory with a prompt file + culture.yaml
.claude/skills/agent-config/scripts/show.sh ../culture

# Suffix mode — resolve a registered agent suffix via the Culture server's
# manifest (location set by culture_server_yaml in skills.local.yaml)
.claude/skills/agent-config/scripts/show.sh daria
```

Output is three sections: the detected system-prompt file, `culture.yaml` (or
`(missing)`), and a one-line summary per local skill (name + description,
truncated to 120 chars).

## What to look at in `culture.yaml`

| Field | Why it matters |
|-------|----------------|
| `suffix` | Identifies the agent on the mesh. |
| `backend` | One of `claude` / `codex` / `copilot` / `acp`. The all-backends rule means a feature in one must land in all four. |
| `model` | Drift here changes behavior silently. |
| `system_prompt` | Should not contradict the prompt file. |
| `channels` | Where the agent listens. |
| `tags`, `extras`, `acp_command` | Backend-specific. |

## Notes

- **Read-only.** The script never edits agent files. It reports; it does not
  flag or fix drift — that judgment is steward's lane.
- **Backend-aware.** Prompt-file detection comes from
  `data/backend-fingerprints.yaml` (the `prompt:` mapping), falling back to the
  built-in `(CLAUDE.md AGENTS.md GEMINI.md)` list if the registry is absent.
- **Per-machine config.** Suffix mode reads `culture_server_yaml` from
  `.claude/skills.local.yaml` (git-ignored), falling back to
  `.claude/skills.local.yaml.example`.
- **Vendored from guildmaster** (`agent-config`; guildmaster forked it from
  steward, which keeps an alignment-judgment variant). Re-sync from
  guildmaster's canonical copy when it changes. Divergences: the SKILL.md is
  reframed for guildmaster's inventory role and adds `type: command` for the
  culture backend's skill loader.
