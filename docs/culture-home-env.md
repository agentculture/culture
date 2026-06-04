---
title: "CULTURE_HOME env variable"
parent: "Operator guide"
nav_order: 16
---

# CULTURE_HOME (v9.1.5+)

`CULTURE_HOME` overrides the directory the `culture` CLI uses for
config, manifest, logs, and runtime state. When unset, the CLI
defaults to `~/.culture`.

## Default paths

| Path | Purpose | Resolved as |
|---|---|---|
| `server.yaml` | Server config + agent manifest | `$CULTURE_HOME/server.yaml` |
| `agents.yaml` | Legacy manifest (pre-v9.0) | `$CULTURE_HOME/agents.yaml` |
| `logs/` | Daemon stdout/stderr | `$CULTURE_HOME/logs` |
| `helpers/<nick>/` | Per-worker home dir | `$CULTURE_HOME/helpers/<nick>` |
| `perm-queue/` | Permission-request queue | `$CULTURE_HOME/perm-queue` |
| `perm-decisions/` | Decision audit trail | `$CULTURE_HOME/perm-decisions` |
| `run/bridge-*.pid` | Bridge PID files | `$CULTURE_HOME/run/bridge-*.pid` |

## Usage

```bash
# Use a different config + state location for one invocation
CULTURE_HOME=/tmp/culture-staging culture agent status

# Switch the whole shell session
export CULTURE_HOME=~/.culture-dev
culture server status
```

## Why this matters

Pre-v9.1.5, the CLI's argparse `--config` defaults were computed at
module-import time via `os.path.expanduser("~/.culture/server.yaml")`.
That resolved `~` from `$HOME`, NOT from `CULTURE_HOME`. Tests that
set `CULTURE_HOME=<tmp>` and invoked the CLI as a subprocess saw the
subprocess's default `--config` still point at the operator's real
`~/.culture/server.yaml`. A regression in v9.1.4 corrupted a live
operator manifest because of this.

v9.1.5 replaced the import-time constants with lazy resolvers that
honor `CULTURE_HOME` at access time. Existing imports like
`from culture.cli.shared.constants import DEFAULT_CONFIG` continue
to work via PEP 562 `module.__getattr__`, but every read now
re-resolves the path.

## `--config` still wins

When you pass `--config /path/to/server.yaml` explicitly to any
`culture` subcommand, that path is used regardless of
`CULTURE_HOME`. The env var only changes the *default*.

```bash
# Explicit --config beats CULTURE_HOME
CULTURE_HOME=/tmp/culture-A culture agent status --config /tmp/culture-B/server.yaml
# Reads from /tmp/culture-B/server.yaml
```

## Programmatic access

If you're writing a tool that integrates with culture's filesystem
layout, use the helpers in `culture.cli.shared.constants`:

```python
from culture.cli.shared.constants import (
    culture_home,
    default_config_path,
    default_legacy_config_path,
    default_log_dir,
)

print(culture_home())              # /Users/x/.culture  OR  $CULTURE_HOME
print(default_config_path())       # <home>/server.yaml
print(default_legacy_config_path())  # <home>/agents.yaml
print(default_log_dir())           # <home>/logs
```

These functions resolve `CULTURE_HOME` on every call, so they're
safe to use in long-running processes where the env may change.
