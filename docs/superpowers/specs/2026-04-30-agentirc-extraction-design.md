# AgentIRC Extraction Design

**Status:** Proposed
**Date:** 2026-04-30
**Owner:** Ori Nachum

## Summary

Extract the IRCd in `culture/agentirc/` into a standalone repo at `../agentirc`, published as the `agentirc-cli` PyPI package (import name `agentirc`, CLI binary `agentirc`). Culture consumes it as an external dependency. The user-visible CLI surface is preserved: `culture server <verb>` becomes a transparent passthrough into `agentirc.cli.dispatch`.

### Naming (called out, because three names appear)

| Role | Name |
|---|---|
| PyPI distribution name | `agentirc-cli` (TestPyPI also has the squatted `agentirc`; we use `agentirc-cli` everywhere) |
| Python import package | `agentirc` |
| CLI binary | `agentirc` |
| Repo path | `../agentirc` |

This is the first of several planned splits — `culture-agent` and `culture-bot` will follow the same pattern in subsequent refactors. The seam established here is the template.

## Motivation

`culture/agentirc/` is ~3,600 lines of self-contained IRCd code (RFC 2812 base, server-to-server linking, channels, persistence, server-side skill plugins). Today it lives inside the culture repo and is launched in-process by `culture server start`. Three things make extraction useful now:

1. **Independent reuse.** Other projects could run an AgentIRC daemon without pulling in culture's bot/agent runtime.
2. **Sharper boundaries.** Today culture's CLI server module imports the IRCd directly and reaches into its internals. A package boundary forces a small, documented API surface (`agentirc.config`, `agentirc.cli`, `agentirc.protocol`).
3. **Pattern for future splits.** Culture-agent and culture-bot will be peeled off the same way. Doing agentirc first establishes the dependency model, the CLI passthrough idiom, and the testing strategy.

## Goals

- agentirc lives in `../agentirc` as an independently versioned package on PyPI (distribution name `agentirc-cli`, import name `agentirc`), with its own CLI binary `agentirc`.
- Culture consumes it as a normal dependency (`agentirc-cli>=0.1,<0.2`).
- `culture server <verb> <args>` continues to work for every existing verb, with identical flags, output, and exit codes — implemented as a 1:1 passthrough into `agentirc.cli.dispatch`.
- On-disk footprint stays culture-named: `~/.culture/server.yaml`, current socket paths, `culture-agent-*.service` systemd units, current log paths. Existing deployments need zero migration.
- The IRCd's protocol surface (`protocol/extensions/`) lives in agentirc, where it belongs.

## Non-Goals

- **Splitting the client transport.** `client.py` and `remote_client.py` stay in culture (relocated to `culture/transport/`). Only the server core moves.
- **Splitting bots/agents in this refactor.** Those splits are planned but separate.
- **Preserving git history.** Approach 2 was chosen: agentirc gets a synthetic "initial import" commit. Anyone wanting historical context for a line uses `git log` in culture before the deletion SHA.
- **Renaming on-disk artifacts.** Config paths, sockets, systemd units stay culture-named — see Goals.
- **Backwards compatibility for `python -m culture.agentirc`.** That entrypoint is removed (no known callers). Use `agentirc` (CLI) or `python -m agentirc` instead.

## Boundary

### What moves to `../agentirc`

| File / dir | Source path |
|---|---|
| `agentirc/ircd.py` | `culture/agentirc/ircd.py` |
| `agentirc/server_link.py` | `culture/agentirc/server_link.py` |
| `agentirc/channel.py` | `culture/agentirc/channel.py` |
| `agentirc/config.py` | `culture/agentirc/config.py` |
| `agentirc/events.py` | `culture/agentirc/events.py` |
| `agentirc/room_store.py` | `culture/agentirc/room_store.py` |
| `agentirc/thread_store.py` | `culture/agentirc/thread_store.py` |
| `agentirc/history_store.py` | `culture/agentirc/history_store.py` |
| `agentirc/rooms_util.py` | `culture/agentirc/rooms_util.py` |
| `agentirc/skill.py` | `culture/agentirc/skill.py` |
| `agentirc/skills/` | `culture/agentirc/skills/` |
| `agentirc/cli.py` | new — extracted from culture's `cli/server.py` server lifecycle code |
| `agentirc/protocol.py` | new — verb names, numerics, extension tags pulled from string-literals in `client.py` and `ircd.py` |
| `protocol/extensions/` | `protocol/extensions/` (moved wholesale) |
| `tests/` | tests under culture's `tests/` that import `culture.agentirc.*` and aren't transport-focused |

### What stays in culture

| File / dir | New path | Why |
|---|---|---|
| `culture/transport/client.py` | from `culture/agentirc/client.py` | Culture's bots and backend daemons are the only consumers. |
| `culture/transport/remote_client.py` | from `culture/agentirc/remote_client.py` | Same. |
| `culture/cli/server.py` | rewritten as 1-function passthrough | See "CLI surface" below. |
| `culture/cli/shared/mesh.py` | imports `LinkConfig`, `PeerSpec` from `agentirc.config` | Mesh manifest read/write stays culture-side. |
| `culture/bots/`, `culture/clients/*/daemon.py` | imports updated to `culture.transport` | Transport ownership unchanged from the consumer's POV. |
| `packages/agent-harness/` | unchanged | Agent backend citation reference is culture's concern. |

### What is deleted from culture

- `culture/agentirc/` — the entire directory.
- `culture/agentirc/__main__.py` (no replacement; `python -m culture.agentirc` is gone).

## Architecture

### Repo layout — `../agentirc`

```
agentirc/
├── pyproject.toml          # name = "agentirc-cli"; scripts = { agentirc = "agentirc.cli:main" }
├── CHANGELOG.md            # starts at 0.1.0
├── README.md
├── LICENSE
├── agentirc/
│   ├── __init__.py
│   ├── __main__.py         # python -m agentirc
│   ├── cli.py              # main(), dispatch(argv)
│   ├── protocol.py         # verb names, numerics, extension tags
│   ├── config.py           # ServerConfig, LinkConfig, PeerSpec (public)
│   ├── ircd.py
│   ├── server_link.py
│   ├── channel.py
│   ├── events.py
│   ├── room_store.py
│   ├── thread_store.py
│   ├── history_store.py
│   ├── rooms_util.py
│   ├── skill.py
│   └── skills/
│       ├── rooms.py
│       ├── threads.py
│       ├── history.py
│       └── icon.py
├── protocol/
│   └── extensions/         # moved from culture
├── tests/
└── docs/
    ├── api-stability.md    # documents the public surface culture depends on
    ├── cli.md
    └── deployment.md
```

### Repo layout — culture (delta only)

```
culture/
├── pyproject.toml          # adds: agentirc-cli>=0.1,<0.2
├── uv.lock                 # regenerated
├── culture/
│   ├── agentirc/           # DELETED
│   ├── transport/          # NEW
│   │   ├── __init__.py     # re-exports Client, RemoteClient for back-compat
│   │   ├── client.py       # from culture/agentirc/client.py
│   │   └── remote_client.py
│   ├── cli/server.py       # passthrough shim (see below)
│   ├── cli/shared/mesh.py  # imports from agentirc.config
│   ├── bots/               # imports updated
│   └── clients/            # imports updated
├── tests/                  # IRCd tests removed; transport + shim + mesh tests stay
└── protocol/extensions/    # MOVED to agentirc
```

## CLI surface

### `agentirc` subcommands (1:1 with what `culture server …` does today)

| Command | Replaces | Notes |
|---|---|---|
| `agentirc serve [--config PATH]` | in-process IRCd launch from `cli/server.py:421` | Default `--config` path: `~/.culture/server.yaml`. |
| `agentirc start [--name NAME]` | `culture server start` | Same systemd / supervisor handoff. |
| `agentirc stop [--name NAME]` | `culture server stop` | |
| `agentirc restart [--name NAME]` | `culture server restart` | |
| `agentirc status [--name NAME]` | `culture server status` | |
| `agentirc link <peer> [...]` | `culture server link` | Mesh link registration. |
| `agentirc logs [--name NAME] [-f]` | `culture server logs` | |
| `agentirc version` | — | Reports agentirc version. |

Default behaviors that preserve transparency:

- `--config` defaults to `~/.culture/server.yaml`.
- Socket paths, log paths, systemd unit names: unchanged from current culture defaults.
- Exit codes and stderr formatting are inherited from the moved code (no rewrites in this refactor).

### `culture server` passthrough

`culture/cli/server.py` reduces to a single forwarding function:

```python
# culture/cli/server.py
from agentirc.cli import dispatch as _agentirc_dispatch

def server(argv: list[str]) -> int:
    """culture server <verb> <args> → agentirc <verb> <args>, in-process."""
    return _agentirc_dispatch(argv)
```

Properties of the passthrough:

- **Pure forwarding.** Culture does not parse, validate, or rename any flag. It does not enumerate verbs. New verbs added in agentirc are reachable via `culture server <new-verb>` automatically.
- **In-process.** No subprocess fork; same Python interpreter, same env. Faster and easier to debug than `subprocess.run`.
- **Single source of truth.** Help text, error messages, exit codes — all come from agentirc.

### `python -m culture.agentirc` removed

There are no known callers. Anyone running it gets an `ImportError` after upgrade. The migration path is `agentirc` (binary) or `python -m agentirc`.

## Import contract

The public surface of agentirc — what culture (and any third-party consumer) is allowed to import:

| Module | Members | Stability |
|---|---|---|
| `agentirc.config` | `ServerConfig`, `LinkConfig`, `PeerSpec`, plus dataclass fields | Public, semver-tracked. Breaking changes require a major bump. |
| `agentirc.cli` | `main()`, `dispatch(argv) -> int` | Public, semver-tracked. |
| `agentirc.protocol` | Verb name constants, numeric reply codes, extension tag names | Public, semver-tracked. |

Everything else — `agentirc.ircd`, `agentirc.server_link`, `agentirc.channel`, the stores, the skills — is internal. Agentirc may refactor freely without breaking culture. Documented in `agentirc/docs/api-stability.md`.

### Where culture imports from agentirc

| Culture module | Imports |
|---|---|
| `culture/cli/server.py` | `agentirc.cli.dispatch` |
| `culture/cli/shared/mesh.py` | `agentirc.config.LinkConfig`, `agentirc.config.PeerSpec` |
| `culture/transport/client.py` | `agentirc.protocol.*` (verb / numeric / tag constants) |
| `culture/bots/*`, `culture/clients/*/daemon.py` | nothing — they import `culture.transport` |

## Configuration & on-disk footprint

Unchanged from today. `agentirc` reads the same files culture reads now:

- Default config path: `~/.culture/server.yaml`.
- Socket paths: same as current culture defaults.
- systemd units: `culture-agent-<name>.service` (unchanged).
- Log paths: unchanged.

Standalone (non-culture) users of agentirc can override the config path via `agentirc serve --config /path/to/their.yaml`.

## Migration mechanics

### Order of operations

1. **Bootstrap agentirc** (in `../agentirc`):
   - Copy server-core files from `culture/agentirc/` (excluding `client.py`, `remote_client.py`, `__main__.py`) into `../agentirc/agentirc/`.
   - Copy `protocol/extensions/` into `../agentirc/protocol/extensions/`.
   - Copy IRCd-specific tests from culture's `tests/`.
   - Add `pyproject.toml` (`name = "agentirc-cli"`, `version = "0.1.0"`, scripts `agentirc = "agentirc.cli:main"`).
   - Rewrite imports in the new tree: `from culture.agentirc.X` → `from agentirc.X`.
   - Add new modules `agentirc/cli.py` (extracted from `culture/cli/server.py` server-lifecycle code) and `agentirc/protocol.py` (verb / numeric / tag constants pulled from string-literals in `client.py` and `ircd.py`).
   - First commit: `Initial import from culture@<sha>` — single synthetic commit pointing at the source SHA.
   - CI mirrors culture's: pytest + black + isort + flake8 + bandit + markdownlint, plus version-check.
   - Tag `v0.1.0`, publish to PyPI (publishing is already set up).
2. **Culture-side cutover PR** (single PR, branch out per culture's standard workflow):
   - `pyproject.toml`: add `agentirc-cli>=0.1,<0.2`. Regenerate `uv.lock`.
   - Delete `culture/agentirc/` entirely.
   - Move `client.py`, `remote_client.py` → `culture/transport/`. Add `culture/transport/__init__.py` re-exporting the public class names.
   - Replace `culture/cli/server.py` with the passthrough shim.
   - Update imports across culture: `culture.agentirc.{client,remote_client}` → `culture.transport.{client,remote_client}`; `culture.agentirc.config` → `agentirc.config`; protocol-constant string-literals → `agentirc.protocol`.
   - Move `protocol/extensions/` out (lives in agentirc now).
   - Move IRCd-targeted tests out of culture; keep transport / shim / mesh / bot / backend tests.
   - Add `tests/test_server_shim.py`: invokes `culture server --help`, asserts it matches `agentirc --help` output to prove the passthrough.
   - `/version-bump major` on culture (deleting the in-tree IRCd is a structural change, even if user-visible CLI is unchanged).
   - `doc-test-alignment` audit before first push (per culture's CLAUDE.md).
3. **Verification on a real deployment:**
   - `pip install -U culture` on a host running `culture-agent-spark-culture.service`.
   - Confirm the service restarts cleanly with the new agentirc dependency.
   - Confirm `culture server status` and `agentirc status` produce identical output.
   - Confirm an existing peer link still establishes (server-link tests in agentirc's CI cover the protocol; this is the in-prod sanity check).

### Distribution

- agentirc → PyPI as `agentirc-cli` (semver). Culture pins `agentirc-cli>=0.1,<0.2`.
- TestPyPI carries both `agentirc-cli` and a squatted `agentirc`; only `agentirc-cli` is the canonical name. Anything publishing or installing should use `agentirc-cli`.
- agentirc adopts culture's version workflow (`/version-bump`, CHANGELOG, version-check CI).
- All-backends rule still applies: changes that cross the agentirc / culture-transport boundary must be reflected across all four backends in culture (`claude`, `codex`, `copilot`, `acp`).

### Rollback

If the cutover PR breaks something not caught in CI, the rollback is a `git revert` of the culture-side PR. The agentirc repo itself is independent and stays. Pinning `agentirc-cli>=0.1,<0.2` means culture cannot accidentally pick up an incompatible 0.2.0 release.

## Testing strategy

### Coverage split

- **agentirc CI** owns:
  - IRCd integration tests (real TCP, real IRC clients).
  - Server-link tests (peer-to-peer mesh).
  - Channel / store / skill unit tests.
  - CLI dispatch tests (argv → handler).
- **Culture CI** owns:
  - Transport tests (`culture/transport/`).
  - Bot tests, backend daemon tests.
  - Mesh manifest tests (`culture/cli/shared/mesh.py`).
  - The shim parity test (`tests/test_server_shim.py`).
  - One end-to-end smoke test that spins up `agentirc serve` from the installed package and connects a culture transport client to it. This is the only cross-repo integration test in culture; it imports nothing from agentirc internals.

### Test-suite migration

Tests under culture's `tests/` are sorted into three buckets at copy time:

1. Imports `culture.agentirc.X` only → moves to `../agentirc/tests/`.
2. Imports `culture.agentirc.client` / `remote_client` only → stays in culture (transport-focused).
3. Imports both → split into two tests, one per repo. If the test is genuinely cross-cutting (a bot connecting to an IRCd in the same process), it stays in culture and is rewritten to use `agentirc serve` as a subprocess fixture rather than importing `IRCd` directly.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Hidden coupling — culture imports something from `culture.agentirc` we missed. | Pre-cutover, `grep -rn 'culture\.agentirc' culture/ tests/` and review every match. The cutover PR includes an explicit checklist of every importer file. |
| Protocol constants drift between agentirc (server) and culture/transport (client). | `agentirc.protocol` is the single source. Both sides import from there; tests on either side fail fast on missing constants. |
| Existing deployment breaks because something on disk is unexpectedly named. | Goals/Non-Goals: nothing on disk is renamed. The verification step on a real deployment catches anything we missed. |
| `culture server --help` output diverges from `agentirc --help` over time. | The shim parity test asserts byte-equality of the help text. CI fails if drift appears. |
| First PyPI release of agentirc is broken; culture cutover PR can't merge. | Culture pins `agentirc-cli>=0.1,<0.2`. Patches go out as `0.1.1`, `0.1.2`. Culture's PR can wait or pin to a specific known-good `==0.1.X`. |
| Future changes touch both repos at once and become hard to coordinate. | All-backends rule already requires multi-backend coordination; the same discipline extends to multi-repo coordination. Significant cross-repo work pairs an agentirc PR with a culture PR that bumps the floor pin. |

## Open questions

None blocking. Design is ready for implementation planning.

## Future work (out of scope)

- Same pattern applied to `culture-agent` (extract backend daemons into their own repo).
- Same pattern applied to `culture-bot` (extract bot runtime).
- Whether `culture/transport/` should also become a separate distribution (e.g. `agentirc-client`) once two or more independent consumers exist. Not now.
- Publishing agentirc reference docs / protocol extensions on a public docs site.
