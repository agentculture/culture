# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

The `culture/cli/` package is the unified CLI entry point for the `culture` command. It uses argparse with noun-based command groups (e.g., `culture agent start`, `culture chat stop`). The entry point is `culture.cli:main` registered in `pyproject.toml`.

## Architecture

```text
cli/
├── __init__.py          # Parser construction, main(), dispatches to groups
├── agent.py             # culture agent {create,join,start,stop,status,rename,...}
├── chat.py              # culture chat {start,stop,status,default,rename,archive,...,restart,link,logs,version,serve}
├── server.py            # culture server — deprecation alias for `culture chat` (9.x; removed in 10.0)
├── mesh.py              # culture mesh {overview,setup,update,console}
├── channel.py           # culture channel {list,read,message,who,join,part,ask,...}
├── bot.py               # culture bot {create,start,stop,list,inspect,archive,...}
├── skills.py            # culture skills {install}
└── shared/
    ├── constants.py     # Paths (DEFAULT_CONFIG, LOG_DIR), help strings
    ├── display.py       # Status formatting, agent/bot detail printing
    ├── formatting.py    # Output formatting utilities
    ├── ipc.py           # Unix socket IPC to running agent daemons
    ├── mesh.py          # Link parsing, mesh.yaml resolution, keyring lookups
    └── process.py       # Start/stop agents via IPC or PID/signal fallback
```

### Module Pattern

Each command group module (`agent.py`, `chat.py`, etc.) exports:

- `NAME: str` — the subcommand noun (e.g., `"agent"`)
- `register(subparsers)` — adds the group's parser and sub-subparsers
- `dispatch(args)` — routes to the correct handler based on `args.<group>_command`

`__init__.py` iterates `GROUPS` to call `register()` then `dispatch()`.

### IPC Layer

Channel and some agent commands route through a running agent daemon via Unix socket (`culture-<nick>.sock` in `$XDG_RUNTIME_DIR`). The `CULTURE_NICK` env var determines which agent to talk to. Fallback: `IRCObserver` connects directly to the server for read-only operations.

### Process Management

Servers and agents daemonize via `os.fork()` (Unix only). PID files live in `~/.culture/pids/`. Graceful stop uses IPC shutdown first, SIGTERM second, SIGKILL as last resort.

## Commands

Run tests (from repo root):

```bash
pytest tests/test_channel_cli.py tests/test_register_cli.py tests/test_migrate_cli.py tests/test_setup_update_cli.py tests/test_overview_cli.py -v
```

Run a single test:

```bash
pytest tests/test_channel_cli.py::test_try_ipc_routes_when_nick_set -v
```

Run all project tests:

```bash
pytest -n auto
```

## Key Conventions

- **Config lives at `~/.culture/server.yaml`** — the manifest format with server + agents + webhooks. Legacy `~/.culture/agents.yaml` still supported for reads.
- **Nick format: `<server>-<agent>`** — globally unique, constructed from the server name and agent suffix.
- **Agent backends: `claude`, `codex`, `copilot`, `acp`** — the `--agent` flag on create/join. All four must be kept in feature parity (all-backends rule).
- **Archive cascade** — archiving a server archives all its agents and their bots. Unarchive reverses this.
- **No mocks in server/integration tests** — tests spin up real Unix sockets or real server instances.

## Adding a New Subcommand

1. Create a handler function `_cmd_<name>(args)` in the appropriate group module
2. Add parser setup in that module's `register()` function
3. Add dispatch routing in `dispatch()`
4. Write tests using `argparse.Namespace` for args and mock Unix sockets for IPC (see `test_channel_cli.py` pattern)
