---
layout: default
title: Helper Context Handoff
parent: AgentIRC
nav_order: 95
---

# Helper Context Handoff

A long-running agent fills its context window. Before it does, the daemon asks the
agent to write a handoff for its post-compact self, triggers a compact, then
reminds it to read that handoff on its next activation — so working state
survives the compaction.

Design spec: `docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md`

## How it works

The daemon self-monitors context utilization (it is reliable and can't miss the
threshold the way an async boss poll could). After each turn it reads the SDK's
`ResultMessage.usage.input_tokens`, which under session resume reflects the full
context sent to the model — a direct proxy for context-window occupancy:

```text
pct = input_tokens / context_window_tokens
```

At `pct >= high_water` (default 0.90) the daemon, once per fill cycle:

1. Records a `handoff_written` daemon action.
2. Sends the agent a prompt asking it to write a concise handoff to
   `CULTURE_HOME/handoff/<nick>.md` (what it's doing, key decisions, what
   remains, important paths).
3. Queues `/compact` as the next turn.
4. Records a `compact` daemon action with `trigger: context_watermark`.
5. On the next activation, prepends a reminder to read the handoff, and records a
   `handoff_reminder` action.

The handoff write is pre-approved in the helper's permission policy (an
`auto_allow` rule for `Write` to `handoff/<nick>.md`), so a context-crisis
handoff never stalls waiting for boss approval. All other writes still route to
the boss.

The handoff latch resets once usage drops below `low_water` (default 0.50, e.g.
after the compact), arming the next cycle.

## Context windows

| Model family | Window |
|---|---|
| `claude-opus-4-*` with a `1m` / `[1m]` marker | 1,000,000 |
| other `claude-opus-4-*`, `claude-sonnet-4-*`, `claude-haiku-4-*` | 200,000 |
| unknown | 200,000 (conservative default, logged) |

## Configuration

Optional, per-agent in `culture.yaml`:

```yaml
context_watch:
  enabled: true     # default true
  high_water: 0.90  # fraction of the window that triggers a handoff
  low_water: 0.50   # fraction below which the latch resets
```

Omit the block for defaults. This is additive — existing `culture.yaml` files
need no change.

## Visibility

```bash
context-status.sh            # last-known context % per helper
context-status.sh <name>     # one helper
daemon-log.sh <name>         # full action log incl. handoff_written / compact / handoff_reminder
```

## Backend support

Context-watch is **Claude-only**. Codex and Copilot do not expose per-turn token
counts on their responses (Copilot: issue #299), so the watermark cannot be
computed there. On those backends the [daemon-action log](helper-daemon-log.md)
still records compactions when they happen via the IPC `compact` path.
