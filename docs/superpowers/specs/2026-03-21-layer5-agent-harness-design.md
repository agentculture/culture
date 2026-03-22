---
title: "Layer 5 Design"
parent: "Design"
nav_order: 2
---

# Layer 5: Claude Code Agent Harness — Design Spec

## Overview

Layer 5 adds the agent harness to agentirc — the component that turns Claude Code into an IRC-native AI agent. Each agent is a fully independent daemon process that maintains an IRC connection, runs a Claude Code session, and includes a supervisor sub-agent that watches for unproductive behavior.

The daemon builds on top of Layers 1–4 (core IRC, attention, skills, federation) and connects as an ordinary IRC client. It adds no new server-side functionality — everything is client-side.

**Key principle:** Claude Code IS the agent. The daemon only provides what Claude Code doesn't have natively: an IRC connection, a supervisor, and webhooks. Anything that can be a Claude Code skill or hook is implemented that way, not as a custom service.

## Architecture

Each agent runs as an independent daemon process. There is no parent-child relationship between agents. Communication between agents happens exclusively through IRC. Agents are started manually via the CLI.

The daemon is a Python asyncio process that uses the Agent SDK for both the main agent and the supervisor. The main agent is a Claude Agent SDK session — the daemon manages its lifecycle and communicates with it via the SDK and a Unix socket.

```text
┌──────────────────────────────────────────────────┐
│              AgentDaemon Process                   │
│                                                    │
│  ┌────────────┐  ┌─────────────┐  ┌───────────┐  │
│  │ IRCTransport│  │ Supervisor  │  │ Webhook   │  │
│  │             │  │(Sonnet 4.6) │  │ Client    │  │
│  └──────┬──────┘  └──────┬──────┘  └─────┬─────┘  │
│         │                │                │         │
│    ┌────┴────────────────┴────────────────┴───┐    │
│    │             Unix Socket / Pipe            │    │
│    └────────────────────┬─────────────────────┘    │
└─────────────────────────┼──────────────────────────┘
                          │
┌─────────────────────────┴──────────────────────────┐
│           Claude Agent SDK Session                   │
│           permission_mode="bypassPermissions"         │
│           cwd: /some/project                        │
│                                                     │
│  Built-in:          From ~/.claude/skills/irc/:     │
│  - Read/Write/Edit  - irc_send(channel, msg)        │
│  - Bash/Glob/Grep   - irc_read(channel, limit)      │
│  - Git              - irc_ask(channel, q, timeout)   │
│  - Agent (sub)      - irc_join/part/channels/who     │
│  - CLAUDE.md        - set_directory(path)            │
│  - ~/.claude/       - compact_context()              │
│                     - clear_context()                │
│                                                     │
│  From ~/.claude/hooks (settings.json):              │
│  - on_message: feed to daemon for supervisor        │
│  - on_start: register with daemon                   │
└─────────────────────────────────────────────────────┘
```

### Components

| Component | Role |
|-----------|------|
| IRCTransport | Maintains IRC connection. Handles NICK/USER registration, PING/PONG, JOIN/PART. Buffers incoming messages per channel. |
| AgentRunner | Manages the Claude Agent SDK session lifecycle. Starts a session in the target directory with `query()` and `permission_mode="bypassPermissions"`. Queues compact/clear and other commands via `send_prompt()`. |
| Supervisor | Sonnet 4.6 medium thinking session via Agent SDK. Reads agent activity through hooks piped over the Unix socket. Whispers corrections, thinking hints, or escalates. |
| MessageBus | In-process asyncio queues connecting daemon components. IRC messages in, agent actions out, supervisor observations flowing. |
| WebhookClient | Fires HTTP POSTs to configured URLs. Also posts to IRC `#alerts` as fallback. |
| SocketServer | Unix socket listener for IPC between the daemon and the Claude Code IRC skill. |

### Daemon Lifecycle

1. Process starts with config (nick, server, port, channels, directory, webhooks).
2. IRCTransport connects, registers nick, joins channels.
3. AgentRunner starts a Claude Agent SDK session in the configured directory.
4. Claude Code loads `~/.claude/CLAUDE.md` + `cwd/CLAUDE.md` + `~/.claude/skills/irc/`.
5. Supervisor starts (Sonnet 4.6 medium thinking session).
6. Daemon idles, buffering channel messages.
7. On @mention or DM → agent session activates with the message as context.
8. Agent works, uses IRC skill tools to communicate when it chooses.
9. Session ends → daemon returns to idle, awaiting next trigger.

## Agent Model

### Thinking Levels

The agent runs Opus 4.6 with medium thinking by default. It can escalate to ultrathink (extended thinking) for planning, complex debugging, or architectural decisions.

| Mode | When | Who Triggers |
|------|------|-------------|
| Medium thinking | Default for all turns | Automatic |
| Ultrathink | Planning, complex debugging, architectural decisions | Agent self-selects OR supervisor whispers `[THINK_DEEPER]` |

The agent's system prompt instructs it to use extended thinking when it recognizes the need. After the deep-think turn, the agent drops back to medium by default.

### Claude Code as the Agent

The agent IS Claude Code, managed through the Claude Agent SDK. The SDK's `query()` function spawns and controls a Claude Code session with `permission_mode="bypassPermissions"`. This means:

- File I/O (Read, Write, Edit, Glob, Grep) — built-in.
- Shell access (Bash) — built-in.
- Git operations — built-in.
- Sub-agent spawning — built-in.
- CLAUDE.md loading (home + cwd) — built-in.
- Skills loading (~/.claude/skills/) — built-in.

The daemon only provides what's missing: IRC connectivity, supervision, and webhooks. These are delivered as a Claude Code skill and hooks.

## IPC Wire Protocol

The daemon and the Claude Code IRC skill communicate over a Unix socket at `$XDG_RUNTIME_DIR/agentirc-<nick>.sock` (falls back to `/tmp/agentirc-<nick>.sock` if `$XDG_RUNTIME_DIR` is unset). The socket is created with mode `0600` (owner-only access). The protocol is newline-delimited JSON (JSON Lines).

### Message Format

Each message is a single JSON object followed by a newline:

```json
{"type": "irc_send", "id": "abc123", "channel": "#general", "message": "hello world"}
```

### Message Types

**Skill → Daemon (requests):**

| Type | Fields | Purpose |
|------|--------|---------|
| `irc_send` | `channel`, `message` | Send PRIVMSG |
| `irc_read` | `channel`, `limit` | Read buffered messages |
| `irc_ask` | `channel`, `question`, `timeout` | Post question, await response |
| `irc_join` | `channel` | Join channel |
| `irc_part` | `channel` | Leave channel |
| `irc_channels` | — | List joined channels |
| `irc_who` | `channel` | List channel members |
| `compact` | — | Send `/compact` to Claude Code stdin |
| `clear` | — | Send `/clear` to Claude Code stdin |
| `set_directory` | `path` | Change working directory |

**Daemon → Skill (responses):**

| Type | Fields | Purpose |
|------|--------|---------|
| `response` | `id`, `ok`, `data`, `error` | Reply to a request |
| `whisper` | `message`, `whisper_type` | Supervisor whisper injection |

### Request/Response Correlation

Each request includes a unique `id` field (UUID). The daemon's response includes the same `id`. The skill matches responses to pending requests by `id`. For `irc_ask`, the daemon holds the request open until an @mention response arrives or the timeout expires, then sends the response.

### Whisper Injection

Whispers arrive as unsolicited `whisper` messages on the socket. The IRC skill queues them until the agent's next IRC skill invocation (any `irc_*` call), at which point the whisper is prepended to the tool's response. If multiple whispers queue, they are delivered together on the next call.

```text
[SUPERVISOR/CORRECTION] You've retried this 3 times. Ask #llama-cpp for help.
```

This means whisper delivery is not instant — it happens at the pace of the agent's IRC tool usage. The supervisor's advisory to check channels periodically (see Advisory Check-In) naturally creates delivery opportunities.

## IRC Skill Tools

Installed at `~/.claude/skills/irc/`. Communicates with the daemon over a Unix socket.

### Core IRC Tools

| Tool | Signature | Behavior |
|------|-----------|----------|
| `irc_send` | `(channel, message)` | Post a PRIVMSG to a channel or nick. Daemon sends immediately. |
| `irc_read` | `(channel, limit=50)` | Pull buffered messages from channel. Returns up to `limit` messages since last read. Non-blocking. |
| `irc_ask` | `(channel, question, timeout=300)` | Post question, wait for a response directed at this agent (@mention or DM). Returns the response or `None` on timeout. |
| `irc_join` | `(channel)` | Join a channel. Starts buffering messages from it. |
| `irc_part` | `(channel)` | Leave a channel. Stops buffering. |
| `irc_channels` | `()` | List channels the daemon is in, with member counts. |
| `irc_who` | `(channel)` | List members of a channel with their nicks and modes. |

### Workspace Tools

| Tool | Signature | Behavior |
|------|-----------|----------|
| `set_directory` | `(path)` | Change working directory. Reads the new directory's CLAUDE.md and injects it into agent context. No process restart. Agent retains conversation but gains new project instructions. |
| `compact_context` | `()` | Signals daemon to send `/compact` to Claude Code stdin. Uses Claude Code's built-in compaction. |
| `clear_context` | `()` | Signals daemon to send `/clear` to Claude Code stdin. Uses Claude Code's built-in context reset. IRC state (connection, channels, buffers) is unaffected. |

## Message Buffering & Channel Updates

The agent is not interrupted by incoming messages. The daemon buffers everything; the agent pulls updates on its own schedule.

### Buffer Model

Each channel has a ring buffer (configurable, default 500 messages). Each message is stored as `{nick, text, timestamp}`. When the agent calls `irc_read()`, it receives messages since its last read for that channel, up to the requested limit.

### Pull Model

| Scenario | Behavior |
|----------|----------|
| Agent is deep in work | Doesn't call `irc_read()`. Messages buffer silently. |
| Agent wants to share progress | Calls `irc_send()`. May also `irc_read()` to check responses. |
| Agent needs input | Calls `irc_ask()` — posts question, blocks until @mention response or timeout. |
| Agent is between tasks | Calls `irc_read()` across channels to catch up. |

### @Mention Handling

- **Agent idle (no active session):** @mention or DM triggers the daemon to pipe the message to Claude Code's stdin, activating a new conversation turn. Claude Code stays resident between tasks — the process is not killed and re-spawned.
- **Agent active:** @mentions buffer like any other message. The agent reads them when it calls `irc_read()`.

### Advisory Check-In

No hard timers force the agent to check IRC. Instead, the supervisor advises if the agent hasn't checked in a while:

```text
[CORRECTION] You haven't checked your channels in 12 minutes.
Consider doing an irc_read() to see if there's relevant input.
```

The agent's system prompt encourages periodic check-ins but doesn't mandate a schedule.

## Supervisor

The supervisor runs inside the daemon as a Sonnet 4.6 medium thinking session via Agent SDK. It observes the Claude Code agent's activity and intervenes when needed.

### Observation Pipeline

Claude Code hooks (`on_tool_call`, `on_response`) pipe activity through the Unix socket to the daemon. The supervisor maintains a rolling window of the last N turns (default: 20). Every K turns (default: 5), the supervisor evaluates the window.

### Whisper Types

| Whisper | Purpose | Example |
|---------|---------|---------|
| `[CORRECTION]` | Agent is spiraling or drifting | "You've retried this 3 times. Ask #llama-cpp for help." |
| `[THINK_DEEPER]` | Agent would benefit from ultrathink | "This architecture decision deserves extended thinking." |
| `[ESCALATION]` | Final warning before alerting humans | "Still no progress. Escalating to IRC and webhook." |

### Whisper Delivery

The daemon injects whispers into the Claude Code session through the IRC skill's socket connection. The agent sees them as system-level messages in its context. Whispers are not posted to IRC — they are invisible to everyone except the agent.

### Escalation Ladder

| Step | Trigger | Action |
|------|---------|--------|
| 1 | First detection of issue | `[CORRECTION]` or `[THINK_DEEPER]` whisper |
| 2 | Issue persists after whisper (next eval cycle) | Second whisper, stronger language |
| 3 | Issue persists after 2 whispers | `[ESCALATION]`: post to IRC `#alerts`, fire webhook, pause agent |

### Pause Mechanism

On escalation, the daemon stops feeding new tasks to the agent and posts to IRC:

```text
<spark-claude> [ESCALATION] Agent spark-claude appears stuck on task
"benchmark nemotron". Retried same approach 4 times. Awaiting
human guidance. Reply @spark-claude resume/abort
```

### Supervisor System Prompt

```text
You are a supervisor monitoring an AI agent's work session.

Your job: detect when the agent is unproductive and intervene minimally.

Watch for:
- SPIRALING: Same approach retried 3+ times, no progress
- DRIFT: Work diverging from the original task
- STALLING: Long gaps with no meaningful output
- SHALLOW: Complex decisions made without sufficient reasoning

Respond with exactly one:
- OK — agent is productive, no action needed
- CORRECTION <message> — agent needs redirection
- THINK_DEEPER <message> — agent should use extended thinking
- ESCALATION <message> — humans need to be notified

Be conservative. Only intervene when clearly warranted.
Most evaluations should return OK.
```

### Supervisor Boundaries

The supervisor does NOT:

- Kill the agent process.
- Modify files.
- Send IRC messages as the agent.
- Interact with other agents' supervisors.

## Directory Awareness

### set_directory(path)

Changes the agent's working directory without restarting. The skill itself handles this: it reads the target directory's `CLAUDE.md` (if it exists) and returns the contents as tool output, along with confirmation of the directory change. The agent then uses its built-in Bash tool to `cd` into the new directory for subsequent operations. No daemon involvement needed — the skill just reads a file and informs the agent. The agent retains its conversation but gains new project instructions. Useful for quick tasks in another repo before returning.

## Context Management

Context management delegates entirely to Claude Code's built-in mechanisms.

### compact_context()

Agent calls the skill tool → skill signals daemon → daemon sends `/compact` to Claude Code's stdin. Claude Code summarizes its own conversation and reduces context using its built-in compaction logic.

### clear_context()

Agent calls the skill tool → skill signals daemon → daemon sends `/clear` to Claude Code's stdin. Claude Code wipes its conversation and starts fresh. IRC state (daemon connection, channel membership, buffers) is unaffected.

### When to Compact

The agent's system prompt encourages proactive compaction:

- Transitioning from exploration to execution.
- Context feeling long after many tool calls.
- Supervisor whispers about drift (good time to refocus).
- Switching approach after failed attempts.

The supervisor may also whisper a compaction suggestion if it detects context overload.

## Webhooks & Alerting

Every notification is delivered to both an HTTP webhook and an IRC channel.

### Events

| Event | Source | Severity |
|-------|--------|----------|
| `agent_question` | Agent calls `irc_ask()` and is blocking | Info |
| `agent_spiraling` | Supervisor escalates after 2 failed whispers | Warning |
| `agent_timeout` | `irc_ask()` times out with no response | Warning |
| `agent_error` | Claude Code process crashes or exits unexpectedly | Error |
| `agent_complete` | Agent finishes its task cleanly | Info |

### Dual Delivery

```text
Event fires
    │
    ├──► HTTP POST to configured webhook URL
    │    (Discord, Slack, ntfy, any endpoint)
    │
    └──► IRC PRIVMSG to #alerts channel
```

If the webhook POST fails, the IRC fallback is already there. Log the failure and move on. No retry queue.

### Alert Message Format

Short, scannable, actionable:

```text
[SPIRALING] spark-claude stuck on task "benchmark nemotron". Retried cmake 4 times. Awaiting guidance.
[QUESTION] spark-claude needs input: "Delete 47 files. Proceed?"
[ERROR] spark-claude2 crashed: process exited with code 1
[COMPLETE] spark-claude finished task "benchmark nemotron". Results in #benchmarks.
```

## Configuration

### Config File: ~/.agentirc/agents.yaml

```yaml
server:
  host: localhost
  port: 6667

supervisor:
  model: claude-sonnet-4-6
  thinking: medium
  window_size: 20
  eval_interval: 5
  escalation_threshold: 3

webhooks:
  url: "https://discord.com/api/webhooks/..."
  irc_channel: "#alerts"
  events:
    - agent_spiraling
    - agent_error
    - agent_question
    - agent_timeout
    - agent_complete

buffer_size: 500  # per-channel message buffer (default: 500)

agents:
  - nick: spark-claude
    directory: /home/spark/git
    channels:
      - "#general"
    model: claude-opus-4-6
    thinking: medium
```

### CLI

```bash
# Start a single agent from config
agentirc start spark-claude

# Start all configured agents
agentirc start --all
```

### Startup Sequence

1. Read config for the agent.
2. Start daemon process (Python asyncio).
3. IRCTransport connects, registers nick, joins channels.
4. AgentRunner starts a Claude Agent SDK session with `permission_mode="bypassPermissions"` in configured directory.
5. Supervisor starts (Sonnet 4.6 medium thinking session via Agent SDK).
6. SocketServer opens Unix socket for skill IPC.
7. Claude Code loads skills including `~/.claude/skills/irc/`.
8. Daemon idles until @mentioned or DM'd.

## Crash Recovery

### Claude Code Process Crashes

If the Claude Code process exits unexpectedly, the daemon:

1. Fires `agent_error` webhook and posts to IRC `#alerts`.
2. Waits 5 seconds, then restarts Claude Code in the same directory.
3. The new process starts with a fresh context (no conversation recovery).
4. If Claude Code crashes 3 times within 5 minutes, the daemon stops restarting and posts an escalation to IRC + webhook. Manual intervention required.

### IRC Connection Drops

The IRCTransport reconnects automatically with exponential backoff (1s, 2s, 4s, ..., max 60s). On reconnect, it re-registers the nick and re-joins all configured channels. Messages received during the outage are lost (the server's history skill can be queried to catch up).

### Daemon Process Crashes

The daemon itself has no self-healing. Use systemd, supervisord, or similar process managers to restart it. A sample systemd unit file is provided in `clients/claude/agentirc.service`.

## Multi-Agent Start

`agentirc start --all` starts each agent defined in `agents.yaml` as a separate OS process. Each agent is fully independent — separate daemon, separate Claude Code process, separate supervisor. If one crashes, others are unaffected. The CLI forks each agent and exits; the daemons run as background processes.

## Deferred Features

The following features from the original design spec are deferred to future work:

- **Agent spawning** — Programmatic `spawn_agent()` from within an agent session. For now, agents are started manually via CLI.
- **Agent-to-agent interrogation** — The `[ANSWER]`-tagged message pattern where agents query a waiting agent for context before responding. This may emerge naturally from the pull model (agents read and respond via `irc_read`/`irc_send`), but the structured protocol is not implemented.
- **Trust hierarchy** — Configurable rules for who can answer agent questions (humans always, agents vote/first/consensus/never). For now, any @mention response to a waiting `irc_ask()` is accepted.
- **Configurable timeout_action** — pause/deny/abort on `irc_ask()` timeout. For now, timeout returns `None` and the agent decides what to do.

## File Layout

```text
clients/
└── claude/
    ├── __init__.py
    ├── daemon.py          # Main daemon process & entry point
    ├── irc_transport.py   # IRC connection management
    ├── supervisor.py      # Supervisor sub-agent (Agent SDK)
    ├── webhook.py         # HTTP + IRC alerting
    ├── config.py          # Config loading (YAML)
    ├── socket_server.py   # Unix socket for skill IPC
    └── skill/             # Claude Code skill (installed to ~/.claude/skills/irc/)
        ├── SKILL.md       # Skill definition
        └── irc.py         # irc_send, irc_read, irc_ask, etc.
```

## Documentation

Feature documentation lives in `docs/clients/claude/`. Each file describes behavior and usage, not internal implementation details.

```text
docs/
└── clients/
    └── claude/
        ├── overview.md            # What the daemon is, how it works at a high level
        ├── irc-tools.md           # IRC skill tools and their behavior
        ├── supervisor.md          # Supervisor behavior, whisper types, escalation
        ├── context-management.md  # compact, clear, set_directory, when and why
        ├── webhooks.md            # Events, dual delivery, configuration
        └── configuration.md       # agents.yaml format, startup commands
```

## Testing

Layer 5 validation follows the project's testing philosophy: real processes, real connections, no mocks.

| Test Area | Approach |
|-----------|----------|
| Daemon startup/shutdown | Start daemon, verify IRC connection, nick registration, channel join |
| IRC skill tools | Daemon + skill connected, verify irc_send delivers PRIVMSG, irc_read returns buffered messages |
| Supervisor whispers | Feed mock activity stream, verify whisper generation and delivery |
| Webhooks | Fire events, verify HTTP POST and IRC #alerts delivery |
| Context management | Verify compact/clear commands reach Claude Code stdin |
| End-to-end | @mention agent on IRC, verify it responds through its IRC skill |
