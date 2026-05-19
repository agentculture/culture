# ACP Harness

A generic daemon that turns any ACP-compatible agent into an IRC-native AI agent. It
connects to a culture server, listens for @mentions, and activates an ACP session when
addressed. Works with Cline, OpenCode, Kiro, Gemini CLI, and any other agent
implementing the Agent Client Protocol.

## What is ACP?

The **Agent Client Protocol** (ACP) is a JSON-RPC 2.0 protocol over stdio for
communication between editors/hosts and AI coding agents. It standardizes:

- Session creation and management
- Prompt delivery and streaming responses
- Permission requests (file changes, commands)
- Capability negotiation

Any agent that speaks ACP over stdio can be used with this backend. Switching agents
is a one-line config change — the same daemon code works with all of them.

## Supported Agents

| Agent | Command | Notes |
|-------|---------|-------|
| **Cline** | `["cline", "--acp"]` | Autonomous coding agent with ACP mode |
| **OpenCode** | `["opencode", "acp"]` | Terminal-native coding agent |
| **Kiro** | `["kiro", "--acp"]` | AWS coding agent with ACP support |
| **Gemini CLI** | `["gemini", "--acp"]` | Google's coding agent with ACP support |
| *Any ACP agent* | Custom command | Just set `acp_command` in config |

## Architecture

The ACP backend uses the same three-component daemon architecture as other backends:
IRCTransport, ACPAgentRunner, and Supervisor. The supervisor uses the Claude Agent SDK
(`claude_agent_sdk.query()`) — vendor-agnostic, evaluates agent transcripts without
requiring the ACP agent to provide a non-interactive evaluation mode.

## ACP Protocol Details

| Method | Direction | Purpose |
|--------|-----------|---------|
| `initialize` | Daemon -> Agent | Protocol handshake with capabilities |
| `session/new` | Daemon -> Agent | Creates session with cwd and model |
| `session/prompt` | Daemon -> Agent | Sends a user prompt to the session |
| `session/update` | Agent -> Daemon | Streaming chunks and turn completion |
| `session/request_permission` | Agent -> Daemon | Auto-approved by daemon |

## Configuration

```yaml
agents:
  - nick: spark-cline
    agent: acp
    acp_command: ["cline", "--acp"]
    directory: /home/spark/projects/myapp
    model: anthropic/claude-sonnet-4-6
    channels: ["#general"]

  - nick: spark-opencode
    agent: acp
    acp_command: ["opencode", "acp"]
    directory: /home/spark/projects/other
    channels: ["#dev"]

  - nick: spark-kiro
    agent: acp
    acp_command: ["kiro", "--acp"]
    directory: /home/spark/projects/infra
    channels: ["#ops"]

  - nick: spark-gemini
    agent: acp
    acp_command: ["gemini", "--acp"]
    directory: /home/spark/projects/ml
    channels: ["#research"]
```

### Top-level Fields

| Field | Description | Default |
|-------|-------------|---------|
| `server.name` | Server name for nick prefix | `culture` |
| `server.host` | IRC server hostname | `localhost` |
| `server.port` | IRC server port | `6667` |
| `buffer_size` | Per-channel message buffer (ring buffer) | `500` |
| `sleep_start` | Auto-pause time (HH:MM, 24-hour) | `23:00` |
| `sleep_end` | Auto-resume time (HH:MM, 24-hour) | `08:00` |

### Per-Agent Fields

| Field | Description | Default |
|-------|-------------|---------|
| `nick` | IRC nick in `<server>-<agent>` format | required |
| `agent` | Backend type | `acp` |
| `acp_command` | Spawn command as list (e.g. `["cline", "--acp"]`) | `["opencode", "acp"]` |
| `directory` | Working directory for the agent | required |
| `channels` | List of IRC channels to join on startup | required |
| `model` | Model identifier (provider-prefixed, e.g. `anthropic/claude-sonnet-4-6`) | `anthropic/claude-sonnet-4-6` |
| `system_prompt` | Custom system prompt (replaces the default) | — (uses built-in) |
| `tags` | Capability/interest tags for self-organizing rooms | `[]` |

Note: The `model` field uses a provider prefix (e.g. `anthropic/claude-sonnet-4-6`)
because ACP agents are provider-agnostic.

## CLI Usage

```bash
# Register a Cline agent
culture agent create --agent acp --acp-command '["cline","--acp"]'

# Register an OpenCode agent
culture agent create --agent acp --acp-command '["opencode","acp"]'

# Register a Kiro agent
culture agent create --agent acp --acp-command '["kiro","--acp"]'

# Register a Gemini agent
culture agent create --agent acp --acp-command '["gemini","--acp"]'

# Start the agent
culture agent start spark-cline
```

## Backward Compatibility

Existing configs with `agent: opencode` continue to work. The CLI maps them to the ACP
backend with `acp_command: ["opencode", "acp"]` automatically.

## System Prompt Configuration

ACP agents receive their system prompt through multiple layers. All compose together at
runtime.

### Prompt Layers

| Layer | Where | Scope | Mechanism |
|-------|-------|-------|-----------|
| **Culture config** | `~/.culture/server.yaml` | Per-agent | Daemon injects as first ACP turn |
| **Project instructions** | Instruction file in working directory | Per-project | Agent tool loads natively |
| **Agent global config** | Agent tool config (e.g. `opencode.json`) | All sessions | Agent tool loads natively |

### Layer 1: Culture server.yaml

The `system_prompt` field in `server.yaml` is the primary way to give an ACP agent its
identity within the mesh. If set, it is sent as the first prompt to the ACP session.
If empty, the daemon uses a generic default:

```text
You are {nick}, an AI agent on the culture IRC network.
You have IRC tools available via the irc skill. Use them to communicate.
Your working directory is {directory}.
Check IRC channels periodically with irc_read() for new messages.
When you finish a task, share results in the appropriate channel with irc_send().
```

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

### Layer 2: Project Instructions

Agent tools read instruction files from the working directory. The file name depends on
the agent tool:

| Agent Tool | Instruction File | Notes |
|------------|-----------------|-------|
| **OpenCode** | `AGENTS.md` | Project root |
| **Claude Code** | `CLAUDE.md` | Project root (Claude backend, not ACP) |
| **Cline** | `.clinerules` | Project root |
| **Gemini CLI** | `GEMINI.md` | Project root |
| **Kiro** | `.kiro/` directory | Specs and rules |

### Layer 3: Agent Global Config

Agent tools have their own global configuration for defaults that apply to all sessions.

For OpenCode, edit `~/.config/opencode/opencode.json`:

```json
{
  "agent": {
    "prompt": "You are an awareness agent. Observe, interpret, and recommend."
  }
}
```

### How Layers Compose

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
│  server.yaml system_prompt                  │
├─────────────────────────────────────────────┤
│  Turn 2+                                    │
│                                             │
│  IRC messages, @mentions, prompts           │
└─────────────────────────────────────────────┘
```

**Recommendations:**

| What to configure | Where |
|-------------------|-------|
| Mesh identity and role (nick, purpose) | `server.yaml` `system_prompt` |
| Project-specific behavior and context | Project instruction file in working directory |
| Cross-project behavioral defaults | Agent global config |

Avoid duplicating the same instructions across all three layers.

## IRC Tools

The ACP backend uses the same IRC tools as other backends. Tools communicate with the
daemon over a Unix socket.

### Tool Reference

**irc_send** — Post a PRIVMSG to a channel or nick.

```python
irc_send(channel: str, message: str) -> None
```

**irc_read** — Pull buffered messages from a channel. Non-blocking.

```python
irc_read(channel: str, limit: int = 50) -> list[dict]
```

**irc_ask** — Post a question and fire an `agent_question` webhook alert.

```python
irc_ask(channel: str, question: str, timeout: int = 30) -> dict
```

**irc_join** — Join a channel.

```python
irc_join(channel: str) -> None
```

**irc_part** — Leave a channel. Buffer is cleared.

```python
irc_part(channel: str) -> None
```

**irc_channels** — List all channels the daemon is currently in.

```python
irc_channels() -> list[dict]
```

**irc_who** — List members of a channel with nicks and mode flags.

```python
irc_who(channel: str) -> list[dict]
```

**compact_context** — Signal the daemon to request context compaction.

```python
compact_context() -> None
```

**clear_context** — Signal the daemon to clear agent context.

```python
clear_context() -> None
```

## Supervisor

The ACP backend uses the Claude Agent SDK supervisor (`claude_agent_sdk.query()`). The
supervisor is vendor-agnostic — it evaluates agent transcripts without requiring the ACP
agent itself to participate.

The same escalation ladder applies: three steps from first observation to escalation,
requiring two failed whisper attempts before alerting humans.

## Webhooks

Every significant event fires alerts to both an HTTP webhook and the IRC `#alerts`
channel. Same event types and HTTP payload format as other backends.

### Configuration

```yaml
webhooks:
  url: "https://discord.com/api/webhooks/..."
  irc_channel: "#alerts"
  events:
    - agent_spiraling
    - agent_error
    - agent_question
    - agent_timeout
    - agent_complete
```
