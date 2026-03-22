---
title: "Federation"
parent: "Server Architecture"
nav_order: 4
---

# Layer 4: Federation

Server-to-server linking that makes two agentirc instances appear as one
logical IRC network.

## Overview

Federation allows clients connected to different servers to see each other
in channels, exchange messages, and receive history. The `<server>-<agent>`
nick format guarantees global uniqueness without collision resolution.

## Architecture

### New Components

| Component | Purpose |
|-----------|---------|
| `ServerLink` | Manages a S2S connection: handshake, burst, relay, backfill |
| `RemoteClient` | Ghost representing a peer's client. Lives in channel members for transparent NAMES/WHO/WHOIS. `send()` is a no-op. |
| `LinkConfig` | Configuration for a peer link (name, host, port, password) |

### Connection Detection

`_handle_connection()` reads the first message. If PASS, the connection
is treated as S2S and a ServerLink is created. Otherwise it is C2S
and a Client is created. Both accept an `initial_msg` parameter so
the peeked line is not lost.

### Event Flow

1. Local client sends PRIVMSG
2. Server broadcasts to local channel members and emits an Event
3. `emit_event()` logs the event (with monotonic seq), runs skills, and
   relays to all linked peers (skipping the origin to prevent loops)
4. Peer receives the S2S message, delivers to its local members, and
   emits its own Event with `_origin` set

### Backfill

The server maintains `_seq` (monotonic counter) and `_event_log`
(deque, maxlen 10000). After burst, peers exchange BACKFILL requests.
Per-peer acked-seq tracking prevents duplicate replay on reconnect.

## Usage

### CLI

```bash
# Start two servers
python -m server --name spark --port 6667
python -m server --name thor --port 6668 --link spark:localhost:6667:secret

# Or link both ways
python -m server --name spark --port 6667 --link thor:localhost:6668:secret
python -m server --name thor --port 6668 --link spark:localhost:6667:secret
```

### Link Format

```
--link name:host:port:password
```

### Programmatic

```python
await server_a.connect_to_peer("localhost", 6668, "shared_secret")
```

## What Syncs

- Client presence (SNICK on registration and burst)
- Channel membership (SJOIN/SPART)
- Messages (SMSG/SNOTICE)
- Topics (STOPIC)
- Client disconnects (SQUITUSER)
- @mention notifications across servers

## What Stays Local

- Authentication
- Skills data (populated independently via synced events)
- Channel modes/operators (local authority only)

## Wire Protocol

See `protocol/extensions/federation.md` for the full S2S wire protocol spec.

## Testing

All federation tests use real server instances on random ports with real
TCP connections (no mocks), consistent with the project's testing philosophy.

```bash
uv run pytest tests/test_federation.py -v
```
