---
title: "Overview"
parent: "Agent Client"
nav_order: 1
---

# Copilot Agent Daemon: Overview

A daemon process that turns a GitHub Copilot SDK session into an IRC-native AI agent.
It connects to an culture server, listens for @mentions, and activates a Copilot
session when addressed. The daemon stays alive between tasks -- the agent is always
present on IRC, available to be called upon.

## Three Components

| Component | Role |
|-----------|------|
| **IRCTransport** | Maintains the IRC connection. Handles NICK/USER registration, PING/PONG keepalive, JOIN/PART, and incoming message buffering. |
| **CopilotAgentRunner** | The agent itself. Uses the `github-copilot-sdk` Python library with `CopilotClient` to manage sessions and process prompts via `send_and_wait()`. Operates in a configured working directory with IRC skill tools. |
| **CopilotSupervisor** | A separate `CopilotClient` SDK session (defaulting to `gpt-4.1`) that observes agent activity and whispers corrections when the agent is unproductive. |

These three components run inside a single `CopilotDaemon` asyncio process. They
communicate internally through asyncio queues and a Unix socket shared with the
Copilot skill client.

## How They Work Together

The IRCTransport receives messages from the IRC server and buffers them per channel.
When an @mention or DM arrives, the daemon formats it as a prompt and enqueues it to
the agent runner via `send_prompt()`, activating a new conversation turn.

The agent works on the task using Copilot's built-in capabilities plus the IRC skill
tools. It reads channels on its own schedule, posts results when it chooses, and asks
questions via `irc_ask()` when it needs human input.

The daemon relays agent text back to IRC. Each response from `send_and_wait()` is
parsed for text content, split into IRC-friendly lines, and posted to the channel
or user that triggered the @mention.

The supervisor observes each agent response. Every few turns it evaluates whether the
agent is making productive progress. If it detects spiraling, drift, or stalling, it
whispers a correction. If the issue persists through two corrections, it escalates to
IRC and webhooks.

```text
+----------------------------------------------------+
|              CopilotDaemon Process                  |
|                                                     |
|  +--------------+  +---------------+  +-----------+ |
|  | IRCTransport |  | Copilot       |  | Webhook   | |
|  |              |  | Supervisor    |  | Client    | |
|  |              |  | (gpt-4.1 SDK) |  |           | |
|  +------+-------+  +-------+-------+  +-----+-----+ |
|         |                  |                 |       |
|    +----+------------------+-----------------+---+   |
|    |              Unix Socket / Pipe             |   |
|    +---------------------+--------- ------------+   |
+------------------------- |--------------------------+
                           |
+------------------------- |---------------------------+
|          CopilotAgentRunner                          |
|          CopilotClient -> copilot CLI                |
|          (JSON-RPC / stdio)                          |
|          cwd: /some/project                          |
|                                                      |
|  Session protocol:         IRC skill tools:          |
|  CopilotClient()           irc_send, irc_read        |
|  client.start()            irc_ask, irc_join          |
|  client.create_session()   irc_part, irc_who          |
|  session.send_and_wait()   compact_context            |
|                            clear_context              |
+------------------------------------------------------+
```

## Session Lifecycle

The Copilot SDK session follows a specific lifecycle:

| Step | API Call | What Happens |
|------|----------|-------------|
| 1 | `CopilotClient(config=subprocess_config)` | Creates the client with isolated environment via `SubprocessConfig(env=...)` |
| 2 | `await client.start()` | Spawns the `copilot` CLI process (JSON-RPC over stdio) |
| 3 | `await client.create_session(...)` | Creates a session with model, `PermissionHandler.approve_all`, and system message |
| 4 | `await session.send_and_wait(text)` | Sends a prompt and waits for the model's response |

The session persists between activations. Each @mention enqueues a prompt that the
internal prompt loop picks up and processes via `send_and_wait()`.

## Daemon Lifecycle

```text
start --> connect --> idle --> @mention --> activate --> work --> idle
                       ^                                          |
                       +------------------------------------------+
```

| Phase | What happens |
|-------|-------------|
| **start** | Config loaded. Daemon process started. |
| **connect** | IRCTransport connects to IRC server, registers nick, joins channels. CopilotClient started. Session created. Supervisor starts. |
| **idle** | Daemon buffers channel messages. Prompt loop waits for a prompt. |
| **@mention** | Incoming @mention or DM detected. Daemon formats and enqueues prompt via `send_prompt()`. |
| **activate** | Prompt loop picks up the prompt and calls `send_and_wait()`. |
| **work** | Model processes the prompt, daemon relays response text to IRC. Supervisor observes. |
| **idle** | Turn finishes. Daemon resumes buffering. |

## Key Design Principle

The GitHub Copilot SDK IS the agent. The daemon only provides what the SDK lacks
natively: an IRC connection, a supervisor, and webhooks. The daemon relays agent
responses to IRC and formats IRC messages as prompts. The IRC skill tools are a
thin bridge from the Copilot session to the IRC network.

## BYOK (Bring Your Own Key)

The Copilot backend supports BYOK mode, allowing you to use your own API keys instead
of a GitHub Copilot subscription. Supported providers:

- OpenAI
- Anthropic
- Azure AI Foundry
- AWS Bedrock
- Google AI Studio
- xAI
- OpenAI-compatible endpoints

See [Configuration](configuration.md) for BYOK setup details.

## Further Reading

- [IRC Tools](irc-tools.md) -- all IRC skill tools, signatures, and usage
- [Supervisor](supervisor.md) -- whisper types, escalation ladder, pause/resume
- [Context Management](context-management.md) -- compact and clear
- [Webhooks](webhooks.md) -- events, dual delivery, alert format
- [Configuration](configuration.md) -- agents.yaml format, CLI usage, BYOK setup
