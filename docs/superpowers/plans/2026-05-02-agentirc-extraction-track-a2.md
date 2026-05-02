# AgentIRC Extraction — Track A2 (Bot Framework Rewrite, Embedded Mode) Implementation Plan

> **Plan revised 2026-05-03.** The original A2 plan assumed culture would re-implement an `agentirc.io/bot` TCP CAP client from scratch, because agentirc 9.5's bot-side machinery (`_internal.virtual_client.VirtualClient`) was internal. Then we filed [agentculture/agentirc#22](https://github.com/agentculture/agentirc/issues/22) (the **A2-Bridge** track) to promote `agentirc.ircd.IRCd` and `agentirc._internal.virtual_client.VirtualClient` to public. This plan is rewritten for the lighter post-Bridge approach: switch `culture server start` to embed `agentirc.ircd.IRCd` in-process, and rewrite `culture/bots/virtual_client.py` as a thin wrapper over `agentirc.virtual_client.VirtualClient`. The TCP-from-scratch approach is no longer relevant.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop the in-process IRCd reference from culture's bot framework. After A2 lands:

- `culture/cli/server.py:_run_server` constructs `agentirc.ircd.IRCd(config)` directly (in-process, same Python process). The bundled IRCd in `culture/agentirc/` is orphaned but stays on disk for A3 to delete.
- `culture/bots/virtual_client.py` is a thin wrapper around `agentirc.virtual_client.VirtualClient` for culture-specific concerns (BotConfig, template engine, `fires_event` chaining, owner DM). Most of the existing 231-line file collapses to ~50 lines.
- `culture/bots/bot.py`, `bot_manager.py` lose direct in-process IRCd reaches; they consume the publicly-typed `agentirc.ircd.IRCd` instance.
- `culture/bots/http_listener.py` ownership moves from `culture/agentirc/ircd.py` to `culture/bots/bot_manager.py` (agentirc 9.5 already stops binding `webhook_port`; culture takes ownership).
- All 10 bot test files keep their behavioral assertions; the harness shifts from "construct culture's bundled IRCd" to "construct `agentirc.ircd.IRCd`."

**Architecture:** Single PR off `main` after agentirc 9.6.0 ships. Bumps the dep floor `agentirc-cli>=9.4,<10` → `agentirc-cli>=9.6,<10`. Minor bump 8.8.0 → 8.9.0 — additive within the bot framework, no public-API removal.

**Tech Stack:** Python 3.x, uv (deps + lockfile), aiohttp (existing webhook listener), pytest + pytest-asyncio + pytest-xdist, pre-commit (black + isort + flake8 + pylint + bandit + markdownlint).

**Companion spec:** `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md` (see "Implementation status" table for A2-Bridge + A2 rows; "Migration mechanics / Track A2-Bridge" for the full agentirc-side brief).

---

## Preconditions (do not start until all are true)

- agentirc#22 is closed and `agentirc-cli==9.6.0` is published to PyPI:

  ```bash
  uv pip install --dry-run "agentirc-cli>=9.6,<10" 2>&1 | tail -5
  ```

  Expected: a line like `Would install agentirc-cli==9.6.0`.

- The promoted public surface is reachable:

  ```bash
  cd /tmp && uv run --with "agentirc-cli>=9.6,<10" python -c "
  from agentirc.ircd import IRCd
  from agentirc.virtual_client import VirtualClient   # or agentirc.bot.VirtualClient if agentirc chose that namespace
  from agentirc.protocol import Event, EventType, BOT_CAP
  print('IRCd:', IRCd)
  print('VirtualClient:', VirtualClient)
  print('BOT_CAP:', BOT_CAP)
  "
  ```

  Expected: all three classes/constants resolve. If `agentirc.virtual_client` doesn't exist (agentirc may have chosen `agentirc.bot.VirtualClient` instead), update this plan to use the actual name and continue.

- agentirc's `docs/api-stability.md` lists `agentirc.ircd` and `agentirc.virtual_client` (or whatever name) under public modules. Confirms the surface is semver-tracked, not just incidentally importable.

  ```bash
  curl -s https://raw.githubusercontent.com/agentculture/agentirc/main/docs/api-stability.md | grep -E '(agentirc\.ircd|agentirc\.virtual_client|agentirc\.bot)'
  ```

  Expected: matching lines under the "Public" or "Public modules" section.

- Working tree on `main` is clean. `git status` shows no staged/unstaged changes inside `culture/bots/`, `culture/agentirc/`, `culture/cli/server.py`, or `tests/test_*bot*.py`.

If any precondition fails, stop and resolve before continuing.

---

## File Structure (what changes in this PR)

| Path | Action | Notes |
|---|---|---|
| `pyproject.toml` | Modify | Bump `agentirc-cli>=9.4,<10` → `agentirc-cli>=9.6,<10`. |
| `uv.lock` | Modify | Regenerate via `uv lock`. |
| `culture/cli/server.py` | Modify | `_run_server` (or whatever the entrypoint is named) constructs `agentirc.ircd.IRCd(config)` in-process instead of `culture.agentirc.ircd.IRCd(config)`. |
| `culture/bots/virtual_client.py` | Rewrite | Thin (~50 LOC) wrapper around `agentirc.virtual_client.VirtualClient`. Composes culture-specific glue: BotConfig integration, template rendering, fires_event chaining, owner DM. |
| `culture/bots/bot.py` | Modify | Keep the `server: IRCd` field (still needed for `get_client`/`channels`/`emit_event`/`fires_event`), but type it against the public `agentirc.ircd.IRCd` instead of the bundled `culture.agentirc.ircd.IRCd`. Add a `server_config: culture.config.ServerConfig` field for `webhook_port` (which moves out of agentirc). |
| `culture/bots/bot_manager.py` | Modify | Drop in-process `on_event` hook against the bundled IRCd. Telemetry counters source from `culture.telemetry.metrics` directly, not `server.metrics`. |
| `culture/bots/http_listener.py` | Modify | Listener owned by `BotManager`, started by `BotManager.start()`. Same aiohttp code; just owned by culture, not driven from `IRCd._http_listener`. |
| `culture/bots/system/__init__.py` | Modify | `discover_system_bots` no longer needs an `IRCd` reference — takes `server_name: str` (from `culture.config.ServerConfig.name`). |
| `culture/agentirc/CLAUDE.md` | Modify | Add a "Status (after A2)" note: bots no longer depend on the in-process IRCd; `culture/cli/server.py` uses `agentirc.ircd.IRCd` directly. The bundled IRCd is dead; A3 deletes it. |
| `tests/test_*bot*.py`, `tests/test_virtual_client.py`, `tests/test_events_bot_*.py`, `tests/test_welcome_bot.py`, `tests/test_http_listener.py` | Modify (harness only) | Replace "construct `culture.agentirc.ircd.IRCd`, hand to BotManager" with "construct `agentirc.ircd.IRCd`, hand to BotManager." Behavioral assertions stay. |
| `tests/conftest.py` | Modify (fixtures) | The `ircd` fixture (or equivalent) now constructs `agentirc.ircd.IRCd` directly. |
| `culture/__init__.py`, `pyproject.toml`, `CHANGELOG.md` | Modify | `/version-bump minor` (8.8.0 → 8.9.0). |

---

## Task 1 — Verify preconditions

**Files:** none (verification only).

- [ ] **Step 1.1:** Confirm 9.6 on PyPI.

  ```bash
  uv pip install --dry-run "agentirc-cli>=9.6,<10" 2>&1 | tail -5
  ```

- [ ] **Step 1.2:** Confirm the promoted public surface.

  Run the snippet from "Preconditions" above. **Save the actual `agentirc.virtual_client` (or whatever) module name** — every subsequent task references it.

- [ ] **Step 1.3:** Confirm `docs/api-stability.md` lists the new public modules.

  Run the curl + grep snippet from "Preconditions." If it doesn't list them, **stop** — the agentirc release shipped without the doc update; file a follow-up issue and wait for the next patch.

- [ ] **Step 1.4:** Read `agentirc.virtual_client.VirtualClient`'s public signature in the installed wheel:

  ```bash
  cd /tmp && uv run --with "agentirc-cli>=9.6,<10" python -c "
  import inspect
  from agentirc.virtual_client import VirtualClient
  print('init:', inspect.signature(VirtualClient.__init__))
  print('public methods:', sorted(n for n in dir(VirtualClient) if not n.startswith('_')))
  "
  ```

  This is the API culture's wrapper composes around. Record the constructor signature (likely `(nick: str, user: str, server: IRCd)` matching the internal version) and the public method names. Tasks 4-6 reference these.

---

## Task 2 — Branch and bump the dependency

**Files:** `pyproject.toml`, `uv.lock`.

- [ ] **Step 2.1:** Branch out.

  ```bash
  git checkout main && git pull
  git checkout -b feat/bots-embedded-agentirc
  ```

- [ ] **Step 2.2:** Bump the floor in `pyproject.toml`.

  Locate the `dependencies = [...]` block and change `"agentirc-cli>=9.4,<10"` → `"agentirc-cli>=9.6,<10"`.

- [ ] **Step 2.3:** Regenerate the lockfile.

  ```bash
  uv lock
  ```

  Stage both files together (per culture's CLAUDE.md "always stage uv.lock with pyproject.toml" rule).

- [ ] **Step 2.4:** Sanity check — the existing test suite still passes against 9.6 with the bot framework unchanged. Floor bump alone shouldn't change behavior; A1's config shim absorbs any 9.5 → 9.6 dataclass-field additions.

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_bots_integration.py tests/test_virtual_client.py
  ```

  Expected: green.

---

## Task 3 — Retarget `Event`/`EventType` imports to `agentirc.protocol`

**Files:** `culture/bots/virtual_client.py` (and anywhere else culture imports `Event`/`EventType` from a transitional path).

The transitional re-exports at `agentirc.skill.{Event, EventType}` work through 9.5.x (probably 9.6.x too) and disappear in 10.0.0. Pin to `agentirc.protocol` directly.

- [ ] **Step 3.1:** Find every site that imports `Event` or `EventType` from `culture.agentirc.skill` or `agentirc.skill`.

  ```bash
  grep -rn 'from culture\.agentirc\.skill import\|from agentirc\.skill import' culture/ tests/
  ```

  Expected hit list (verify on the current main):
  - `culture/bots/virtual_client.py:8`

  If there are more matches, add them to the change set.

- [ ] **Step 3.2:** Replace each import:

  ```python
  # before
  from culture.agentirc.skill import Event, EventType
  # after
  from agentirc.protocol import Event, EventType
  ```

- [ ] **Step 3.3:** Run a focused test pass:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_virtual_client.py tests/test_events_bot_trigger.py tests/test_events_bot_chain.py
  ```

  Expected: green. This step is purely a renamed import.

---

## Task 4 — Switch `culture/cli/server.py` to embed `agentirc.ircd.IRCd`

**Files:** `culture/cli/server.py`.

This is the key swap that decouples culture's bot framework from the bundled IRCd. After this step, `culture server start` is hosting agentirc's IRCd directly, which natively speaks the bot CAP and routes everything through the public `VirtualClient` interface.

- [ ] **Step 4.1:** Read `culture/cli/server.py:_run_server` (the function the CLI dispatcher calls for `culture server start`). Identify how it constructs the IRCd today:

  ```bash
  grep -n 'IRCd(\|from culture.agentirc.ircd import\|class.*IRCd\|_run_server' culture/cli/server.py
  ```

  Record the exact construction site and the lifecycle methods called on it (`await ircd.start()`, etc.).

- [ ] **Step 4.2:** Replace the import + construction:

  ```python
  # before
  from culture.agentirc.ircd import IRCd
  # ...
  ircd = IRCd(config)
  await ircd.start()

  # after
  from agentirc.ircd import IRCd
  # ...
  ircd = IRCd(config)
  await ircd.start()
  ```

  The constructor signature should match (both take `ServerConfig`). If the published `agentirc.ircd.IRCd` constructor differs from the bundled one, adapt — but the A2-Bridge brief specifically asked for "stable embedding API" so this should be a drop-in.

- [ ] **Step 4.3:** The four culture-owned verbs (`default`, `rename`, `archive`, `unarchive`) stay culture-side. They operate on culture's local state, not the IRCd; their handlers are unchanged. Verify by re-running `grep -n 'default\|rename\|archive\|unarchive' culture/cli/server.py` and confirming each is still wired to its existing handler.

- [ ] **Step 4.4:** Smoke test:

  ```bash
  uv run python -m culture server start --name smoke --port 6700 &
  PID=$!
  sleep 2
  uv run python -c "
  import socket
  s = socket.socket()
  s.connect(('127.0.0.1', 6700))
  s.send(b'NICK smoke\r\nUSER smoke 0 * :smoke\r\n')
  data = s.recv(4096)
  print(data.decode())
  s.close()
  "
  kill $PID
  ```

  Expected: server responds with `001 :Welcome ...` numerics. Confirms `agentirc.ircd.IRCd` boots cleanly from culture's CLI.

---

## Task 5 — Rewrite `culture/bots/virtual_client.py` as a wrapper

**Files:** `culture/bots/virtual_client.py`.

The 231-line file today re-implements what `agentirc.virtual_client.VirtualClient` already provides. After A2-Bridge, that's a public class. Culture's wrapper composes around it for culture-specific concerns: `BotConfig` integration, template-engine rendering (via `culture/bots/template_engine.py`), `fires_event` chaining, owner DM routing.

- [ ] **Step 5.1:** Read both files side-by-side to confirm shape:

  ```bash
  wc -l culture/bots/virtual_client.py
  cd /tmp && uv run --with "agentirc-cli>=9.6,<10" python -c "
  import inspect
  from agentirc.virtual_client import VirtualClient
  print(inspect.getsource(VirtualClient))
  " | wc -l
  ```

  Expected: ~230 LOC in culture, ~250 LOC in agentirc — they're functionally the same class. The wrapper composes; it does not re-implement.

- [ ] **Step 5.2:** Rewrite `culture/bots/virtual_client.py`. Target shape:

  ```python
  """Culture's wrapper around agentirc's public VirtualClient.

  Composes culture-specific glue (BotConfig, template engine, fires_event,
  owner DM) over the protocol-and-channel-membership behavior owned by
  agentirc.virtual_client.VirtualClient.
  """

  from __future__ import annotations
  from typing import TYPE_CHECKING

  from agentirc.protocol import Event, EventType
  from agentirc.virtual_client import VirtualClient as _AgentircVirtualClient

  from culture.bots.template_engine import render_template

  if TYPE_CHECKING:
      from agentirc.ircd import IRCd
      from culture.bots.config import BotConfig


  class VirtualClient(_AgentircVirtualClient):
      """A culture bot's IRC presence — extends agentirc.VirtualClient with culture-specific behavior."""

      def __init__(self, bot_config: BotConfig, server: IRCd) -> None:
          super().__init__(nick=bot_config.nick, user=bot_config.user, server=server)
          self.bot_config = bot_config
          # culture-specific fields here

      async def broadcast_to_channel(self, channel_name: str, text: str) -> None:
          """Render a message via culture's template engine and broadcast."""
          rendered = render_template(text, self.bot_config.template_vars)
          await super().broadcast_to_channel(channel_name, rendered)

      async def fire_event(self, event_type: EventType, channel: str | None, data: dict) -> None:
          """Emit a culture-defined event (for fires_event chaining)."""
          await self.server.emit_event(Event(type=event_type, channel=channel, nick=self.nick, data=data))

      # ... other culture-specific methods composed over super() ...
  ```

  Target: ~50-80 LOC. If you find yourself re-implementing `join_channel`/`part_channel`/etc., stop — the parent class already does that; just call `super().method(...)`.

- [ ] **Step 5.3:** Run the focused tests:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_virtual_client.py
  ```

  Expected: green. Anything that fails is a behavioral mismatch in the rewrite — fix it now, not later.

---

## Task 6 — Refactor `culture/bots/bot.py`

**Files:** `culture/bots/bot.py`.

Bot composes `BotConfig` + `VirtualClient` + `IRCd` ref today. The IRCd ref is used at four sites:

- [ ] **Step 6.1:** `bot.py:98` (`server.get_client` for nick collision check at start). The promoted `agentirc.ircd.IRCd` exposes `get_client(nick)` publicly (verify in Step 1.4's surface dump). Call it directly: `existing = self.server.get_client(self.nick)`.

- [ ] **Step 6.2:** `bot.py:168` (`server.channels.get` for dynamic-join channel check). Same — `IRCd.channels` is publicly accessible after A2-Bridge. If the public surface narrows this to a method (`get_channel(name)`), use that.

- [ ] **Step 6.3:** `bot.py:211` (`server.emit_event` for `fires_event`). Calls into `IRCd.emit_event` publicly. The `fires_event` semantics — bot triggers downstream bots — survive unchanged because emit_event still routes through the same skill-hooks / federation-relay / `#system`-surfacing path.

- [ ] **Step 6.4:** `bot.py:89-90` (`server.config.webhook_port` for URL construction). Read directly from culture's own `culture.config.ServerConfig` (the one BotManager loads in Task 7), not from `IRCd.config` — `webhook_port` is a culture-side concern after agentirc 9.5.

- [ ] **Step 6.5:** Keep the `server: IRCd` field on `Bot.__init__` — it's still needed for `get_client`/`channels`/`emit_event`, just typed against the public class now:

  ```python
  from agentirc.ircd import IRCd

  class Bot:
      def __init__(self, bot_config: BotConfig, server: IRCd, server_config: ServerConfig) -> None:
          self.bot_config = bot_config
          self.server = server
          self.server_config = server_config  # culture-side ServerConfig with webhook_port
  ```

- [ ] **Step 6.6:** Run the focused tests:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_bot.py tests/test_bot_config_fires_event_toplevel.py
  ```

---

## Task 7 — Refactor `culture/bots/bot_manager.py`

**Files:** `culture/bots/bot_manager.py`.

The manager wires bots to the IRCd's event stream. After A2 it consumes the publicly-typed IRCd reference handed to it by `culture/cli/server.py:_run_server`.

- [ ] **Step 7.1:** Replace the `on_event(self, event: Event)` method's wiring. Today `IRCd._dispatch_to_bots` calls `bot_manager.on_event(event)` directly via in-process plumbing in the bundled IRCd. After A2, the public IRCd has `subscription_registry` (or whatever the A2-Bridge promotion exposes) that BotManager can register against. Read the public surface:

  ```bash
  cd /tmp && uv run --with "agentirc-cli>=9.6,<10" python -c "
  import inspect
  from agentirc.ircd import IRCd
  print('IRCd public methods:', sorted(n for n in dir(IRCd) if not n.startswith('_')))
  "
  ```

  Wire BotManager to the appropriate hook; for in-process bots the hook is likely just calling a registered callback on every `emit_event`.

- [ ] **Step 7.2:** Telemetry. The four `server.metrics.bot_invocations.add()` call sites (lines 138-145) and `bot_manager.server.metrics.bot_webhook_duration.record()` in `http_listener.py:73-76` switch to importing the meters directly:

  ```python
  from culture.telemetry import metrics as telemetry_metrics
  telemetry_metrics.bot_invocations.add(1, attributes={...})
  telemetry_metrics.bot_webhook_duration.record(elapsed, attributes={...})
  ```

  The OTEL instruments are already module-level in `culture/telemetry/metrics.py` (per A1's audit); no need to route through the server.

- [ ] **Step 7.3:** `BotManager.__init__` takes the IRCd reference plus culture's own `ServerConfig`:

  ```python
  from agentirc.ircd import IRCd
  from culture.config import ServerConfig as CultureServerConfig, load_server_config

  class BotManager:
      def __init__(self, server: IRCd, server_config: CultureServerConfig) -> None:
          self.server = server
          self.server_config = server_config  # nested format, parsed by culture.config
          self.bots: dict[str, Bot] = {}
  ```

  `culture/cli/server.py:_run_server` constructs both `IRCd(agentirc_config)` and `BotManager(server=ircd, server_config=culture_server_config)` and wires them.

- [ ] **Step 7.4:** Update `BotManager.dispatch(bot_name, payload)` (line 215) — webhook payloads come in via `http_listener.py` and route to the bot's `handle()`. Same as today; just confirm the IRCd reference flows correctly.

- [ ] **Step 7.5:** Run the focused tests:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_bot_manager.py tests/test_events_bot_trigger.py tests/test_events_bot_chain.py
  ```

---

## Task 8 — Move webhook listener ownership from IRCd to BotManager

**Files:** `culture/bots/http_listener.py`, `culture/agentirc/ircd.py`.

agentirc 9.5 already stops binding `webhook_port` (per agentirc#15 closing comment). After A2's switch to `agentirc.ircd.IRCd`, no IRCd is binding it — culture must.

- [ ] **Step 8.1:** Move the listener startup from the bundled IRCd's `__init__` (`culture/agentirc/ircd.py:112-116` today) into `BotManager.start()`:

  ```python
  # in BotManager.start():
  self._http_listener = HttpListener(
      bot_manager=self,
      host="127.0.0.1",
      port=self.server_config.webhook_port,
  )
  await self._http_listener.start()
  ```

  Move the corresponding shutdown into `BotManager.stop()`.

- [ ] **Step 8.2:** Delete the listener instantiation from `culture/agentirc/ircd.py`. After A2, the bundled IRCd is bot-free *and* effectively dead (because `_run_server` uses `agentirc.ircd.IRCd`); A3 deletes the file entirely. This step prevents accidental double-binding if someone constructs the bundled IRCd manually before A3.

- [ ] **Step 8.3:** `HttpListener.__init__` already takes `bot_manager`; no signature change needed. Confirm `_handle_webhook` (line 81) still calls `bot_manager.dispatch(bot_name, payload)`.

- [ ] **Step 8.4:** Run the focused tests:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_http_listener.py
  ```

  The `/health` endpoint test, success/failure POST tests, and 404/503 error code tests should all pass unchanged — they assert HTTP behavior, not who owns the listener.

---

## Task 9 — System bot loading without an IRCd reference

**Files:** `culture/bots/system/__init__.py`, `culture/bots/bot_manager.py`.

`BotManager.load_system_bots()` (line 147) currently calls `discover_system_bots()` and passes its `IRCd` reference (the bundled one's). After A2 the IRCd reference is the public `agentirc.ircd.IRCd`, but more importantly the discovery only needs the server *name* for the prefix, not the IRCd itself.

- [ ] **Step 9.1:** Update `discover_system_bots(server_name: str)` signature — take a string, not an `IRCd`. The function scans `culture/bots/system/<subdir>/bot.yaml` and prefixes each name with `system-{server_name}-`.

- [ ] **Step 9.2:** Update `BotManager.load_system_bots()` to pass `self.server_config.name` (culture's own config — see Task 7.3) instead of an IRCd reference.

- [ ] **Step 9.3:** Run the focused test:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_welcome_bot.py
  ```

---

## Task 10 — Test harness migration

**Files:** all `tests/test_*bot*.py`, `tests/test_virtual_client.py`, `tests/test_events_bot_*.py`, `tests/test_welcome_bot.py`, `tests/test_http_listener.py`, `tests/conftest.py`.

The 10 bot test files assert behavior — webhook → IRC flow, event filter matching, fires_event chains, system bot rendering, virtual-client JOIN/PART. Behaviors are unchanged. The harness shifts.

- [ ] **Step 10.1:** Update `tests/conftest.py`'s IRCd fixture (or equivalent). Replace `from culture.agentirc.ircd import IRCd` with `from agentirc.ircd import IRCd`. Constructor signature should match.

- [ ] **Step 10.2:** For each test file, replace any direct import of `culture.agentirc.ircd.IRCd` with `agentirc.ircd.IRCd`. Most tests use the fixture, so the changes are limited to a handful of files.

  ```bash
  grep -rln 'from culture\.agentirc\.ircd import\|culture\.agentirc\.ircd\.' tests/
  ```

- [ ] **Step 10.3:** Special cases:
  - `tests/test_events_bot_chain.py` — bot fires event, second bot triggers. Both bots are in-process VirtualClients hosted by the constructed `agentirc.ircd.IRCd`; the chain runs through the IRCd's `emit_event` → registered callbacks. Expected to work unchanged in observable behavior.
  - `tests/test_bots_integration.py` — full webhook → IRC flow. Webhook listener is now in `BotManager`; the URL is unchanged (`http://127.0.0.1:<webhook_port>/<bot>`).
  - `tests/test_virtual_client.py` — culture's wrapper now extends `agentirc.virtual_client.VirtualClient`. Behavioral assertions (does broadcast work, does fires_event fire) stay; if any tests reach into private internals of culture's VirtualClient, refactor them to use the public surface or move them to test the wrapper composition specifically.

- [ ] **Step 10.4:** Run the full suite:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q
  ```

  Expected: all green. If anything fails, fix before continuing — don't push broken tests to CI.

---

## Task 11 — `culture/agentirc/CLAUDE.md` note

**Files:** `culture/agentirc/CLAUDE.md`.

- [ ] **Step 11.1:** Append a paragraph noting the bundled IRCd is dead:

  ```markdown
  ## Status (after A2, 2026-05-XX)

  After Phase A2 (`feat/bots-embedded-agentirc`), `culture/cli/server.py:_run_server`
  constructs `agentirc.ircd.IRCd(config)` directly — the bundled IRCd in this
  directory is no longer instantiated by anything in culture. `culture/bots/*`
  uses `agentirc.virtual_client.VirtualClient` (promoted to public in
  agentirc 9.6.0 via agentculture/agentirc#22). The files here are dead
  weight; A3 deletes them and `git mv`s `client.py` / `remote_client.py`
  to `culture/transport/`. `config.py` stays as the A1 re-export shim
  through 9.x.
  ```

---

## Task 12 — Pre-push reviewer + doc audit

**Files:** none (audits only).

- [ ] **Step 12.1:** Stage all changes:

  ```bash
  git add -A
  ```

- [ ] **Step 12.2:** Run the code reviewer per culture/CLAUDE.md's "Pre-push review for library/protocol code" rule. A2 swaps a process boundary (bundled IRCd → public agentirc.ircd.IRCd) and changes who owns the webhook listener — exactly the choke points the rule targets:

  ```text
  Agent(subagent_type="superpowers:code-reviewer", prompt="Review the staged diff on feat/bots-embedded-agentirc — A2 of the agentirc extraction. Key concerns: (1) IRCd reference handoff from culture/cli/server.py:_run_server through BotManager to Bot/VirtualClient, (2) webhook listener ownership transfer from culture/agentirc/ircd.py to culture/bots/bot_manager.py, (3) culture's VirtualClient subclass composing correctly over agentirc.virtual_client.VirtualClient. Spec: docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md. Plan: docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a2.md.")
  ```

- [ ] **Step 12.3:** Run the `doc-test-alignment` subagent:

  ```text
  Agent(subagent_type="doc-test-alignment", prompt="Audit the staged diff on feat/bots-embedded-agentirc for new public surface and report missing docs/ or protocol/extensions/ coverage. Note that culture/bots/virtual_client.py is now a wrapper over agentirc.virtual_client.VirtualClient — culture's docs may need updates noting the public agentirc surface culture depends on.")
  ```

- [ ] **Step 12.4:** Address any findings — fix docs, re-stage, repeat until both audits are clean.

---

## Task 13 — Version bump, push, PR

**Files:** `culture/__init__.py`, `pyproject.toml`, `CHANGELOG.md`.

- [ ] **Step 13.1:** `/version-bump minor` (8.8.0 → 8.9.0). A2 is additive within the bot framework — no public-API removal — so this is minor, not major.

- [ ] **Step 13.2:** Verify the bump:

  ```bash
  grep -n '8\.9\.0' culture/__init__.py pyproject.toml CHANGELOG.md
  ```

- [ ] **Step 13.3:** Edit the new `[8.9.0]` section of `CHANGELOG.md`:

  ```markdown
  ### Changed
  - Switched `culture server start` to embed `agentirc.ircd.IRCd` directly (in-process, via the public class promoted in agentirc 9.6.0 / agentculture/agentirc#22). The bundled IRCd in `culture/agentirc/` is now orphaned but stays on disk for A3 to delete.
  - Rewrote `culture/bots/virtual_client.py` as a thin wrapper around `agentirc.virtual_client.VirtualClient` (promoted to public in agentirc 9.6.0). Culture-specific concerns (BotConfig, template engine, fires_event, owner DM) compose over the public class. Public bot behavior unchanged.
  - Webhook listener (`webhook_port`) ownership moved from `culture/agentirc/ircd.py` to `culture/bots/bot_manager.py`.
  - Bumped `agentirc-cli>=9.4,<10` → `agentirc-cli>=9.6,<10`.

  ### Notes
  - This is the second of three phases extracting `culture/agentirc/` into the standalone `agentirc-cli` PyPI package. Phase A1 (config dataclasses) shipped in 8.8.0 (#309); Phase A3 (delete bundled IRCd, major bump) follows. Cross-repo coordination tracked at agentculture/agentirc#22.
  ```

- [ ] **Step 13.4:** Final test pass + format check:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q
  uv run black culture/bots/ culture/cli/server.py culture/agentirc/ircd.py
  uv run isort culture/bots/ culture/cli/server.py culture/agentirc/ircd.py
  ```

- [ ] **Step 13.5:** Commit and push:

  ```bash
  git commit -m "$(cat <<'EOF'
  feat(bots): embed agentirc.ircd.IRCd; rewrite VirtualClient as wrapper (Track A2)

  Switches culture server start to construct agentirc.ircd.IRCd directly
  (in-process), and rewrites culture/bots/virtual_client.py as a thin
  wrapper around the public agentirc.virtual_client.VirtualClient promoted
  in agentirc 9.6.0 (agentculture/agentirc#22). The bundled IRCd in
  culture/agentirc/ is orphaned; A3 deletes it. Webhook listener ownership
  moves from IRCd to BotManager. Public bot behavior unchanged.

  - Claude

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  git push -u origin feat/bots-embedded-agentirc
  ```

- [ ] **Step 13.6:** Open the PR via the `cicd` skill (or `gh pr create`):

  ```bash
  gh pr create --title "Phase A2: embed agentirc.ircd.IRCd and rewrite VirtualClient as wrapper" --body "$(cat <<'EOF'
  ## Summary

  - Switch `culture/cli/server.py:_run_server` to construct `agentirc.ircd.IRCd(config)` directly (in-process). The bundled IRCd in `culture/agentirc/` is orphaned but stays on disk for A3 to delete.
  - Rewrite `culture/bots/virtual_client.py` as a thin wrapper around `agentirc.virtual_client.VirtualClient` (promoted to public in agentirc 9.6.0 via agentculture/agentirc#22). Culture-specific concerns compose over the public class.
  - Webhook listener (`webhook_port`) ownership moves from `culture/agentirc/ircd.py` to `culture/bots/bot_manager.py`.
  - `Event`/`EventType` imports retargeted from `culture.agentirc.skill` to `agentirc.protocol`.
  - Bumps dep floor `agentirc-cli>=9.4,<10` → `agentirc-cli>=9.6,<10`.
  - `8.8.0 → 8.9.0` (minor — additive, no public-API removal).

  ## Why

  Phase A2 of the agentirc extraction (spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md`). Cross-repo coordination at agentculture/agentirc#22 unblocked this by promoting `agentirc.ircd.IRCd` and `agentirc.virtual_client.VirtualClient` to public. Phase A3 (delete bundled IRCd, major bump) follows.

  ## Test plan

  - [x] `bash .claude/skills/run-tests/scripts/test.sh -p -q` — full suite green
  - [x] All 10 bot test files pass: `test_bots_integration.py`, `test_bot.py`, `test_http_listener.py`, `test_events_bot_trigger.py`, `test_events_bot_chain.py`, `test_bot_config_fires_event_toplevel.py`, `test_bot_config.py`, `test_bot_manager.py`, `test_welcome_bot.py`, `test_virtual_client.py`
  - [x] Pre-commit hooks clean
  - [x] `superpowers:code-reviewer` clean
  - [x] `doc-test-alignment` clean
  - [x] Manual smoke: `culture server start --port 6700` boots; raw IRC client receives 001 welcome numerics

  ## Out of scope (Phase A3 follow-up)

  Deletion of `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py`, `git mv` of `client.py`/`remote_client.py` to `culture/transport/`, culture major bump. Plan: `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a3.md`.

  - Claude

  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  EOF
  )"
  ```

- [ ] **Step 13.7:** Wait for CI:

  ```bash
  gh pr checks
  ```

  If anything fails, fix inline, push, run `/sonarclaude` before declaring ready (per culture/CLAUDE.md). Use the `cicd` skill for automated reviewer comments.

---

## Summary

- Switches `culture server start` to embed `agentirc.ircd.IRCd` (public after agentirc 9.6.0 / agentculture/agentirc#22) instead of culture's bundled IRCd.
- Rewrites `culture/bots/virtual_client.py` as a thin wrapper over `agentirc.virtual_client.VirtualClient` — culture-specific concerns compose over the public class.
- Moves webhook listener ownership from `culture/agentirc/ircd.py` to `culture/bots/bot_manager.py`.
- Bumps dep floor to `agentirc-cli>=9.6,<10`. Minor bump 8.8.0 → 8.9.0.

User-visible bot behavior is unchanged: webhook URLs, event filters, fires_event chains, system bots, telemetry counters all behave identically. The change is architectural — bots and the IRCd are still in the same Python process, but the IRCd is now agentirc's public class, not culture's bundled fork.

Spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md` (Track A2 row).
Cross-repo coordination: agentculture/agentirc#22.
A3 follow-up: `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a3.md`.

## Test plan

- [ ] Existing bot test suite passes (`bash .claude/skills/run-tests/scripts/test.sh -p -q`).
- [ ] `superpowers:code-reviewer` runs clean against the staged diff.
- [ ] `doc-test-alignment` reports no missing `docs/` or `protocol/extensions/` coverage.
- [ ] Manual smoke: `culture server start` boots `agentirc.ircd.IRCd`, raw client receives 001 welcome numerics, system welcome bot greets a JOIN.
- [ ] Post-merge: verify on the spark host that `culture-agent-spark-culture.service` continues to receive bot events with the new dependency.

---

## Post-merge (notes for A3 author)

After this PR merges:

- `culture/cli/server.py:_run_server` already uses `agentirc.ircd.IRCd` — A3 doesn't need to change the launch model further.
- The bundled IRCd in `culture/agentirc/` is orphaned (no consumers in culture); A3 just `git rm`s it.
- `culture/agentirc/config.py` (the A1 re-export shim) stays — A3 keeps it through the 9.x line per the spec.
- The federation envelope sniff (spec § "Federation interop during the migration window") becomes irrelevant once A3 deletes the bundled IRCd entirely; ship it only if mixed-version peers are expected during the brief A2-to-A3 window.
