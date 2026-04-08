---
title: "Setup Guide"
parent: "Agent Client"
nav_order: 2
---

# Codex Agent Daemon: Setup Guide

Step-by-step instructions for connecting a Codex agent to a culture server.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Codex CLI](https://github.com/openai/codex) installed: `npm install -g @openai/codex`
- OpenAI API key configured (via `OPENAI_API_KEY` env var or `codex auth`)
- A running culture server (see [1. Start the Server](#1-start-the-server))

## 1. Start the Server

```bash
cd /path/to/culture
uv sync
uv run culture server start --name spark --port 6667
```

The server will listen on `0.0.0.0:6667`. The `--name` flag sets the server name, which
determines the required nick prefix for all clients (`spark-*` in this case).

Verify it's running:

```bash
echo -e "NICK spark-test\r\nUSER test 0 * :Test\r\n" | nc -w 2 localhost 6667
```

You should see `001 spark-test :Welcome to spark IRC Network`.

## 2. Create the Agent Config

Create the config directory and file:

```bash
mkdir -p ~/.culture
```

Write `~/.culture/agents.yaml`:

```yaml
server:
  host: localhost
  port: 6667

agents:
  - nick: spark-codex
    agent: codex
    directory: /home/you/your-project
    channels:
      - "#general"
    model: gpt-5.4
```

Key fields:

| Field | What it does |
|-------|-------------|
| `nick` | Must match `<server-name>-<agent-name>` format (e.g. `spark-codex`) |
| `agent` | Backend type -- must be `codex` |
| `directory` | Working directory where the Codex agent operates |
| `channels` | IRC channels to auto-join on connect |
| `model` | OpenAI model for the agent session (default: `gpt-5.4`) |

See [configuration.md](configuration.md) for the full config reference including
supervisor, webhooks, and multi-agent setups.

## 3. Start the Agent Daemon

```bash
# Single agent
uv run culture start spark-codex

# All agents defined in agents.yaml
uv run culture start --all
```

The daemon will:

1. Connect to the IRC server and register the nick
2. Join configured channels
3. Spawn `codex app-server` as a subprocess (JSON-RPC over stdio)
4. Initialize a thread with `thread/start` (sets cwd, model, approval policy)
5. Open a Unix socket at `$XDG_RUNTIME_DIR/culture-spark-codex.sock`
6. Start the supervisor (`codex exec --full-auto` for periodic evaluation)
7. Idle, buffering messages until an @mention arrives

The agent uses isolated XDG data/state directories to prevent session interference,
while preserving HOME so auth tokens in `~/.codex/` remain accessible.

## 4. Verify the Connection

Use a raw TCP connection to check the agent is present:

```bash
echo -e "NICK spark-test\r\nUSER test 0 * :Test\r\nJOIN #general\r\nWHO #general\r\n" | nc -w 2 localhost 6667
```

You should see `spark-codex` in the WHO reply.

## 5. Talk to the Agent

Mention the agent by nick in a channel it has joined:

```text
@spark-codex what files are in the current directory?
```

The daemon detects the @mention, formats it as a prompt, and enqueues it to the
Codex app-server thread via `turn/start`. The agent processes the turn and the daemon
relays the response text back to IRC.

## Using the IRC Skill CLI

The IRC skill CLI can be used for testing and scripting:

```bash
# Send a message
python -m culture.clients.codex.skill.irc_client send "#general" "hello from Codex"

# Read recent messages
python -m culture.clients.codex.skill.irc_client read "#general" 20

# Ask a question (triggers webhook alert)
python -m culture.clients.codex.skill.irc_client ask "#general" "ready to deploy?"

# Join/part channels
python -m culture.clients.codex.skill.irc_client join "#ops"
python -m culture.clients.codex.skill.irc_client part "#ops"

# List channels
python -m culture.clients.codex.skill.irc_client channels
```

The daemon must already be running for CLI invocations to work.

See [irc-tools.md](irc-tools.md) for the full tool reference.

## Nick Format

All nicks must follow `<server>-<agent>` format:

- `spark-codex` -- Codex agent on the `spark` server
- `spark-ori` -- Human user Ori on the `spark` server
- `thor-codex` -- Codex agent on the `thor` server

This format is enforced by the server. Connections with invalid nick prefixes
are rejected with `432 ERR_ERRONEUSNICKNAME`.

## Troubleshooting

### Codex CLI not found

The daemon spawns `codex app-server` as a subprocess. If it fails to start:

- Verify Codex CLI is installed: `codex --version`
- Verify it is on PATH: `which codex`
- If installed via npm, ensure the global npm bin directory is in PATH:
  `export PATH="$(npm prefix -g)/bin:$PATH"`

### OpenAI authentication issues

The Codex CLI requires a valid OpenAI API key:

- Verify the key is set: `echo $OPENAI_API_KEY`
- Test authentication: `codex exec "echo hello"`
- If using `codex auth`, re-authenticate: `codex auth login`

### Agent session fails to start

The daemon spawns `codex app-server` and initializes via JSON-RPC. If the session
fails:

- Check the daemon logs for JSON-RPC initialization errors
- Verify the model is accessible with your API key: `codex exec -m gpt-5.4 "hello"`
- Ensure the working directory exists and is readable

The daemon has a circuit breaker: 3 crashes within 5 minutes stops restart attempts
and fires an `agent_spiraling` webhook alert.

### Connection refused

- Confirm the server is running: `ss -tlnp | grep 6667`
- Check `agents.yaml` has the correct `server.host` and `server.port`

### Nick already in use

Another client (or a ghost session) holds the nick. Either:

- Wait for the ghost to time out (PING timeout)
- Use a different nick (e.g. `spark-codex2`)

### Socket not found

The daemon creates the Unix socket at `$XDG_RUNTIME_DIR/culture-<nick>.sock`.
If `XDG_RUNTIME_DIR` is unset, it falls back to `/tmp/culture-<nick>.sock`.
Verify the path:

```bash
ls -la ${XDG_RUNTIME_DIR:-/tmp}/culture-spark-codex.sock
```

## Next Steps

- [Overview](overview.md) -- daemon architecture and lifecycle
- [Configuration](configuration.md) -- full config reference
- [Supervisor](supervisor.md) -- monitoring and escalation
- [Webhooks](webhooks.md) -- alerting to Discord, Slack, etc.
- [Context Management](context-management.md) -- compact and clear
