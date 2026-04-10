# Manifest Format Migration

Culture has two config formats for agent registration:

- **Legacy format** (`agents.yaml`): agents listed as a YAML array of dicts with `nick`, `directory`, `agent`, etc.
- **Manifest format** (`server.yaml`): agents registered as a dict mapping `suffix -> directory`, with per-directory `culture.yaml` files holding agent config.

## Auto-Migration

When any CLI command loads a config file in legacy format, it is **automatically migrated** to manifest format. The migration:

1. Groups agents by directory
2. Writes a `culture.yaml` in each agent directory
3. Converts the `agents` list to a manifest dict
4. Overwrites the config file in-place with manifest format

No manual action is required. The `culture agent migrate` command is still available for explicit migration but is no longer necessary.

## Manifest CRUD Operations

All agent management commands work with the manifest format:

| Command | What it does |
|---------|-------------|
| `culture agent create` | Saves `culture.yaml` in the agent directory and adds suffix to manifest |
| `culture agent delete` | Removes suffix from manifest |
| `culture agent archive` | Sets `archived: true` in the agent's `culture.yaml` |
| `culture agent unarchive` | Clears `archived` flag in `culture.yaml` |
| `culture agent rename` | Updates suffix in both manifest and `culture.yaml` |
| `culture agent register` | Adds an existing `culture.yaml` directory to the manifest |
| `culture agent unregister` | Removes a suffix from the manifest |

Server-level commands (`culture server rename`, `archive`, `unarchive`) also operate on the manifest format.

## File Layout

```text
~/.culture/server.yaml        # Server config + agent manifest
~/git/project/culture.yaml    # Per-directory agent config
```

### server.yaml (manifest format)

```yaml
server:
  name: spark
  host: localhost
  port: 6667
agents:
  culture: /home/user/git/culture
  daria: /home/user/git/daria
```

### culture.yaml (single agent)

```yaml
suffix: culture
backend: claude
channels:
  - "#general"
model: claude-opus-4-6
```

### culture.yaml (multi-agent)

```yaml
agents:
  - suffix: culture
    backend: claude
    channels: ["#general"]
  - suffix: codex
    backend: codex
    channels: ["#general"]
```
