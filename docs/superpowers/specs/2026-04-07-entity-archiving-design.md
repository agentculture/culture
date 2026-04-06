# Entity Archiving Design

**Date:** 2026-04-07
**Status:** Approved

## Context

Culture supports creating and managing servers, agents, and bots, but has no way
to decommission them without losing their configuration history. Entities can be
stopped, but their config persists indefinitely with no distinction between
"active but stopped" and "retired." Rooms and threads already have mature
archiving (flag-based, in-place), but the higher-level entities do not.

This feature adds `archive` and `unarchive` commands for servers, agents, and
bots. Archiving preserves configuration history while hiding retired entities
from default views and preventing accidental restarts.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage strategy | Flag in-place | Matches room/thread pattern. No file moves. |
| Cascade on server archive | Yes, automatic | A server with no running entities is the natural archive state. Mirrors ROOMARCHIVE parting members. |
| Unarchive support | Yes | Symmetrical. Restores entity to "stopped but available" state without auto-starting. |
| Default visibility | Hidden | `--all` flag reveals archived entities. Follows docker/git convention. |
| Bot directory on archive | Untouched | Flag only in bot.yaml. Directory, handler.py remain. |
| Server archive flag | Explicit | `archived: true` on server config prevents `culture server start`. |
| Scope | CLI-only | No IRC protocol changes. Archive is an ops concern, not a federation concern. |

## Data Model Changes

### AgentConfig (`culture/clients/claude/config.py`)

```python
@dataclass
class AgentConfig:
    # ... existing fields ...
    archived: bool = False
    archived_at: str = ""       # ISO date YYYY-MM-DD
    archived_reason: str = ""
```

### ServerConnConfig (`culture/clients/claude/config.py`)

```python
@dataclass
class ServerConnConfig:
    # ... existing fields ...
    archived: bool = False
    archived_at: str = ""
    archived_reason: str = ""
```

### BotConfig (`culture/bots/config.py`)

```python
@dataclass
class BotConfig:
    # ... existing fields ...
    archived: bool = False
    archived_at: str = ""
    archived_reason: str = ""
```

### MeshAgentConfig (`culture/mesh_config.py`)

```python
@dataclass
class MeshAgentConfig:
    # ... existing fields ...
    archived: bool = False
```

**Backward compatibility:** The `load_config()` loader strips unknown fields and
uses dataclass defaults, so older config files without archive fields load
cleanly with `archived=False`.

## CLI Commands

### Agent

```
culture agent archive <nick> [--reason "replaced by opus agent"] [--config PATH]
culture agent unarchive <nick> [--config PATH]
```

**Archive flow:**
1. Find agent in config by nick
2. If agent is running (PID alive), stop it via the existing `stop_agent()` helper
3. Set `archived=True`, `archived_at=<today>`, `archived_reason=<reason>`
4. Save config atomically
5. Print confirmation

**Unarchive flow:**
1. Find agent in config by nick
2. Verify `archived=True` (error if not archived)
3. Clear `archived`, `archived_at`, `archived_reason`
4. Save config atomically
5. Print confirmation + hint to start

### Server

```
culture server archive [--name NAME] [--reason "decommissioned"] [--config PATH]
culture server unarchive [--name NAME] [--config PATH]
```

**Archive flow (cascade):**
1. Stop the server process if running
2. Stop all running agents
3. Set `archived=True` + metadata on server config
4. Set `archived=True` + metadata on all agent configs
5. Archive all bots whose owner matches any of the server's agents
6. Print summary of everything archived

**Unarchive flow (cascade):**
1. Clear `archived` on server config
2. Clear `archived` on all agent configs
3. Unarchive all bots whose owner matches any of the server's agents
4. Print summary -- entities are unarchived but not started

### Bot

```
culture bot archive <name> [--reason "no longer needed"]
culture bot unarchive <name>
```

**Archive flow:**
1. Find bot directory (`~/.culture/bots/<name>/bot.yaml`)
2. Load bot config, set `archived=True` + metadata
3. Save bot.yaml atomically
4. Print confirmation

**Unarchive flow:**
1. Load bot config, verify archived
2. Clear `archived`, `archived_at`, `archived_reason`
3. Save bot.yaml atomically
4. Print confirmation

## Visibility Filtering

### Agent status

`culture agent status` filters out `archived=True` agents from the overview.

`culture agent status --all` shows all agents. Archived agents display an
`[archived]` marker in the output.

`culture agent status <nick>` shows full detail for any agent (archived or not),
including archive metadata if present.

### Agent start

`culture agent start <nick>` on an archived agent prints an error:
```
Agent 'spark-claude' is archived. Unarchive first:
  culture agent unarchive spark-claude
```

`culture agent start --all` skips archived agents silently (only starts active
agents).

### Server start

`culture server start` on an archived server prints an error:
```
Server 'spark' is archived. Unarchive first:
  culture server unarchive --name spark
```

### Bot list

`culture bot list` skips archived bots.

`culture bot list --all` shows all bots with `[archived]` marker.

### Mesh overview

`culture mesh overview` filters archived agents from the display.

`culture mesh overview --all` includes archived agents with marker.

## Config Layer Functions

### `culture/clients/claude/config.py`

```python
def archive_agent(path, nick, reason=""):
    """Set archived flag on an agent. Raises ValueError if not found."""

def unarchive_agent(path, nick):
    """Clear archived flag on an agent. Raises ValueError if not found or not archived."""

def archive_server(path, reason=""):
    """Set archived flag on server and all agents."""

def unarchive_server(path):
    """Clear archived flag on server and all agents."""
```

### `culture/bots/config.py`

The existing `save_bot_config()` handles persistence but uses a manual nested
dict (`bot`/`trigger`/`output` sections). The archive fields (`archived`,
`archived_at`, `archived_reason`) go in the `bot` section. Both
`save_bot_config()` and `load_bot_config()` must be updated to
read/write these fields. The archive/unarchive logic itself is straightforward
field mutation + save, done directly in the CLI handlers since bot config is
per-file (no list to search through).

## Testing

### Unit tests (`tests/test_daemon_config.py`)

- `test_archive_agent` -- sets fields, verifies YAML output
- `test_archive_agent_not_found` -- raises ValueError
- `test_unarchive_agent` -- clears fields
- `test_unarchive_agent_not_archived` -- raises ValueError
- `test_archive_server_cascades` -- archives server + all agents
- `test_unarchive_server_cascades` -- unarchives server + all agents

### Unit tests (`tests/test_bot.py`)

- `test_archive_bot` -- sets fields in bot.yaml
- `test_unarchive_bot` -- clears fields

### CLI tests (`tests/test_archive_cli.py`)

- `test_agent_archive_stops_and_flags` -- verify process stop + config update
- `test_agent_unarchive` -- verify flag cleared
- `test_server_archive_cascade` -- verify server + agents + bots all archived
- `test_server_unarchive_cascade` -- verify all unarchived
- `test_bot_archive_and_unarchive` -- verify bot.yaml updated
- `test_status_filters_archived` -- verify hidden from default view
- `test_status_all_shows_archived` -- verify `--all` includes them
- `test_start_refuses_archived_agent` -- verify error message
- `test_start_refuses_archived_server` -- verify error message
- `test_start_all_skips_archived` -- verify `--all` only starts active agents

## Verification

1. Create test agents: `culture agent create --nick test1 && culture agent create --nick test2`
2. Archive one: `culture agent archive spark-test1 --reason "testing"`
3. Verify hidden: `culture agent status` (should only show test2)
4. Verify shown with flag: `culture agent status --all` (both, test1 marked)
5. Verify start blocked: `culture agent start spark-test1` (should refuse)
6. Unarchive: `culture agent unarchive spark-test1`
7. Verify restored: `culture agent status` (both visible)
8. Test server cascade: `culture server archive --reason "decommission test"`
9. Verify all archived: `culture agent status --all`
10. Unarchive server: `culture server unarchive`
11. Run full test suite: `pytest -n auto`
