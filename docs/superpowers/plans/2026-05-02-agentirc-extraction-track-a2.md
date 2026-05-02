# AgentIRC Extraction — Track A2 (Bot Framework Rewrite) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite culture's bot framework against the public bot extension API in `agentirc-cli==9.5.0` (`agentirc.io/bot` CAP, `EVENTSUB`/`EVENTUNSUB`/`EVENT`/`EVENTERR`/`EVENTPUB` verbs, canonical 5-field envelope in `agentirc.protocol`). After this lands, `culture/bots/*` no longer holds an in-process `IRCd` reference and no longer reaches into `server.emit_event`/`server.channels`/`server.get_or_create_channel`/`server.get_client`/`server.config`/`server.metrics`. This unblocks Track A3 — the deletion of `culture/agentirc/{ircd,server_link,channel,…}` and the subprocess shim at `culture server`.

**Architecture:** Single PR off `main`. Bumps the dep floor to `agentirc-cli>=9.5,<10`. Rewrites `culture/bots/{virtual_client,bot,bot_manager,http_listener}.py` and `culture/bots/system/__init__.py`. Culture takes ownership of the `webhook_port` HTTP listener (agentirc 9.5 no longer binds it). Bot tests stay; the harness shifts from "construct an IRCd, hand it to BotManager" to "spin up an IRCd, connect a CAP-bot to it." Minor bump 8.8.0 → 8.9.0.

**Tech Stack:** Python 3.x, uv (deps + lockfile), aiohttp (existing webhook listener), pytest + pytest-asyncio + pytest-xdist, pre-commit (black + isort + flake8 + pylint + bandit + markdownlint).

**Companion spec:** `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md` (see "Implementation status" table for A2 row, "Federation interop during the migration window" for the optional sniff patch).

---

## Preconditions (do not start until all are true)

- `agentirc-cli==9.5.0` (or any later 9.5.x patch) is installable from PyPI:

  ```bash
  uv pip install --dry-run "agentirc-cli>=9.5,<10" 2>&1 | tail -5
  ```

  Expected: a line like `Would install agentirc-cli==9.5.0`.

- The public surface culture will pin against is reachable:

  ```bash
  cd /tmp && uv run --with "agentirc-cli>=9.5,<10" python -c "
  from agentirc.protocol import Event, EventType, EVENT_TYPE_RE
  from agentirc.protocol import EVENTSUB, EVENTUNSUB, EVENT, EVENTERR, EVENTPUB
  print('envelope fields:', sorted(Event.__dataclass_fields__))
  print('verbs:', EVENTSUB, EVENTUNSUB, EVENT, EVENTERR, EVENTPUB)
  "
  ```

  Expected: `envelope fields: ['channel', 'data', 'nick', 'timestamp', 'type']`. If `agentirc.protocol` doesn't export these, **stop** — the floor was bumped past a release that doesn't actually carry the surface.

- `agentirc.io/bot` CAP is advertised by **both** the in-tree bundled IRCd and a freshly-installed `agentirc serve`:

  ```bash
  # Bundled IRCd (still in tree until A3):
  python -c "from culture.agentirc.ircd import IRCd; from agentirc.config import ServerConfig; print('bundled CAP', IRCd(ServerConfig(name='t')).advertised_caps)"

  # Installed agentirc:
  cd /tmp && uv run --with "agentirc-cli>=9.5,<10" python -c "from agentirc.ircd import IRCd; from agentirc.config import ServerConfig; print('installed CAP', IRCd(ServerConfig(name='t')).advertised_caps)"
  ```

  Both must list `agentirc.io/bot`. If the bundled IRCd doesn't yet, **stop** — A2 lands before A3 with the bundled IRCd still hosting bots, so the bundled IRCd must speak the CAP. If only the bundled IRCd is missing, the simplest fix is to merge the bot CAP support upstream (forward-port from agentirc 9.5 into `culture/agentirc/`) before continuing — that's a separate small PR, see the "Bundled-IRCd CAP backport" precondition note below.

- Working tree on `main` is clean. `git status` shows no staged/unstaged changes inside `culture/bots/`, `culture/agentirc/`, `culture/cli/server.py`, or `tests/test_*bot*.py`.

### Bundled-IRCd CAP backport (separate small PR if needed)

If the bundled IRCd in `culture/agentirc/` doesn't yet advertise `agentirc.io/bot` or handle `EVENTSUB`/`EVENTPUB`, ship a minimal backport as a standalone patch PR (8.8.0 → 8.8.1 patch — pure compat, no public-API change). Cherry-pick the relevant changes from agentirc PR #20 into `culture/agentirc/{ircd.py,server_link.py,protocol.py}`. Independent of this plan; a hard precondition for it. The simpler alternative is to skip A2 in-tree testing entirely and run A2 + A3 in the same release window — but that loses the safety of landing A2 against the still-bundled IRCd.

---

## File Structure (what changes in this PR)

| Path | Action | Notes |
|---|---|---|
| `pyproject.toml` | Modify | Bump `agentirc-cli>=9.4,<10` → `agentirc-cli>=9.5,<10`. |
| `uv.lock` | Modify | Regenerate via `uv lock`. |
| `culture/bots/virtual_client.py` | Rewrite | Drop `server` reference; become a CAP-bot client (see Task 4 decision point). |
| `culture/bots/bot.py` | Modify | Replace `server.get_client`/`server.channels.get`/`server.emit_event`/`server.config.webhook_port` with `EVENTPUB` + bot-client helpers. |
| `culture/bots/bot_manager.py` | Modify | Replace in-process `on_event` hook with an `EVENTSUB` subscription. Telemetry handle moves to `culture.telemetry.metrics` directly. |
| `culture/bots/http_listener.py` | Modify | Listener owned by `BotManager`, not by `IRCd`. Route handlers unchanged. |
| `culture/bots/system/__init__.py` | Modify | `discover_system_bots` no longer needs an `IRCd` reference. |
| `culture/agentirc/ircd.py` | Modify | Stop instantiating `BotManager`/`HttpListener` here. Bots are external CAP clients now. (Lines ~92-122 today.) |
| `culture/agentirc/CLAUDE.md` | Modify | Note: bots no longer depend on the in-process IRCd; only `culture/cli/server.py:_run_server` still imports `culture.agentirc.{ircd,server_link,channel}`. |
| `tests/test_*bot*.py`, `tests/test_virtual_client.py`, `tests/test_events_bot_*.py`, `tests/test_welcome_bot.py`, `tests/test_http_listener.py` | Modify (harness) | Where tests construct an `IRCd` and pass it to `BotManager`, switch to constructing an `IRCd` and starting a separate `BotManager` that connects to it as a CAP client. Test assertions about behavior stay. |
| `tests/conftest.py` | Modify (fixtures) | Add a `cap_bot_client` fixture that wraps `agentirc.protocol.Event` decoding for tests. |
| `culture/__init__.py`, `pyproject.toml`, `CHANGELOG.md` | Modify | `/version-bump minor` (8.8.0 → 8.9.0). |

---

## Task 1 — Verify preconditions

**Files:** none (verification only).

- [ ] **Step 1.1:** Confirm 9.5.0 is on PyPI.

  ```bash
  uv pip install --dry-run "agentirc-cli>=9.5,<10" 2>&1 | tail -5
  ```

- [ ] **Step 1.2:** Confirm the public surface.

  Run the `agentirc.protocol` snippet from "Preconditions" above. **Save the output** — Tasks 4-6 reference these symbol names.

- [ ] **Step 1.3:** Confirm both IRCds advertise `agentirc.io/bot`.

  Run both CAP-advertise snippets from "Preconditions". If only the bundled one is missing, branch out and ship the cherry-pick PR (see "Bundled-IRCd CAP backport"); merge it before continuing.

- [ ] **Step 1.4:** Smoke-test an `EVENTSUB` round-trip against a freshly-installed `agentirc serve`.

  ```bash
  cd /tmp && uv venv eventsub-check && uv pip install --python eventsub-check/bin/python "agentirc-cli>=9.5,<10" >/dev/null
  eventsub-check/bin/agentirc serve --config /tmp/test-server.yaml &
  AGENTIRC_PID=$!
  sleep 2
  # connect a raw client, NICK/USER, CAP REQ agentirc.io/bot, EVENTSUB 1, JOIN #room from another client, expect EVENT 1 :<base64-json>
  # (full snippet in agentirc/docs/cli.md)
  kill $AGENTIRC_PID
  ```

  Smoke test only — full integration coverage comes from the rewritten bot tests in Task 9.

---

## Task 2 — Branch and bump the dependency

**Files:** `pyproject.toml`, `uv.lock`.

- [ ] **Step 2.1:** Branch out.

  ```bash
  git checkout main && git pull
  git checkout -b feat/bots-public-extension-api
  ```

- [ ] **Step 2.2:** Bump the floor in `pyproject.toml`.

  Locate the `dependencies = [...]` block and change `"agentirc-cli>=9.4,<10"` → `"agentirc-cli>=9.5,<10"`.

- [ ] **Step 2.3:** Regenerate the lockfile.

  ```bash
  uv lock
  ```

  Stage both files together (per culture's CLAUDE.md "always stage uv.lock with pyproject.toml" rule).

- [ ] **Step 2.4:** Sanity check — the existing test suite still passes against 9.5 with the bot framework unchanged.

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_bots_integration.py tests/test_virtual_client.py
  ```

  Expected: green. The dep bump alone shouldn't change behavior; A1's config shim absorbs any 9.4 → 9.5 dataclass-field additions.

---

## Task 3 — Retarget `Event`/`EventType` imports to `agentirc.protocol`

**Files:** `culture/bots/virtual_client.py`, anywhere else culture imports `Event`/`EventType`.

The transitional re-exports at `agentirc.skill.{Event, EventType}` work through 9.5.x and disappear in 10.0.0. Pin to `agentirc.protocol` directly.

- [ ] **Step 3.1:** Find every site that imports `Event` or `EventType` from `culture.agentirc.skill` or `agentirc.skill`.

  ```bash
  grep -rn 'from culture\.agentirc\.skill import\|from agentirc\.skill import' culture/ tests/
  ```

  Survey-time hit list (verify on the current main):
  - `culture/bots/virtual_client.py:8`

  Expect this to be the only match. If there are more, add them to the change set.

- [ ] **Step 3.2:** Replace each import:

  ```python
  # before
  from culture.agentirc.skill import Event, EventType
  # after
  from agentirc.protocol import Event, EventType
  ```

- [ ] **Step 3.3:** Run a focused test pass to confirm no behavior change.

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_virtual_client.py tests/test_events_bot_trigger.py tests/test_events_bot_chain.py
  ```

  Expected: green. This step is purely a renamed import.

---

## Task 4 — Rewrite `culture/bots/virtual_client.py` against the public extension API

**Files:** `culture/bots/virtual_client.py`.

This is the heart of A2. `VirtualClient` today is a duck-typed in-process bot that holds an `IRCd` reference (`self.server`) and uses it for: (a) emitting events, (b) reading/writing channel state, (c) resolving nicks, (d) reading server config. Each of these has a public-API equivalent in 9.5.

- [ ] **Step 4.1: Decision point — connection model.** Before writing code, decide:

  - **Option A: TCP CAP-bot client.** `VirtualClient` opens a real connection to `127.0.0.1:<irc_port>` (the same port humans use), `CAP REQ agentirc.io/bot`, and uses standard IRC verbs + the new `EVENT*` verbs for all bot operations.
  - **Option B: In-process Python API.** If `agentirc-cli==9.5` exposes a class like `agentirc.bot.Bot` or `agentirc.bot.Client` for in-process bot hosting, use that.

  Verify which option agentirc 9.5 supports:

  ```bash
  cd /tmp && uv run --with "agentirc-cli>=9.5,<10" python -c "
  import agentirc
  print(sorted(n for n in dir(agentirc) if not n.startswith('_')))
  try:
      import agentirc.bot
      print('bot module:', sorted(n for n in dir(agentirc.bot) if not n.startswith('_')))
  except ImportError:
      print('no agentirc.bot module — Option A required')
  "
  ```

  **Record the decision in this task before continuing.** Recommendation: prefer Option B if available (matches the in-process pattern agentirc's own `VirtualClient` uses for the system bot, per agentirc#15 closing comment); fall back to Option A.

- [ ] **Step 4.2:** Replace the four `server.emit_event(Event(...))` call sites (current lines 76, 95, 136, 158) with `EVENTPUB`. The wire form is `EVENTPUB <type> <channel-or-*> :<base64-json-data>`. Under Option B, the API call is the agentirc-provided `bot.emit(type, channel, data)`. Under Option A, the call is the bot client's own `send_eventpub(type, channel, data_dict)`. The 5-field envelope (`type`/`channel`/`nick`/`data`/`timestamp`) is constructed server-side; the bot only supplies `type`, `channel`, and `data` (`nick` is the bot's own nick; `timestamp` is server-set).

- [ ] **Step 4.3:** Replace the four `server.channels.get(name)`/`del` accesses (lines 82, 109, 121, 144) and the `server.get_or_create_channel(name)` access (line 56). These are used today for: pre-checking JOIN, post-PART cleanup, broadcast membership walk, mention recipient walk.

  - For JOIN/PART: rely on the IRC `JOIN` verb (server tracks membership; bot doesn't need to).
  - For broadcast and mention walks: use the inbound `EVENT` envelopes plus the existing IRC `NAMES` / `WHO` paths the harness uses for any human-facing client. Do not maintain a duplicate channel registry inside the bot process.
  - If a piece of state (e.g., "is this user opt-in for this channel") is genuinely needed, surface it via an `EVENT` filter instead of polling state.

- [ ] **Step 4.4:** Replace the two `server.get_client(nick)` lookups (lines 175, 212). These are for DM target resolution and `@`-mention recipient lookup. Equivalents:

  - DM: just send `PRIVMSG <nick> :<text>`. The server resolves the nick.
  - Mention: parse mentions out of incoming `EVENT` payloads; for each mention, send `NOTICE <nick> :<mention-text>`. No client lookup needed.

- [ ] **Step 4.5:** Replace `server.config.name` (line 36) with the bot's own configured server identity, read from `~/.culture/server.yaml` via `agentirc.config.ServerConfig`. The system-nick prefix is a culture-side concept; keep the prefix logic in `culture/bots/virtual_client.py` and source the server name from culture's own config loader, not from a server reference.

- [ ] **Step 4.6:** Add `EVENTSUB` subscription registration in `VirtualClient.__init__` or `start`. The bot's filter is whatever the bot's `BotConfig.trigger_type == "event"` filter compiles to (see `culture/bots/filter_dsl.py`). For non-event-triggered bots (webhook only), no subscription is needed.

- [ ] **Step 4.7:** Run the focused tests:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_virtual_client.py
  ```

  Expected: green. Anything that fails here is a behavioral mismatch in the rewrite — fix it now, not later.

---

## Task 5 — Refactor `culture/bots/bot.py`

**Files:** `culture/bots/bot.py`.

Bot composes `BotConfig` + `VirtualClient` + `IRCd` ref today. The IRCd ref is used at four sites:

- [ ] **Step 5.1:** `bot.py:98` (`server.get_client` for nick collision check at start). Replace with a `WHO <nick>` query against the bot's own connection (Option A) or a public helper (Option B).

- [ ] **Step 5.2:** `bot.py:168` (`server.channels.get` for dynamic-join channel check). Replace with a `LIST <channel>` or remove entirely if the JOIN itself is sufficient (server returns an error if the channel doesn't exist and the bot's not allowed to create it).

- [ ] **Step 5.3:** `bot.py:211` (`server.emit_event` for `fires_event`). Replace with `EVENTPUB`. The `fires_event` semantics — bot triggers downstream bots — survive unchanged because `EVENTPUB` reuses `IRCd.emit_event` server-side and federation/skill-hooks/`#system` surfacing all happen on the same path (per agentirc#15 closing comment, "Decision E").

- [ ] **Step 5.4:** `bot.py:89-90` (`server.config.webhook_port` for URL construction). Read directly from culture's own `ServerConfig` instance (the one `BotManager` constructs in Task 6).

- [ ] **Step 5.5:** Remove the `server: IRCd` field from `Bot.__init__`. The bot composes `BotConfig` + `VirtualClient` + culture's own `ServerConfig`; that's all it needs.

- [ ] **Step 5.6:** Run the focused tests:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_bot.py tests/test_bot_config_fires_event_toplevel.py
  ```

---

## Task 6 — Refactor `culture/bots/bot_manager.py`

**Files:** `culture/bots/bot_manager.py`.

`BotManager.on_event` is hooked by `IRCd._dispatch_to_bots` today. After the cutover, the IRCd doesn't dispatch to bots — bots subscribe to events themselves via `EVENTSUB`. The manager becomes a coordinator of bot subscriptions, not an event sink.

- [ ] **Step 6.1:** Replace the `on_event(self, event: Event)` method with a per-bot `EVENTSUB` registration that runs at bot start. For event-triggered bots, the manager (or each bot's `VirtualClient`) sends `EVENTSUB <id> [type=…] [channel=…] [nick=…]`; the filter is compiled from `BotConfig` via `filter_dsl.py` (already exists).

- [ ] **Step 6.2:** Telemetry. The four `server.metrics.bot_invocations.add()` call sites (lines 138-145) and `bot_manager.server.metrics.bot_webhook_duration.record()` in `http_listener.py:73-76` switch to importing the meters directly:

  ```python
  from culture.telemetry import metrics as telemetry_metrics
  telemetry_metrics.bot_invocations.add(1, attributes={...})
  telemetry_metrics.bot_webhook_duration.record(elapsed, attributes={...})
  ```

  The OTEL instruments are already module-level in `culture/telemetry/metrics.py` (per A1's audit); no need to route through the server.

- [ ] **Step 6.3:** Remove the `IRCd` reference from `BotManager.__init__`. The manager constructs:
  - `culture.telemetry.metrics` (module import).
  - `agentirc.config.ServerConfig` (loaded from `~/.culture/server.yaml`).
  - A `VirtualClient` per bot (per Task 4), each with its own `EVENTSUB` subscription if event-triggered.

- [ ] **Step 6.4:** Update `BotManager.dispatch(bot_name, payload)` (line 215) to no longer assume an IRCd is local — webhook payloads come in via `http_listener.py` and just route to the bot's `handle()` method, same as today.

- [ ] **Step 6.5:** Run the focused tests:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_bot_manager.py tests/test_events_bot_trigger.py tests/test_events_bot_chain.py
  ```

---

## Task 7 — Move webhook listener ownership from IRCd to BotManager

**Files:** `culture/bots/http_listener.py`, `culture/agentirc/ircd.py`.

agentirc 9.5 stops binding `webhook_port` (per agentirc#15 closing comment, "Webhook ownership"). `webhook_port` stays in `ServerConfig` for backward compat, but the listener moves to culture.

- [ ] **Step 7.1:** Move the listener startup. Today (`culture/agentirc/ircd.py:112-116`):

  ```python
  self._http_listener = HttpListener(bot_manager, "127.0.0.1", webhook_port)
  ```

  Move this into `BotManager.start()`:

  ```python
  self._http_listener = HttpListener(self, "127.0.0.1", self.config.webhook_port)
  await self._http_listener.start()
  ```

  Move the corresponding shutdown call (somewhere around `ircd.py:122` today) into `BotManager.stop()`.

- [ ] **Step 7.2:** Delete the listener instantiation from `culture/agentirc/ircd.py`. After A2 lands, the bundled IRCd is bot-free — it's just an IRC server. (A3 will then delete the bundled IRCd entirely.)

- [ ] **Step 7.3:** `HttpListener.__init__` already takes `bot_manager`; no signature change. Confirm `_handle_webhook` (line 81) still calls `bot_manager.dispatch(bot_name, payload)` — that path is unchanged.

- [ ] **Step 7.4:** Run the focused tests:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_http_listener.py
  ```

  The `/health` endpoint test, success/failure POST tests, and 404/503 error code tests should all pass unchanged — they assert HTTP behavior, not who owns the listener.

---

## Task 8 — System bot loading without an IRCd reference

**Files:** `culture/bots/system/__init__.py`, `culture/bots/bot_manager.py`.

`BotManager.load_system_bots()` (line 147) currently calls `discover_system_bots()` and passes its `IRCd` reference. After A2, system bots are just normal bots that happen to ship in the wheel; they connect via the same CAP-bot path as user bots.

- [ ] **Step 8.1:** Update `discover_system_bots(server_name: str)` signature — take a server name string, not an `IRCd`. The function scans `culture/bots/system/<subdir>/bot.yaml` and prefixes each name with `system-{server_name}-`.

- [ ] **Step 8.2:** Update `BotManager.load_system_bots()` to pass `self.config.name` instead of an IRCd reference.

- [ ] **Step 8.3:** Run the focused test:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_welcome_bot.py
  ```

---

## Task 9 — Test harness migration

**Files:** all `tests/test_*bot*.py`, `tests/test_virtual_client.py`, `tests/test_events_bot_*.py`, `tests/test_welcome_bot.py`, `tests/test_http_listener.py`, `tests/conftest.py`.

The tests assert bot behavior — webhook → IRC flow, event filter matching, fires_event chains, system bot rendering, virtual-client JOIN/PART. These behaviors are unchanged; only the harness for setting up "an IRCd plus some bots" changes.

- [ ] **Step 9.1:** Add a `cap_bot_client` fixture to `tests/conftest.py`:

  ```python
  @pytest.fixture
  async def cap_bot_client(ircd):
      """Connect a CAP-bot client to the running IRCd. Returns a helper with .send(), .recv(), .eventsub(), .eventpub()."""
      # ... establish connection, NICK/USER, CAP REQ agentirc.io/bot, ACK
  ```

  The fixture is the testing-side analog of `VirtualClient` — it mirrors the verb sequence but exposes assertion-friendly helpers.

- [ ] **Step 9.2:** For each test file, replace the "construct IRCd, hand to BotManager, manipulate bots in-process" pattern with "construct IRCd, start BotManager, BotManager opens CAP-bot connections to IRCd". Most tests need at most a few lines changed in the setup; assertions stay.

- [ ] **Step 9.3:** Special cases:
  - `tests/test_events_bot_chain.py` — bot fires event, second bot triggers. After A2, both bots are CAP clients; the chain runs over the wire (`EVENTPUB` → server `emit_event` → reflexive `EVENT` to subscribed bots). Expected to work unchanged in observable behavior.
  - `tests/test_bots_integration.py` — full webhook → IRC flow. Webhook listener is now in `BotManager`; the `POST /<bot>` URL is unchanged (`http://127.0.0.1:<webhook_port>/<bot>`).
  - `tests/test_virtual_client.py` — was unit-testing the in-process `VirtualClient`. Rename helper assertions if internal method names change; behavioral assertions stay.

- [ ] **Step 9.4:** Run the full test suite:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q
  ```

  Expected: all green. If anything fails, fix before continuing — don't push broken tests to CI.

---

## Task 10 — `culture/agentirc/CLAUDE.md` note

**Files:** `culture/agentirc/CLAUDE.md`.

- [ ] **Step 10.1:** Append a paragraph noting that bots are no longer in-process consumers of this directory:

  ```markdown
  ## Status (after A2, 2026-05-XX)

  After Phase A2 (`feat/bots-public-extension-api`), `culture/bots/*` no
  longer holds an in-process `IRCd` reference. Bots connect to the IRCd
  via the public `agentirc.io/bot` CAP and use `EVENTSUB`/`EVENTPUB`
  instead of `server.emit_event` and `server.channels`. The only
  remaining culture-side consumer of `culture/agentirc/{ircd, server_link,
  channel, ...}` internals is `culture/cli/server.py:_run_server`. A3
  deletes the bundled IRCd and replaces that import with a subprocess
  shim into the installed `agentirc` binary.
  ```

---

## Task 11 — Pre-push reviewer + doc audit

**Files:** none (audits only).

- [ ] **Step 11.1:** Stage all changes and run the code reviewer agent on the diff:

  ```bash
  git add -p   # selective; or git add for everything in the change set
  ```

  Then invoke `superpowers:code-reviewer` per culture/CLAUDE.md's "Pre-push review for library/protocol code" rule. A2 touches transport (CAP negotiation, EVENT verb wire format) and protocol parsers — exactly the choke points the rule targets.

- [ ] **Step 11.2:** Run the `doc-test-alignment` subagent:

  ```bash
  Agent(subagent_type="doc-test-alignment", prompt="Audit the staged diff on feat/bots-public-extension-api for new public surface (CLI commands, config fields, IRC verbs, exceptions, public functions) and report missing docs/ or protocol/extensions/ coverage.")
  ```

  A2 is mostly internal rewrite, but the change in webhook ownership and the `agentirc.io/bot` CAP usage may need updates in `docs/` (search for `webhook_port`, `bot_manager`, `webhook listener`).

- [ ] **Step 11.3:** Address any findings — fix docs, re-stage, repeat until both audits are clean.

---

## Task 12 — Version bump, push, PR

**Files:** `culture/__init__.py`, `pyproject.toml`, `CHANGELOG.md`.

- [ ] **Step 12.1:** `/version-bump minor` (8.8.0 → 8.9.0). A2 is additive within the bot framework — no public-API removal — so this is minor, not major.

- [ ] **Step 12.2:** Verify the bump:

  ```bash
  grep -n '8\.9\.0' culture/__init__.py pyproject.toml CHANGELOG.md
  ```

- [ ] **Step 12.3:** Edit the new `[8.9.0]` section of `CHANGELOG.md` to describe what changed. Suggested entries:

  ```markdown
  ### Changed
  - Rewrote culture/bots/* against the public bot extension API in agentirc-cli 9.5.0 (`agentirc.io/bot` CAP, `EVENTSUB`/`EVENTPUB`, 5-field envelope from `agentirc.protocol`). Bots no longer hold an in-process IRCd reference. Webhook listener (`webhook_port`) ownership moved from `culture/agentirc/ircd.py` to `culture/bots/bot_manager.py`. Public bot behavior unchanged.
  - Bumped `agentirc-cli>=9.4,<10` → `agentirc-cli>=9.5,<10`.

  ### Notes
  - This is the second of three phases extracting `culture/agentirc/` into the standalone `agentirc-cli` PyPI package. Phase A1 (config dataclasses) shipped in 8.8.0 (#309); Phase A3 (delete bundled IRCd, subprocess shim, major bump) follows.
  ```

- [ ] **Step 12.4:** Final test pass + format check:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q
  uv run black culture/bots/ culture/agentirc/ircd.py
  uv run isort culture/bots/ culture/agentirc/ircd.py
  ```

- [ ] **Step 12.5:** Commit and push:

  ```bash
  git commit -m "$(cat <<'EOF'
  feat(bots): rewrite against agentirc-cli 9.5 public extension API (Track A2)

  Drops the in-process IRCd reference from culture/bots/*. Bots now subscribe
  via EVENTSUB/EVENTPUB and connect with the agentirc.io/bot CAP. Webhook
  listener ownership moves from IRCd to BotManager (agentirc 9.5 no longer
  binds webhook_port). Unblocks Phase A3.

  - Claude

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  git push -u origin feat/bots-public-extension-api
  ```

- [ ] **Step 12.6:** Open the PR via the `pr-review` skill (or `gh pr create`):

  ```bash
  gh pr create --title "Phase A2: rewrite bots against agentirc-cli 9.5 public extension API" --body "$(cat <<'EOF'
  ## Summary

  - Rewrite `culture/bots/{virtual_client,bot,bot_manager,http_listener}.py` against the public bot extension API in agentirc-cli 9.5.0 (`agentirc.io/bot` CAP, `EVENTSUB`/`EVENTUNSUB`/`EVENT`/`EVENTERR`/`EVENTPUB` verbs, 5-field envelope from `agentirc.protocol`).
  - `Event`/`EventType` now sourced from `agentirc.protocol` directly (the `agentirc.skill` re-exports are transitional, gone in 10.0.0).
  - Webhook listener (`webhook_port`) ownership moves from the IRCd to `BotManager` — agentirc 9.5 stops binding the port (per agentculture/agentirc#15 closing comment).
  - Bumps dep floor `agentirc-cli>=9.4,<10` → `agentirc-cli>=9.5,<10`.
  - `8.8.0 → 8.9.0` (minor — additive, no public-API removal).

  ## Why

  Phase A2 of the agentirc extraction (spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md`). Unblocks Phase A3 (delete bundled IRCd, subprocess shim, major bump) by removing culture/bots' last in-process dependency on `culture/agentirc/`.

  ## Test plan

  - [x] `bash .claude/skills/run-tests/scripts/test.sh -p -q` — full suite green
  - [x] All 10 bot test files pass: `test_bots_integration.py`, `test_bot.py`, `test_http_listener.py`, `test_events_bot_trigger.py`, `test_events_bot_chain.py`, `test_bot_config_fires_event_toplevel.py`, `test_bot_config.py`, `test_bot_manager.py`, `test_welcome_bot.py`, `test_virtual_client.py`
  - [x] Pre-commit hooks clean
  - [x] `superpowers:code-reviewer` clean
  - [x] `doc-test-alignment` clean

  ## Out of scope (Phase A3 follow-up)

  Deletion of `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill,client,remote_client}.py`, subprocess shim at `culture/cli/server.py:_run_server`, culture major bump. Plan: `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a3.md`.

  - Claude

  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  EOF
  )"
  ```

- [ ] **Step 12.7:** Wait for CI:

  ```bash
  gh pr checks
  ```

  If anything fails, fix inline, push, run `/sonarclaude` before declaring ready (per culture/CLAUDE.md). Use the `pr-review` skill for automated reviewer comments.

---

## Summary

- Rewrites `culture/bots/*` against the public bot extension API in `agentirc-cli==9.5.0`. Drops the in-process IRCd reference. `EVENTSUB`/`EVENTPUB` replace `BotManager.on_event`/`server.emit_event`.
- Webhook listener (`webhook_port`) ownership moves from `culture/agentirc/ircd.py` to `culture/bots/bot_manager.py`.
- `agentirc.skill.{Event, EventType}` import path → `agentirc.protocol.{Event, EventType}`.
- Bumps dep floor to `agentirc-cli>=9.5,<10`. Minor bump 8.8.0 → 8.9.0.

User-visible bot behavior is unchanged: webhook URLs, event filters, fires_event chains, system bots, telemetry counters all behave identically. The change is purely architectural — bots become CAP clients of the IRCd instead of in-process consumers.

Spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md` (Track A2 row).
A3 follow-up: `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a3.md`.

## Test plan

- [ ] Existing bot test suite passes (`bash .claude/skills/run-tests/scripts/test.sh -p -q`).
- [ ] `superpowers:code-reviewer` runs clean against the staged diff.
- [ ] `doc-test-alignment` reports no missing `docs/` or `protocol/extensions/` coverage.
- [ ] Post-merge: verify on the spark host that `culture-agent-spark-culture.service` continues to receive bot events (system welcome bot still greets joins, any user-configured event-triggered bots still fire).

---

## Post-merge (notes for A3 author)

After this PR merges:

- The bundled IRCd in `culture/agentirc/` is bot-free. Only `culture/cli/server.py:_run_server` still imports it.
- A3 can now delete `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill,client,remote_client}.py` and `culture/agentirc/skills/`, and replace `_run_server` with a subprocess shim into the installed `agentirc` binary.
- `culture/agentirc/config.py` (the A1 re-export shim) stays — A3 keeps it through the 9.x line per the spec.
- The federation envelope sniff (spec § "Federation interop during the migration window") is no longer relevant once A3 lands; ship it only if mixed-version peers are expected during the A2-to-A3 window.
