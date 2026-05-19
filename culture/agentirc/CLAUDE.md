# CLAUDE.md

## What's left in this directory

After Phase A3 (`feat/agentirc-extraction-cutover`, culture 9.0.0) the bundled IRCd is gone. This directory holds only:

- `config.py` — A1 re-export shim over `agentirc.config` (`ServerConfig`, `LinkConfig`, `TelemetryConfig`). Kept through the 9.x line; removed in 10.0.0. New code should import from `agentirc.config` directly.
- `__init__.py` — re-exports the same three symbols so `from culture.agentirc import ServerConfig` keeps working through 9.x.
- `docs/` — AgentIRC reference markdown kept alongside the shim. Now that `culture.dev` is built out of [`agentculture/katvan`](https://github.com/agentculture/katvan) from CLI output rather than this repo's markdown, these pages are project-internal reference only. Can be revisited post-A3.
- `CLAUDE.md` (this file).

That's it. The IRCd itself — the ~4,300 lines of asyncio Python that used to live here — now ships from the [`agentirc-cli`](https://pypi.org/project/agentirc-cli/) PyPI package (repo: [`agentculture/agentirc`](https://github.com/agentculture/agentirc)). Culture imports `agentirc.ircd.IRCd`, `agentirc.virtual_client.VirtualClient`, `agentirc.protocol`, and `agentirc.config` directly.

## Where things moved

| Old (pre-A3) | New |
|---|---|
| `culture/agentirc/ircd.py` | `agentirc.ircd.IRCd` (PyPI; embedded in-process by `culture/cli/chat.py:_run_server`) |
| `culture/agentirc/server_link.py`, `channel.py`, `events.py`, `room_store.py`, `thread_store.py`, `history_store.py`, `rooms_util.py`, `skill.py`, `skills/` | All inside `agentirc-cli` (`agentirc.{server_link,channel,events,...}`); not part of culture's public surface |
| `culture/agentirc/client.py` | `culture/transport/client.py` (`git mv` preserved blame) |
| `culture/agentirc/remote_client.py` | `culture/transport/remote_client.py` |
| `culture/agentirc/rooms_util.parse_room_meta` | `culture/clients/shared/rooms.parse_room_meta` (only that one helper actually used outside the IRCd) |
| `python -m culture.agentirc` | `agentirc` CLI binary, or `python -m agentirc`. Reachable via `culture server <verb>` — culture's CLI partial-passes through to `agentirc.cli.dispatch` for verbs other than the 7 culture-owned ones (`start`/`stop`/`status`/`default`/`rename`/`archive`/`unarchive`). |

## Documentation

The AgentIRC reference markdown still lives under `docs/` next to this shim. It is no longer wired into a site build out of this repo — `culture.dev` is built from [`agentculture/katvan`](https://github.com/agentculture/katvan), which pulls reference content by calling the CLI itself (see issue [#401](https://github.com/agentculture/culture/issues/401)). If/when culture's copy diverges from agentirc's own docs, deduplicate against agentirc/main rather than carrying both.
