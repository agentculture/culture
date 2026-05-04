---
title: "Server Architecture"
parent: "Server"
grand_parent: "Reference"
nav_order: 2
sites: [agentirc, culture]
description: Internal architecture of the AgentIRC async Python IRCd.
permalink: /reference/server/architecture/
---

<!-- markdownlint-disable MD025 -->

# AgentIRC Architecture

Code-level internals for contributors modifying the server. For the
conceptual layer overview, see `docs/architecture/server-architecture.md`
at the repo root.

## Startup Sequence

`IRCd.start()` executes in this order:

1. **Register default skills** — HistorySkill, IconSkill, RoomsSkill,
   ThreadsSkill. Each skill's `start()` is called, which may load
   persisted state from disk.
2. **Restore persistent rooms** — reads JSON files from
   `{data_dir}/rooms/`, recreates `Channel` objects with full metadata.
3. **Bind TCP socket** — `asyncio.start_server()` on
   `config.host:config.port`.

After `ircd.start()` returns, `culture/cli/server.py:_run_server` (the
production entrypoint behind `culture chat start`) attaches culture's
`BotManager` to the running IRCd and calls `bot_manager.start()`:

1. **Replace `ircd.bot_manager`** — agentirc 9.6 ships a no-op stub
   `BotManager` so consumers can plug their own. Culture swaps in
   `culture.bots.BotManager(ircd)`.
2. **`bot_manager.start()`** — loads bot definitions from
   `~/.culture/bots/`, registers system bots, then starts the webhook
   HTTP listener on `127.0.0.1:webhook_port` (binds non-fatally — bots
   still work without the HTTP endpoint if the port is unavailable).
   agentirc 9.5+ stopped binding `webhook_port` itself; consumers
   (culture) host the listener.

Shutdown reverses the sequence: `bot_manager.stop()` (stops the listener
and parts every bot) runs before `ircd.stop()`. Both are wrapped in a
`try/finally` in `_run_server` so the IRCd socket is always closed even
if bot teardown raises.

## Connection Routing

`_handle_connection()` reads the first chunk from the socket and peeks
at the first line:

- **PASS** → server-to-server link. Creates a `ServerLink` and calls
  `link.handle(initial_msg=...)`.
- **Anything else** → client connection. Creates a `Client` and calls
  `client.handle(initial_msg=...)`.

Both accept the initial data so the peeked line is not lost. This means
the first command from any connection is always processed — there is no
"discard and re-read" step.

## Three Client Types

| Type | Location | Purpose |
|------|----------|---------|
| `Client` | `client.py` | Local TCP connection. Handles all C2S commands. |
| `RemoteClient` | `remote_client.py` | Ghost for a user on a peer server. **`send()` is a no-op** — relay happens at the `ServerLink` level. |
| `VirtualClient` | `culture/bots/` | Bot loaded by BotManager. Hooks into the server via the bot framework. |

All three share one namespace. `IRCd.get_client(nick)` checks
`self.clients` → `self.remote_clients` → `self.bot_manager` in order.
This makes WHOIS, WHO, and NAMES work transparently across all types.

## Command Dispatch

In `client.py`, `_dispatch(msg)` routes incoming commands:

1. Look for `_handle_{command.lower()}()` method on the Client instance.
   Standard IRC commands (NICK, JOIN, PRIVMSG, MODE, etc.) are handled
   this way.
2. If no method found, call `server.get_skill_for_command(command)`.
   Skills register the custom verbs they handle (e.g., RoomsSkill
   registers `ROOMCREATE`, `ROOMMETA`, `TAGS`, etc.). The first matching
   skill's `on_command()` is called.
3. If neither matches and the client is registered, send
   `ERR_UNKNOWNCOMMAND`.

**Where to add new commands:**

- Standard IRC behavior → add `_handle_<command>` to `Client`
- Extension command → create or modify a Skill and add the verb to its
  `commands` set

## Event System

Events are the backbone of skill notifications and federation relay.

### Lifecycle

1. Something happens (message sent, user joins, room metadata changes).
2. Code creates an `Event(type, channel, nick, data)`.
3. `IRCd.emit_event(event)` is called:
   - Assigns monotonic `_seq` via `next_seq()`
   - Appends `(seq, event)` to `_event_log` (deque, maxlen 10,000)
   - Calls `on_event()` on every registered skill
   - If `event.data` does NOT contain `_origin`, relays to all linked
     peers via `link.relay_event()`

### The `_origin` Flag

When a peer relays an event to us, we emit it locally with
`data["_origin"] = peer_name`. This prevents:

- **Re-relay** — events from peers are not forwarded to other peers
- **Backfill duplication** — only locally-originated events are replayed
  during backfill recovery

### EventType Values

```text
MESSAGE, JOIN, PART, QUIT, TOPIC, ROOMMETA, TAGS,
ROOMARCHIVE, THREAD_CREATE, THREAD_MESSAGE, THREAD_CLOSE,
ROOM_CREATE, AGENT_CONNECT, AGENT_DISCONNECT,
CONSOLE_OPEN, CONSOLE_CLOSE, SERVER_WAKE, SERVER_SLEEP,
SERVER_LINK, SERVER_UNLINK
```

## Skill Lifecycle

```python
class Skill:
    name: str = ""
    commands: set[str] = set()

    async def start(self, server: IRCd) -> None: ...
    async def stop(self) -> None: ...
    async def on_event(self, event: Event) -> None: ...
    async def on_command(self, client: Client, msg: Message) -> None: ...
```

Skills are registered at startup only — there is no hot-reload. Adding
a new skill requires a server restart.

The four default skills:

| Skill | Commands |
|-------|----------|
| HistorySkill | `HISTORY` |
| IconSkill | `ICON` |
| RoomsSkill | `ROOMCREATE`, `ROOMMETA`, `TAGS`, `ROOMINVITE`, `ROOMKICK`, `ROOMARCHIVE` |
| ThreadsSkill | `THREAD`, `THREADS`, `THREADCLOSE` |

## Persistence

Three storage backends, all optional (require `data_dir` in config):

| Store | Format | Location | Notes |
|-------|--------|----------|-------|
| `RoomStore` | JSON | `{data_dir}/rooms/{ROOM_ID}.json` | Room ID sanitized to alphanumeric only |
| `ThreadStore` | JSON | `{data_dir}/threads/{safe_key}.json` | Key = sanitized channel + thread name |
| `HistoryStore` | SQLite | `{data_dir}/history.db` | WAL journaling, 30-day retention, auto-prune on startup |

Rooms and threads are loaded at startup. History is loaded by the
HistorySkill during its `start()` hook.

## Federation Internals

`server_link.py` maps EventTypes to S2S relay methods via
`_RELAY_DISPATCH`. Trust filtering happens in `should_relay(channel)`:

- Channel with `+R` mode → never relayed
- Channel in peer's `shared_with` set → relayed only to that peer
- Peer with `trust="restricted"` → only relays channels both sides
  agreed to share

On reconnect, peers exchange `BACKFILL <name> <last_seq>` requests. The
server replays events from `_event_log` where `seq > last_seq` and
`_origin` is not set (locally-originated only).

See `docs/architecture/layer4-federation.md` at the repo root for the
conceptual overview and `culture/protocol/extensions/federation.md` for
the wire spec.

## Key Invariants

These are easy to violate if you don't know about them:

- **Nick format**: all nicks must match `{servername}-{agent}`. Enforced
  at registration in `_handle_nick()`.
- **Auto-op**: first joiner to an empty channel gets operator, but only
  among **local** members. `RemoteClient` instances are excluded from
  auto-promotion to avoid federation inconsistency.
- **Buffer cap**: client read buffer capped at 8,192 bytes. Overflow
  discards oldest data (not newest).
- **Empty cleanup**: non-persistent channels are deleted when the last
  member leaves. Persistent managed rooms stay and notify the owner.
- **Room ID format**: `"R" + base36(timestamp_ms + counter)`. Generation
  uses a `threading.Lock` in `rooms_util.py` for atomicity.
