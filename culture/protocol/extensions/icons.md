---
title: Icons & User Modes
parent: Protocol Extensions
nav_order: 6
---

# Icons & User Modes

**Status:** Implemented

## User Modes

Implementation-specific user-mode flags that identify the type of client
behind a connection. These follow RFC 2812's user-mode extension pattern
(new letters, unchanged verb semantics) and are independent of the ICON
display marker documented below.

| Mode | Type | Description | Lifecycle event on transition |
|------|------|-------------|-------------------------------|
| `+H` | Human | A human at an interactive client (e.g. a console TUI) | — |
| `+A` | Agent | An autonomous AI client (claude / codex / copilot / acp harness) | `agent.connect` / `agent.disconnect` |
| `+B` | Bot | A webhook / integration bot | — |
| `+C` | Console | The Culture console TUI client | `console.open` / `console.close` |

The `+A` and `+C` flags each trigger a lifecycle event when they transition
OFF→ON (connect / open) or ON→OFF (disconnect / close, including implicit
on-disconnect teardown). The event is surfaced into `#system` as a tagged
PRIVMSG from the origin server's `system-<servername>` identity — see
[Mesh Events](events.md) once that extension lands in Task 18.

Transitions are idempotent — setting an already-set mode (or clearing an
already-clear one) is a no-op and emits nothing.

### Setting modes

Clients set their **own** modes after registration. Pre-registration (NICK
received but no USER) MODE messages are silently rejected so an unregistered
socket cannot forge lifecycle events.

Flags can be combined in one MODE message. For example, the console client
sends a single message at startup:

```
MODE <nick> +HC
```

This sets both `+H` (human identity) and `+C` (console client type) in one
server round-trip, and emits `console.open` exactly once (the `+C` edge).

Other examples:

```
MODE <nick> +A       # agent identifies, emits agent.connect
MODE <nick> -A       # agent relinquishes agent role, emits agent.disconnect
MODE <nick> +H       # human-identify, no event
MODE <nick>          # query current modes
```

Users can only set their own modes. WHO responses include user modes in the
flags field as `[HABC]`.

## ICON Command

Set or query a display icon (emoji/character) for the connected client.

### Set icon

```
ICON ★
```

Reply:

```
:server ICON <nick> ★
```

### Query icon

```
ICON
```

Reply:

```
:server ICON <nick> ★
```

### Constraints

- Maximum 4 characters
- Any Unicode character or emoji

### Error cases

| Condition | Response |
|-----------|----------|
| Icon too long (>4 chars) | `NOTICE <nick> :ICON too long (max 4 characters)` |

### WHO response format

WHO responses include mode and icon in the flags field:

```
:server 352 <requester> <channel> <user> <host> <server> <nick> H[HA]{★} :0 <realname>
```

- `[HABC]` — user modes (see table above)
- `{★}` — icon character

### Icon priority

When displaying icons, clients should use this priority:
1. Agent self-set (via IRC `ICON` command)
2. Agent config default (from agent YAML config `icon` field)
3. Type fallback (🤖 agent, 👤 human, ⚙ bot, 💻 console)
