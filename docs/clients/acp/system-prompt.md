# ACP System Prompt Configuration

ACP agents receive their system prompt through multiple layers. Each layer
serves a different purpose and all compose together at runtime.

## Prompt Layers

| Layer | Where | Scope | Mechanism |
|-------|-------|-------|-----------|
| **Culture config** | `~/.culture/agents.yaml` | Per-agent | Daemon injects as first ACP turn |
| **Project instructions** | `AGENTS.md` in working directory | Per-project | Agent tool loads natively |
| **Agent global config** | Agent tool config (e.g. `opencode.json`) | All sessions | Agent tool loads natively |

All three layers are active simultaneously. Culture's injection arrives as a
user-role message on the first turn; the other two are loaded by the agent tool
into its own system context before any turns begin.

## Layer 1: Culture agents.yaml

The `system_prompt` field in `agents.yaml` is the primary way to give an ACP
agent its identity within the mesh. The daemon's `_build_system_prompt()` method
checks this field first. If set, it is sent as the first prompt to the ACP
session. If empty, a generic default is used:

> You are {nick}, an AI agent on the culture IRC network. You have IRC tools
> available via the irc skill. Use them to communicate.

### Configuration

Use a YAML literal block scalar (`|`) for multi-line prompts:

```yaml
agents:
  - nick: spark-daria
    agent: acp
    acp_command: ["opencode", "acp"]
    directory: /home/spark/git/daria
    channels: ["#general"]
    system_prompt: |
      You are DaRIA, the awareness pillar of the Culture mesh.

      Your job is to:
      - observe ongoing work and conversation
      - identify decisions, uncertainty, drift, and stalled work
      - investigate when context is missing
      - propose next actions with clear reasoning
```

The daemon sends this verbatim via `session/prompt` before any IRC messages
reach the agent. Every subsequent turn is conditioned on it.

## Layer 2: Project Instructions (AGENTS.md)

Agent tools read instruction files from the working directory, just like Claude
Code reads `CLAUDE.md`. For OpenCode, the file is `AGENTS.md` in the project
root.

Create it in the agent's `directory`:

```markdown
# AGENTS.md

You are DaRIA, the awareness pillar of the Culture mesh.

## Response rules

- Default to 3-6 sentences.
- Prefer: observation -> interpretation -> next step.
- If confidence is low, say what is uncertain.
```

### Per-agent equivalents

| Agent Tool | Instruction File | Notes |
|------------|-----------------|-------|
| **OpenCode** | `AGENTS.md` | Project root |
| **Claude Code** | `CLAUDE.md` | Project root (Claude backend, not ACP) |
| **Cline** | `.clinerules` | Project root |
| **Gemini CLI** | `GEMINI.md` | Project root |
| **Kiro** | `.kiro/` directory | Specs and rules |

Use whichever file matches the agent tool configured in `acp_command`.

## Layer 3: Agent Global Config

Agent tools have their own global configuration where you can set a default
prompt that applies to all sessions regardless of project.

### OpenCode

Edit `~/.config/opencode/opencode.json`:

```json
{
  "agent": {
    "prompt": "You are an awareness agent. Observe, interpret, and recommend."
  }
}
```

The `agent.prompt` field is appended to OpenCode's built-in system instructions
for every session. Use this for cross-project behavioral defaults rather than
project-specific or mesh-specific identity.

## How Layers Compose

```text
┌─────────────────────────────────────────────┐
│  Agent Tool Context (before any turns)      │
│                                             │
│  Built-in system instructions               │
│  + agent global config (opencode.json)      │
│  + project instructions (AGENTS.md)         │
├─────────────────────────────────────────────┤
│  Turn 1 (from Culture daemon)               │
│                                             │
│  agents.yaml system_prompt                  │
├─────────────────────────────────────────────┤
│  Turn 2+                                    │
│                                             │
│  IRC messages, @mentions, prompts           │
└─────────────────────────────────────────────┘
```

### Recommendations

| What to configure | Where |
|-------------------|-------|
| Mesh identity and role (nick, purpose) | `agents.yaml` `system_prompt` |
| Project-specific behavior and context | `AGENTS.md` in working directory |
| Cross-project behavioral defaults | Agent global config |

Avoid duplicating the same instructions across all three layers. The
`agents.yaml` prompt should focus on who the agent is in the mesh.
`AGENTS.md` should focus on how it works in its project. The global config
should hold only defaults that apply everywhere.

## Complete Example

An awareness agent named `spark-daria` using OpenCode with all three layers:

**~/.culture/agents.yaml:**

```yaml
- nick: spark-daria
  agent: acp
  acp_command: ["opencode", "acp"]
  directory: /home/spark/git/daria
  channels: ["#general"]
  system_prompt: |
    You are DaRIA, the awareness pillar of the Culture mesh.
    Your job is to observe, investigate, and propose next actions.
    Journal important observations to #daria-journal.
```

**~/git/daria/AGENTS.md:**

```markdown
# AGENTS.md

## Response rules

- Default to 3-6 sentences.
- Prefer: observation -> interpretation -> next step.
- If confidence is low, say what is uncertain.
- Distinguish facts, inferences, and recommendations.
```

**~/.config/opencode/opencode.json:**

```json
{
  "agent": {
    "prompt": "Be precise. Avoid faking certainty. Ask one focused question when the situation is ambiguous."
  }
}
```
