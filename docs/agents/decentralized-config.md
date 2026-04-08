# Decentralized Agent Configuration

## Overview

Agent configuration in culture is decentralized: each agent lives in its own
directory and owns a `culture.yaml` file that describes what it is and how it
runs. A machine-level `~/.culture/server.yaml` holds only server connection
details, supervisor settings, webhooks, and a manifest mapping agent suffixes
to their directories.

This split keeps agent definitions close to the code they operate on, makes
agents portable (move the directory, re-register), and eliminates the
monolithic `agents.yaml` file where every agent definition lived in one place.

## The `culture.yaml` Format

### Single-agent (flat)

```yaml
suffix: myagent
backend: claude
model: claude-opus-4-6
channels:
  - "#general"
system_prompt: |
  You are a focused agent for this project.
tags:
  - project
```

### Multi-agent (list)

```yaml
agents:
  - suffix: writer
    backend: claude
    model: claude-opus-4-6
    channels:
      - "#general"
    system_prompt: You handle documentation.

  - suffix: reviewer
    backend: codex
    model: gpt-5.4
    channels:
      - "#general"
    system_prompt: You review code changes.
```

### Fields

| Field | Default | Description |
|-------|---------|-------------|
| `suffix` | (required) | Agent name suffix — combined with server name to form nick |
| `backend` | `claude` | Agent backend: `claude`, `codex`, `copilot`, `acp` |
| `model` | `claude-opus-4-6` | Model identifier passed to the backend |
| `channels` | `["#general"]` | IRC channels to join on startup |
| `system_prompt` | `""` | System prompt injected into the agent |
| `tags` | `[]` | Arbitrary labels for filtering and display |
| `icon` | `null` | Optional display icon |
| `archived` | `false` | Set by `culture agent archive`; hides from listings |

Backend-specific extras (e.g. `acp_command`) are stored as-is under the same
document and passed through to the harness.

## The `server.yaml` Format

`~/.culture/server.yaml` is the machine-level config. The `agents` key is a
manifest: a mapping from suffix to absolute directory path.

```yaml
server:
  name: spark
  host: localhost
  port: 6667

supervisor:
  model: claude-sonnet-4-6
  thinking: medium
  window_size: 20
  eval_interval: 5
  escalation_threshold: 3

webhooks:
  url: https://hooks.example.com/culture
  irc_channel: "#alerts"
  events:
    - agent_spiraling
    - agent_error
    - agent_question

buffer_size: 500
poll_interval: 60
sleep_start: "23:00"
sleep_end: "08:00"

agents:
  myagent: /home/user/projects/myproject
  harness: /home/user/git/culture/packages/agent-harness
  harness-claude: /home/user/git/culture/culture/clients/claude
```

At startup, `culture agent start` reads `server.yaml`, walks the manifest, and
loads each `culture.yaml` to construct full `AgentConfig` objects with computed
nicks (`<server>-<suffix>`).

## Registration Workflow

```bash
# 1. Create or edit culture.yaml in your project directory
cd /path/to/myproject
cat culture.yaml
# suffix: myagent
# backend: claude
# ...

# 2. Register with the server manifest
culture agent register .
# Registered myagent → /path/to/myproject

# 3. Start the agent
culture agent start spark-myagent

# 4. Unregister when done
culture agent unregister myagent
```

`culture agent register [path]` defaults to the current working directory.
For a multi-agent `culture.yaml`, pass `--suffix` to select one entry.

`culture agent unregister` accepts either the suffix (`myagent`) or the full
nick (`spark-myagent`).

## Migration from agents.yaml

If you have a legacy `~/.culture/agents.yaml` (the old monolithic format where
every agent was an inline dict in a list), run the one-time migration command:

```bash
culture agent migrate
```

This reads `~/.culture/agents.yaml` (or a custom path via `--config`) and:

1. Writes `~/.culture/server.yaml` with server, supervisor, and webhook
   settings from the old file.
2. For each agent entry, creates a `culture.yaml` in a new directory under
   `~/.culture/agents/<suffix>/`.
3. Adds each agent to the manifest in the new `server.yaml`.

The legacy file is not deleted — keep it until you have verified the migration.

```bash
# Custom paths
culture agent migrate \
  --config /path/to/old/agents.yaml \
  --output /path/to/new/server.yaml
```

## Harness Agents and `#harness` Propagation

The `packages/agent-harness/` directory contains the canonical agent harness
template. An agent running from that directory (`spark-harness`) monitors the
`#harness` channel and coordinates propagation of template changes to backend
agents.

Each backend (`claude`, `codex`, `copilot`, `acp`) has its own `culture.yaml`
in `culture/clients/<backend>/`, also joined to `#harness`. When the harness
agent posts propagation instructions, backend agents apply the changes to their
own directory using the assimilai pattern.

```text
packages/agent-harness/culture.yaml  →  suffix: harness
culture/clients/claude/culture.yaml  →  suffix: harness-claude
culture/clients/codex/culture.yaml   →  suffix: harness-codex
culture/clients/copilot/culture.yaml →  suffix: harness-copilot
culture/clients/acp/culture.yaml     →  suffix: harness-acp
```

All harness agents join `#harness` and carry `tags: [harness, <backend>]` for
identification. The `spark-harness` agent additionally carries `tags: [harness, template]`.

See: [Assimilai Pattern](../CLAUDE.md#assimilai-pattern) and
[All-backends rule](../CLAUDE.md#assimilai-pattern).
