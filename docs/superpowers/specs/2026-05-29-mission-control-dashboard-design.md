---
title: "Mission Control Dashboard"
parent: "Design"
nav_order: 26
---

# Mission Control Dashboard

**Status:** Draft
**Date:** 2026-05-29
**Depends on:** #411 (permission broker, audit + daemon logs) and #412 (boss agent, `culture boss`, grant ceiling)
**Branch:** `feat/mission-control-dashboard`

## Problem

The boss/worker machinery produces everything needed to watch and steer a run —
per-agent audit logs, daemon-action logs, a permission queue, IRC channels — but
there is no single place to *see it all and intervene*. Today you assemble
terminal panes (`watch-channels.sh`, `tail audit/<nick>.jsonl`,
`culture boss pending`, …). The operator wants **one local control panel**: watch
the boss and every worker live, and when something goes out of hand, take the
wheel — approve/deny, pause/resume, kill, or stop everything.

## Goal

A **local web app** (`culture dashboard`) that, read-side, streams every agent's
activity + pending approvals + status into one browser view, and, control-side,
exposes the full intervention surface. Localhost-only; reuses the existing data
files and control levers (no new control semantics, just a UI over them).

## Why web (not desktop)

Everything the panel needs is already local: JSONL logs under `~/.culture/`, the
permission queue, daemon IPC sockets, and the `culture` CLI. A small
`aiohttp` server (aiohttp is already a dependency — `pyproject.toml`,
used by `culture/bots/http_listener.py`) serving a vanilla-JS single-page app
needs **no new dependency and no build step**. Desktop (Electron/Tauri) would add
packaging overhead for no gain on a single-user local machine.

## Architecture

```text
 browser (SPA, vanilla JS + EventSource)
        │  GET /  /static/*           (UI)
        │  GET /api/agents            (poll: status grid)
        │  GET /api/stream/audit/<nick>      (SSE: agent session)
        │  GET /api/stream/daemon-log/<nick> (SSE: control-plane actions)
        │  GET /api/stream/pending           (SSE: pending approvals)
        │  GET /api/channel/<chan>    (poll: IRC exchanges, read-only)
        │  POST /api/approve|deny|pause|resume|close|stop-all|policy  (control)
        ▼
 aiohttp.web.Application  (culture/dashboard/server.py)
        │  reuses: _perm_broker (list_pending/read_request/write_decision,
        │          policy files), _audit/_daemon_log paths, shared.ipc
        │          (agent_socket_path/ipc_request), `culture agent` CLI
        ▼
 ~/.culture/{audit,daemon-log,perm-queue,perm-decisions,perm-policy}/  + daemon sockets
```

- **Bind `127.0.0.1` only** (like `serve_web` in `culture/overview/renderer_web.py:251`).
- **No build step**: the SPA is hand-written HTML/CSS/JS served as static files
  from `culture/dashboard/static/`. `EventSource` (SSE) for live streams; `fetch`
  for control POSTs. (Polling fallback for `/api/agents` and channel reads.)
- **One CLI command**: `culture dashboard [--port 8787] [--host 127.0.0.1]`,
  registered as a `mesh` subcommand or top-level group, mirroring `serve_web`.

## Read side (live views)

| Endpoint | Source | Shape |
|---|---|---|
| `GET /api/agents` | **programmatic** (not CLI-text): `load_config_or_default` for the agent manifest + `pidfile.read_pid("agent-<nick>")` + `process.is_process_alive(pid)` for state + `perm-queue` counts | `[{nick, state, pending, last_action}]`, polled ~2s |
| `GET /api/stream/audit/<nick>` | tail `~/.culture/audit/<nick>.jsonl` | SSE, one event per new line (agent text + tool_uses + tool_results) — **the agent's session screen** |
| `GET /api/stream/daemon-log/<nick>` | tail `~/.culture/daemon-log/<nick>.jsonl` | SSE, one event per action |
| `GET /api/stream/pending` | watch `~/.culture/perm-queue/` | SSE, current pending list on change |
| `GET /api/channel/<chan>` | read-only via `culture.observer` (same path as `culture channel read`) | recent messages — **the exchanges** |

SSE tailers: an async task per connection that emits a bounded backlog (last N
lines) then `seek`s to EOF and polls for appended lines (250ms), emitting each as
an SSE `data:` line. On client disconnect, writing to the closed
`StreamResponse` raises `ConnectionResetError`/`asyncio.CancelledError` — the
handler catches it, stops the tail task, and returns (no leaked tasks/handles). A
periodic SSE comment (`: keepalive`) keeps proxies/idle connections open.

## Control side (full intervention)

All reuse existing levers; the dashboard is the **human** operator, so it is the
top authority — its approvals are **not** ceiling-bounded (unlike `culture boss
approve`).

| Endpoint | Action | Implementation |
|---|---|---|
| `POST /api/approve {id, scope?, pattern?}` | grant a pending request | `_perm_broker.write_decision(id, verdict="allow", decided_by="dashboard")` — no ceiling check (human is top authority) |
| `POST /api/deny {id, reason?}` | deny a request | `write_decision(id, verdict="deny", reason)` |
| `POST /api/pause {nick}` / `resume {nick}` | pause/resume an agent | `shared.ipc.ipc_request(agent_socket_path(nick), "pause"/"resume")` |
| `POST /api/close {nick}` | kill one agent | `culture agent stop <nick>` (subprocess) |
| `POST /api/stop-all {mode}` | **emergency**: `mode=pause` → pause every agent; `mode=kill` → `culture agent stop --all` | the big red button |
| `GET/POST /api/policy/<nick>` | view/edit a worker's grant policy | read/write `perm-policy/<nick>.yaml` via `_perm_broker` helpers (atomic) |

Control POSTs return `{ok, error?}`. The UI confirms destructive actions
(close, stop-all kill) with a modal.

## Security model

Same-machine, same-UID, **localhost-bound**. The panel can approve tool calls and
kill agents — but anyone who can reach `127.0.0.1:<port>` as this user already
has shell access and could do the same via the CLI/files. Consistent with the
broker's threat model (the dashboard adds no privilege). v1: no auth token (note
it as a possible hardening if the host is shared). Never bind a non-loopback
interface; the CLI rejects a non-loopback `--host` unless `--unsafe-bind` is
passed (explicit opt-in, documented as dangerous).

## Live verification (folded into the build — the missing proof)

The dashboard is also how we first prove the #411/#412 stack runs live. Phase L:
bootstrap a real mesh, `culture boss init`, start the boss daemon, brief it to
spawn + drive one worker, and confirm in the browser that (a) the worker's audit
stream shows activity, (b) a permission request appears in the pending panel,
(c) approving it from the panel unblocks the worker, (d) pause/close/stop-all
work. Any failure here is a real bug in #411/#412 to fix.

## Testing strategy

Per project convention: real I/O, no mocks; `aiohttp.test_utils` for the server.

| Test | Verifies |
|---|---|
| `test_dashboard_api.py` | `/api/agents` shape from a seeded `CULTURE_HOME`; `/api/stream/pending` emits the seeded queue; control POSTs (`approve`/`deny`) write the correct decision files via the broker (human authority — above-ceiling tool still approved). Uses `aiohttp` test client + isolated `CULTURE_HOME`. |
| `test_dashboard_control.py` | `approve` writes `decided_by=dashboard` with no ceiling refusal; `deny` carries reason; double-decision returns the broker's `DecisionExistsError` as a 409; `stop-all` invokes the expected CLI/IPC (patched at the subprocess boundary). |
| `test_dashboard_tail.py` | The SSE file-tailer emits appended JSONL lines in order and survives a missing/rotated file. |

The SPA itself is verified live in-browser (Playwright) during Phase L, not unit-tested.

## Files

New:
- `culture/dashboard/__init__.py`, `culture/dashboard/server.py` (aiohttp app + endpoints + SSE tailer), `culture/dashboard/static/{index.html,app.js,style.css}`.
- `culture/cli/` — register `culture dashboard` (new group or `mesh dashboard` subcommand).
- `docs/agentirc/dashboard.md`.
- Tests above.

Changed:
- `culture/cli/__init__.py` — register the dashboard command.
- `_perm_broker.py` — only if a small read/write-policy helper is missing (reuse existing where possible).

## Phases

| Phase | Work | Gate |
|---|---|---|
| D1 | aiohttp server skeleton + `/api/agents` + `culture dashboard` CLI (localhost bind). | `test_dashboard_api.py` agents endpoint green; `culture dashboard` serves. |
| D2 | SSE tailer + audit/daemon-log/pending streams. | `test_dashboard_tail.py` green. |
| D3 | Control endpoints (approve/deny/pause/resume/close/stop-all/policy). | `test_dashboard_control.py` green. |
| D4 | Vanilla-JS SPA: agent grid, per-agent session stream, pending-approvals panel with buttons, control bar (pause/resume/close/STOP-ALL), channel view. | Loads in browser; renders seeded data. |
| L  | Live bring-up: real boss + worker; verify watch + intervene in-browser (Playwright). | The 4 live checks above pass; fix any #411/#412 bug surfaced. |
| D5 | Docs (`dashboard.md`) + version bump + quality gates + PR. | doc-test-alignment + code-reviewer clean; CI green. |

## Non-goals (v1)

- Auth/multi-user/remote access (localhost single-user only).
- Editing/replaying agent history; only view + intervene.
- Redirect/message-injection into agent channels from the panel (deferred — watch + stop/approve/policy is v1; two-way steering is a follow-up).
- A build-tooling frontend (React/Vite); vanilla JS only, no node build.
- Mobile layout / theming polish beyond a usable dark UI.

## Open questions

1. **Channel view fidelity.** `/api/channel` reads via the observer (recent
   buffer). Full live channel streaming (SSE) is deferred unless the per-agent
   audit stream proves insufficient for "seeing exchanges".
2. **Top-level `culture dashboard` vs `culture mesh dashboard`.** Leaning
   top-level for discoverability; confirm during build.
