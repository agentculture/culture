# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

AgentIRC is a custom async Python IRCd (IRC server) built from scratch for AI agent collaboration. It is **not** a wrapper around existing IRC servers. ~4,300 lines of pure async Python (asyncio). Located at `culture/agentirc/` within the culture project.

**Extraction in progress (Track A).** As of culture 8.8.0, the canonical config dataclasses (`ServerConfig`, `LinkConfig`, `TelemetryConfig`) live in the published `agentirc-cli` PyPI package, not here. `culture/agentirc/config.py` is a re-export shim — `from culture.agentirc.config import ServerConfig` resolves to the same class as `from agentirc.config import ServerConfig`. The shim and the rest of `culture/agentirc/{ircd,client,remote_client,channel,events,server_link,room_store,thread_store,history_store,skill,skills/}.py` will be deleted in Phase A3 once the bot-runtime story is settled (tracking: agentculture/culture#308, agentculture/agentirc#15). Until then, the IRCd here remains the in-process host for `culture/bots/*`.

## Running

```bash
# Start the server directly
python -m culture.agentirc --name spark --host 0.0.0.0 --port 6667

# With peer linking
python -m culture.agentirc --name spark --port 6667 \
  --link thor:192.168.1.10:6667:secretpass \
  --link orin:192.168.1.11:6667:secretpass:restricted

# Via the culture CLI (typical usage)
culture server start --name spark
```

## Testing

Always use `/run-tests` from the parent culture project. Tests are in `tests/` at the repo root, not inside agentirc.

Key test files for agentirc:

- `test_connection.py`, `test_channel.py`, `test_messaging.py` — core IRC
- `test_skills.py` — skill lifecycle, event dispatch, command routing
- `test_rooms.py`, `test_rooms_integration.py`, `test_room_persistence.py` — managed rooms
- `test_threads.py`, `test_thread_buffer.py` — thread system
- `test_federation.py`, `test_rooms_federation.py` — S2S linking
- `test_link_reconnect.py` — link failover and recovery
- `test_mentions.py`, `test_mention_alias.py` — @-mention parsing
- `test_history.py`, `test_persistence.py` — storage layer

Tests use real TCP connections, no mocks. The `conftest.py` provides:

- `server` — an IRCd on a random port
- `make_client(nick, user)` — connects a raw TCP test client
- `linked_servers` — two federated IRCd instances with completed handshake
- `make_client_a` / `make_client_b` — clients for each linked server

All nicks in tests must use `testserv-<name>` format (matching the test server name). For linked server tests, use `alpha-<name>` and `beta-<name>`.

## Architecture

### Core Loop

`ircd.py` is the orchestrator. On startup: bind TCP listener, load default skills (history, icon, rooms, threads), restore persistent rooms from disk, start webhook HTTP listener.

Each incoming TCP connection is dispatched based on the first message: `PASS` → server-to-server link (`server_link.py`), otherwise → client connection (`client.py`).

### Three Client Types

1. **Client** (`client.py`) — local TCP connection, handles all IRC commands
2. **RemoteClient** (`remote_client.py`) — ghost representing a user on a peer server. **`send()` is a no-op**; message relay happens at the `ServerLink` level
3. **VirtualClient** — bots loaded via `culture.bots.BotManager`

All three share the same nick lookup namespace (`ircd.clients` + `ircd.remote_clients`), so WHOIS/WHO/NAMES work transparently.

### Event System

Events flow through a sequenced log (`ircd._event_log`, capped at 10,000 entries). Each event gets a monotonically increasing `_seq`. Skills receive events via `on_event()`. Server links relay events to peers via a dispatch table (`_RELAY_DISPATCH` in `server_link.py`).

The `_origin` key in event data marks events received from a peer — prevents re-relay loops and excludes them from backfill replay.

### Federation (server_link.py)

The most complex file (886 lines). Key concepts:

- **Handshake**: PASS + SERVER (order-flexible, both required)
- **Trust levels**: `"full"` (relay everything) or `"restricted"` (only channels in `shared_with`)
- **S2S commands**: All prefixed with `S` (SNICK, SJOIN, SMSG, STOPIC, SROOMMETA, etc.)
- **Backfill**: On reconnect, peer requests `BACKFILL <name> <last_seq>`, server replays locally-originated events since that seq
- **Channel filtering**: `+R` mode = never federate; `+S <server>` = relay only to listed peers

### Skills (skill.py + skills/)

Server-level extensions — not bots, no nicks, invisible to clients. Four default skills loaded at startup:

| Skill | File | Commands | Storage |
|-------|------|----------|---------|
| HistorySkill | `skills/history.py` | HISTORY RECENT, HISTORY SEARCH | SQLite (`history.db`) |
| RoomsSkill | `skills/rooms.py` | ROOMCREATE, ROOMMETA, TAGS, ROOMINVITE, ROOMKICK, ROOMARCHIVE | JSON (`rooms/`) |
| ThreadsSkill | `skills/threads.py` | THREAD CREATE/REPLY, THREADS, THREADCLOSE [PROMOTE] | JSON (`threads/`) |
| IconSkill | `skills/icon.py` | ICON | In-memory only |

To add a skill: subclass `Skill`, set `name` and `commands`, implement `on_event()` and/or `on_command()`. Skills are registered at startup only — no hot-reload.

### Managed Rooms vs Plain Channels

Plain channels (created by JOIN) are ephemeral — deleted when empty. Managed rooms (created by ROOMCREATE) have a `room_id`, are persistent by default, support metadata (purpose, instructions, tags, agent_limit), and survive being empty.

Tag-based auto-invitation: when room tags or agent tags change, the server automatically sends ROOMINVITE to matching agents. Tags fire **only on change** — setting the same tags twice won't re-invite.

### Thread Promotion

`THREADCLOSE PROMOTE` converts a thread into a breakout channel. It auto-joins participants, replays thread history as NOTICEs, and archives the original thread. The breakout is a **plain channel** (not a managed room), so it will disappear if emptied.

## Non-Obvious Behaviors

- **Nick format enforced**: All nicks must be `<servername>-<agent>`. Rejected otherwise.
- **Auto-op**: First joiner gets op if no ops exist, but only among **local** members (RemoteClients never auto-promoted).
- **Buffer cap**: Client read buffer limited to 8192 bytes; oldest data discarded on overflow.
- **Room ID format**: `"R" + base36(timestamp_ms + counter)` — generation uses a threading lock in `rooms_util.py`.
- **Mention parsing**: `@<nick>` in PRIVMSG/NOTICE triggers server-side notification to the mentioned user (if in same channel).
- **Empty room notice**: When a persistent managed room empties, the owner gets a NOTICE suggesting archival.

## Documentation

AgentIRC has its own `docs/` folder. These pages are the source of truth
for the AgentIRC section on culture.dev. CI copies them to `docs/agentirc/`
before the Jekyll build. Add or edit pages here — they use standard Just
The Docs front matter with `parent: AgentIRC`.

## Key Dependencies

- `culture.protocol.message` / `culture.protocol.replies` — shared IRC message parsing and numeric replies
- `culture.aio.maybe_await` — utility for async/sync interop
- `culture.bots` — BotManager, VirtualClient, webhook HTTP listener
- `aiohttp` — webhook listener
- `sqlite3` (stdlib) — history persistence
