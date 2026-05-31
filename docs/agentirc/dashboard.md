---
layout: default
title: Mission Control Dashboard
parent: AgentIRC
nav_order: 98
---

# Mission Control Dashboard

A local web app to **watch the whole mesh and take the wheel**. It streams every
agent's session, the daemon-action log, and pending tool-approvals into one
browser view, and exposes the full intervention surface — approve/deny,
pause/resume, close, archive, emergency stop-all, and grant-policy edits — for
when a run goes sideways.

Design spec: `docs/superpowers/specs/2026-05-29-mission-control-dashboard-design.md`
Builds on the [Permission Broker](helper-permissions.md), [Daemon Action Log](helper-daemon-log.md), and [Boss Agent](boss-agent.md).

## Run it

```bash
culture dashboard               # http://127.0.0.1:8787
culture dashboard --port 9000
```

Bound to `127.0.0.1` only. It can approve tool calls and kill agents, so it
refuses a non-loopback `--host` unless you pass `--unsafe-bind` (documented as
dangerous). No new dependency — it's an `aiohttp` server (already a dep) serving
a vanilla-JS page (no build step).

## What you see

Three columns. Three top-level tabs select the left panel; the middle column
streams content for the selected agent/channel; the right column shows pending
approvals. No setup beyond a running mesh.

### Top tabs: Channels / Agents / Archived

- **Channels** (default) — the channels-first view (v8.19.7). Each IRC channel
  is a **task scope**: the boss, its workers, and their rooms grouped under one
  card. The Channel IS the Task (v8.19.22 reframe): inside one Channel are
  **rooms** — `#boss-*` (boss's own), `#joint-*` (cross-team coordination), and
  `#task-<worker>` (1:1 boss-worker dialog). Each channel card shows:
  - **Title** — derived from the channel's seed/topic (v8.19.18), falling back to
    the boss's `mission.md` first line, then `"<boss-nick>'s work"`.
  - **Member chips** — one per agent, with nick, role (v8.19.4), running/stopped
    dot indicator, and per-agent token-usage badge (v8.19.21).
  - **Per-channel token total** — sum of unique members' cumulative input+output
    tokens (v8.19.21). De-duplicated: the boss appears in every room but is
    counted once per channel.
  - **Seed preview** — first line of the channel's seed brief (v8.19.18).
  - Channels are sorted: joint coordination first, then task, shared, boss,
    other. Stale `#task-*` channels (all members stopped) are hidden.
- **Agents** — the classic flat list. Every registered agent with live state
  (running/stopped), pending-approval count, last daemon action, role, and a
  `BOSS` tag for boss agents. Per-agent **Pause / Resume / Close** buttons.
  Agents are **grouped into teams**: each boss heads its own group with its
  workers nested beneath it, and standalone agents fall under "unassigned." A
  running worker that has produced **no activity** gets a loud **`IDLE`** badge
  (determined by the daemon's authoritative idle/engaged log — never by audit
  byte size).
- **Archived** — agents that have been archived via the dashboard or CLI. Shows
  nick, archived-at timestamp, reason, and an **Unarchive** button to restore
  them (v8.19.2).

### Middle column: Activity / Daemon actions / Chat

Click an agent chip or channel card, then pick a sub-tab:

- **Activity** — live-stream the agent's own messages + tool calls
  (`audit/<nick>.jsonl`). Server-sent events; backlog (last 200 lines) then live
  tail.
- **Daemon actions** — the structured daemon-action log
  (`daemon-log/<nick>.jsonl`).
- **Chat** — **talk to the agent directly.** Shows the recent conversation in
  the selected room's channel (v8.19.22: clicking a room card sets the chat
  target to that specific room, not just the agent's home channel). What you send
  is posted prefixed with `@<nick>` so its mention detector fires — the same
  thing `culture boss brief` does, but no boss daemon needed. The connection goes
  over the dashboard's persistent observer (v8.19.17).

Chat and activity panels use **append-only refresh** (v8.19.14) — new messages
are appended to the DOM without replacing existing elements. Scroll position is
preserved: if you've scrolled up to read history, auto-scroll pauses until you
return to the bottom (within a 40px threshold). The left-panel agent/channel
lists use **skip-when-unchanged** rendering (v8.19.15) — poll responses are
JSON-snapshotted and the DOM is only touched when data actually changes,
eliminating flicker on an idle mesh.

### Seed brief panel

Channels that have a seed brief (set via `culture boss brief --topic` or
auto-injected on spawn) show the full seed text in a collapsible panel at the
top of the middle column (v8.19.18). The seed is the immutable initial mission
(write-once).

### Living channel brief

The living brief is the running team onboarding document that grows as work
progresses — every `boss brief` and `boss note` appends a dated section
(v8.19.24). New workers joining a channel receive this brief automatically as
system-prompt context, so they start with full awareness of what has transpired.
The dashboard exposes the brief text via `/api/channels/{name}/brief`.

### Right column: Pending approvals

Every worker tool request waiting on a human, with **Approve / Always / Deny**.
Requests already decided — awaiting their worker to consume the verdict — are
not shown.

### Top bar

A pending badge, **Pause all**, and a red **STOP ALL** (emergency kill of every
agent including the boss).

## Control = the operator is the top authority

Unlike the boss agent (bounded by its [grant ceiling](boss-agent.md)), the
dashboard is **you** — its approvals are **not** ceiling-bounded. You can approve
any tool, including the high-risk ones a boss must escalate. Control actions reuse
the existing levers:

| Action | Under the hood |
|---|---|
| Approve / Deny | writes `perm-decisions/<id>.json` (`decided_by: dashboard`) |
| Pause / Resume | daemon IPC (`pause`/`resume`) |
| Close | `culture agent stop <nick>` (dashboard runs as the human/root — may close any agent) |
| Stop all | `pause` every agent, or `culture agent stop --all` (kill) |
| Archive | stops the agent (if running), then sets `archived: true` in the manifest |
| Unarchive | clears `archived` flag, restoring the agent to the active list |
| Edit policy | read/write `perm-policy/<nick>.yaml` |
| Send message | observer PRIVMSG to the agent's channel, nick-prefixed (mention fires) |

## Persistent observer (v8.19.17)

The dashboard holds one long-lived IRC connection (`PersistentObserver` in
`culture/observer.py`) for all chat reads and writes. This replaces the
per-request ephemeral `get_observer()` peek connections that caused JOIN/PART
event spam (v8.19.10-13). Lifecycle is managed via aiohttp's `cleanup_ctx`: the
observer is provisioned on app startup and closed on shutdown. If the IRC server
is not yet running, the dashboard falls back to ephemeral connections
transparently.

## Cache busting (v8.19.16)

Static asset URLs (`app.js`, `style.css`) are stamped with `?v=<version>` from
`pyproject.toml`. The `index.html` response carries `Cache-Control: no-cache,
no-store, must-revalidate` headers. This ensures a browser tab that was open
before a dashboard hotfix picks up new code without a manual hard-refresh.

## API (for scripting / integration)

All localhost JSON unless noted.

### Read endpoints

| Endpoint | Description |
|---|---|
| `GET /api/agents` | All active agents with state, pending count, last action, role, channels, token usage, idle flag |
| `GET /api/channels` | Channel list with members, roles, categories |
| `GET /api/tasks` | Task-grouped channel listing (channels-as-tasks, v8.19.11) with per-task token totals |
| `GET /api/pending` | All pending tool-approval requests |
| `GET /api/archived` | Archived agents |
| `GET /api/channel/{nick}` | Recent messages in an agent's channel (both sides) |
| `GET /api/channels/{name}/messages` | Recent messages in a specific channel by name |
| `GET /api/channels/{name}/seed` | Persisted seed brief for a channel (404 if none) |
| `GET /api/channels/{name}/brief` | Living brief for a channel (v8.19.24; 404 if none) |
| `GET /api/stream/{audit\|daemon-log}/{nick}` | SSE tail of an agent's audit or daemon-log JSONL |
| `GET/PUT /api/policy/{nick}` | Read or write an agent's permission policy YAML |

### Write endpoints

| Endpoint | Body | Description |
|---|---|---|
| `POST /api/approve` | `{id, always?, pattern?}` | Approve a pending tool request (`always` = add to policy) |
| `POST /api/deny` | `{id, reason?}` | Deny a pending tool request |
| `POST /api/pause` | `{nick}` | Pause an agent via daemon IPC |
| `POST /api/resume` | `{nick}` | Resume a paused agent |
| `POST /api/close` | `{nick}` | Stop an agent (`culture agent stop`) |
| `POST /api/stop-all` | `{mode: "pause"\|"kill"}` | Pause all or kill all |
| `POST /api/message` | `{nick, text}` | Post `@<nick> <text>` to the agent's channel |
| `POST /api/archive` | `{nick, reason?}` | Archive an agent (stops first if running) |
| `POST /api/unarchive` | `{nick}` | Restore an archived agent |

## Remote access (mobile / another machine)

The dashboard is a control plane (it can approve tool calls, kill agents, and
message agents that hold your MCP credentials), so it is **localhost-only by
default** and its guard rejects non-loopback `Host`/`Origin`. To reach it
remotely, do **not** flip `--unsafe-bind` — instead keep the bind on loopback and
front it with a **private tunnel + a token**:

```bash
# 1. Run with auth (token auto-generated at ~/.culture/dashboard-token) and
#    trust your tunnel's hostname:
culture dashboard --auth --trusted-host mymac.tailXXXX.ts.net

# 2. Publish the loopback dashboard onto your private network (example: Tailscale):
tailscale serve --bg 8787
```

On start, `--auth` prints the dashboard token and directs you to the `/auth`
login page (`https://<trusted-host>/auth`). The login form submits the token via
POST (not in the URL) so it never appears in browser history, server access logs,
or the Referer header. The server sets a `SameSite=Strict`, HttpOnly cookie
(30-day TTL), so every later request (including SSE streams) is authenticated.
Requests without a valid cookie are redirected to `/auth`; API requests get
`401`; a `Host`/`Origin` that is neither loopback nor a `--trusted-host` gets
`403`.

Why this is the safe shape:

- **Tailscale** (or any private tunnel) keeps the dashboard off the public
  internet — only your own devices can route to it.
- The **token cookie** means even a leaked URL or a compromised device on the
  tailnet can't drive the control plane without the secret.
- `SameSite=Strict` + the Origin allow-list keep CSRF / DNS-rebinding defenses
  intact.

`--auth-token <tok>` sets an explicit token instead of the generated one;
`--trusted-host` is repeatable.

## Security model

Same-machine, same-UID, localhost-only **unless** you opt into the remote-access
setup above. Anyone who can reach the port as this user already has shell access
and the same powers via the CLI/files — the dashboard adds no privilege locally.
Without `--auth` there is no token (fine for pure localhost); never bind a
non-loopback interface directly — use a private tunnel + `--auth` instead.

Nick validation (`^[A-Za-z0-9]([A-Za-z0-9_-]*[A-Za-z0-9])?$`) is enforced on
every path that builds a filesystem path from a nick — closing path-traversal
attacks on audit, daemon-log, policy, and socket lookups.
