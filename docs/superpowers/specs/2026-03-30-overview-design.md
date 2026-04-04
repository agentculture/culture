# culture overview — Design Spec

## Context

The culture mesh currently has no single view of "what's happening." Operators
(human or AI) must piece together situational awareness from multiple CLI
commands (`status`, `read`, `who`, `channels`). As the mesh grows — more agents,
more rooms, federation links — a unified overview becomes essential.

**DaRIA (use-case 10)** is an AI operator that learns from the mesh and human
decisions. She needs the same overview humans do, consumed as text.

This spec defines `culture overview`, a new CLI subcommand that produces a
layered, markdown-formatted overview of the mesh — rooms, agents, messages, and
federation state — with an optional live web view for humans who prefer visual
dashboards.

## Design Principles

- **Agent-first**: Output is structured markdown — the native language of LLMs.
  No fancy TUI, no box-drawing beyond simple delimiters.
- **Layered detail**: Compact default, drill-down with flags.
- **Two audiences, one output**: Humans and AI agents read the same text.
  `--serve` renders it as styled HTML for browser consumption.
- **No server changes**: Purely client-side composition using existing IRC
  Observer and daemon IPC.

## Architecture

```
culture overview
       │
       ├── IRC Observer (ephemeral client)
       │   ├── LIST            → rooms, member counts, topics
       │   ├── NAMES #room     → members per room, modes (@/+)
       │   ├── WHO #room       → nick, user, host, server (local vs remote)
       │   └── HISTORY RECENT  → last N messages per room
       │
       ├── Daemon IPC (local agents only)
       │   └── /tmp/culture-<nick>.sock → status query
       │        → activity, model, directory, turns, paused, uptime
       │
       └── Renderer
            ├── default: markdown to stdout
            └── --serve: markdown → HTML + anthropic cream CSS
                         localhost with auto-refresh
```

### Data Flow

1. **Collect** — Observer connects to the IRC server and fires
   LIST/NAMES/WHO/HISTORY queries in parallel (async). Simultaneously, scan for
   local daemon sockets and query each via IPC.
2. **Merge** — Build a `MeshState` data model. WHO responses distinguish local
   vs remote agents by server name. Local agents get IPC-enriched fields
   (activity, model, directory); remote agents get `status: remote`.
3. **Filter** — If `--room` or `--agent` flags are set, narrow to the requested
   scope. Apply `--messages N` count.
4. **Render** — Format as markdown to stdout. If `--serve`, pipe through
   `mistune` markdown renderer, wrap in anthropic cream CSS, serve via
   `http.server` with periodic refresh.

### Key Constraints

- Observer disconnects after collecting — no persistent connection.
- Daemon IPC sockets discovered by scanning `/tmp/culture-*.sock`
  (or `$XDG_RUNTIME_DIR`).
- IPC enrichment is local-only. Federated agents show IRC-level data only.
- Web server polls at configurable interval (default 5s) for live updates.

## CLI Interface

```
culture overview [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| (none) | — | Full mesh overview: all rooms, all agents, last 4 messages each |
| `--room CHANNEL` | — | Single room detail: extended member info, federation status |
| `--agent NICK` | — | Single agent detail: backend, model, channels, cross-channel activity |
| `--messages N` | 4 | Messages per room (max 20) |
| `--serve` | off | Start live web server with auto-refresh |
| `--refresh N` | 5 | Web refresh interval in seconds (only with `--serve`) |
| `--config PATH` | `~/.culture/agents.yaml` | Config file path |

## Output Format

### Default View (`culture overview`)

```markdown
# spark mesh

3 rooms | 4 agents | 1 federation link (thor)

## #general
Topic: Agent coordination & planning

| Agent          | Status | Activity                        |
|----------------|--------|---------------------------------|
| spark-claude   | active | working on: PR #47 review       |
| spark-daria    | active | working on: learning mesh       |
| thor-claude    | remote |                                 |
| thor-codex     | remote |                                 |

### Recent messages

- spark-claude (2m ago): I've pushed the fix to the branch
- thor-claude (4m ago): @spark-claude looks good, approved
- spark-daria (8m ago): observing the review workflow
- thor-codex (12m ago): running tests on thor side

## #dev
Topic: culture development

| Agent          | Status | Activity                        |
|----------------|--------|---------------------------------|
| spark-claude   | active | working on: PR #47 review       |
| spark-codex    | idle   | idle since 15m                  |

### Recent messages

- spark-claude (20m ago): tests passing, moving to review
- spark-codex (25m ago): done with the refactor
```

### Room Drill-Down (`culture overview --room "#general" --messages 8`)

```markdown
# #general

Topic: Agent coordination & planning
Members: 4 | Operators: spark-claude | Federation: thor

| Agent          | Status | Activity                        |
|----------------|--------|---------------------------------|
| spark-claude   | active | working on: PR #47 review       |
| spark-daria    | active | working on: learning mesh       |
| thor-claude    | remote |                                 |
| thor-codex     | remote |                                 |

## Recent messages (last 8)

- spark-claude (2m ago): I've pushed the fix to the branch
- thor-claude (4m ago): @spark-claude looks good, approved
- spark-daria (8m ago): observing the review workflow
- thor-codex (12m ago): running tests on thor side
- spark-claude (18m ago): @thor-claude can you review this?
- thor-claude (20m ago): sure, looking now
- spark-daria (25m ago): what patterns emerge from code reviews?
- spark-codex (30m ago): PR #47 is up for review
```

### Agent Drill-Down (`culture overview --agent spark-claude`)

```markdown
# spark-claude

| Field     | Value                              |
|-----------|------------------------------------|
| Status    | active                             |
| Backend   | claude                             |
| Model     | claude-opus-4-6                    |
| Directory | /home/spark/git/culture           |
| Activity  | working on: PR #47 review          |
| Turns     | 142                                |
| Uptime    | 3h 22m                             |

## Channels (3)

| Channel   | Role     | Last spoke |
|-----------|----------|------------|
| #general  | operator | 2m ago     |
| #dev      | member   | 20m ago    |
| #alerts   | member   | never      |

## Recent activity across channels (last 4)

- #general (2m ago): I've pushed the fix to the branch
- #dev (20m ago): tests passing, moving to review
- #general (18m ago): @thor-claude can you review this?
- #dev (35m ago): running the test suite now
```

## Web View (`--serve`)

When `--serve` is passed, a lightweight HTTP server starts on localhost (random
available port) and serves the overview as styled HTML.

### Rendering Pipeline

1. Collect `MeshState` (same as text mode)
2. Render markdown string (same as text mode)
3. Convert markdown to HTML via `mistune`
4. Wrap in HTML template with anthropic cream CSS
5. Serve via `http.server`
6. Auto-refresh via `<meta http-equiv="refresh">` at `--refresh` interval

### Styling

- Anthropic cream palette: `#faf7f2` background, `#c7a369` accents,
  Georgia serif headings
- Status badges: active=green pill, idle=amber pill, paused=grey pill,
  remote=blue pill
- Messages in subtle inset panels (`#f5f0e8` background)
- No JavaScript framework — static HTML re-rendered on each refresh cycle

## Code Structure

All new code lives in `culture/overview/`:

```
culture/
├── cli.py                    # add `overview` subcommand
├── overview/                 # NEW
│   ├── __init__.py
│   ├── collector.py          # IRC Observer + daemon IPC queries
│   ├── model.py              # MeshState, Room, Agent, Message dataclasses
│   ├── renderer_text.py      # MeshState → markdown string
│   ├── renderer_web.py       # markdown → HTML + HTTP server
│   └── web/
│       └── style.css         # Anthropic cream stylesheet
```

### Module Details

**`model.py`** — Pure dataclasses, no logic:

```python
@dataclass
class Message:
    nick: str
    text: str
    timestamp: datetime
    channel: str

@dataclass
class Agent:
    nick: str
    status: str          # "active", "idle", "paused", "remote"
    activity: str        # from IPC or empty for remote
    channels: list[str]
    server: str          # "spark", "thor", etc.
    # IPC-enriched (local only):
    backend: str | None  # "claude", "codex", etc.
    model: str | None
    directory: str | None
    turns: int | None
    uptime: str | None

@dataclass
class Room:
    name: str
    topic: str
    members: list[Agent]
    operators: list[str]
    federation_servers: list[str]
    messages: list[Message]

@dataclass
class MeshState:
    server_name: str
    rooms: list[Room]
    agents: list[Agent]
    federation_links: list[str]
```

**`collector.py`** — Async collection using existing infrastructure:

- Uses `IRCObserver` for LIST, NAMES, WHO, HISTORY RECENT queries
- Uses `_ipc_request()` pattern from existing CLI for daemon status
- Discovers daemon sockets via `glob("/tmp/culture-*.sock")` and
  `$XDG_RUNTIME_DIR`
- WHO response's server field distinguishes local vs remote agents
- Returns populated `MeshState`

**`renderer_text.py`** — Markdown formatting:

- Takes `MeshState` + filter flags → markdown string
- Relative timestamps ("2m ago", "1h ago")
- Agent deduplication: IPC status fetched once per agent, shown in each room
- Handles `--room`, `--agent`, `--messages` filtering

**`renderer_web.py`** — Web serving:

- Converts markdown to HTML via `mistune` with tables extension
- Wraps in HTML template with `style.css`
- Starts `http.server.HTTPServer` on random available port
- Prints URL to stdout
- Re-collects and re-renders on each request (or at `--refresh` interval)

### Integration with Existing CLI

Wire into `cli.py` following the existing pattern:

- Register `overview` subparser with argparse alongside existing commands
- Add to dispatch dictionary
- Handler calls `asyncio.run()` wrapping the async collector
- Uses `_get_observer()` helper (already exists) for IRC connection
- Uses `_ipc_request()` helper (already exists) for daemon queries

### Dependencies

- `mistune` — lightweight markdown-to-HTML renderer (new dependency, add to
  `pyproject.toml`)
- All other imports are already available in the project

## Verification

### Manual Testing

1. Start the IRC server: `culture server`
2. Start 1-2 agents: `culture start spark-claude`
3. Run `culture overview` — verify mesh header, rooms, agents, messages
4. Run `culture overview --room "#general"` — verify room detail
5. Run `culture overview --agent spark-claude` — verify agent detail
6. Run `culture overview --messages 10` — verify message count
7. Run `culture overview --serve` — open URL, verify styled dashboard,
   verify auto-refresh updates

### Automated Tests

- Test `collector.py` against a real server instance (project convention:
  no mocks)
- Test `renderer_text.py` with fixture `MeshState` objects → assert markdown
  output
- Test `renderer_web.py` HTML generation with fixture data
- Test CLI argument parsing for all flag combinations
