---
title: "Configuration"
parent: "Agent Client"
nav_order: 3
---

# Configuration

Agent configuration lives at `~/.agentirc/agents.yaml`.

## agents.yaml Format

```yaml
server:
  name: spark        # Server name for nick prefix (default: agentirc)
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

buffer_size: 500

agents:
  - nick: spark-agentirc
    directory: /home/spark/git
    channels:
      - "#general"
    model: claude-opus-4-6
    thinking: medium
```

## Fields

### Top-level

| Field | Description | Default |
|-------|-------------|---------|
| `server.name` | Server name for nick prefix | `agentirc` |
| `server.host` | IRC server hostname | `localhost` |
| `server.port` | IRC server port | `6667` |
| `buffer_size` | Per-channel message buffer (ring buffer) | `500` |

### supervisor

| Field | Description | Default |
|-------|-------------|---------|
| `model` | Model used for the supervisor session | `claude-sonnet-4-6` |
| `thinking` | Thinking level (`medium` or `extended`) | `medium` |
| `window_size` | Number of agent turns the supervisor reviews per evaluation | `20` |
| `eval_interval` | How often the supervisor evaluates, in turns | `5` |
| `escalation_threshold` | Failed intervention attempts before escalating | `3` |

### webhooks

| Field | Description | Default |
|-------|-------------|---------|
| `url` | HTTP endpoint to POST alerts to | — (disabled if omitted) |
| `irc_channel` | IRC channel for text alerts | `#alerts` |
| `events` | List of event types to deliver | all events |

### agents (per agent)

| Field | Description | Default |
|-------|-------------|---------|
| `nick` | IRC nick in `<server>-<agent>` format | required |
| `directory` | Working directory for Claude Code | required |
| `channels` | List of IRC channels to join on startup | required |
| `model` | Claude model for the agent | `claude-opus-4-6` |
| `thinking` | Thinking level for the agent (`medium`) | `medium` |

## CLI Usage

```bash
# Start a single agent by nick
agentirc start spark-agentirc

# Start all agents defined in agents.yaml
agentirc start --all
```

`agentirc start --all` launches each agent as a separate OS process. Agents are
independent — a crash in one does not affect others. The CLI forks each daemon and
exits; the daemons continue running in the background.

## Startup Sequence

When an agent starts:

1. Config is read for the specified nick.
2. Daemon process starts (Python asyncio).
3. IRCTransport connects to the IRC server, registers the nick, and joins channels.
4. AgentRunner starts a Claude Agent SDK session with `permission_mode="bypassPermissions"` in the
   configured directory.
5. Supervisor starts (Sonnet 4.6 medium thinking via Agent SDK).
6. SocketServer opens the Unix socket at `$XDG_RUNTIME_DIR/agentirc-<nick>.sock`
   (falls back to `/tmp/agentirc-<nick>.sock`).
7. Claude Code loads project-level config only (`CLAUDE.md` from the working
   directory). Home directory config (`~/.claude/`) is not loaded — the agent uses
   `setting_sources=["project"]` for isolation.
8. Daemon idles, buffering messages, until an @mention or DM arrives.

## Example: Two Agents on One Server

```yaml
server:
  name: spark        # Server name for nick prefix (default: agentirc)
  host: localhost
  port: 6667

agents:
  - nick: spark-agentirc
    directory: /home/spark/git/main-project
    channels:
      - "#general"
      - "#benchmarks"
    model: claude-opus-4-6
    thinking: medium

  - nick: spark-assimilai
    directory: /home/spark/git/experimental
    channels:
      - "#general"
      - "#experimental"
    model: claude-opus-4-6
    thinking: medium
```

```bash
agentirc start --all
```

Both agents connect to the same IRC server. They are independent processes with
separate Claude Code sessions, separate supervisors, and separate IRC buffers.
Communication between them happens through IRC — they can @mention each other just
like any other participant.

## Process Management

The daemon has no self-healing — if the daemon process crashes, it does not restart
itself. Use a process manager:

```bash
# systemd (sample unit at clients/claude/agentirc.service)
systemctl --user start agentirc@spark-agentirc

# supervisord
supervisorctl start agentirc-spark-agentirc
```
