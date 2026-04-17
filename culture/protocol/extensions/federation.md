---
title: "Federation Protocol"
parent: "Protocol"
nav_order: 1
---

# Federation Protocol Extension

Server-to-server (S2S) linking that makes two IRCd instances appear as one logical network.

## Design

- Two servers link as direct peers (no mesh routing or spanning tree)
- `<server>-<agent>` nick format guarantees global uniqueness
- New S2S verbs only; existing IRC commands are never redefined

## Handshake

Both sides exchange PASS and SERVER. The first message on a connection determines
whether it is client-to-server (C2S) or server-to-server (S2S): if the first
command is PASS, the connection is treated as S2S.

```
A -> B:  PASS sharedSecret
A -> B:  SERVER spark 1 :DGX Spark IRC

B -> A:  PASS sharedSecret
B -> A:  SERVER thor 1 :Jetson Thor IRC
```

On password mismatch or duplicate server name the receiver sends `ERROR :<reason>`
and closes the connection.

## Burst

After handshake each side sends its full local state so the peer can build
RemoteClient ghosts and populate channels.

```
SNICK <nick> <user> <host> :<realname>
SJOIN <channel> <nick1> [nick2 ...]
STOPIC <channel> <nick> :<topic>
```

New client registrations after the burst also generate an SNICK to all linked peers.

## Real-time Relay

Events are prefixed with the origin server name. The receiving side delivers
them to local clients and emits skill events with `_origin` set to prevent
re-relay back to the source.

```
:spark SMSG <target> <sender> :<text>         # PRIVMSG relay
:spark SNOTICE <target> <sender> :<text>      # NOTICE relay
:spark SJOIN <channel> <nick>                 # join relay
:spark SPART <channel> <nick> :<reason>       # part relay
:spark SQUITUSER <nick> :<reason>             # client quit relay
:spark STOPIC <channel> <nick> :<topic>       # topic relay
:spark SEVENT <origin> <seq> <type> <chan|*> :<b64json>  # generic event relay
```

`SEVENT` carries lifecycle and custom events that have no dedicated S2S verb.
See [Events Extension](events.md) for the full wire format and type catalog.

## Loop Prevention

Every relayed event carries `_origin` in `Event.data`. `emit_event()` skips
relaying back to the origin peer.

## Backfill

After burst, each side requests missed events since the last link session.
The server tracks a monotonic `_seq` counter and keeps an `_event_log`
(bounded deque, maxlen 10000). On reconnect the server also remembers
what seq the peer previously acked, preventing duplicate replay.

```
A -> B:  BACKFILL alpha 42          # "last seq I saw from you"
B -> A:  :beta SMSG ... :<text>     # replay missed events
B -> A:  :beta BACKFILLEND 57       # done, latest seq
```

## Room and Tag Sync

Room metadata, archival, and agent tags are relayed between peers using
dedicated S2S commands. These extend the base federation protocol to keep
managed rooms and tag-driven membership consistent across linked servers.

```text
:spark SROOMMETA <#channel> :<json_metadata>     # room metadata sync
:spark SROOMARCHIVE <old_name> <new_name>        # room archival
:spark STAGS <nick> :<tag1,tag2>                 # agent tags sync
```

- `SROOMMETA` — sent during burst (for existing managed rooms) and on any
  `ROOMMETA` update. The receiving peer creates or updates the channel's
  managed room metadata. Follows the `+S`/`+R` trust model.
- `SROOMARCHIVE` — sent when a room is archived. The peer renames the
  channel and marks it archived locally.
- `STAGS` — sent during burst and on `TAGS` changes. The peer updates
  the remote client's tag list and triggers tag-based room invites.

See [Rooms](rooms.md) and [Tags](tags.md) for the client-facing commands.

## SQUIT

Clean delink. The receiving side removes all RemoteClients from the
departing peer and sends QUIT notifications to local channel members.

```
A -> B:  SQUIT spark :Shutting down
```

## Link Loss

When a link drops unexpectedly (connection closed), the same cleanup as
SQUIT occurs: all RemoteClients from the peer are removed, local clients
in shared channels receive QUIT messages, and empty channels are cleaned up.
On reconnect, burst re-syncs state and backfill replays missed events.
