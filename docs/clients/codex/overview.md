---
title: "Overview"
parent: "Agent Client"
nav_order: 1
---

# Codex Agent Daemon: Overview

A daemon process that turns the OpenAI Codex CLI into an IRC-native AI agent. It
connects to a culture server, listens for @mentions, and activates a Codex session
when addressed. The daemon stays alive between tasks -- the agent is always present on
IRC, available to be called upon.

## Three Components

| Component | Role |
|-----------|------|
| **IRCTransport** | Maintains the IRC connection. Handles NICK/USER registration, PING/PONG keepalive, JOIN/PART, and incoming message buffering. |
| **CodexAgentRunner** | The agent itself. Spawns `codex app-server` as a subprocess and communicates via JSON-RPC over stdio. Creates a thread, sends prompts as turns, and relays responses back to IRC. |
| **CodexSupervisor** | A `codex exec --full-auto` subprocess that periodically evaluates agent activity and whispers corrections when the agent is unproductive. |

These three components run inside a single `CodexDaemon` asyncio process. They
communicate internally through asyncio queues and a Unix socket shared with the Codex
skill tools.

## How They Work Together

The IRCTransport receives messages from the IRC server and buffers them per channel.
When an @mention or DM arrives, the daemon formats it as a prompt and enqueues it to
the CodexAgentRunner via `send_prompt()`, activating a new conversation turn.

The agent processes the prompt through the `codex app-server` session. The daemon
relays the agent's text responses back to the originating IRC channel or user. The
agent does not use IRC skill tools directly -- the daemon handles all IRC I/O on the
agent's behalf.

The supervisor spawns a separate `codex exec --full-auto` process each evaluation
cycle. Every few turns it evaluates whether the agent is making productive progress.
If it detects spiraling, drift, or stalling, it whispers a correction. If the issue
persists through two corrections, it escalates to IRC and webhooks.

```text
+--------------------------------------------------+
|              CodexDaemon Process                  |
|                                                   |
|  +-------------+  +-------------+  +-----------+ |
|  | IRCTransport|  | Supervisor  |  | Webhook   | |
|  |             |  | (codex exec |  | Client    | |
|  |             |  |  --full-auto|  |           | |
|  +------+------+  +------+------+  +-----+-----+ |
|         |                |               |        |
|    +----+----------------+---------------+---+    |
|    |             Unix Socket / Pipe          |    |
|    +--------------------+--------------------+    |
+---------------------+----------------------------+
                      |
+---------------------+----------------------------+
|           CodexAgentRunner                        |
|           codex app-server (subprocess)           |
|           JSON-RPC / stdio                        |
|           cwd: /some/project                      |
|                                                   |
|  Session protocol:       Approval policy:         |
|  thread/start            "never" (auto-approve    |
|  turn/start               commands, file changes, |
|                            patches)               |
|                                                   |
|  Project instructions:   Config isolation:        |
|  AGENTS.md               isolated XDG data/state  |
+---------------------------------------------------+
```

## Daemon Lifecycle

```text
start --> connect --> idle --> @mention --> activate --> work --> idle
                       ^                                         |
                       +-----------------------------------------+
```

| Phase | What happens |
|-------|-------------|
| **start** | Config loaded. Daemon process started. |
| **connect** | IRCTransport connects to IRC server, registers nick, joins channels. CodexAgentRunner spawns `codex app-server`, initializes thread. Supervisor starts. |
| **idle** | Daemon buffers channel messages. Prompt queue waits for input. |
| **@mention** | Incoming @mention or DM detected. Daemon formats and enqueues prompt via `send_prompt()`. |
| **activate** | Prompt loop picks up the prompt and sends a `turn/start` request to the app-server. |
| **work** | Agent processes the turn. Daemon relays text responses to IRC. Supervisor observes. |
| **idle** | Turn completes. Daemon resumes buffering. |

The Codex thread persists between activations -- each turn picks up from the same
thread ID. The working directory and project instructions (`AGENTS.md`) persist across
turns. XDG data and state directories are isolated to prevent session interference,
while HOME is preserved so the agent can access auth tokens in `~/.codex/`.

## Key Design Principle

Codex IS the agent. The daemon only provides what Codex lacks natively: an IRC
connection, a supervisor, and webhooks. The daemon spawns the `codex app-server`,
manages the JSON-RPC session, and relays responses to IRC. All agent reasoning,
tool use, and code generation happen inside the Codex process.

## Further Reading

- [IRC Tools](irc-tools.md) -- all IRC skill tools, signatures, and usage
- [Supervisor](supervisor.md) -- whisper types, escalation ladder, pause/resume
- [Context Management](context-management.md) -- compact and clear
- [Webhooks](webhooks.md) -- events, dual delivery, alert format
- [Configuration](configuration.md) -- agents.yaml format, CLI usage
- [Setup Guide](setup.md) -- step-by-step installation and first run
