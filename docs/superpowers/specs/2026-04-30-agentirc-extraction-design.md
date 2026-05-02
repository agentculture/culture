# AgentIRC Extraction Design

**Status:** In progress — A1 landed in culture 8.8.0 (2026-05-01); A2 blocked on **A2-Bridge** (agentculture/agentirc#22, public IRCd + VirtualClient); A3 unblocked once A2 lands
**Date:** 2026-04-30
**Owner:** Ori Nachum

## Implementation status (updated 2026-05-03)

Track A on the culture side is split into phases because the literal "delete `culture/agentirc/{ircd, ...}.py` and shim `culture server start`" described below cannot land in one PR — culture's bots (`culture/bots/*`) hold an in-process `IRCd` reference and call `server.emit_event`/`server.channels`/`server.get_or_create_channel` directly.

`agentirc-cli==9.5.0` (2026-05-02) shipped the **wire-protocol** half of the bot extension API (`agentirc.io/bot` CAP, `EVENTSUB`/`EVENTUNSUB`/`EVENT`/`EVENTERR`/`EVENTPUB` verbs, public 5-field envelope in `agentirc.protocol`). What it did **not** ship is a public **Python embedding API** — the only currently-public way to drive an IRCd from another Python process is `agentirc.cli.dispatch(argv) -> int`, which runs the CLI to completion with no hook to inject in-process bots. Culture's bot framework is in-process by design. Without a public `IRCd` constructor and a public `VirtualClient` class, A2 has to either re-implement the bot-CAP TCP client from scratch (~300+ LOC, all of which agentirc has internally as `_internal.virtual_client.VirtualClient` but marks off-limits) or backport CAP/`EVENTSUB`/`EVENTPUB` machinery into the bundled IRCd (~500 LOC, all thrown away in A3).

The lighter path is to ask agentirc to promote two existing internal classes to public — `agentirc.ircd.IRCd` and `agentirc._internal.virtual_client.VirtualClient`. That's the **A2-Bridge** track filed as agentculture/agentirc#22 (2026-05-03). Once 9.6.0 ships, culture's A2 collapses to "switch `culture/cli/server.py:_run_server` to construct `agentirc.ircd.IRCd(config)` directly; rewrite `culture/bots/virtual_client.py` as a ~50-LOC wrapper around the promoted `agentirc.virtual_client.VirtualClient`."

| Phase | Scope | Status |
|---|---|---|
| **B** (agentirc bootstrap) | `agentirc-cli==9.4.1` published, public API stable, on-disk layout preserved | ✅ done (see culture#308) |
| **A1** (culture-side public-API migration) | Pin `agentirc-cli>=9.4,<10`; `culture/agentirc/config.py` becomes a re-export shim over `agentirc.config`; canonical call sites (telemetry, mesh CLI, conftest) retarget; minor bump 8.7.1 → 8.8.0 | ✅ done 2026-05-01 (culture#309) |
| **A2-Bridge** (agentirc-side public-API expansion) | Promote `agentirc.ircd.IRCd` and `agentirc._internal.virtual_client.VirtualClient` to public surface. Document in `agentirc/docs/api-stability.md`. Transitional re-export at the old internal path through 9.6.x. agentirc minor bump 9.5 → 9.6. | ⏸ filed at agentculture/agentirc#22 (2026-05-03) |
| **A2** (culture-side bot rewrite, embedded mode) | Bump dep floor to `agentirc-cli>=9.6,<10`; switch `culture/cli/server.py:_run_server` to `agentirc.ircd.IRCd(config)`; rewrite `culture/bots/virtual_client.py` as a thin wrapper over `agentirc.virtual_client.VirtualClient`; webhook listener (`webhook_port`) moves from `culture/agentirc/ircd.py` to `culture/bots/bot_manager.py`; minor bump 8.8.0 → 8.9.0 | ⏸ blocked on A2-Bridge — see `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a2.md` |
| **A3** (final cutover) | Delete the bundled IRCd: `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py` + `culture/agentirc/skills/`; `git mv` `culture/agentirc/{client,remote_client}.py` → `culture/transport/`; A2 already swapped `_run_server` to embedded `agentirc.ircd.IRCd`, so this phase is mostly deletion + culture-owned verb preservation (`default`/`rename`/`archive`/`unarchive`); keep `culture/agentirc/config.py` as the A1 re-export shim through 9.x; major bump 8.x → 9.0 | ⏸ ready after A2 — see `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a3.md` |

The Boundary / Plan sections below describe the A3 end state. They
remain the target; the phased approach is purely about how culture
gets there without breaking bots in flight.

## Federation interop during the migration window

`agentirc-cli==9.5.0` ships a canonical 5-field envelope on the
federation wire (`SEVENT` payloads and the IRCv3 `event-data` tag on
`#system` PRIVMSGs):

```json
{"type": "user.join", "channel": "#room", "nick": "alice", "data": {"text": "hi"}, "timestamp": 1777680000.0}
```

9.5 receivers sniff for the envelope and tolerate the legacy data-only
shape, so **≤9.4 → 9.5 federation works**. The reverse direction
(**9.5 → ≤9.4**) breaks because pre-9.5 daemons have no sniff and
fail to find expected fields. Culture's bundled IRCd is ≤9.4 until A3
deletes it, so any operator running a `culture server` instance
peered with an upgraded `agentirc serve` peer will see one-way drops.

Mitigation (optional): mirror agentirc 9.5's ~10-LOC envelope sniff
into `culture/agentirc/server_link.py:_handle_sevent`. After the
existing `_decode_sevent_payload` call, if the decoded dict has the
canonical 5 keys with `data` as a top-level dict, treat the value of
`data` as the legacy data-only payload that the rest of `_handle_sevent`
already expects; otherwise fall through unchanged. Zero behavior
change for ≤9.4 → ≤9.4 traffic, fixes the 9.5 → ≤9.4 break. Reference:
[agentirc#15 comment 4363115522](https://github.com/agentculture/agentirc/issues/15#issuecomment-4363115522).

Ship as a small standalone patch PR (8.8.x patch bump) if mixed-version
peers are expected during the A2/A3 window. Independent of the A2/A3
plans below; not required when A3 lands.

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
- Culture consumes it as a normal dependency (`agentirc-cli>=9.6,<10.0`).
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
| `culture/cli/shared/mesh.py` | imports `LinkConfig` from `agentirc.config` | Mesh manifest read/write stays culture-side. |
| `culture/bots/`, `culture/clients/*/daemon.py` | imports updated to `culture.transport` | Transport ownership unchanged from the consumer's POV. |
| `packages/agent-harness/` | unchanged | Agent backend citation reference is culture's concern. |

### What is deleted from culture

- `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py` and `culture/agentirc/skills/`.
- `culture/agentirc/__main__.py` (no replacement; `python -m culture.agentirc` is gone).
- `culture/agentirc/{client,remote_client}.py` are **moved**, not deleted — `git mv` to `culture/transport/{client,remote_client}.py`. They're transport code, not IRCd code, and they stay in culture (see "What stays in culture" above).
- `culture/agentirc/config.py` is **kept** as an A1-introduced re-export shim over `agentirc.config` through the 9.x line, then removed in 10.0.0. (Decision deferred to A3 in `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a3.md`.)
- `culture/bots/http_listener.py` is **not** deleted — culture takes ownership of the webhook listener in A2 because agentirc 9.5 stops binding `webhook_port`. The listener moves from being driven by `culture/agentirc/ircd.py` (today) to being driven by `culture/bots/bot_manager.py`.

## Architecture

### Repo layout — `../agentirc`

```
agentirc/
├── pyproject.toml          # name = "agentirc-cli"; scripts = { agentirc = "agentirc.cli:main" }
├── CHANGELOG.md            # starts at 9.0.0 (aligned with culture's version stream)
├── README.md
├── LICENSE
├── agentirc/
│   ├── __init__.py
│   ├── __main__.py         # python -m agentirc
│   ├── cli.py              # main(), dispatch(argv)
│   ├── protocol.py         # verb names, numerics, extension tags
│   ├── config.py           # ServerConfig, LinkConfig, TelemetryConfig (public)
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
├── pyproject.toml          # adds: agentirc-cli>=9.6,<10.0
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
| `agentirc.config` | `ServerConfig`, `LinkConfig`, `TelemetryConfig`, plus dataclass fields | Public, semver-tracked. Breaking changes require a major bump. |
| `agentirc.cli` | `main()`, `dispatch(argv) -> int` | Public, semver-tracked. |
| `agentirc.protocol` | Verb name constants (incl. `EVENTSUB`, `EVENTUNSUB`, `EVENT`, `EVENTERR`, `EVENTPUB`), numeric reply codes, extension tag names, the canonical 5-field `Event` envelope (`type`/`channel`/`nick`/`data`/`timestamp`), per-type `EVENT_TYPE_*` string constants, `BOT_CAP = "agentirc.io/bot"` | Public, semver-tracked. Wire format byte-locked under semver (see agentirc `tests/test_wire_format_envelope.py::test_envelope_byte_lock`). |
| `agentirc.ircd` | `IRCd` (constructor + lifecycle) | 🟡 **Pending agentirc#22 (A2-Bridge).** Currently listed as internal in `agentirc/docs/api-stability.md`; promotion is the prerequisite for culture A2 to embed it in-process. After 9.6.0, public + semver-tracked. |
| `agentirc.virtual_client` | `VirtualClient` (in-process bot, duck-typed Client/RemoteClient) | 🟡 **Pending agentirc#22 (A2-Bridge).** Today at `agentirc._internal.virtual_client.VirtualClient`; A2-Bridge promotes to `agentirc.virtual_client.VirtualClient` (or `agentirc.bot.VirtualClient` if agentirc prefers that namespace). After 9.6.0, public + semver-tracked, with transitional re-export at the old internal path through 9.6.x. |
| `agentirc.skill` | Re-exports `Event`, `EventType` from `agentirc.protocol` | **Transitional** — present through the 9.5.x line, removed in 10.0.0. New code should import from `agentirc.protocol` directly. |

Everything else — `agentirc.server_link`, `agentirc.channel`, the stores, the in-tree skills, anything under `agentirc._internal.*` — is internal. Agentirc may refactor freely without breaking culture. Documented in `agentirc/docs/api-stability.md`.

### Where culture imports from agentirc

| Culture module | Imports |
|---|---|
| `culture/cli/server.py` | `agentirc.cli.dispatch` (unchanged); after A2, also `agentirc.ircd.IRCd` (embeds in-process for `culture server start`) |
| `culture/cli/shared/mesh.py` | `agentirc.config.LinkConfig` |
| `culture/config.py`, `culture/telemetry/{metrics,tracing,audit}.py`, `tests/conftest.py` | `agentirc.config.{ServerConfig, LinkConfig, TelemetryConfig}` (since A1, culture#309) |
| `culture/transport/client.py` | `agentirc.protocol.*` (verb / numeric / tag constants) |
| `culture/bots/virtual_client.py` (after A2) | `agentirc.virtual_client.VirtualClient` (thin wrapper for culture-specific concerns: BotConfig, template engine, fires_event, owner DM) |
| `culture/bots/{bot,bot_manager}.py` (after A2) | `agentirc.protocol.{Event, EventType}` for envelope decoding; `IRCd` reference comes from `culture/cli/server.py:_run_server` |
| `culture/clients/*/daemon.py` | `culture.transport` only (no agentirc internals) |

## Configuration & on-disk footprint

Unchanged from today. `agentirc` reads the same files culture reads now:

- Default config path: `~/.culture/server.yaml`.
- Socket paths: same as current culture defaults.
- systemd units: `culture-agent-<name>.service` (unchanged).
- Log paths: unchanged.

Standalone (non-culture) users of agentirc can override the config path via `agentirc serve --config /path/to/their.yaml`.

## Migration mechanics

The migration runs across **multiple cross-repo tracks** owned by different agents. The culture-side agent (this work) implements culture-side tracks (A1, A2, A3) and produces hand-off briefs (Track B for the original bootstrap, A2-Bridge for the embedding-API expansion). Briefs are handed to the agent working in `../agentirc`. Culture-side cutover PRs **wait** on the agentirc releases the briefs trigger.

### Track A2-Bridge — hand-off brief for the agentirc agent (filed as agentculture/agentirc#22 on 2026-05-03)

This block is **handed to the agent working in `../agentirc`** and is intended to be self-contained — the receiving agent should not need culture-side context to act on it. It is mirrored here from the GitHub issue body for searchability.

**One-line ask:** Promote `agentirc._internal.virtual_client.VirtualClient` and `agentirc.ircd.IRCd` to the public API surface so a Python embedder can construct an IRCd in-process and host bots inside it.

**Why this exact shape:**

- `_internal.virtual_client.VirtualClient` already encapsulates everything a co-hosted bot needs: `caps = frozenset({BOT_CAP, "message-tags"})` so it gets the silent-JOIN / no-auto-op / `+`-prefix / `B`-flag treatment exactly like a CAP-negotiated TCP bot, plus `join_channel(name, *, emit_event)`, `part_channel(name)`, and the duck-typed Client/RemoteClient interface for `channel.members` / NAMES / WHO / WHOIS. agentirc's own `#system` welcome bot consumes this class today. Culture's `culture/bots/virtual_client.py` is functionally identical to it.
- `agentirc.ircd.IRCd` is the constructor. Culture's `culture server start` needs a public way to instantiate one in-process and add bots to it. The class is already importable; it just sits under "Internal modules" in `docs/api-stability.md`.
- If agentirc prefers a different layout (e.g. `agentirc.host.Host` as a façade owning an internal IRCd + exposing `host.add_bot(VirtualClient(...))`), that's fine. The constraint is the **capability**, not the names.

**Concrete asks:**

1. **Promote `IRCd`.** Move `agentirc.ircd` out of the "Internal modules" list in `docs/api-stability.md`. Document the public constructor signature, lifecycle methods (`start`/`stop`), and bot-extension hooks (event emission, subscription registration). If the current internal API surface is too wide, expose a narrower public façade and keep the wider class internal.
2. **Promote `VirtualClient`.** Move `agentirc._internal.virtual_client` → `agentirc.virtual_client` (or `agentirc.bot.VirtualClient` if a `bot` namespace is preferred). Keep a transitional re-export at the old path through 9.6.x; remove in 10.0.0 with a deprecation warning across that window.
3. **Federation interop sniff (optional).** While agentirc is touching `_handle_sevent` for any reason, please consider mirroring the 9.5 → ≤9.4 envelope sniff back to a 9.4.x patch release. If awkward, no problem — culture can ship the workaround in `culture/agentirc/server_link.py` until A3 deletes that file.
4. **Out of scope.** No new code is required — this is mostly relabeling existing internals to public, plus documentation. No protocol changes. No CLI changes.

**Acceptance criteria:**

- `agentirc-cli==9.6.0` (or whatever minor) is on PyPI.
- `from agentirc.virtual_client import VirtualClient` works (or whatever public name).
- `from agentirc.ircd import IRCd` works AND `agentirc/docs/api-stability.md` lists it under public modules.
- The transitional `_internal.virtual_client.VirtualClient` re-export still works (deprecation warning OK).
- `agentirc/docs/api-stability.md` has a section explaining the embedding pattern with a minimal worked example.

This is a minor bump for agentirc — additive, no breaking change.

### Track A — culture-side (implemented in this repo)

A single PR off `main`, following culture's standard workflow.

1. `pyproject.toml`: add `agentirc-cli>=9.6,<10.0`. Regenerate `uv.lock`.
2. Delete `culture/agentirc/` entirely.
3. Move `client.py`, `remote_client.py` → `culture/transport/`. Add `culture/transport/__init__.py` re-exporting the public class names.
4. Replace `culture/cli/server.py` with the passthrough shim (see "CLI surface" above).
5. Update imports across culture:
   - `culture.agentirc.{client,remote_client}` → `culture.transport.{client,remote_client}`
   - `culture.agentirc.config` → `agentirc.config`
   - Protocol-constant string-literals in `culture/transport/client.py` → `agentirc.protocol.*`
6. Move `protocol/extensions/` out of culture (it lives in agentirc now).
7. Move IRCd-targeted tests out of culture (they live in agentirc now); keep transport / shim / mesh / bot / backend tests.
8. Add `tests/test_server_shim.py`: invokes `culture server --help` and asserts it matches `agentirc --help` output to prove the passthrough is byte-faithful.
9. `/version-bump major` on culture (deleting the in-tree IRCd is a structural change even though user-visible CLI is unchanged).
10. Run the `doc-test-alignment` subagent before the first push (per culture's CLAUDE.md).
11. CI must be green and `agentirc-cli==<chosen pin>` must be installable from PyPI before merge.

### Track B — hand-off brief for the agentirc agent

This block is the deliverable. It is **handed to the agent working in `../agentirc`** and is intended to be self-contained — the receiving agent should not need culture-side context to act on it.

**Goal:** Bootstrap `../agentirc` as a publishable Python package called `agentirc-cli` (import name `agentirc`, CLI binary `agentirc`) carrying the IRCd server core extracted from culture, plus a new CLI dispatch module and protocol-constants module. Tag `v9.0.0` and publish to PyPI.

**Inputs:**

- `../culture/culture/agentirc/` — server-core source, *minus* `client.py`, `remote_client.py`, `__main__.py` (those stay in culture).
- `../culture/protocol/extensions/` — protocol docs, moved wholesale.
- `../culture/tests/` — any test that imports `culture.agentirc.*` and is *not* transport-focused; sort per "Test-suite migration" below.
- `../culture/culture/cli/server.py` — current server-lifecycle CLI dispatch; the new `agentirc/cli.py` is extracted from this.
- The source-of-truth culture commit SHA at the time of copy (caller will provide).

**Tasks:**

1. Create the package layout under `../agentirc/agentirc/` exactly as specified in "Repo layout — `../agentirc`" earlier in this spec.
2. Copy each server-core file from culture into `../agentirc/agentirc/` per the "What moves to `../agentirc`" table.
3. Copy `protocol/extensions/` from culture into `../agentirc/protocol/extensions/`.
4. Sort the relevant tests from culture's `tests/` into `../agentirc/tests/` per the "Test-suite migration" rules in this spec's Testing section.
5. Rewrite imports inside the new tree: `from culture.agentirc.X` → `from agentirc.X`. There must be no remaining `culture.` imports in agentirc.
6. Create `agentirc/cli.py` from culture's `cli/server.py` server-lifecycle code. Expose:
   - `main()` — entrypoint for the `agentirc` console script.
   - `dispatch(argv: list[str]) -> int` — the function culture's shim will call. Same flag set, same exit codes, same output as the CLI binary.
   - Subcommands per the "CLI surface" table (`serve`, `start`, `stop`, `restart`, `status`, `link`, `logs`, `version`).
   - `--config` defaults to `~/.culture/server.yaml`.
7. Create `agentirc/protocol.py`. Pull verb names, numeric reply codes, and extension tags out of string-literals in `ircd.py` (and in culture's `client.py` — read it for reference but do **not** copy the file). Export them as named constants.
8. Create `agentirc/__main__.py` so `python -m agentirc` works (delegates to `agentirc.cli:main`).
9. Write `pyproject.toml`: `name = "agentirc-cli"`, `version = "9.0.0"` (aligned with culture's version stream — agentirc-cli prior history runs through 8.7.1; 9.0.0 is the first release of the extracted IRCd), scripts `agentirc = "agentirc.cli:main"`. Mirror culture's dev-dep set (pytest, pytest-asyncio, pytest-xdist, black, isort, flake8, pylint, bandit, markdownlint).
10. Mirror culture's pre-commit, CI, and `/version-bump`/CHANGELOG workflow. CHANGELOG starts at `9.0.0`.
11. Write `docs/api-stability.md` documenting the public surface culture pins on: `agentirc.config`, `agentirc.cli`, `agentirc.protocol`. Mark everything else internal.
12. First commit message: `Initial import from culture@<SHA>` (where `<SHA>` is the culture commit ID provided by the caller).
13. Run the full test suite — it must pass before tagging.
14. Tag `v9.0.0`, push, publish to PyPI as `agentirc-cli`. (PyPI publishing is already set up.)
15. Report back the published version + git SHA so culture's Track A PR can pin against it.

**Out of scope for Track B:**

- Editing culture. Track B touches `../agentirc` only.
- Rewriting any of the moved code beyond import-path adjustments and the new `cli.py`/`protocol.py` modules. The IRCd, stores, channels, server-link, and skills are copied as-is.
- Renaming on-disk artifacts (config paths, sockets, systemd unit names) — they stay culture-named per the Configuration section.
- Publishing protocol/extensions docs externally.

**Acceptance criteria:**

- `pip install agentirc-cli==9.0.0` from PyPI produces a working `agentirc` binary.
- `agentirc serve --config ~/.culture/server.yaml` starts an IRCd indistinguishable from today's `culture server start`.
- `agentirc.config.LinkConfig`, `agentirc.config.ServerConfig`, `agentirc.config.TelemetryConfig`, `agentirc.cli.dispatch`, and `agentirc.protocol.*` are importable.
- All tests in `../agentirc/tests/` pass under `pytest -n auto`.
- `git grep -E '^(from|import) culture' agentirc/ tests/` returns nothing.

### Track C — verification on a real deployment

After Track A merges and culture is released:

- `pip install -U culture` on a host running `culture-agent-spark-culture.service`.
- Confirm the service restarts cleanly with the new `agentirc-cli` dependency.
- Confirm `culture server status` and `agentirc status` produce byte-identical output.
- Confirm an existing peer link still establishes (server-link tests in agentirc's CI cover the protocol; this is the in-prod sanity check).

### Distribution

- agentirc → PyPI as `agentirc-cli` (semver). Culture pins `agentirc-cli>=9.6,<10.0`.
- TestPyPI carries both `agentirc-cli` and a squatted `agentirc`; only `agentirc-cli` is the canonical name. Anything publishing or installing should use `agentirc-cli`.
- agentirc adopts culture's version workflow (`/version-bump`, CHANGELOG, version-check CI).
- All-backends rule still applies: changes that cross the agentirc / culture-transport boundary must be reflected across all four backends in culture (`claude`, `codex`, `copilot`, `acp`).

### Rollback

If the cutover PR breaks something not caught in CI, the rollback is a `git revert` of the culture-side PR. The agentirc repo itself is independent and stays. Pinning `agentirc-cli>=9.6,<10.0` means culture cannot accidentally pick up an incompatible 10.0.0 release.

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
| An agentirc-cli release introduces a regression that breaks culture. | Culture pins `agentirc-cli>=9.6,<10.0`. Patches go out as `9.6.1`, `9.6.2`, etc. Culture can pin to a specific known-good `==9.6.X` if a patch in the floor range turns out to be broken. |
| Future changes touch both repos at once and become hard to coordinate. | All-backends rule already requires multi-backend coordination; the same discipline extends to multi-repo coordination. Significant cross-repo work pairs an agentirc PR with a culture PR that bumps the floor pin. |

## Open questions

None blocking. Design is ready for implementation planning.

## Future work (out of scope)

- Same pattern applied to `culture-agent` (extract backend daemons into their own repo).
- Same pattern applied to `culture-bot` (extract bot runtime).
- Whether `culture/transport/` should also become a separate distribution (e.g. `agentirc-client`) once two or more independent consumers exist. Not now.
- Publishing agentirc reference docs / protocol extensions on a public docs site.
