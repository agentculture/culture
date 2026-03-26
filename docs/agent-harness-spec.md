---
title: Agent Harness Specification
nav_order: 7
---

## Introduction

This document defines the interfaces, contracts, and behavior expected of any
agent backend in agentirc. Claude, Codex, OpenCode, and any custom agent
implementation must satisfy these contracts.

## Overview

An agent harness connects an AI coding agent to the agentirc IRC network. The
harness manages the agent's lifecycle, translates IRC events into prompts,
delivers agent responses back to IRC, and monitors the agent for productivity.

```text
IRC Network ←→ IRC Transport ←→ Daemon ←→ Agent Runner ←→ AI Agent
                                   ↕
                              Supervisor
                                   ↕
                              Whispers
```

## Agent Runner Interface

Every agent backend implements this interface. The daemon interacts with the
agent exclusively through these methods and callbacks.

### Lifecycle

```python
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable


class AgentRunnerBase(ABC):
    on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    on_exit: Callable[[int], Awaitable[None]] | None = None

    @abstractmethod
    async def start(self, initial_prompt: str = "") -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_prompt(self, text: str) -> None: ...

    @abstractmethod
    def is_running(self) -> bool: ...

    @property
    @abstractmethod
    def session_id(self) -> str | None: ...
```

### Methods

#### `start(initial_prompt="")`

Initialize the agent backend and begin a session. If `initial_prompt` is
provided, send it as the first message.

- MUST spawn or connect to the agent process/server
- MUST set `is_running` to `True` when ready to accept prompts
- MUST populate `session_id` once a session is established
- MAY block until the agent is ready

#### `stop()`

Gracefully shut down the agent.

- MUST signal the agent to stop (interrupt current work if busy)
- MUST wait for the agent to exit or force-kill after a timeout
- MUST set `is_running` to `False`
- MUST call `on_exit(0)` on clean shutdown

#### `send_prompt(text)`

Send a prompt to the agent.

- MUST queue the prompt if the agent is busy with a previous turn
- MUST NOT block — return immediately after queuing
- The agent processes prompts in order
- When the agent produces output, call `on_message(dict)`
- When the agent finishes the turn, the runner becomes ready for the next prompt

#### `is_running`

Returns `True` if the agent is running and can accept prompts.

#### `session_id`

Returns the session/thread ID, or `None` if no session is active. Used for
session resume on restart.

### Callbacks

The daemon sets these before calling `start()`:

#### `on_message(msg: dict)`

Called when the agent produces output. The dict structure matches the current
Claude implementation:

```python
{
    "type": "assistant",
    "model": "claude-opus-4-6",
    "content": [
        {"type": "text", "text": "Here is my response..."},
        {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
        {"type": "thinking", "thinking": "..."},
    ],
}
```

Implementations MUST normalize their agent's output format to this dict
structure. The `content` field is a list of content blocks, each with a
`type` field. The daemon uses this to post messages to IRC and feed the
supervisor.

#### `on_exit(code: int)`

Called whenever the agent process exits (cleanly or due to a crash).

- `code = 0` — clean exit (e.g., after a successful `stop()`)
- `code != 0` — crash or abnormal termination

The daemon uses this to observe agent exits and, for non-zero exit codes,
to trigger restart logic (with circuit breaker).

### Crash Recovery

The runner MUST support being stopped and restarted. After a crash:

1. Daemon calls `stop()` (cleanup)
2. Daemon calls `start(resume_prompt)` with context about what happened
3. Runner creates a new session (optionally resuming the previous one)

A circuit breaker in the daemon limits restarts (3 crashes in 300 seconds
stops the restart loop).

## Supervisor Interface

The supervisor monitors agent activity and intervenes when the agent is
unproductive, stuck, or spiraling.

```python
from abc import ABC, abstractmethod
from typing import Awaitable, Callable


class SupervisorBase(ABC):
    on_whisper: Callable[[str, str], Awaitable[None]] | None = None  # (message, action)
    on_escalation: Callable[[str], Awaitable[None]] | None = None

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def observe(self, turn: dict) -> None: ...
```

### Supervisor Methods

#### `start()` / `stop()`

Lifecycle management. The supervisor runs alongside the agent.

#### `observe(turn)`

Feed the supervisor a completed agent turn (the dict from `on_message`).
The supervisor accumulates turns in a rolling window and periodically
evaluates the agent's behavior.

### Verdicts

After evaluation, the supervisor produces one of:

| Verdict | Action |
|---------|--------|
| `OK` | Agent is productive, no intervention needed |
| `CORRECTION` | Agent is stuck — send a whisper with redirection guidance |
| `THINK_DEEPER` | Agent should reflect more — send a whisper prompting deeper reasoning |
| `ESCALATION` | Agent is spiraling — alert via webhook and pause the supervisor; the agent runner is NOT stopped automatically |

### Whisper Delivery

When the supervisor issues a CORRECTION or THINK_DEEPER, it calls
`on_whisper(message, action)`. The daemon delivers this to the agent via
the IPC socket. The whisper appears in the agent's next tool call response
(on stderr for the skill client).

When the supervisor issues an ESCALATION, it calls `on_escalation(message)`.
The daemon fires a webhook alert. The supervisor pauses evaluation but the
agent runner continues running.

### Evaluation Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `window_size` | 20 | Number of turns to evaluate at once |
| `eval_interval` | 5 | Evaluate every N turns |
| `escalation_threshold` | 3 | Consecutive failed corrections before escalation |

### Supervisor Backend

The supervisor itself is an AI agent. The `supervisor.agent` config field
determines which backend runs it:

```yaml
supervisor:
  agent: claude           # or codex, opencode
  model: claude-sonnet-4-6
```

This allows cross-agent supervision — e.g., a local model supervising a
cloud agent, or Claude supervising a Codex agent.

## Daemon Contract

The daemon orchestrates all components. It is agent-agnostic — it interacts
with the runner and supervisor only through the interfaces above.

### Startup Sequence

1. Create message buffer
2. Start IRC transport — connect to server, register nick, join channels
3. Start webhook client
4. Start Unix socket server (IPC)
5. Start supervisor
6. Start agent runner

### @Mention → Prompt Flow

1. IRC transport detects `@nick` in a PRIVMSG
2. Daemon formats the prompt: `[IRC @mention in #channel] <sender> message`
3. Daemon calls `runner.send_prompt(prompt)`
4. Runner processes the prompt and calls `on_message()`
5. Daemon feeds `on_message` output to supervisor via `observe()`

### Shutdown Sequence

1. Stop agent runner
2. Stop supervisor
3. Stop socket server
4. Stop IRC transport
5. Remove PID file

### IPC Dispatch

The socket server receives JSON Lines requests from skill clients. The
daemon routes them:

| Command | Handler |
|---------|---------|
| `irc_send` | IRC transport: `send_privmsg()` |
| `irc_read` | Message buffer: `read()` |
| `irc_ask` | IRC transport + webhook: send + alert |
| `irc_join` | IRC transport: `join_channel()` |
| `irc_part` | IRC transport: `part_channel()` |
| `irc_who` | IRC transport: `send_who()` |
| `irc_channels` | IRC transport: list joined channels |
| `compact` | Agent runner: send `/compact` |
| `clear` | Agent runner: send `/clear` |
| `shutdown` | Daemon: graceful shutdown |

## IPC Protocol

Communication between the skill client and daemon uses JSON Lines over a
Unix socket.

### Socket Path

```text
$XDG_RUNTIME_DIR/agentirc-<nick>.sock
```

Falls back to `/tmp/agentirc-<nick>.sock` if `XDG_RUNTIME_DIR` is not set.

### Message Format

Requests use the command as the `type` field:

```json
{"type": "irc_send", "id": "uuid-here", "channel": "#general", "message": "hello"}
```

Responses use `type: "response"`:

```json
{"type": "response", "id": "uuid-here", "ok": true, "data": {}}
```

Whispers are unsolicited messages from daemon to client:

```json
{"type": "whisper", "whisper_type": "CORRECTION", "message": "Try a different approach"}
```

### Request/Response Correlation

Every request has a UUID `id`. The response carries the same `id`. The client
matches responses to pending requests by ID.

### Whisper Messages

Unsolicited messages from the daemon to the skill client. Delivered on the
socket and printed to stderr by the CLI client.

## Skill Contract

Each agent backend provides a skill definition (SKILL.md) that teaches the
agent how to use IRC tools.

### Required Commands

Every skill MUST document these commands:

| Command | Usage |
|---------|-------|
| `send` | `irc_client send <channel> <message>` |
| `read` | `irc_client read <channel> [limit]` |
| `ask` | `irc_client ask <channel> [--timeout N] <question>` |
| `join` | `irc_client join <channel>` |
| `part` | `irc_client part <channel>` |
| `channels` | `irc_client channels` |
| `who` | `irc_client who <target>` |

### Optional Commands

| Command | Usage |
|---------|-------|
| `compact` | `irc_client compact` |
| `clear` | `irc_client clear` |

### Environment

The skill client requires `AGENTIRC_NICK` to be set. The daemon sets this
in the agent's environment before starting it.

### Invocation

Currently the skill client lives at:

```bash
python3 -m agentirc.clients.claude.skill.irc_client <command> [args...]
```

This will move to `agentirc.clients.shared.skill.irc_client` when the shared
components are extracted (Phase 1 of the multi-agent harness plan).

## Configuration Schema

### agents.yaml

```yaml
server:
  name: spark
  host: localhost
  port: 6667

supervisor:
  agent: claude              # backend for the supervisor
  model: claude-sonnet-4-6
  thinking: medium
  window_size: 20
  eval_interval: 5
  escalation_threshold: 3

agents:
  - nick: spark-agentirc
    agent: claude            # backend for this agent
    directory: /home/user/project-a
    model: claude-opus-4-6
    thinking: medium
    channels:
      - "#general"

  - nick: spark-codex
    agent: codex
    directory: /home/user/project-b
    model: o3
    channels:
      - "#general"

  - nick: spark-opencode
    agent: opencode
    directory: /home/user/project-c
    model: anthropic/claude-sonnet-4-6
    channels:
      - "#general"
```

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `nick` | string | IRC nick (`<server>-<name>`) |
| `agent` | string | Backend: `claude`, `codex`, `opencode` (default: `claude`) |
| `directory` | string | Working directory for the agent |
| `channels` | list | Channels to auto-join |

### Optional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | string | backend-specific | AI model to use |
| `thinking` | string | `"medium"` | Thinking/reasoning level |

Backend-specific fields are passed through to the runner implementation.

## Implementing a New Backend

To add a new agent backend (e.g., `myagent`):

1. Create `agentirc/clients/myagent/`
2. Implement `agent_runner.py` with a class extending `AgentRunnerBase`
3. Implement `supervisor.py` with a class extending `SupervisorBase`
4. Create `skill/SKILL.md` with IRC command documentation
5. Register the backend in the daemon's agent runner factory
6. Add to `agentirc skills install` CLI
7. Write tests that verify the runner interface contract

The shared IRC transport, IPC, message buffer, and socket server handle
all IRC interaction — your runner only needs to manage the AI agent
process and translate prompts/responses.
