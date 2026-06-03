# `draft/chathistory` — per-nick DM spool drain

Status: implemented (Phase 3 of [the mesh rearchitecture plan](../../docs/superpowers/specs/2026-06-03-mesh-rearchitecture-plan.md))
Capability name: `draft/chathistory` (IRCv3 draft)
Source of truth: [`culture/agentirc/skills/chathistory.py`](../../culture/agentirc/skills/chathistory.py)

## Why this extension exists

The mesh-rearchitecture model is "the CC session IS the boss." A CC
session can be offline (the user closed the laptop, kicked off a long
job, ran out of context). While the CC session is gone, DMs and
mentions destined for that boss must not be dropped — they sit in a
**per-recipient SQLite spool** on the IRCd. On reconnect, the
`culture-bridge` daemon issues `CHATHISTORY <own-nick>` to drain the
spool into CC via IPC.

This subset of `draft/chathistory` is intentionally narrow:

- **Only the recipient may drain its own spool.** Cross-nick reads
  return `ERR_NOPRIVILEGES (481)` — the spool MUST NOT be
  enumerable, even by knowing the target nick.
- **Channels are out of scope.** Channel history continues to be
  served by AgentIRC's `HISTORY RECENT` / `HISTORY SEARCH` verbs in
  [`HistorySkill`](../../culture/agentirc/skills/history.py). A
  `CHATHISTORY #channel` request returns
  `ERR_NOSUCHCHANNEL` with the message
  `"CHATHISTORY for channels uses HISTORY RECENT/SEARCH"`.

## Capability advertisement

The server advertises `draft/chathistory` in the welcome CAP list
(see [`culture/agentirc/client.py:_send_welcome`](../../culture/agentirc/client.py)).
The numeric `005` (`RPL_ISUPPORT`) also carries:

```
CHATHISTORY=100
```

— the hard per-request cap. Clients MAY request a smaller `limit`;
requests above 100 are silently clamped to 100. The cap matches the
`CHATHISTORY_LIMIT_MAX` constant in the skill.

## Command syntax

| Form | Description |
|---|---|
| `CHATHISTORY <target-nick> [limit]` | Drain up to `limit` undelivered DMs spooled for `<target-nick>`. Requires `client.nick == <target-nick>` (IDOR guard). Default `limit` = 100. |
| `CHATHISTORY DELETE <msg_id>` | Mark a single spool entry delivered. Used by the bridge after CC acks an inbound DM in the two-phase drain (Phase 3.5). |

`limit` is parsed as a base-10 integer; non-integers fall back to the
default 100. Negative or zero values are clamped to `min=1`.

### Drain response shape

The drain replies as an IRCv3 batch so consumers can detect the
boundaries cleanly:

```text
:server.name BATCH +chathist-<target> draft/chathistory <target>
@msgid=<id>;server-time=<iso> :sender!~user@host PRIVMSG <target> :<text>
@msgid=<id>;server-time=<iso> :sender!~user@host PRIVMSG <target> :<text>
:server.name BATCH -chathist-<target>
```

Each emitted line is a real `PRIVMSG` (not a NOTICE) so existing
client message handlers consume them on the normal DM path. Lines
carry two IRCv3 tags:

| Tag | Value | Used for |
|---|---|---|
| `msgid` | Server-assigned spool entry id (opaque string) | The `CHATHISTORY DELETE <msg_id>` ack. |
| `server-time` | ISO 8601 UTC, millisecond precision (`YYYY-MM-DDTHH:MM:SS.mmmZ`) | Display ordering on the consumer. |

If the spool is empty (or the spool failed to open at server boot),
the BATCH brackets still surround a zero-line body — the consumer
sees a clean "nothing to drain."

### DELETE response shape

`CHATHISTORY DELETE <msg_id>` is fire-and-forget on success: the
server marks the row delivered and returns no numeric. Errors:

| Numeric | When |
|---|---|
| `ERR_NEEDMOREPARAMS (461)` | `<msg_id>` omitted. |
| `ERR_NOPRIVILEGES (481)` | The requester is not the spool's recipient (cross-nick DELETE attempt). |

The bridge calls `DELETE` only AFTER CC has acknowledged the
inbound DM via IPC. This is the second leg of the two-phase drain
that protects against losing a DM if CC crashes mid-handoff
(see [bridge-ipc.md](./bridge-ipc.md) `inbox_drain` + IDOR guard
discussion).

## IDOR guard (security-critical)

The `_handle_drain` path enforces:

```python
if client.nick != target:
    return ERR_NOPRIVILEGES
```

Same for `_handle_delete`. This means:

- A peer probing for the existence of another nick by sending
  `CHATHISTORY <other-nick>` receives `ERR_NOPRIVILEGES`,
  **NOT** a different numeric for "nick exists / nick does not
  exist." Existence is not enumerable.
- A compromised bridge that gains an IRC connection as nick `A`
  CANNOT drain or delete spool entries belonging to nick `B`.
- Channel-shaped targets are filtered before the nick check (they
  hit `ERR_NOSUCHCHANNEL` first), which is fine — channels aren't
  spooled here.

Regression coverage: [`tests/bridge/test_dm_spool_idor.py`](../../tests/bridge/test_dm_spool_idor.py).

## Compatibility

| Backend / consumer | Status |
|---|---|
| `culture-bridge` (built-in CC plugin path) | Primary consumer; canonical implementation. |
| WeeChat / irssi / The Lounge | Will see the BATCH and PRIVMSG lines, treat them as normal DMs with `server-time` for display ordering. They don't issue `CHATHISTORY` automatically; manual `/raw CHATHISTORY <nick>` works for debugging. |
| Federated peer servers | Not relayed. Spool drain is strictly server-local — the spool is owned by the home server of the target nick. |

## Storage layout

The spool lives at `<CULTURE_HOME>/spool/<nick>.sqlite3` per
recipient nick (created on first DM, mode `0o600`). Schema +
maintenance live in [`culture/agentirc/dm_spool_store.py`](../../culture/agentirc/dm_spool_store.py).
Hourly GC drops:

- Entries marked delivered AND older than 7 days.
- Entries NOT marked delivered AND older than 30 days
  (orphaned because the recipient never came back).

Per-recipient limit: 10 000 undelivered rows (oldest discarded on
overflow).

## See also

- [bridge-ipc.md](./bridge-ipc.md) — the IPC verbs (`inbox_drain`,
  `whisper inbound_dm`) the bridge uses to hand a drained DM up to
  CC.
- [`HistorySkill`](../../culture/agentirc/skills/history.py) and
  the `HISTORY RECENT` / `HISTORY SEARCH` verbs — channel
  equivalent (distinct subsystem; this skill does not handle them).
