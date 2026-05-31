---
title: "Mission Control Dashboard"
parent: "Design"
nav_order: 26
---

# Mission Control Dashboard

**Status:** Draft (partially superseded — see v8.19.x update below)
**Date:** 2026-05-29
**Depends on:** #411 (permission broker, audit + daemon logs) and #412 (boss agent, `culture boss`, grant ceiling)
**Branch:** `feat/mission-control-dashboard`

## v8.19.x update — 2026-05-31

Status header for the original 2026-05-29 spec: superseded for many sections by what shipped between v8.19.0 and v8.19.24. This section maps the original spec to the current implementation and adds what was added.

### Channels-first tab + Channels as Tasks reframe (v8.19.7, v8.19.11, v8.19.22)

The default dashboard tab is now **Channels** (not Agents). v8.19.7 inverted the layout so the left column renders channel cards with member chips, role badges, and state dots. v8.19.11 restructured the Channels tab to group by **Task** — one task group per boss agent, headed by a state dot + title (from seed / mission.md / boss nick) + boss badge + worker count. v8.19.22 adopted the user's data-model clarification: **Channel = Task scope**; rooms inside a Channel are `#boss` (BOSS), `#team`/`#joint-*` (SHARED), and `#task-<worker>` (WORKER). The group heading now reads "CHANNEL <title>" with a channel-level token total. Source: `list_tasks()` in `server.py`, `renderTaskGroup()` / `renderChannelCard()` in `app.js`, `/api/tasks` endpoint.

### PersistentObserver replacing per-poll peek connections (v8.19.17)

The dashboard originally opened a fresh TCP+IRC connection for every channel-read poll (~24 ephemeral connections/min). v8.19.17 introduced `PersistentObserver` in `culture/observer.py` — a single long-lived IRC connection that lazy-JOINs channels and auto-reconnects after a server bounce. Wired into the dashboard via aiohttp `cleanup_ctx` at `_persistent_observer_lifecycle`; stored at `app[_OBSERVER]`. Falls back to ephemeral `get_observer()` if instantiation fails. Nick uses `_peek` prefix so server-side event suppression (v8.19.13) hides the observer's JOINs from other members. Source: `culture/observer.py`, `server.py:_persistent_observer_lifecycle`, `server.py:_observer_for`.

### Watcher daemon — 5 patterns + 3 sinks (v8.19.19)

A new out-of-process deterministic watcher reads `daemon-log/*.jsonl`, `audit/*.jsonl`, and `perm-queue/` and evaluates 5 failure patterns: `silent_death`, `crash_burst` (>=3 in 5 min), `token_spike` (>50k input tokens in 10 min), `perm_escalation_above_ceiling`, and `mission_stuck` (boss >=2 h with no engaged activity). Alerts route to 3 sinks: IRC (on by default, to boss + `#alerts`), email (opt-in SMTP), and webhook (opt-in JSON POST). Per-pattern cooldown dedupe (600 s default). Config at `~/.culture/watcher.yaml`; CLI: `culture watcher {start,once,status,test}`. Reuses `PersistentObserver` for IRC alerts. Source: `culture/watcher/{patterns,alerts,state,service}.py`, `docs/watcher.md`.

### Seed briefs + living channel briefs (v8.19.18, v8.19.24)

**Seed (v8.19.18):** `culture boss spawn --topic "..."` sets the IRC TOPIC and persists the full text to `~/.culture/seeds/task-<worker>.md`. `culture boss brief` auto-seeds on first brief if no seed exists. Dashboard renders a collapsible "Seed brief" panel per channel card with lazy-fetch of the full text via `/api/channels/<name>/seed`. Task-group title falls back through seed then mission.md then boss nick. Source: `culture/clients/_seed.py`, `server.py:_handle_channel_seed`, `app.js` seed toggle.

**Living brief (v8.19.24):** `~/.culture/briefs/<channel>.md` — a running onboarding doc that grows as work progresses. `culture boss brief` auto-appends a dated Markdown section on every send; `culture boss note` adds explicit non-task updates. All 4 backends inject the brief into the SDK system prompt on worker boot (capped at 64 KiB tail). Dashboard endpoint `/api/channels/<name>/brief` returns full text + size. Source: `culture/clients/_channel_brief.py`, `server.py:_handle_channel_brief`.

### Role badges and state dots (v8.19.4, v8.19.7)

`role:` field added to `AgentConfig` + `--role` spawn flag (v8.19.4). The dashboard renders a `.role-badge` on each member chip and a `.member-dot` colored by state (green=running, grey=stopped, amber=unknown). Boss members are highlighted with amber text + `is-boss` class. Source: `list_agents()` and `list_channels()` in `server.py` (both stamp `role`), `app.js` member chip rendering.

### Per-task and per-agent token counters (v8.19.21–v8.19.22)

New `culture/clients/_usage.py` persists per-turn token records to `~/.culture/usage/<nick>.jsonl`. Wired through all 4 backends (Claude actively records; codex/copilot/acp stub). `list_tasks()` stamps every member with `tokens_used`/`tokens_in`/`tokens_out` and every channel with `tokens_total`. Channel-level total uses a unique-nicks set so the boss (present in every room) is counted once. Frontend renders `.channel-tokens-total` per room card and `.task-tokens-total` on the Channel heading (prominent blue badge), plus `.token-badge` per member chip. `formatTokens()` helper: 999=`999t`, 12345=`12.3k`. Source: `culture/clients/_usage.py`, `server.py:list_tasks`, `app.js:formatTokens`.

### Cache-bust + no-cache headers (v8.19.16)

`_handle_index` now rewrites asset URLs to `/static/app.js?v=<version>` (sourced from `culture.__version__`). `index.html` is served with `Cache-Control: no-cache, no-store, must-revalidate` + `Pragma: no-cache` + `Expires: 0`. Static assets remain cacheable per-URL but each version bump invalidates automatically — eliminates the "hard-refresh to pick up a hotfix" trap. Source: `server.py:_handle_index`, `server.py:_ASSET_BUSTER`.

### List-flicker / sticky tabs / chat-per-room (v8.19.14–v8.19.22)

**Flicker fix (v8.19.14–v8.19.15):** `refreshChat` now diffs the message list and appends only the delta (no more `replaceChildren()` every 2.5 s). `renderActivityTurn` and `appendStreamLine` snapshot `isAtBottom(box)` before appending and only auto-scroll if the user was already at the bottom. `withListSnapshot(listId, data, render)` JSON-snapshots incoming data and skips re-render when identical; on a real change, preserves `scrollTop`. Source: `app.js:withListSnapshot`, `app.js:refreshChat`.

**Sticky tabs (v8.19.22):** `#stream-title` and `.stream-tabs` use `position: sticky` at the top of the centre column so they remain visible when scrolling deep into history. Source: `style.css` lines 240–258.

**Chat-per-room (v8.19.22):** `state.selectedChannel` carries the clicked room name. Card body click sets it (routes Chat tab to `/api/channels/<name>/messages`); chip click clears it (reverts to the agent's home channel via `/api/channel/<nick>`). Fixes the bug where clicking `#team` showed an empty `#task-<first-worker>`. Source: `app.js:refreshChat`, `app.js:renderChannelCard`.

### Archive/restore flow (v8.19.1, v8.19.2)

Agent archiving: `culture agent archive <nick>` sets `state: archived`; `culture agent restore <nick>` moves back to active/stopped. Channel archiving: `CHANARCHIVE` IRC verb refuses new JOINs + hides from LIST but preserves history. Dashboard: Archived tab with `POST /api/archive` (stops daemon first, refuses if stop fails) and `POST /api/unarchive`. Archived cards render with reduced opacity + Restore button. Source: `server.py:_handle_archive_agent`, `server.py:_handle_unarchive_agent`, `app.js:renderArchivedList`.

### The Channel data model

Shipped model (v8.19.22): a **Channel** is the top-level Task scope. Inside one Channel are several **rooms**: `#boss` (category BOSS), `#team`/`#joint-*` (SHARED), and `#task-<worker>` (WORKER — the 1:1 boss-worker dialog). `list_tasks()` builds one task group per boss agent; each group contains its rooms sorted boss then joint then shared then worker. `_classify_channel()` in `server.py` assigns categories. Orphan workers (boss not in manifest) get a synthetic "Unassigned workers" group. Source: `server.py:list_tasks`, `server.py:_classify_channel`.

### New-worker onboarding via channel briefs (v8.19.24)

When a worker spawns into a channel that has a brief (`~/.culture/briefs/<channel>.md`), all 4 backend daemons inject the brief's text into the SDK system prompt under a "Joining channel — current state" heading. The brief is the TAIL of the file (capped at 64 KiB) with early history elided. Idempotence guard: identical body in the same minute window is skipped. Source: `culture/clients/_channel_brief.py`, all 4 backend `daemon.py` files.

### Additional endpoints not in the original spec

| Endpoint | Version | Purpose |
|---|---|---|
| `GET /api/tasks` | v8.19.11 | Task-grouped channel listing |
| `GET /api/channels` | v8.19.2 | Flat channel listing with members |
| `GET /api/archived` | v8.19.2 | Archived agents |
| `POST /api/archive` | v8.19.2 | Archive an agent (stops first) |
| `POST /api/unarchive` | v8.19.2 | Restore an archived agent |
| `POST /api/message` | v8.19.2 | Send a message to an agent's channel |
| `GET /api/channels/{name}/messages` | v8.19.22 | Read a specific channel by name |
| `GET /api/channels/{name}/seed` | v8.19.18 | Channel seed text |
| `GET /api/channels/{name}/brief` | v8.19.24 | Living channel brief |
| `GET /auth` / `POST /auth` | v8.18.3 | Token-based login form (replaces ?token= URL leak) |

### Open questions from this update

1. **`culture boss init` one-command bootstrap** — captured in `docs/v8.19.22-orchestrator-friction.md` item #5, not yet implemented.
2. **Brief vs seed overlap** — the seed is write-once initial mission; the brief is the living onboarding doc. Both exist as separate files. Whether to merge them is an open design question (see friction doc item #6).

---

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

>>> See "v8.19.x update" at top for current behaviour.

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

>>> See "v8.19.x update" at top for current behaviour.

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

>>> See "v8.19.x update" at top for current behaviour.

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

>>> See "v8.19.x update" at top for current behaviour.

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

>>> See "v8.19.x update" at top for current behaviour.

Per project convention: real I/O, no mocks; `aiohttp.test_utils` for the server.

| Test | Verifies |
|---|---|
| `test_dashboard_api.py` | `/api/agents` shape from a seeded `CULTURE_HOME`; `/api/stream/pending` emits the seeded queue; control POSTs (`approve`/`deny`) write the correct decision files via the broker (human authority — above-ceiling tool still approved). Uses `aiohttp` test client + isolated `CULTURE_HOME`. |
| `test_dashboard_control.py` | `approve` writes `decided_by=dashboard` with no ceiling refusal; `deny` carries reason; double-decision returns the broker's `DecisionExistsError` as a 409; `stop-all` invokes the expected CLI/IPC (patched at the subprocess boundary). |
| `test_dashboard_tail.py` | The SSE file-tailer emits appended JSONL lines in order and survives a missing/rotated file. |

The SPA itself is verified live in-browser (Playwright) during Phase L, not unit-tested.

## Files

>>> See "v8.19.x update" at top for current behaviour.

New:
- `culture/dashboard/__init__.py`, `culture/dashboard/server.py` (aiohttp app + endpoints + SSE tailer), `culture/dashboard/static/{index.html,app.js,style.css}`.
- `culture/cli/` — register `culture dashboard` (new group or `mesh dashboard` subcommand).
- `docs/agentirc/dashboard.md`.
- Tests above.

Changed:
- `culture/cli/__init__.py` — register the dashboard command.
- `_perm_broker.py` — only if a small read/write-policy helper is missing (reuse existing where possible).

## Phases

>>> See "v8.19.x update" at top for current behaviour.

| Phase | Work | Gate |
|---|---|---|
| D1 | aiohttp server skeleton + `/api/agents` + `culture dashboard` CLI (localhost bind). | `test_dashboard_api.py` agents endpoint green; `culture dashboard` serves. |
| D2 | SSE tailer + audit/daemon-log/pending streams. | `test_dashboard_tail.py` green. |
| D3 | Control endpoints (approve/deny/pause/resume/close/stop-all/policy). | `test_dashboard_control.py` green. |
| D4 | Vanilla-JS SPA: agent grid, per-agent session stream, pending-approvals panel with buttons, control bar (pause/resume/close/STOP-ALL), channel view. | Loads in browser; renders seeded data. |
| L  | Live bring-up: real boss + worker; verify watch + intervene in-browser (Playwright). | The 4 live checks above pass; fix any #411/#412 bug surfaced. |
| D5 | Docs (`dashboard.md`) + version bump + quality gates + PR. | doc-test-alignment + code-reviewer clean; CI green. |

## Non-goals (v1)

>>> See "v8.19.x update" at top for current behaviour.

- Auth/multi-user/remote access (localhost single-user only).
- Editing/replaying agent history; only view + intervene.
- Redirect/message-injection into agent channels from the panel (deferred — watch + stop/approve/policy is v1; two-way steering is a follow-up).
- A build-tooling frontend (React/Vite); vanilla JS only, no node build.
- Mobile layout / theming polish beyond a usable dark UI.

## Open questions

>>> See "v8.19.x update" at top for current behaviour.

1. **Channel view fidelity.** `/api/channel` reads via the observer (recent
   buffer). Full live channel streaming (SSE) is deferred unless the per-agent
   audit stream proves insufficient for "seeing exchanges".
2. **Top-level `culture dashboard` vs `culture mesh dashboard`.** Leaning
   top-level for discoverability; confirm during build.
