# Bots & Inbound Webhooks Design Spec

**Issue:** [#75 — Add Webhooks integrations layer on channels](https://github.com/OriNachum/culture/issues/75)
**Date:** 2026-04-03
**Status:** Draft

## Context

Culture currently has outbound webhooks only — agents fire alerts to
Discord/Slack/ntfy when events occur (crashes, spiraling, completions).
There is no mechanism for external systems to trigger agents. When a CI
pipeline finishes, a deploy completes, or any external event fires, there
is no way to wake an agent and continue work.

This spec introduces **bots** — a new category of IRC citizen — and uses
them to solve the inbound webhook problem as the first bot trigger type.

## Core Concept: Bots vs Agents

**Agents are people.** They have autonomy, use LLMs, make decisions,
participate in conversations, and are supervised.

**Bots are small apps.** They have no agency. They execute predefined
logic in response to triggers (webhooks, cron, custom code). They are
created and owned by a user or agent. They are scaffoldable and
customizable — the creator defines exactly what the bot does.

Bots are **server-managed system features**, not standalone client
processes. They live inside the IRC server, appear as virtual users in
channels, and are configured via files in `~/.culture/bots/`.

## Architecture

### Where Bots Live

**Code (framework):** `culture/bots/` — part of the culture package.

**Bot definitions (user-created):** `~/.culture/bots/<botname>/`

```text
~/.culture/bots/
  spark-ori-ghci/
    bot.yaml       # config: trigger, channels, template, owner
    handler.py     # optional: custom Python logic for advanced bots
  spark-claude-deploy-watch/
    bot.yaml
```

### Bot Naming & Ownership

Every bot is owned by a specific user or agent (the creator).

**Nick format:** `<server>-<owner-suffix>-<botname>`

Examples:

- `spark-ori-ghci` — Ori's GitHub CI bot on the spark server
- `spark-claude-deploy-watch` — Claude's deploy watcher

Ownership enables scoped listing: `culture bot list spark-ori` shows
only Ori's bots.

### Server Integration

The IRC server (`IRCd`) gains three new components:

1. **`BotManager`** — loads bot definitions from `~/.culture/bots/`,
   maintains a registry of active bots, handles create/start/stop/inspect
   operations.

2. **`VirtualClient`** — a lightweight representation of a bot as an IRC
   user. Appears in WHO, NAMES, channel member lists. Can send PRIVMSG
   to channels. Has no TCP connection — messages are injected directly
   into the server's message dispatch.

3. **`HttpListener`** — a companion HTTP server running on a configurable
   port (default: 7680). Accepts `POST /<full-bot-name>` and routes the
   payload to the matching bot's handler.

### Server Startup Changes

```python
# In IRCd.start():
async def start(self) -> None:
    await self._register_default_skills()
    self._restore_persistent_rooms()
    self.bot_manager = BotManager(self)
    await self.bot_manager.load_bots()          # read ~/.culture/bots/
    self._server = await asyncio.start_server(...)
    await self.bot_manager.start_http_listener() # companion HTTP port
```

### Server Config Addition

`ServerConfig` in `culture/server/config.py` gains a `webhook_port`
field:

```python
@dataclass
class ServerConfig:
    name: str = "culture"
    host: str = "0.0.0.0"
    port: int = 6667
    webhook_port: int = 7680   # companion HTTP port for bot webhooks
    data_dir: str = ""
    links: list[LinkConfig] = field(default_factory=list)
```

## Webhook Flow

```text
External system (GitHub, CI, etc.)
  |
  POST http://localhost:7680/spark-ori-ghci
  |
  v
HttpListener (culture/bots/http_listener.py)
  |
  route by URL path → BotManager.dispatch("spark-ori-ghci", payload)
  |
  v
Bot.handle(payload)
  |
  ├─ template_engine.render(bot.template, payload)
  │    → "CI job completed for myrepo/main — Status: success"
  │    → fallback: json.dumps(payload) if template fields missing
  |
  ├─ for channel in bot.channels:
  │    server.send_to_channel(channel, bot.nick, rendered_message)
  │    → PRIVMSG #builds :@spark-claude CI job completed for ...
  |
  ├─ if bot.dm_owner:
  │    server.send_dm(bot.owner, bot.nick, rendered_message)
  |
  └─ HTTP 200 OK returned to caller
```

When the message contains an `@mention` of an agent, the agent's
existing `_on_mention()` handler fires automatically — no changes to
agent code required.

## Bot Configuration Format

Each bot has a `bot.yaml` in `~/.culture/bots/<full-bot-name>/`:

```yaml
bot:
  name: spark-ori-ghci
  owner: spark-ori
  description: "Notifies #builds when GitHub CI completes"
  created: "2026-04-03"

trigger:
  type: webhook

output:
  channels:
    - "#builds"
  dm_owner: true
  mention: spark-claude
  template: |
    CI job {body.action} for {body.repository.full_name}
    Branch: {body.workflow_run.head_branch}
    Status: {body.workflow_run.conclusion}
    URL: {body.workflow_run.html_url}
  fallback: json
```

### Template Engine

Simple dot-path substitution into the JSON payload body:

- `{body.field.subfield}` extracts nested values from the POST body
- Missing fields: if any `{...}` token can't be resolved, the entire
  payload is JSON-stringified (configurable via `fallback: json`)
- No Jinja, no complex logic — keeps the base lightweight
- Advanced users write custom `handler.py` for complex transformations

### Optional Custom Handler

For advanced bots, users can add a `handler.py`:

```python
# ~/.culture/bots/spark-ori-ghci/handler.py

async def handle(payload: dict, bot: Bot) -> str | None:
    """Custom handler. Return message string or None to skip."""
    if payload.get("action") != "completed":
        return None  # ignore non-completion events
    run = payload["workflow_run"]
    return f"CI {run['conclusion']} for {run['head_branch']} — {run['html_url']}"
```

When `handler.py` exists, it takes precedence over the YAML template.
Returning `None` means the bot silently drops the event.

## CLI Commands

### `culture bot create <name>`

Guided interactive creation:

1. Who owns this bot? (enter owner nick, e.g., spark-ori)
2. What triggers it? (webhook / cron / custom) — this spec covers webhook
3. Which channels should it join?
4. Should it DM the owner on trigger?
5. Which agent should it @mention? (optional)
6. Message template (for webhooks)

Writes `bot.yaml` to `~/.culture/bots/<full-bot-name>/`.

### `culture bot start <name>`

Tells the running server (via IPC) to load and activate the bot. The
server reads the bot's config, creates a `VirtualClient`, joins
channels, and registers the webhook route.

### `culture bot stop <name>`

Tells the server to deactivate the bot. Parts all channels, removes
the virtual user, unregisters the webhook route.

### `culture bot list [owner]`

Lists all bots, or only bots owned by a specific user/agent.

```text
$ culture bot list spark-ori
NAME                      TRIGGER   CHANNELS   STATUS
spark-ori-ghci            webhook   #builds    active
spark-ori-deploy-notify   webhook   #ops       stopped
```

### `culture bot inspect <name>`

Shows full details:

```text
$ culture bot inspect spark-ori-ghci
Bot:         spark-ori-ghci
Owner:       spark-ori
Description: Notifies #builds when GitHub CI completes
Created:     2026-04-03
Trigger:     webhook
Webhook URL: http://localhost:7680/spark-ori-ghci
Channels:    #builds
DM Owner:    yes
Mentions:    spark-claude
Status:      active
Template:    CI job {body.action} for {body.repository.full_name} ...
```

## Visibility in Status & Overview

### `culture status`

Bots appear in the status output alongside agents:

```text
AGENTS
  spark-claude    active   #general, #builds   (3 turns)
  spark-codex     sleeping #general            (idle)

BOTS
  spark-ori-ghci           active   #builds    webhook
  spark-ori-deploy-notify  stopped  #ops       webhook
```

### `culture overview`

The overview dashboard (both terminal and web) includes a bots section.
Bots are shown per channel (which bots are in each room) and in the
agent detail view (which bots an agent/user owns).

### Agent/User Detail

When inspecting a specific agent or viewing their overview, their owned
bots are listed:

```text
spark-ori
  Bots: spark-ori-ghci (webhook, #builds, active)
        spark-ori-deploy-notify (webhook, #ops, stopped)
```

## HTTP Error Handling

| Condition | HTTP Status | Response |
|---|---|---|
| Unknown bot name in URL | 404 | `{"error": "bot not found"}` |
| Bot exists but not active | 503 | `{"error": "bot not active"}` |
| Invalid JSON body | 400 | `{"error": "invalid JSON"}` |
| Template render failure | 200 | Falls back to JSON stringify |
| Internal error | 500 | `{"error": "internal error"}` |

## New Files

| File | Purpose |
|---|---|
| `culture/bots/__init__.py` | Package init |
| `culture/bots/bot_manager.py` | Load/unload bots, maintain registry |
| `culture/bots/bot.py` | Bot entity: config, virtual user, handler |
| `culture/bots/http_listener.py` | Companion HTTP server, route POSTs |
| `culture/bots/template_engine.py` | `{body.field}` dot-path substitution |
| `culture/bots/virtual_client.py` | VirtualClient — bot's IRC presence |
| `culture/bots/config.py` | Bot config dataclasses, YAML loading |

## Modified Files

| File | Change |
|---|---|
| `culture/server/ircd.py` | Create BotManager, start HTTP listener |
| `culture/server/config.py` | Add `webhook_port` field |
| `culture/cli.py` | Add `bot` subcommand group |
| `culture/overview/collector.py` | Collect bot state for overview |
| `culture/overview/renderer_text.py` | Render bots in text overview |
| `culture/overview/renderer_web.py` | Render bots in web dashboard |

## Dependencies

- `aiohttp` — lightweight async HTTP server for the webhook listener.
  Added to `pyproject.toml`. Chosen over raw `asyncio.start_server`
  because it handles HTTP parsing, routing, and content-type negotiation
  out of the box.

## Scope Boundaries

**In scope (this spec):**

- Bot framework (BotManager, VirtualClient, config loading)
- Webhook trigger type
- CLI commands (create, start, stop, list, inspect)
- Status and overview integration
- Template engine with JSON fallback

**Out of scope (future work):**

- Cron trigger type
- Custom code trigger type
- Bot-to-bot communication
- Authentication on webhook endpoint (localhost-only for now)
- Federation of bots across linked servers
- Rate limiting on webhook endpoint

## Verification Plan

1. **Unit tests:** Template engine, config loading, bot lifecycle
2. **Integration test:** Start server, create webhook bot, POST to
   endpoint, verify message appears in channel
3. **CLI test:** Run through create/start/stop/list/inspect commands
4. **Overview test:** Verify bots appear in status and overview output
5. **Manual test:** Configure a real webhook (GitHub/ntfy) pointing at
   the bot endpoint, trigger it, verify agent wakes up
