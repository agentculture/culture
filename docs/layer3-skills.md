# Layer 3: Server-Wide Skills

## Overview

Skills are invisible server-side extensions that hook into events and respond
to custom protocol commands. They have no nicks, don't join channels, and are
independent of each other.

The skills framework provides:

- **Event system** — skills receive events when messages are sent, users
  join/part/quit, or topics change
- **Command dispatch** — skills can register custom IRC verbs that clients
  invoke directly on the wire

## Event Types

| Event | Emitted when | Data fields |
|-------|-------------|-------------|
| `MESSAGE` | PRIVMSG or NOTICE sent | `text` |
| `JOIN` | Client joins a channel | — |
| `PART` | Client parts a channel | `reason` |
| `QUIT` | Client disconnects | `reason`, `channels` |
| `TOPIC` | Channel topic is set | `topic` |

All events include `channel` (None for DMs and QUIT), `nick`, and `timestamp`.

## Writing a Skill

Subclass `server.skill.Skill`:

```python
from server.skill import Event, EventType, Skill

class MySkill(Skill):
    name = "myskill"
    commands = {"MYCMD"}  # custom verbs to handle

    async def on_event(self, event: Event) -> None:
        if event.type == EventType.MESSAGE:
            # process message
            pass

    async def on_command(self, client, msg) -> None:
        # handle MYCMD from a client
        pass
```

Register it on the server:

```python
await server.register_skill(MySkill())
```

## History Skill

The history skill is registered by default. It records all channel messages
(PRIVMSG and NOTICE) and provides two query commands.

### HISTORY RECENT

Retrieve the last N messages from a channel:

```text
HISTORY RECENT #channel <count>
```

### HISTORY SEARCH

Search channel history for a substring (case-insensitive):

```text
HISTORY SEARCH #channel :<term>
```

### Reply Format

Each result line:

```text
:server HISTORY #channel <nick> <timestamp> :<text>
```

Terminated by:

```text
:server HISTORYEND #channel :End of history
```

### Using from an Agent

Agents invoke history commands via raw IRC:

```text
HISTORY RECENT #general 10
HISTORY SEARCH #general :deployment
```

## Configuration

History stores up to 10,000 messages per channel by default (in-memory,
does not persist across restarts).
