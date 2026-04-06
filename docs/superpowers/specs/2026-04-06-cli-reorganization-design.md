# CLI Reorganization Design

## Context

The culture CLI has grown to 22 top-level commands in a single 2,432-line `cli.py` file. Only `server`, `skills`, and `bot` have subcommand grouping — everything else is flat. This makes `culture --help` overwhelming, command discovery difficult, and the codebase hard to navigate. The goal is a clean, noun-first command hierarchy that groups commands by entity, splits the monolith into focused modules, and drops deprecated commands with no backward-compatibility shims (pre-1.0 clean break).

## Command Hierarchy

Six noun-based groups replace the 22 flat commands:

```
culture
├─ agent
│   ├─ create       Create an agent for the current directory
│   ├─ join         Create + start agent (shorthand)
│   ├─ start        Start agent daemon(s)         [--all, --foreground]
│   ├─ stop         Stop agent daemon(s)           [--all]
│   ├─ status       List/query running agents      [--full]
│   ├─ rename       Rename an agent (same server)
│   ├─ assign       Move agent to a different server
│   ├─ sleep        Pause agent(s)                 [--all]
│   ├─ wake         Resume paused agent(s)         [--all]
│   ├─ learn        Print self-teaching prompt
│   ├─ message      Send a message to an agent
│   └─ read         Read DM history with an agent
├─ server
│   ├─ start        Start the IRC server daemon
│   ├─ stop         Stop the IRC server daemon
│   ├─ status       Check server daemon status
│   ├─ default      Set default server
│   └─ rename       Rename server
├─ mesh
│   ├─ overview     Show mesh overview             [--room, --agent, --serve, --refresh]
│   ├─ setup        Set up mesh from mesh.yaml     [--uninstall]
│   ├─ update       Upgrade and restart mesh       [--dry-run, --skip-upgrade]
│   └─ console      Interactive admin console
├─ channel
│   ├─ list         List active channels
│   ├─ read         Read recent channel messages   [-n]
│   ├─ message      Send a message to a channel
│   └─ who          List channel members
├─ bot
│   ├─ create       Create a new bot
│   ├─ start        Start a bot
│   ├─ stop         Stop a bot
│   ├─ list         List bots
│   └─ inspect      Show bot details
└─ skills
    └─ install      Install IRC skills
```

### Mirrored commands

`message` and `read` exist under both `agent` and `channel`. Both register their own argparse subparser but call the same underlying handler. The target argument determines routing:

- `culture agent message spark-claude "hello"` — DM to an agent
- `culture channel message #general "hello"` — message to a channel
- `culture agent read spark-claude` — DM history
- `culture channel read #general` — channel history

### Dropped commands

- `init` — deprecated alias for `create`, removed entirely

### Unchanged groups

- `server` — already had subcommands, stays the same
- `bot` — already had subcommands, stays the same
- `skills` — already had subcommands, stays the same

## File Structure

Split the monolithic `cli.py` (2,432 lines) into a package:

```
culture/cli/
├── __init__.py      # main(), _build_parser(), top-level dispatch
├── _helpers.py      # shared utilities: config loading, async runner, print helpers
├── agent.py         # agent subcommands (~700 lines)
├── server.py        # server subcommands (~360 lines)
├── mesh.py          # overview, setup, update, console (~600 lines)
├── channel.py       # channel list, read, message, who (~200 lines)
├── bot.py           # bot CRUD (~160 lines)
└── skills.py        # skills install (~30 lines)
```

### Module contract

Each module exports two functions:

```python
def register(subparsers: argparse._SubParsersAction) -> None:
    """Add this group's subcommands to the top-level parser."""

def dispatch(args: argparse.Namespace) -> None:
    """Route to the correct handler based on the subcommand."""
```

`__init__.py` imports all modules and wires them together:

```python
from culture.cli import agent, server, mesh, channel, bot, skills

GROUPS = [agent, server, mesh, channel, bot, skills]

def _build_parser():
    parser = argparse.ArgumentParser(prog="culture", description="Culture — AI agent mesh")
    sub = parser.add_subparsers(dest="command")
    for group in GROUPS:
        group.register(sub)
    return parser

def main():
    parser = _build_parser()
    args = parser.parse_args()
    for group in GROUPS:
        if args.command == group.NAME:
            group.dispatch(args)
            return
    parser.print_help()
```

### Shared helpers (`_helpers.py`)

Extract from the current `cli.py`:

- `DEFAULT_CONFIG`, `_CONFIG_HELP` — config path constants
- `_run_async()` — asyncio runner wrapper
- `_parse_link()` — link argument parser
- `_load_config()` / `_save_config()` — YAML config I/O
- `_resolve_nick()` — agent nick resolution from cwd
- `_print_agents_overview()` / `_print_agent_detail()` / `_print_bot_listing()` — display functions
- `_start_agent_daemon()` / `_stop_agent_daemon()` — process management
- `read_pid()` / `_is_alive()` — PID file utilities
- `_wait_for_readiness()` — socket readiness check

## Help Output

Top-level help becomes scannable:

```
usage: culture [-h] {agent,server,mesh,channel,bot,skills} ...

Culture — AI agent mesh

commands:
  agent     Manage AI agents
  server    Manage the IRC server
  mesh      Mesh operations (overview, setup, update, console)
  channel   Channel messaging
  bot       Manage bots and webhooks
  skills    Install IRC skills
```

Each group's help shows its subcommands:

```
$ culture agent --help
usage: culture agent [-h] {create,join,start,stop,status,rename,assign,sleep,wake,learn,message,read} ...

Manage AI agents

subcommands:
  create    Create an agent for the current directory
  join      Create + start agent (shorthand)
  start     Start agent daemon(s)
  ...
```

## Critical Files

- `culture/cli.py` — the monolith being split (will become `culture/cli/__init__.py`)
- `culture/overview/` — overview/collector/renderer code, referenced by `mesh.py`
- `culture/console/` — TUI app, referenced by `mesh.py`
- `culture/config.py` — config loading utilities (check for reuse in `_helpers.py`)
- `culture/credentials.py` — credential helpers used by server/setup commands
- `tests/` — all CLI tests need command path updates

## Migration Checklist

1. Create `culture/cli/` package directory
2. Extract `_helpers.py` with shared utilities
3. Extract each group module (agent, server, mesh, channel, bot, skills)
4. Wire up `__init__.py` with register/dispatch pattern
5. Add mirrored `message` and `read` under both `agent` and `channel`
6. Remove `init` (deprecated alias)
7. Update entry point in `pyproject.toml` from `culture.cli:main` to `culture.cli:main` (same, since `__init__.py` exports it)
8. Update all tests to use new command paths
9. Update docs referencing CLI commands
10. Update the `culture` skill and CLAUDE.md if they reference specific commands

## Verification

1. **Unit tests pass:** `pytest -n auto` — all existing tests updated for new paths
2. **Help output:** `culture --help`, `culture agent --help`, etc. — verify clean output
3. **Round-trip commands:** Test each group's subcommands against a running server:
   - `culture server start --name test`
   - `culture agent create --server test`
   - `culture agent start test-<nick>`
   - `culture channel list`
   - `culture channel message #general "test"`
   - `culture channel read #general`
   - `culture agent message test-<nick> "hello"`
   - `culture mesh overview`
   - `culture agent stop --all`
   - `culture server stop --name test`
4. **No regressions:** Verify `culture mesh setup`, `culture mesh update --dry-run`, `culture bot list` all work
5. **Console:** `culture mesh console` launches the TUI successfully
