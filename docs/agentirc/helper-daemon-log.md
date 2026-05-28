---
layout: default
title: Helper Daemon Action Log
parent: AgentIRC
nav_order: 96
---

# Helper Daemon Action Log

The daemon-action log is a structured, control-plane record of what each agent
daemon *does* to manage its agent — distinct from the agent-message audit log,
which records what the agent *says* and which tools it calls.

Design spec: `docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md`

## File layout

One JSONL line per action at `CULTURE_HOME/daemon-log/<nick>.jsonl` (default
`~/.culture/daemon-log/<nick>.jsonl`). Universal across all four backends. Each
line:

```json
{"ts": "2026-05-28T14:32:17.123Z", "nick": "local-research", "action": "compact", "detail": {"trigger": "context_watermark", "pct": 0.91}}
```

## Action vocabulary

| action | when | detail |
|---|---|---|
| `agent_start` | the daemon starts the runner | `model`, `directory` |
| `agent_stop` | graceful stop | — |
| `agent_exit` | runner exits (incl. crash) | `exit_code` |
| `crash` | crash recorded in the sliding window | `exit_code`, `count` |
| `circuit_open` | circuit breaker trips | `count`, `window_s` |
| `pause` / `resume` | pause state changes | `manual` |
| `compact` | compact triggered | `trigger` (`ipc` or `context_watermark`), `pct?` |
| `clear` | context cleared | — |
| `handoff_written` | context-watermark handoff prompt sent | `pct`, `path` |
| `handoff_reminder` | post-compact reminder injected | `path` |

> Across backends, the universal actions are `agent_start`, `agent_stop`,
> `agent_exit`, and `compact`. The remaining actions (crash, circuit_open,
> pause, resume, clear, handoff_*) are emitted by the Claude daemon; other
> backends emit the universal subset.

## Relationship to Python logging

The daemon keeps its existing `logger.info/warning` calls for the systemd
journal. The action log is the structured, boss-readable complement — not a
replacement. Where both fire for one event, that's intentional: one for ops, one
for the boss.

## Boss workflow

```bash
daemon-log.sh <name> [limit]   # tail + pretty-print (default limit 30)
```

`status.sh` also prints the most recent action per supervised helper.

## Writing semantics

Lines are appended with `O_APPEND` and fsynced per line; concurrent writes on one
agent serialize through an in-process lock so each line stays intact. The log
grows without bound — rotation is out of scope and left to the operator.

## Related: the agent-message audit log

The daemon-action log is the *control-plane* record. Its companion is the
*agent-message audit log* at `CULTURE_HOME/audit/<nick>.jsonl` — one line per
`AssistantMessage` the agent emits, on every backend. Where the daemon-action
log says "the daemon compacted the agent", the audit log says "the agent said X
and called tool Y". Schema:

```json
{
  "ts": "2026-05-28T14:32:17.123Z",
  "nick": "local-research",
  "type": "assistant",
  "model": "claude-opus-4-7",
  "text": "I'll search for PR #123 …",
  "tool_uses": [{"name": "Bash", "input_digest": "sha256:…"}],
  "tool_results": [{"name": "Bash", "content_digest": "sha256:…", "preview": "first 200 chars"}]
}
```

Tool inputs and results are stored as 16-hex SHA-256 digests plus a 200-char
preview to keep the log compact; the digest lets you correlate an audit entry
with the full input captured in a `perm-queue/<id>.json` request (see
[Helper Permission Broker](helper-permissions.md)). Read it the same way:

```bash
tail -f ~/.culture/audit/local-research.jsonl | jq .
```

This is distinct from the **server-level** audit log documented in
[Audit](audit.md), which records IRC protocol events across the whole server.
