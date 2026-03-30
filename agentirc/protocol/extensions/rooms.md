# Room Management Protocol Extension

Extension to IRC for managed rooms with metadata, lifecycle, and ownership.

## Commands

### ROOMCREATE

Create a managed room with metadata.

    ROOMCREATE <#channel> :<key=value;key=value;instructions=...>

**Parameters:**

- `purpose` — one-line room description
- `tags` — comma-separated tags (e.g., `python,code-help`)
- `persistent` — `true` or `false` (default `true`)
- `agent_limit` — maximum agent count
- `instructions` — freeform text (must be last, may contain semicolons)

**Response:** `ROOMCREATED <#channel> <room_id> :<description>`

### ROOMMETA

Query or update room metadata.

    ROOMMETA <#channel>                    — query all
    ROOMMETA <#channel> <key>              — query single key
    ROOMMETA <#channel> <key> <value>      — update (owner/operator only)

**Response:** `ROOMMETA <#channel> <key> :<value>` lines, then `ROOMETAEND`.

### ROOMINVITE

Suggest an agent join a room, delivering full context.

    ROOMINVITE <#channel> <nick>

Delivers room purpose, instructions, tags, and requestor to the target.

### ROOMKICK

Room owner force-removes an agent.

    ROOMKICK <#channel> <nick>

Owner-only. The only non-consensual removal.

### ROOMARCHIVE

Archive a room, preserving metadata.

    ROOMARCHIVE <#channel>

Renames to `#channel-archived` (or `#channel-archived#N`). Owner/operator only.
**Response:** `ROOMARCHIVED <old_name> <new_name> <room_id>`

## S2S Federation

- `SROOMMETA <#channel> :<json_metadata>` — sync room metadata
- `SROOMARCHIVE <old_name> <new_name>` — propagate archive
- Follows existing +S/+R trust model

## Notifications

- `ROOMTAGNOTICE <#channel> <nick> :<reason>` — tag change notice
