# Agent Harness — Reference Implementation

This is the **citation-cli** reference for building new agent backends — copy, don't import. See [citation-cli](https://github.com/OriNachum/citation-cli) for the standalone tool.

## How to use

1. Copy this entire directory into `culture/clients/<your-backend>/`
2. Update the imports in each file to reference your backend's path
3. Replace `agent_runner.py` — implement your agent's SDK/CLI integration
4. Replace `supervisor.py` — implement your agent's productivity monitor
5. Adapt `daemon.py` — wire up your runner in `_start_agent_runner()`
6. Write your `skill/SKILL.md` with IRC commands for your agent

## What each file does

| File | Purpose | Adapt? |
|------|---------|--------|
| `daemon.py` | Orchestrates IRC + agent + IPC | Yes — wire up your runner |
| `irc_transport.py` | IRC client (asyncio, RFC 2812) | Rarely — works as-is |
| `message_buffer.py` | Ring buffer for channel messages | No — use as-is |
| `socket_server.py` | Unix socket for skill IPC | No — use as-is |
| `ipc.py` | JSON Lines message format | No — use as-is |
| `webhook.py` | HTTP + IRC alerting | No — use as-is |
| `config.py` | YAML config loader | Maybe — add backend-specific fields |
| `skill/irc_client.py` | CLI for IRC tools | No — use as-is |
| `skill/SKILL.md` | Agent skill definition | Yes — adapt for your agent |

## Citation pattern

These files are **copied, not imported**. Each backend owns its copy and can
modify it independently. There are no shared imports between backends.

If you improve a generic component (e.g., `irc_transport.py`), update the
reference here in `packages/` too, so the next backend starts from the
latest version.

## Reference implementation

See `culture/clients/claude/` for the working Claude backend — it was
the original source for these reference files.

## Specification

See `docs/agent-harness-spec.md` for the full interface contracts that
any backend must satisfy.
