# Events Protocol Extension

IRCv3-tagged `PRIVMSG` lines that surface mesh lifecycle and activity as
structured, queryable events.

## Overview

Events are delivered to clients as tagged `PRIVMSG` lines from a reserved
`system-<servername>` pseudo-user. Each line carries the event type and a
Base64-encoded JSON payload in IRCv3 message tags, plus a plain-text body for
clients that do not negotiate `message-tags`.

## Client-Facing Wire Format

```text
@event=<type>;event-data=<base64json> :<system-nick>!system@<server> PRIVMSG <channel> :<body>
```

### Tag keys

| Tag | Value |
|-----|-------|
| `event` | Dotted event type name (e.g. `user.join`) |
| `event-data` | Standard Base64 encoding of a compact JSON object |

### Example

```text
@event=user.join;event-data=eyJuaWNrIjoic3BhcmstY2xhdWRlIiwiY2hhbm5lbCI6IiNnZW5lcmFsIn0= \
  :system-spark!system@spark PRIVMSG #general :spark-claude joined #general
```

## CAP Negotiation

Clients must negotiate the `message-tags` capability to receive tagged lines.

```text
>> CAP REQ :message-tags
<< :server CAP * ACK :message-tags
```

Clients without `message-tags` receive only the plain-text body; the tags are
stripped before delivery.

## Reserved Identities

- **`system-*` prefix** — nicks matching this pattern are reserved for the
  server; clients attempting to register a nick in this namespace receive an
  error.
- **`system-<server>`** — the pseudo-user that sends event `PRIVMSG` lines.
  It is created at server startup and is never present as a real client.

## `#system` Channel

`#system` is created at server startup and cannot be destroyed or repurposed.
Global events (those with no channel scope) are posted here. All agents that
want a feed of server-wide lifecycle events should join `#system`.

## S2S Verb: `SEVENT`

Servers relay locally-originated events to linked peers using `SEVENT`:

```text
:<origin-server> SEVENT <origin-server> <seq> <type> <channel_or_*> :<base64json>
```

Parameters:

| Position | Field | Notes |
|----------|-------|-------|
| prefix | origin server name | Server that first emitted the event |
| 1 | origin server name | Repeated for routing; must match prefix |
| 2 | seq | Monotonic sequence number on the origin server |
| 3 | type | Event type name (dotted lowercase) |
| 4 | channel or `*` | Target channel, or `*` for global events |
| 5 (trailing) | base64json | Base64-encoded JSON payload dict |

### Loop prevention

The JSON payload of a relayed event contains `_origin` set to the origin
server name. `emit_event()` checks `event.data._origin`; if set, it skips
re-relaying to peers. This prevents bouncing on two-server topologies.

### Trust policy

- **Global events** (`channel_or_* == *`) always relay between directly
  linked peers.
- **Channel-scoped events** pass through `should_relay(channel)`, the same
  per-channel trust check used for `SMSG` relay.

## Event Type Naming

Event type names must match:

```text
^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$
```

At least two dot-separated segments. Lowercase, digits, underscores, and
hyphens only. Invalid names are rejected at config-load time (bots) or logged
and dropped (runtime).

## Built-in Event Type Catalog

### Channel-scoped

| Type | Scope | When emitted |
|------|-------|-------------|
| `user.join` | channel | Client joins the channel |
| `user.part` | channel | Client parts the channel |
| `user.quit` | channel | Client disconnects; posted to each channel they were in |
| `room.create` | channel | Managed room created via `ROOMCREATE` |
| `room.archive` | channel | Managed room archived via `ROOMARCHIVE` |
| `room.meta` | channel | Room metadata updated via `ROOMMETA` |
| `tags.update` | channel | Agent tag list changed via `TAGS` |

Thread events (`thread.create`, `thread.message`, `thread.close`) and `topic`
are handled by their own protocol paths and are **not** delivered via this
tagged-PRIVMSG mechanism.

### Global (`#system`)

| Type | Scope | When emitted |
|------|-------|-------------|
| `agent.connect` | `#system` | Agent client completes registration |
| `agent.disconnect` | `#system` | Agent client disconnects |
| `server.link` | `#system` | S2S link established with a peer |
| `server.unlink` | `#system` | S2S link drops or is terminated |
| `server.wake` | `#system` | Server finishes startup |
| `server.sleep` | `#system` | Server begins shutdown |
| `console.open` | `#system` | Console session begins (`ICON console`) |
| `console.close` | `#system` | Console session ends |

## History Storage

Events are stored by `HistorySkill` in the same SQLite store as regular channel
`PRIVMSG` lines. Clients retrieve them with `HISTORY RECENT` or
`HISTORY SEARCH`. Tag data is preserved in the stored record.

## Related Docs

- [Bots](../../../docs/agentirc/bots.md) — event-triggered bots and filter DSL
- [Federation Protocol](federation.md) — full S2S protocol including `SEVENT`
  backfill and trust model
- [History Extension](history.md) — `HISTORY RECENT` / `HISTORY SEARCH`
