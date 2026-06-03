---
title: Mesh Rearchitecture — CC IS the Boss
created: 2026-06-03
mode: feature
status: draft / awaiting human approval
authors:
  - assistant (synthesis via mesh-rearch-evidence + mesh-rearch-synthesis workflows)
  - human (architectural decisions Q1–Q4 + binding rules)
supersedes_partially: docs/superpowers/specs/2026-04-28-fork-rearchitecture-design.md (D1–D10 still apply where labelled)
references:
  - docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md
  - docs/superpowers/specs/2026-05-28-boss-agent-orchestration-design.md
  - docs/superpowers/specs/2026-05-29-mission-control-dashboard-design.md
---

# Mesh Rearchitecture — CC IS the Boss

> **The CC session you are talking to right now is `local-boss` on the mesh.** No separate boss-daemon brain. Workers, IRC server, and a thin `culture-bridge` process exist around CC; CC owns every decision.

## Mode + Project Conventions Applied

**Mode:** `feature` (substantial new capability; has refactor seams where the boss daemon's SDK loop is removed).

Conventions in scope (from `culture/CLAUDE.md`):

- **All-backends rule.** The plan deliberately violates this for v1 — see "Architectural Decisions" §AD-3. Documented carve-out + follow-up issue required.
- **Cite-don't-import (`packages/` → `clients/<backend>/`).** Updates to `packages/agent-harness/` are required wherever a generic harness component changes.
- **Idempotent migrations.** No SQL here, but `~/.culture/server.yaml` / perm-policy YAML / spool DB schema all need idempotent upgrade paths.
- **Pre-push code review** for shared choke points. Every phase of this plan IS a choke point — review per phase, not per PR.
- **Branch + version bump per phase.** One feature branch per phase; `/version-bump minor` before PR.
- **Run `git status` before branching.** Verify there are no stale staged files between phases.

## Reviewer Context Check

**What culture is.** A mesh of IRC servers (`AgentIRC` — custom async Python IRCd in `culture/agentirc/`) where AI agents collaborate. Each agent runs a Claude Agent SDK client harness (or other backend) that connects to the mesh. Humans participate as first-class members.

**Current state (`main` ≈ v8.19.45).** A `local-boss` daemon (`culture/clients/claude/daemon.py`, ~1660 lines) runs an autonomous Claude Agent SDK loop. The Claude Code session (CC) the user types into is a SEPARATE process that uses `culture boss …` CLI commands to drive the boss daemon. Two Claude brains exist concurrently for the same identity; one is the user-facing session, the other is the autonomous daemon. The user wanted, from day one, ONE entity — the CC session itself.

**Desired end state.**

- CC owns the `local-boss` IRC identity directly (via a thin bridge process — see Architecture).
- A `culture-bridge` daemon holds the IRC connection, audit log, daemon log, IPC socket, and watchdogs that don't need an SDK. No SDK loop, no LLM.
- A CC-side **Claude Code plugin** with a `SessionStart` hook connects to the bridge on launch; exposes `mesh send` / `mesh dm` / `mesh inbox` / `mesh who` / `mesh status` tools; receives inbound DMs and mentions via the bridge's IPC push.
- Workers stay as SDK daemons but are tied to CC's lifecycle (CC starts ⇒ workers run; CC stops ⇒ workers stopped).
- Server-side per-nick **DM spool** (IRCv3 chathistory-like) holds DMs sent while the bridge isn't connected.
- Push everywhere; no agent prompt instructs the SDK agent to poll.

**Why this plan exists.** This session surfaced four real defects that all trace back to the missing CC-IS-boss architecture: (a) `culture channel read '#team'` returns empty for offline-delivered messages (no spool); (b) no DM verb between bosses (only channels); (c) workers auto-join `#team` (confinement leak); (d) every worker wastes 1–2 turns searching for nonexistent `irc_read`/`irc_send` tools (the prompt teaches polling for a push channel). plenty-staging-boss's report adds P0/P1/P2/P3 worker-resilience issues. The user has also added a new perm rule: a boss can grant a worker anything the boss itself has — no separate `grant_ceiling` denylist.

## Binding Architectural Rules (from user, this session)

1. **CC IS the boss.** The CC session is *a* boss identity on the mesh — named after its project / feature / task (e.g., `fork-rearch`, `payment-debug`). No `local-` prefix in single-server mode. Multiple CC sessions on the same host coexist as different bosses, one per concurrent context.
2. **Workers inherit the boss's prefix.** A boss named `fork-rearch` spawns `fork-rearch-qa`, `fork-rearch-migration`, etc. Channel names follow: `#task-fork-rearch-qa`. Cross-project collision is impossible by construction.
3. **Humans are on the mesh as themselves.** A human's nick is their name (e.g., `edo`) — they have no boss role but they ARE a first-class participant for chat purposes. From the dashboard they can DM any agent (their bosses, peer bosses, any worker they have access to). The dashboard is interactive, not just observational.
4. **Offline presence.** When CC closes, that boss nick goes OFFLINE. Server-side DM spool catches DMs sent while offline; spool replays into CC on next launch (push, not poll). [Q1 = offline]
5. **Mid-turn DMs queue.** Inbound peer DM mid-CC-turn: surface end-of-turn as system reminder. [Q2 = queue]
6. **Claude-only for v1.** Codex/Copilot/ACP-boss backends are deferred. [Q3 = (a) claude-only]
7. **Workers follow CC's lifecycle.** Bridge follows CC. NO autonomous worker activity while CC is offline. [Q4 = workers offline when CC offline]
8. **No `grant_ceiling`.** A boss can grant a worker any tool the boss itself has. Runtime is governed by the worker's policy file.
9. **PUSH EVERYWHERE.** No agent prompt should instruct polling. Polling in agent prompts is a leak to fix.
10. **`#team` is removed entirely.** Discovery via `mesh who`; targeted talk via DM; group coordination via opt-in `#joint-<topic>` (cross-boss) or `#team-<project>` (per-boss sibling fireplace). No global EVERYONE channel.

## Architectural Decisions Made During Planning

Seven decisions the synthesis surfaced but binding rules don't directly answer. Each has a default below — call out any override in review.

### AD-1. Perm-request priority overrides Rule 5

A worker blocks on the perm broker for up to `_PERM_DECISION_TIMEOUT_SECONDS = 600s` (`_perm_broker.py:741-776`). If a perm request arrives mid-CC-turn and the turn runs > 600s, the worker auto-denies. That violates the worker contract.

**Decision:** Perm requests **interrupt** the CC turn (jump-to-queue, surface immediately). Non-perm DMs and mentions still queue end-of-turn per Rule 5.

**Rationale:** End-of-turn queuing only works for events where peers can wait minutes. Worker-broker rendezvous cannot.

**Override surface:** if you want strict end-of-turn queuing even for perm requests, the worker-broker timeout must be raised AND a UX path for "your turn caused N auto-denies" must exist.

### AD-2. Project-centric naming convention (bosses are projects; workers are project.role)

**Decision:** A CC session names its boss after the project / feature / task it's focused on. Examples: `fork-rearch`, `payment-debug`, `mesh-design`, `culture-dev`. Default for a session that doesn't pick: derive from the cwd's git remote name or branch — the human can always override with `culture boss init --name <project>` or by typing in CC's first turn.

Workers inherit the boss's prefix: `fork-rearch` spawns `fork-rearch-qa`, `fork-rearch-migration`, etc. Channels follow: `#task-fork-rearch-qa`. The single hyphen separator means tools can split the nick on `-` to recover the namespace. Max length budget: project name ≤ 14 chars + worker suffix ≤ 14 chars + separator = 29 chars (well under IRC's typical 30-char cap).

The legacy `local-` server-prefix is **removed in single-server mode**. In multi-server (federated) setups the server prefix returns as `<server>-<project>` for cross-server disambiguation.

**Rationale:** Names should describe what the boss is for, not where it physically runs. `local-boss` is a default nobody picked. The brief a boss writes for its first worker should self-name (*"You are fork-rearch-qa, working under fork-rearch on …"*) — explicit identity for both sides.

### AD-3. Workers default to `#task-<own>` ONLY; project-team and joint channels are explicit opt-ins

**Decision:** Server-side ACL refuses worker JOIN to any channel except `#task-<own-suffix>` by default. Two ways to widen:

- **`#team-<project>`** — a per-boss group fireplace for THIS boss's workers + boss. Useful when 3+ workers benefit from sibling-awareness. Boss creates it via `mesh team-channel create`; workers opt in at spawn (`culture boss spawn qa --team`) or post-spawn (`mesh invite qa #team-fork-rearch`). Workers in `#team-fork-rearch` see siblings but NOTHING outside the project.
- **`#joint-<topic>`** — cross-boss coordination. Created by mutual invite. Workers can be invited via spawn flag `--joint <#chan>` or `mesh invite`.

The culture vision doc names `#joint-*` channels for cross-team coordination; this AD adds `#team-<project>` as the intra-project analogue.

**Rationale:** Keeps default scope tight; explicit opt-in surfaces collaboration in code review. The boss decides per-project whether sibling-awareness helps or hurts.

### AD-4. `#team` is removed entirely (not just confined)

**Decision:** No global `#team` channel. The previous default that every agent auto-joined is killed. The named replacements (AD-3 plus `mesh who` discovery + `mesh dm` for direct talk) cover every legitimate use without the leak surface.

**Rationale (from user, this session):** Two failure modes that `#team` causes:

- **Token leak.** Every boss in `#team` reads every other boss's chatter, across projects with different secrets, customers, contexts.
- **Unknown spinoffs.** Bosses see something in `#team` they weren't briefed on and silently start acting on it. This is the documented "I went on a tangent" failure mode.

`#team` was a vestige of the older model where humans-as-bosses-of-agents would hang out together. In the new model humans aren't on the mesh as bosses, and each AI boss is project-scoped. There's no use case `#team` serves that `mesh who` + DM + `#joint-*` + `#team-<project>` doesn't cover better.

**Migration note:** Existing `#team` channel histories in SQLite are NOT deleted (history is durable evidence); the channel is simply not auto-joined and no new ACL admits anyone. Effectively frozen.

### AD-5. Dashboard becomes interactive: boss-centered tree view + human chat surface

Today `culture dashboard --port 8787` is a flat localhost web app reading daemon-log + audit + manifest. Under CC-as-boss with project-named bosses:

**Decision:**

- **Tree view by boss.** Top-level rows are bosses (one per project). Each expands to its workers, perm queue, and recent activity. Collapsible per project. Cross-host visibility shows peer bosses as siblings (read-only — peer state is observed via IRC).
- **Human chat surface.** Dashboard exposes a chat panel where the human can DM any agent on the mesh — their own bosses, peer bosses, their own workers, or any human reachable on the mesh. The human's mesh identity is their own name (`edo`), distinct from any boss role. Two paths to talk to your own boss: through CC normally (managed conversation; CC's full turn pipeline), or quick-DM from the dashboard (direct, no CC turn cycle). Quick-DM is for "ping the boss, see what they say" interactions; CC is for full work conversations.
- `mesh status` / `mesh agents` / `mesh pending` / `mesh inbox` exposed as CC plugin tools for in-conversation queries.

**Rationale:** At-a-glance visualization stays valuable; chat surface makes the dashboard the human's communication tool, not just a monitor; CC tools cover programmatic / conversational queries.

### AD-6. Server-side spool via per-nick IRCv3 chathistory store; bridge holds the nick only when CC is online

The synthesis offered a `VirtualClient` pattern (a fake connection that keeps the boss nick "online" while CC is closed). That contradicts Rule 4 (offline). Instead:

**Decision:** Add per-nick offline-message storage inside `agentirc`. When the bridge disconnects (CC closes), the nick goes offline (peers see this via WHO / NICK presence events). The IRCd retains DMs targeted at the nick in a SQLite spool (next to `history_store.py`, same shape). On bridge reconnect, bridge issues `CHATHISTORY` to drain. Schema: `(msg_id PK, sender, recipient, ts_server, payload, tags, delivered_at)`.

**Rationale:** Honors Rule 4 strictly. Reuses the existing `agentirc/history_store.py` SQLite mechanism. Aligns with IRCv3 standards (see Tech Validation §TV-3).

### AD-7. Bridge nick is the project-named boss (legacy `local-boss` is a fallback)

**Decision:** The bridge registers as the project-named boss nick (e.g., `fork-rearch`). The nick is decided at SessionStart from one of (in priority): explicit `culture boss init --name X`, the human's first-turn input ("call this session …"), the cwd's git-remote basename, or as a final fallback the legacy `local-boss`. The manifest entry `boss: <nick>` becomes the project-named nick; `_task_channel_acl` reads the manifest unchanged. Existing setups using `local-boss` continue to work — they just look unhelpful in the dashboard.

**Rationale:** Replaces AD-5's earlier "reuse `local-boss`" pick. The cost of changing the nick is just a manifest rewrite — already in scope because we're adding entries. Existing perm-policy YAMLs that mention `local-boss` get a one-time migration script that aliases to the new name (idempotent, safe to re-run).

---

## Evidence Log

Verified claims pulled from the 222KB evidence + 303KB synthesis outputs (workflow run IDs `w81b9ldku` and `wf5zhjgxd`). Every "today" claim cites file+lines. Confidence: **C** = confirmed (verbatim quote); **I** = inferred (read-through judgment); **G** = gap (needs verification at implementation).

### EL-1. The boss daemon owns eight separable concerns

| Concern | Cited evidence | Lines | Conf |
|---|---|---|---|
| AgentRunner (SDK loop) | `daemon.py` `_start_agent_runner` | 517–571 (function runs to 571; lines 558-571 contain the inline `silent_death_task = asyncio.create_task(self._silent_death_watchdog())` arm — see B-2 from review iteration 1) | C |
| IRC transport | `irc_transport.py` `_send_raw` / `_read_loop` / `_cmd_handlers` | 201–256, 74–85 | C |
| Channel JOIN ACL | `agentirc/client.py` `_task_channel_acl` | 147–199 | C |
| Audit log (per-nick) | `daemon.py:150` ctor, `:996` write call | 150, 996 | C |
| Daemon log | `daemon.py` 14 callsites | 534–538, 975, 990, 1039, 1052, 1063, 1082, 1257, 1339–1343, 1354, 1369, 1647, 1654 | C |
| Watchdog: `_idle_watchdog` outer loop | `daemon.py` | 573–605 | C |
| Watchdog: `_watchdog_tick` (the classifier helper) | `daemon.py` (a co-located helper called by `_idle_watchdog`) | 607–668 | C |
| Watchdog: `_silent_death_watchdog` | `daemon.py` | 670–745 | C |
| Supervisor + crash-restart | `daemon.py` ctor + `_delayed_restart` | 301–313, 161, 1192–1236, 1286–1289 | C |
| Mission persistence | `daemon.py` `_on_mention` (function 871-896; persist_mention call at 891-896) + `_build_system_prompt` (1157-1158 read site) | 871-896, 1157-1158 | C |

### EL-2. IRCTransport callbacks are already push-shaped

> `_read_loop` parses every inbound IRC line and dispatches via `_cmd_handlers`; PRIVMSG handler reaches `_detect_and_fire_mention` which calls `self.on_mention(target, sender, text)`. `daemon.py:272` wires this to `AgentDaemon._on_mention` (`daemon.py:871-896`) which builds a prompt and calls `agent_runner.send_prompt`.

Cites: `irc_transport.py:233-256`, `:370-382`, `daemon.py:272`, `daemon.py:871-896`. Conf: **C**.

### EL-3. Workers auto-join `#team` today (the confinement leak)

> `base_channels = ["#team", _task_channel(suffix)]`

Cite: `culture/cli/boss.py:846-851`. Conf: **C**. Server-side ACL (`agentirc/client.py:164-165`) only gates `#task-*`; `#team` / `#joint-*` / `#system` pass through unconditionally.

### EL-4. v8.19.42 silent-JOIN-drop fix is MISSING from this branch (`feat/helper-boss-permission-broker`)

> `irc_transport.join_channel` updates `self.channels` optimistically BEFORE the server confirms.

Cite: `irc_transport.py:174-183`. The channel-presence ACL on outbound at `daemon.py:1469` then silently drops every subsequent send. The 474 handler that was added in v8.19.42 on `main` is not on this branch. Risk class: **High**. Conf: **C**.

### EL-5. MessageBuffer has no cursor persistence

> `MessageBuffer` is an in-process `deque(maxlen=500)` per channel with no cursor persistence. On bridge restart, cursors reset to 0 and the next `irc_read` re-delivers ~200 HISTORY-replayed messages as 'unread'.

Cite: `culture/clients/claude/irc_transport.py:44, 58` (buffer accepted as ctor arg, assigned to `self.buffer`); `daemon.py:262` (buffer constructed in `AgentDaemon.start()` — Phase 2.7 patches happen HERE, not in `irc_transport.py`); `culture/clients/claude/message_buffer.py:19-71`. Risk class: **Medium**. Conf: **C**.

### EL-6. `#task-*` channels auto-delete on last-member PART

> Non-persistent channels are deleted server-side the moment the last member parts.

Cite: `culture/agentirc/ircd.py:626-629`, `client.py:687-690`. If the bridge is offline and the worker exits, the channel disappears and history is unreachable until a CHANARCHIVE flips it persistent. Conf: **C**.

### EL-7. The perm-broker decision flow runs inside the worker daemon's PreToolUse hook

> `gate()` at `_perm_broker.py:635-664` runs inside the worker daemon's PreToolUse hook (set via `AgentRunner._can_use_tool = self._broker.gate`). It spools `perm-queue/<id>.json`, polls `perm-decisions/<id>.json` at 250ms for up to `_PERM_DECISION_TIMEOUT_SECONDS=600s`.

Cite: `_perm_broker.py:635-664`, `:741-776`, `:36`. Conf: **C**.

### EL-8. `DEFAULT_BOSS_CEILING` and the ceiling stack to be deleted

> Remove `DEFAULT_BOSS_CEILING` (lines 334-352), `_boss_policy_dir` (line 355), `boss_policy_path_for` (line 359), `write_default_boss_ceiling` (line 364), `load_boss_ceiling` (line 373), `is_above_ceiling` (line 387). Also remove the ceiling re-check inside `gate()` at lines 649-658.

Cite: `_perm_broker.py:322-395`, `:649-658`; consumer at `culture/cli/boss.py:32, 37, 371-379, 924`. Conf: **C**.

### EL-9. The SDK system prompt instructs the agent to poll IRC

> Four daemon backends literally write into the SDK system prompt: "Check IRC channels periodically with irc_read() for new messages."

Cites: `culture/clients/claude/daemon.py:1145-1155`, `copilot/daemon.py:552`, `codex/daemon.py:570`, `acp/daemon.py:577`. The named **P2** leak from plenty-staging-boss's report. Conf: **C**.

### EL-10. `_poll_loop` injects synthetic `[IRC Channel Poll]` prompts — three co-deletable methods

Range `daemon.py:451-498` spans THREE methods that must all be deleted together:
- `_poll_loop` (451-463) — the outer sleep loop
- `_process_poll_cycle` (465-469) — per-channel dispatcher
- `_send_channel_poll` (471-498) — the actual prompt synthesizer

Also: `daemon.py:323` creates `self._poll_task = asyncio.create_task(self._poll_loop())` — that line must be deleted with the methods. Phase 2.2 task must list all four sites explicitly.

Becomes obsolete once mentions/joins/ROOMINVITE are push-only via `on_mention`/`on_roominvite`. Conf: **C**.

### EL-11. `_silent_death_watchdog` is filesystem-only (no SDK dependency)

> For each owned worker in the manifest, reads PID file, checks process aliveness, reads worker daemon-log's last action, surfaces `idle_warning reason=silent_death_after_…`.

Cite: `daemon.py:670-744`. Perfect home in the persistent bridge process. Conf: **C**.

### EL-12. Today's IPC dispatch in the daemon — 19 verbs (NOT 8 as previously stated)

`daemon.py:226-246 _ipc_dispatch` is a 19-entry table. Full enumeration with rearch disposition:

| Verb | Disposition under bridge model |
|---|---|
| `irc_send` | KEEP — bridge IPC verb (CC plugin's `mesh send` calls it) |
| `irc_read` | KEEP — bridge IPC verb (drains MessageBuffer; CC plugin reads via IPC) |
| `irc_join` | KEEP |
| `irc_part` | KEEP |
| `irc_channels` | KEEP |
| `irc_who` | KEEP |
| `irc_topic` | KEEP |
| `irc_ask` | KEEP — webhook-fired question pattern |
| `irc_thread_create` | KEEP — thread API survives; bridge owns thread state |
| `irc_thread_reply` | KEEP |
| `irc_threads` | KEEP |
| `irc_thread_close` | KEEP |
| `irc_thread_read` | KEEP |
| `compact` | REPLACE — was a CC-side trigger for the boss daemon's SDK; under CC-as-boss, CC does its own `/compact` natively. Bridge IPC verb `compact` is repurposed as `daemon_log_record(action='compact')` so bridge writes the daemon-log entry on CC's behalf. |
| `clear` | DROP — boss daemon no longer has an SDK conversation to clear. Workers keep this in their own daemons. |
| `status` | KEEP — bridge returns `{cc_connected, channels, queued_dms, ...}`. Field shape changes (drops `circuit_open` from boss; adds `cc_connected`); verb name preserved. |
| `pause` | DROP for bridge — no SDK loop to pause. Workers keep `pause` in their own daemon. CC has its own sleep/resume via plugin tools (separate from IRC presence). |
| `resume` | DROP for bridge — symmetric with `pause`. |
| `shutdown` | KEEP — bridge IPC verb that triggers `bridge stop()` (graceful PART, CHANARCHIVE, audit flush, socket close). Used by `culture agent stop <nick>` CLI. |

Cite: `daemon.py:226-246`. Conf: **C**. The IPC abstraction IS already the right boundary; the bridge implements 16 verbs (the 13 IRC/thread verbs + status + shutdown + compact-as-log-record) and drops 3 (clear, pause, resume).

### EL-13. Mission persistence is 32 KiB rolling window with stable wrapper

> 32 KiB rolling window with rotation marker; stable wrapper format for SDK prompt-cache hits.

Cite: `_mission.py`, plus `daemon.py:891-896` (write site), `daemon.py:1157-1158` (read site). Commit `b221496` v8.19.3. Conf: **I** (commit-message paraphrase).

### EL-14. Audit captures full tool I/O + thinking blocks since v8.18.0

Cite: commit `0cfa744` v8.18.0, schema in `culture/telemetry/audit.py:107-119`. The bridge MUST preserve this byte-for-byte. Conf: **C**.

### EL-15. Server-side DM spool does not exist

> Today `client.py:1062-1096` (`_send_to_client`) returns False when `get_client(target)` is None and the caller sends `ERR_NOSUCHNICK`. No spooling. Channel history is persisted in SQLite (`history_store.py:14-91`) but `skills/history.py:81` deliberately skips DMs.

Cite: above paths. Conf: **C**.

### EL-16. Bridge IRC nick is the ACL trust anchor

The boss nick `_task_channel_acl` admits MUST be the bridge's IRC nick — manifest entries written via `culture boss spawn` carry `boss: <nick>` and `_load_owner_map` returns those entries (`agentirc/client.py:56-129`). Per AD-5, bridge reuses `local-boss`; no change. Conf: **C**.

### EL-17. claude-agent-sdk current stable is 0.2.88, no breaking changes since 0.2.87

Cite: https://pypi.org/project/claude-agent-sdk/ accessed 2026-06-03 (tech-val); changelog notes one substantive change (trio-compat for `session_store=`). Conf: **C** (web source).

---

## Unverified Assumptions (verify before / during named phase)

| # | Assumption | Verification step | Bound by phase |
|---|---|---|---|
| UA-1 | Claude Code's SessionStart hook can emit `hookSpecificOutput.additionalContext` as the silent-injection path that surfaces spool entries | Read `~/.claude/plugins/<plugin>/SessionStart.md` example OR run a no-op hook locally and inspect stdin/stdout JSON shape | Phase 4 |
| UA-2 | Mid-conversation `<system-reminder>` injection from a plugin tool is supported when CC is in the middle of a tool-call cycle (not just between turns) | Spike: write a tiny test plugin that injects a reminder while a long-running Bash is mid-call; observe whether CC sees it on the next assistant turn | Phase 5 (interrupts AD-1) |
| UA-3 | IRCv3 `chathistory` semantics work cleanly when implemented as a SQLite-backed per-nick spool (vs `agentirc/history_store.py`'s per-channel design) | Prototype a 30-line shim in `agentirc/` that calls into `history_store` for channels and a new `dm_spool` for DMs; run integration test with bridge offline → DM sent → bridge reconnect → drain | Phase 3 |
| UA-4 | `_append_sticky_rule` can be tightened (refuse `tool: Bash` without `input_regex`) without breaking existing perm-policy YAMLs in the wild | Grep `~/.culture/perm-policy/*.yaml` across the user's environment; report any bare-Bash sticky rules; confirm the v8.19.39 audit-policies remediation already removed them | Phase 5, before EL-8 deletion |
| UA-5 | The `culture-bridge` process starting with `skip_claude=True` (no SDK loop) cleanly inherits the existing `daemon.py` startup sequence | Code-walk `daemon.py:252-325` (`start()` body — note `start()` begins at 252, last line is 325; lines 236-246 are the tail of `__init__`, lines 327+ begin `stop()`); identify every branch that hard-requires `_agent_runner` (none should — `skip_claude` is already a config flag per `daemon.py:316`) | Phase 2 |

---

## Risk Class

### RC-1. `culture/clients/claude/daemon.py` — 15 risk-class commits in 30

History: startup crashes (`a58f065` — `_context_watch`/`boss` not on runtime config), CRLF injection sinks (`317a591`), worker silent-death watchdog added (`7523dc7`), socket symlink for CLI reachability (`33a3ea6`), model-inheritance latch+orphan (`6e01547`).

**Implication for plan:** The bridge split must verify (1) broker-via-PreToolUse hook survives the split (v8.18.1 lesson — broker was a no-op for a full release); (2) daemon-log remains sole authority for idle detection (v8.17.2 commit `955110d`); (3) `_last_activation` fires on every task-dispatch path; (4) handoff regex stays anchored (`^…$`); (5) ownership derives from manifest only; (6) bridge `stop()` cancels all background tasks.

**Mandatory phase-tied check:** Phase 2 acceptance gate INCLUDES a test that boots the bridge with `skip_claude=True`, spawns a worker, and asserts (a) the worker's `AgentRunner` PreToolUse hook is installed, (b) the worker's daemon-log records `agent_start`, (c) the bridge's daemon-log does NOT record `agent_start` (CC's session does).

### RC-2. `culture/clients/claude/agent_runner.py` — 8 risk-class commits

Canonical lesson: `14983ea` v8.18.1 — `permission_mode='default'` is REQUIRED when `can_use_tool` is wired; an earlier `bypassPermissions` shape silently disabled the entire broker in production. Followed by `9705be5` v8.18.2.

**Implication for plan:** When the bridge takes over the broker-approve path (Phase 5), keep a parity integration test that wires the broker via PreToolUse and asserts a denied tool actually fails the worker's tool call — not just that `gate()` was called. This is the test v8.18.1 lacked.

### RC-3. `culture/clients/claude/irc_transport.py` — 9 risk-class commits (a CLAUDE.md pre-push-review choke point)

Recent classes: CRLF injection block (`317a591`), self-message filter in HISTORY replay (`458f534` — closed PR #416 blocker), multi-line truncation (`25c15b8`), sync-handler-must-be-sync (`c63ff06`), CancelledError handling.

**Implication for plan:** **Port v8.19.42** (the 474 handler + server-confirmed JOIN) into the bridge's transport on day one of Phase 2. Add a regression test that simulates `ERR_BANNEDFROMCHAN` and asserts the channel is removed from `self.channels`.

### RC-4. `culture/clients/_perm_broker.py` — 5 risk-class commits, all security-class

`dc81ead` — request-id and worker-name path traversal closed. `3a9e687` — write_decision placeholder cleanup + spawn name validation. `0cfa744` v8.18.0 — ceiling re-checked on every policy-allow (closed a sticky-rule bypass: an "always allow ANY Bash" rule could whitelist every Bash).

**Implication for plan:** EL-8 deletion is a **semantic** change in the security model — the second-layer denylist defense is gone. Sequencing matters. Phase 5 tightens `_append_sticky_rule` FIRST (refuse bare-tool sticky rules) BEFORE the ceiling delete; document under "Security trade-offs accepted."

### RC-5. `culture/agentirc/client.py` + `ircd.py` — 6 risk-class commits in the ACL hot zone

`3242db9` v8.19.44 — mtime-keyed `owner_map` cache eliminated the 5s-TTL race window. `8fab7ef` v8.18.7 — task-channel ACL enforcement.

**Implication for plan:** Bridge IRC nick MUST equal the manifest's `boss:` field (AD-5: reuse `local-boss`). Phase 2 startup invariant: bridge refuses to start if the manifest has zero entries with `boss: <bridge-nick>` OR if any `boss:` value disagrees across entries.

### RC-6. `culture/clients/_audit.py` — light churn, single risk-class touch

Commit `0cfa744` v8.18.0 expanded audit to FULL tool input + result + thinking blocks. Audit is the evidence channel the data-state debugging rule depends on.

**Implication:** Phase 4 acceptance gate INCLUDES a schema-fixture test that asserts a CC → bridge IPC `sdk_event` produces an audit JSONL whose fields match the v8.18.0+ schema byte-for-byte (action, tool input full, tool result full, thinking blocks present).

### RC-7. `culture/cli/boss.py` — 10 risk-class commits in 30 (review iter-1 C-MISS-1 from agent 4)

History: path-traversal defenses (`_require_worker_suffix`, `_require_server`), ceiling bypass closures, manifest-flood UX, brief-error coverage, spawn-vs-init parity. The plan modifies `boss.py` across Phase 0.3, Phase 1.1 (line 846 + 947), Phase 4.8 (worker auto-prefix), and Phase 5.2 (ceiling deletion at lines 32, 37, 371-379, 924).

**Implication for plan:** (a) `_require_worker_suffix` / `_require_server` are second-line path-traversal defenses that MUST be preserved through Phase 4.8 worker-naming changes — they validate the resulting nick. (b) The Phase 5.2 deletion exhaustiveness check is `git grep -E 'BOSS_CEILING\|boss_policy\|is_above_ceiling\|write_default_boss_ceiling\|load_boss_ceiling' culture/ tests/` returns ZERO hits (already in Phase 5.2 acceptance). (c) `_MANAGER_PROMPT` at lines 48-72 teaches the ceiling concept and must be re-authored to the new Rule 8 model. (d) Spawn-vs-init parity (the `_write_boss_yaml` site at 947) must stay aligned with the spawn helper.

### RC-8. `culture/dashboard/server.py` + `culture/dashboard/static/app.js` — 10+5 risk-class commits (review iter-1 C-MISS-2/3 from agent 4)

History: 8 agentirc+dashboard security findings closed in v8.18.3-B; Qodo perf + channel-routing in PR #28; idle-signal fixed-tail coupling closed in v8.17.3; peek-welcome spam + stale task channels in v8.19.10; scroll preservation in v8.19.14/15; card-click default in v8.19.21.

**Implication for Phase 7.5:** (a) Any new `_is_idle` resolution path reads the FULL bridge daemon-log, not a fixed tail (v8.17.3 lesson). (b) Human-chat panel routes via the bridge IPC (existing nick-validation regex `^[A-Za-z0-9][A-Za-z0-9_-]*$` from `bfcb34b` v8.9.0 reused, not re-implemented). (c) `app.js` tree-view rendering preserves the append-only refresh pattern (scroll preservation lesson v8.19.14/15). (d) SSE replacement of the 3 polling intervals MUST NOT regress peek-welcome suppression (v8.19.10 closed channel-routing leaks during dashboard polls).

### RC-10. `culture/clients/claude/message_buffer.py` — 1 risk-class commit (review iter-2 C-1 from agent 4)

Commit `f75d1a8` (PR #198, "Fix IRC skill issues #191-197") touches cross-backend send-time buffering + mention warning propagation. The send-time-buffering invariant (own-messages buffered before `_on_privmsg` echo filter) must be preserved through Phase 2.7's cursor-persistence work.

**Implication for plan:** Phase 2.7 acceptance gate INCLUDES a test that posts an own-message via `irc_send`, expects it to appear in the buffer BEFORE the server echoes back (per the PR #198 invariant), and confirms cursor persistence preserves that ordering across bridge restart.

### RC-9. Codex / Copilot / ACP daemons — 11 risk-class commits EACH (review iter-1 C-MISS-4 from agent 4)

This is the heaviest history in the file set, four-way symmetric: poll-loop race conditions (#117), `_mention_targets` cleanup race (#118), CRLF injection (`317a591`), socket-symlink-for-CLI (v8.18.7), sync-handler-must-be-sync fix (#122). The plan touches all four backends in Phase 1.3 (system-prompt strip).

**Implication for Phase 1.3:** (a) Restrict the strip to the system-prompt string literal — do NOT regress any of the 11 fixed bugs in each backend's surrounding code. (b) Add a per-backend regression test that boots the daemon and asserts no crash + system-prompt string contains the new wording. (c) The strip MUST be applied to four separate files (`claude/daemon.py:1145-1155`, `copilot/daemon.py:552`, `codex/daemon.py:570`, `acp/daemon.py:577`) — no shared utility extraction in this PR (would unify code that the project deliberately separates per cite-don't-import rule).

---

## Code Alignment Analysis (per skill's rules 1–4)

### 1. Parallel-implementation audit

The plan introduces THREE places that write daemon-log records: the existing worker daemon (workers' own `agent_start` etc.), the new bridge (`silent_death_after_…`, `idle_warning`, etc., relayed from CC IPC events), and the CC plugin (emits IPC events for `session_start` → bridge writes `agent_start`).

**Shared helper:** `_daemon_log.py` is already that helper. No extraction needed. The bridge and worker daemon each `await self._daemon_log.record(action, **kwargs)`. CC plugin sends a structured IPC message; bridge translates to `_daemon_log.record(...)`. The string `"agent_start"` is the load-bearing literal (see Load-Bearing Literals table).

### 2. Seed-data contract

Manifest (`~/.culture/server.yaml`) under the new model:
- Bridge entry: `nick: local-boss`, `tags: [boss]`, `model: <CC's resolved model>` ← key change from today's autonomous boss daemon: model is whatever CC's session uses at SessionStart. Phase 4 emits an IPC event `set_runtime_model(model)` on first AssistantMessage that the bridge persists into the manifest.
- Worker entries: unchanged. `boss: local-boss` field continues to point at the bridge nick.

`SELECT DISTINCT` equivalents (config introspection):
- `grep "tags:" ~/.culture/server.yaml | sort -u` — should contain only `[boss]`, `[]`, and worker-shaped tags.
- `grep "model:" ~/.culture/server.yaml | sort -u` — bridge entry's model MUST agree with CC's resolved model after first turn.

### 3. Constraint-defense

The perm broker's structural integrity constraints (defended in code, not at the DB layer because there's no DB):

| Constraint | Defense | Caller |
|---|---|---|
| `perm-decisions/<id>.json` exists at most once per request_id | `O_CREAT | O_EXCL` in `write_decision` (`_perm_broker.py:526-574`) | bridge IPC handler (was: boss daemon CLI handler) |
| `request_id` matches regex (no path traversal) | `valid_request_id` (`_perm_broker.py:418-425`) | bridge inotify watcher (was: boss daemon `_on_perm_request`) |
| Sticky `--always` rules carry an `input_regex` when tool is Bash/Edit/Write/mcp__.* | NEW guard in `_append_sticky_rule` (Phase 5) | CC plugin `approve_perm` tool |
| Worker policy file is the upper bound at runtime | `gate()` reads worker YAML, deny-over-allow precedence | worker daemon PreToolUse hook (unchanged) |

### 4. Latent behavior drift

Today's `_idle_watchdog` (`daemon.py:573-668`) detects three classes on the boss-side (the boss daemon watching its own SDK loop). Under the rearch, the boss has no SDK loop — but the wachdog also watches workers (it activates on `'boss' in self.agent.tags`). The bridge inherits the WATCHER role for workers; the SELF-WATCHING role is discarded (CC sees its own turns natively).

**Asymmetry to document:** workers' stalled_in_retry_loop / stalled_in_failed_retry watchdogs STAY in the worker daemon (driven by the worker's own `_last_assistant_message_at`). The bridge does NOT mirror these; it only relays the worker → boss DM the worker emits when its watchdog fires.

---

## Behavior-Tracing Matrix

### Pattern 1 — Writer/reader divergence

| State key | Canonical writer (NEW) | Readers (NEW) | Shape-agreement risk |
|---|---|---|---|
| DM spool | `agentirc` server-side spool (new); writer = the IRCd's PRIVMSG handler when recipient is offline | Bridge `CHATHISTORY` drain on reconnect; CC plugin SessionStart drain | Must persist `tags` (trace-context CAP) — else trace continuity breaks across CC restarts |
| Audit log (per-nick) | Bridge (was: `AgentDaemon._audit.write`) | Dashboard `/api/audit`, forensic jq | IPC `sdk_event` schema MUST carry `session_id` + `turn_id` so the audit reader can discriminate CC events from worker events |
| Daemon log (boss records) | Bridge (was: `AgentDaemon._daemon_log.record`); writes triggered by CC IPC events | Dashboard Activity tab; `_silent_death_watchdog` | `model_resolved` MUST be emitted by CC's first AssistantMessage IPC — otherwise `_boss_inherits` regresses to SDK CLI default (v8.18.6 lesson) |
| Daemon log (worker records) | Worker daemon (unchanged) | Dashboard; bridge's `_silent_death_watchdog` reads worker log tail | No shape change |
| Perm-policy YAML | (a) seed at spawn; (b) `_append_sticky_rule` in worker broker (unchanged path) | `gate()` on every PreToolUse | Rule schema unchanged; tightening of `_append_sticky_rule` is Phase 5 |
| `owner_map` (server.yaml) | `culture boss spawn` / `culture agent register` via bridge IPC | (a) IRCd `_load_owner_map` for ACL; (b) bridge `_rejoin_owned_task_channels` | mtime-keyed cache (v8.19.44) preserved — bridge writes manifest with normal write to bump mtime |

### Pattern 5 — Multi-callsite enumeration

**`AgentRunner` constructor (`daemon.py:518-557`):**
- `daemon.py:317` from `start()` — NORMAL — REMOVED in bridge (`skip_claude=True`); stays in worker daemon.
- `daemon.py:1288` from `_delayed_restart` — RECOVERY — REMOVED in bridge.
- `tests/test_agent_runner.py` test mocks — TEST-MOCKS — keep in worker tests; remove from bridge tests.
- 3 supervisor wiring sites — REMOVED from bridge.

**`IRCTransport.on_mention` (`irc_transport.py:370-382`, wired at `daemon.py:273`):**
- 1 NORMAL callsite (every PRIVMSG + DM landing).
- 0 RECOVERY.
- ~6 TEST-MOCKS in `tests/test_mentions.py` + `test_mention_alias.py` + `test_worker_boss_notice.py`.
- Bridge change: callback body changes from `agent_runner.send_prompt(prompt)` to `_spool_inbound(target, sender, text) + ipc_push("inbound_mention", payload)`.

**`PermissionBroker.gate()` (`_perm_broker.py:635-664`):**
- 1 DIRECT NORMAL callsite (the PreToolUse hook in `agent_runner.py:_broker_pre_tool_use_hook`).
- Signature unchanged; the ceiling re-check at lines 649–658 is REMOVED (EL-8 + AD-1 + Rule 6).

### Pattern 9 — Cross-layer source-of-truth

| Concept | Authoritative layer | Fallback |
|---|---|---|
| Boss identity | IRC nick of the bridge (`local-boss`) | none — single source |
| Worker state (engagement, stall, failure count) | Worker AgentDaemon process (in-memory) | Worker daemon-log on disk (for dashboard/`_silent_death_watchdog` post-mortem) |
| Boss/CC state | CC session itself (in-conversation, natively tracked) | Bridge daemon-log mirrors via IPC for cross-restart inspection |
| Perm policy | `~/.culture/perm-policy/<worker-nick>.yaml` on disk | None (no policy file = `can_use_tool=None` = SDK bypassPermissions, different MODE not fallback) |
| Channel ACL | `agentirc/client.py:_task_channel_acl` (server-side) | Outbound ACL guard in `daemon.py:1469` is a client-side convenience, NOT authoritative |
| `#team` / `#joint-*` / `#system` ACL | **NEW** — Phase 1 adds server-side worker-class refusal | Today: unrestricted (the leak) |

### Pattern 11 — Spec-vs-implementation alignment (binding-rule enforcement)

For each binding rule, name the enforcing plan task.

| Rule | Enforcing task(s) | Status |
|---|---|---|
| R1 — CC IS the boss | T2.1 (bridge skeleton, `skip_claude=True`); T4.1 (CC plugin SessionStart) | Covered |
| R2 — boss offline when CC closed | T3.2 (per-nick spool with offline drain on `CHATHISTORY`) | Covered |
| R3 — mid-turn DMs queue (except perm — AD-1) | T4.3 (CC plugin queues inbound to `mesh inbox` until end-of-turn surface); T5.3 (perm requests bypass queue) | Covered |
| R4 — Claude-only for v1 | T7.1 (CHANGELOG carve-out note); no task in `culture/clients/codex/...` | Covered |
| R5 — workers follow CC lifecycle | T4.2 (SessionStart starts owned workers); T4.4 (SessionEnd stops owned workers) | Covered |
| R6 — no `grant_ceiling` | T5.1 (tighten `_append_sticky_rule`); T5.2 (delete ceiling stack) | Covered |
| R7 — push everywhere | T1.2 (strip polling language from system prompts); T2.3 (delete `_poll_loop`); T6.1 (perm decision push via IPC, replace 250ms file poll) | Covered |

### Pattern 13 — Load-bearing string literals

| Literal | Producer (today) | Consumer | Status under rearch |
|---|---|---|---|
| `PRIVMSG` | `irc_transport.py:148` | `agentirc/client.py:_handle_privmsg` (routed via `_dispatch` at line 401 — the `_cmd_handlers` symbol does NOT exist in `agentirc/client.py`; review iter-1 B-1 from agent 6) | preserve in bridge transport |
| `JOIN` | `irc_transport.py` `join_channel` | `client.py:_handle_join` | preserve + add 474 handler |
| `PART` | `irc_transport.py` `send_part` | `client.py:_handle_part` | preserve; bridge MUST `CHANARCHIVE #task-*` before parting on shutdown (EL-6) |
| `ROOMINVITE` | IRCd → bridge | `irc_transport.py:79` → `daemon.py:919-967` | preserve; in bridge, spool the invite, never silent auto-join |
| `NOTICE` | IRCd system-server | `irc_transport.py:78` → `_on_notice` | preserve filter; do not relay mesh events to CC unless opted in |
| `404 ERR_CANNOTSENDTOCHAN` | `agentirc/client.py:1135` | NOT HANDLED in bridge today | **NET-NEW handler** required in bridge (Phase 2) |
| `474 ERR_BANNEDFROMCHAN` | `agentirc/client.py:600` | NOT HANDLED in bridge today | **NET-NEW handler** + remove channel from `self.channels` on receipt (Phase 2 — this is the v8.19.42 carry-forward) |
| `#task-` | `agentirc/client.py:53` `_TASK_PREFIX` | ACL, federation block, dashboard, CLI spawn | preserve — shared constant |
| `#joint-` | (today: not constant) | nowhere gated | promote to constant; ACL refuses worker JOIN unless `culture.yaml` explicitly lists |
| `#team` | `boss.py:846` spawn default | unrestricted | **Phase 1: remove from spawn default + server-side ACL refuses worker JOIN** |
| `mesh send` / `mesh dm` / `mesh inbox` / `mesh who` / `mesh status` | NEW in CC plugin | bridge IPC dispatch | NET-NEW; document in plugin tool registry |
| `auto_allow` / `auto_deny` / `require_approval` | `_perm_broker.py:54-61` | `gate()` matcher | preserve byte-exact |
| `agent_start` / `agent_stop` / `agent_exit` / `idle_warning` / `supervisor_escalation` / `compact` / `model_resolved` | various | dashboard, silent-death scanner | preserve byte-exact; writers shift from boss daemon to bridge for boss records (worker records unchanged) |
| IPC: `irc_send` / `irc_read` / `irc_join` / `irc_part` / `irc_channels` / `irc_who` / `irc_topic` / `irc_ask` | CC plugin (was: skill) | bridge IPC dispatch (`daemon.py:226-246`) | preserve byte-exact |
| IPC NEW: `inbound_dm` / `inbound_mention` / `inbound_roominvite` / `perm_request` / `perm_decision` / `send_ack` / `cc_session_start` / `cc_session_end` / `set_runtime_model` | NEW in bridge + CC plugin | NEW | first-use definitions in Phase 2/4 IPC protocol doc |

---

## Technology Validation

Web searches run 2026-06-03 against the canonical docs. Findings adopted:

### TV-1. Claude Code SessionStart hook

Fires on startup, resume, clear, and compact. Since CC 2.1.0+ the hook's stdout JSON carries `hookSpecificOutput.additionalContext` for silent context injection (no visible message). Best practices: keep hooks idempotent across SessionStart firings, short timeouts (<5s for blocking), use `$CLAUDE_ENV_FILE` for per-session env.

**Decision:** Ship the bridge as a Claude Code plugin with a SessionStart hook that returns `hookSpecificOutput.additionalContext` containing (a) mesh roster snapshot and (b) spooled offline messages addressed to the bridge nick. Idempotent under all four firings.

Source: https://code.claude.com/docs/en/hooks-guide ; https://platform.claude.com/docs/en/agent-sdk/hooks

### TV-2. Mid-conversation injection — `<system-reminder>` is the supported mechanism

Two channels: queued-message path for routine "teammate spoke" nudges; `<system-reminder>` reserved for must-act protocol events. Hard cap on reminder volume per turn (token-cost guardrail; CC issues #4464, #17601).

**Decision:** Routine DMs/mentions queue and surface as system reminders at end-of-turn (Rule 3). Perm requests use `<system-reminder>` mid-turn (AD-1).

Source: https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages

### TV-3. IRCv3 `draft/chathistory` is the right primitive (NOT XMPP MAM)

IRCv3 `chathistory` with `chathistory` batch type, `message-ids`, `server-time`, `message-tags`, `batch` CAPs is the standardized IRC mechanism. Servers advertise `CHATHISTORY=<max>` in ISUPPORT.

**Important nuance (review iter-1 ext-concern-2 from agent 10):** the chathistory spec is currently DRAFT, not ratified — the IRCv3 site explicitly warns of possible major incompatible changes. Use the `draft/chathistory` CAP name (with the prefix) until ratification. Add a TODO to drop the prefix when the spec is finalized.

**Decision:** Implement per-nick spool with `msgid` + `server-time` tags. Advertise `draft/chathistory` CAP + `CHATHISTORY=<N>` ISUPPORT. Replay on `CHATHISTORY` request after bridge reconnect. Regression test: CAP string includes the `draft/` prefix.

Source: https://ircv3.net/specs/extensions/chathistory ; https://ircv3.net/specs/extensions/message-ids

### TV-4. asyncio Unix-domain-socket pattern

`asyncio.start_unix_server()` / `open_unix_connection()` cover the transport. Established patterns: exponential backoff with jitter on reconnect (prevents thundering herd); periodic heartbeats (ping/pong) to detect dead peers; idempotent client recovery on partial state.

**Decision:** Bridge daemon = `asyncio.start_unix_server` at fixed path under `$XDG_RUNTIME_DIR/culture/<nick>.sock`. CC plugin = `open_unix_connection` with backoff+jitter retry. Heartbeats every 15s, dead-peer detection at 60s. Idempotent recovery: every IPC message carries a sequence number; receiver dedups.

Source: https://discuss.python.org/t/how-open-a-unix-domain-socket-with-asyncio/55399

### TV-5. `claude-agent-sdk` pin

Current stable is 0.2.88 (2026-05-31). No public-API breaking changes since 0.2.87. Bundled CLI bumped 2.1.150 → 2.1.161.

**Decision:** Pin `claude-agent-sdk==0.2.88`. No migration shims needed. Watch GitHub issue #1012 (PyPI lag — main routinely ahead of latest tagged release).

Source: https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md

---

## Security Considerations

### Credential Surface

Three durable persistence channels + one in-process IPC:

| Surface | At-rest location | In-motion path | Sensitive content |
|---|---|---|---|
| Bridge IPC socket | `$XDG_RUNTIME_DIR/culture/<nick>.sock` (symlink `~/.culture/run/<nick>.sock`) | Unix datagram, same-user-only | Outbound IRC payloads, perm-grant decisions quoting tool inputs, Bash command strings, MCP token-bearing tool inputs |
| DM spool DB | `~/.culture/<server>.history.db` (SQLite, alongside channel history) | server-side disk | Plaintext DM bodies, perm-notification text (which quotes tool inputs per v8.18.0 audit expansion) |
| Audit log | `~/.culture/audit/<nick>-YYYY-MM-DD.jsonl` | append-only writes | FULL tool I/O + thinking blocks (v8.18.0) |
| Daemon log | `~/.culture/daemon-log/<nick>.jsonl` | append-only writes | Event metadata (no tool I/O); but `idle_warning` text may quote tool name |

**Required filesystem hardening:** all directories 0o700; all files 0o600. Audit dir is already 0o700 (`telemetry/audit.py:115`). Bridge socket bind: `os.fchmod(fd, 0o600)` after bind. Multi-user shared boxes are out of scope for v1 — document.

### Threat Table

| ID | Threat | Severity | Mitigation | Phase |
|---|---|---|---|---|
| T1 | Peer-boss spoofing via manifest entry (with no `grant_ceiling`, spoofed boss grants ANY tool) | **HIGH** | Bridge starts with project-named nick per AD-7. Startup invariant: refuse to start if a worker's `boss:` field points at the bridge's nick AND another manifest entry tagged `boss` claims that same nick (i.e., two CC sessions trying to use the same boss identity). Multi-CC per host is supported when each session uses a DISTINCT project-named nick (NT-16). | Phase 2 |
| T2 | DM-spool tampering on multi-tenant boxes | **HIGH** | Spool dir/files 0o700/0o600. Document multi-user shared boxes as v1 out-of-scope. | Phase 3 |
| T3 | Perm-broker bypass via `_append_sticky_rule` (write `--always allow` for Bash with no input_regex) | **CRITICAL** | **Sequencing**: tighten `_append_sticky_rule` to refuse bare `tool: Bash`/`Edit`/`Write`/`mcp__.*` (Phase 5.1) BEFORE deleting `DEFAULT_BOSS_CEILING` (Phase 5.2). Add test that bare sticky rule raises `BareStickyApproveRefusedError`. | Phase 5 — strict order |
| T4 | Worker escape from `#task-*` (today `#team` and other channels are unrestricted) | **HIGH** | Add `_worker_class_acl` predicate in `client.py` that refuses worker nicks JOIN to `#team`/`#system`/`#boss`. Remove `#team` from `boss.py:846` spawn default. Update `tests/test_task_channel_acl.py`. | Phase 1 |
| T5 | Bridge IPC socket auth bypass | **HIGH** | Socket bound in `$XDG_RUNTIME_DIR/culture/`, `fchmod 0o600` after bind. Accept-loop calls `SO_PEERCRED`/`getpeereid()` and refuses non-self uid. | Phase 2 |
| T6 | Replay attack: stale DM spool entries re-delivered (CC sees flood of expired perm requests as actionable) | MEDIUM | Spool entries carry `_now_iso` server-time. CC plugin checks `perm-decisions/<id>.json` for each perm entry; surfaces settled entries as read-only with `[ALREADY DECIDED]` tag. Bridge purges perm-spool entries past `_PERM_DECISION_TIMEOUT_SECONDS` on startup. | Phase 5 |
| T7 | Path traversal via spawned worker nick (CC passes `../foo` as nick → bridge writes outside expected dir) | MEDIUM | Bridge validates nick on every IPC entry using shared regex (`^[A-Za-z0-9][A-Za-z0-9_-]*$`, reused from dashboard `bfcb34b`). Fail-closed. Unit test: `audit_path_for('../etc/passwd')` raises. | Phase 2 |
| T8 | Worker bypass via hypothetical bridge `inject_prompt` IPC (would skip ACL + worker's `_on_mention`) | **HIGH** | **Decision**: do NOT add `inject_prompt` IPC. All CC→worker prompting goes through IRC PRIVMSG (which goes through ACL). | Phase 4 — explicit non-feature |
| T9 | Broker silent disablement during bridge split (the v8.18.1 lesson — PreToolUse hook accidentally dropped) | **CRITICAL** | Block bridge merge until: (a) existing `test_make_options_wires_pretooluse_hook_when_broker_present` remains green; (b) NEW integration test boots worker with policy, fires a real tool, asserts the tool actually fails on deny (not just that `gate()` was called). | Phase 2 acceptance gate |
| T10 | OTEL traceparent leakage via raw IRC tag-bearing line storage | LOW | Spool stores STRUCTURED `{action, target, sender, text, ts}` not raw IRC lines. Traceparent regenerated on replay. | Phase 3 |
| T11 | Supply-chain regression in bundled CLI re-introduces auth-helper OS-command-injection (CVE-2026-35021, CVE-2026-35022 — both CVSS 9.8 in `apiKeyHelper` / `awsAuthRefresh` / `awsCredentialExport` / `gcpAuthRefresh` / prompt-editor invocation; patched in CLI 2.1.150+) | **HIGH** | Pin `claude-agent-sdk==0.2.88` (CLI 2.1.161 bundled). Phase 0.5 acceptance gate includes `pip show claude-agent-sdk` confirming the pinned version. Plugin install refuses to honor `apiKeyHelper`-style config from `culture.yaml` (only user `~/.claude/settings.json` allowed). Add a `~/.culture/version-checks/sdk_cli_version.json` snapshot during `culture boss init`. **Version-drift check at every SessionStart** (review iter-2 C-3 from agent 10 — the 0.2.x series ships multiple releases per week; periodic pip update can silently downgrade): bridge compares installed `claude-agent-sdk` version against the snapshot on every connect; emits a daemon-log warning + a system reminder to CC if a drift is detected (especially a downgrade or a major version jump). | Phase 0, Phase 4 |

### Fail-Closed Paths

| Path | How it fails closed | Confirmation |
|---|---|---|
| `#task-*` ACL | `_task_channel_acl` ends with `return False` (`client.py:147-199`); mtime-keyed cache prevents stale-manifest false positives | Existing `tests/test_task_channel_acl.py` covers no-manifest case |
| Perm broker | Policy miss → spool → poll → if no decision within 600s, synthesize `{verdict: 'deny', auto: True}`. Broker hook exceptions also deny (v8.18.1 `14983ea`). Cancellation deletes queue file. | Phase 5 acceptance gate: `test_perm_gate_times_out_with_auto_deny` becomes more important under perm-priority AD-1 |
| Spool delivery (new) | Two-phase drain: bridge sends, CC acks, bridge marks consumed. Entries consumed only on ack — CC crash mid-drain → replay safely. Perm entries surface as `[ALREADY DECIDED]` if decision-file present. | Phase 3 — new tests |
| Worker confinement (new) | NEW server-side `_worker_class_acl` refuses by default for worker-tagged nicks. | Phase 1 — new tests |

---

## Test Registry

(Buckets from synthesis topic 4. Counts: KEEP 7, MOVE-TO-BRIDGE 4, MOVE-TO-CC-PLUGIN 2, REWRITE 7, DELETE 2 + xfail 3 backend-deferred.)

### KEEP unchanged

- `tests/test_agent_runner.py` (drop only `test_make_options_keeps_bypass_for_standalone`)
- `tests/harness/test_agent_runner_claude.py`
- `tests/test_perm_broker.py` — **MOSTLY** keep, but two tests move to DELETE: `test_sticky_allow_does_not_bypass_ceiling` and `test_sticky_allow_below_ceiling_still_fast_path` (both exercise the ceiling stack Phase 5.2 removes). **Additionally REWRITE** `test_scope_always_appends_to_policy` (lines 230-252) (review iter-3 B-1 from agent 7) — the test currently calls `broker.gate("Edit", {"file_path": "/x"}, ...)` with scope=always; under Phase 5.1b's tightening, the test must include a non-empty `input_regex` to avoid raising `BareStickyApproveRefusedError`. Add a parallel test `test_scope_always_with_bare_high_risk_raises` that exercises the new guard explicitly.
- `tests/test_daemon_log.py`
- `tests/test_irc_targets.py`
- `tests/test_channel_brief.py`
- `tests/test_boss_brief_verify.py`
- `tests/test_dashboard_seed_integration.py`
- `tests/test_claude_runner_inheritance.py` (review iter-1 C-2 from agent 5 — workers still use AgentRunner; verify `permission_mode='default'` assertion against RC-2's v8.18.1 lesson)
- `tests/test_poll_loop.py` (review iter-2 B-1 from agent 5) — exercises worker daemon's poll_loop; workers retain the poll_loop in v1 (only bridge drops it), so this file STAYS in KEEP. Phase 2.2 task table explicitly notes that `_poll_loop` deletion is bridge-only (worker daemons keep it for the in-process push-message buffering it currently provides).
- `tests/test_context_watch.py` (review iter-2 B-2 from agent 5) — `_context_watch` is Claude-only and stays in worker daemon for now. KEEP unchanged.
- `tests/test_runtime_agent_config.py` (review iter-2 B-2 from agent 5) — imports `_context_watch_state` from `culture.clients.claude.daemon`; KEEP since `_context_watch` survives in workers.
- `tests/test_mention_target_cleanup.py` (review iter-2 B-3 from agent 5) — sets `daemon._agent_runner = runner` directly; worker behavior unchanged. KEEP.
- `tests/test_display.py` (review iter-2 C-2 from agent 5) — culture/console renders both workers and bridge; circuit_open assertions on workers stay; add bridge case asserting `cc_connected` instead. **REWRITE bucket actually** — moving below.
- `tests/test_daemon_config.py` already covered in REWRITE above (SupervisorConfig dataclass survives in shared config).
- `tests/harness/test_daemon_telemetry.py` (review iter-2 B-4 from agent 5) — asserts `packages/agent-harness/_start_agent_runner` raises `NotImplementedError`. Phase 1.6 says "update packages/agent-harness/ reference to reflect new prompt" — that reference is the SDK-wiring TEMPLATE workers extend from. **KEEP unchanged** — workers still need the template's _start_agent_runner hook; only the prompt string changes.

### MOVE to `tests/bridge/`

- `tests/test_irc_transport.py` → rewrite DM half against spool semantics
- `tests/test_mentions.py` → rewrite `test_mention_in_dm` against spool
- `tests/test_mention_alias.py` → rewrite `test_dm_activates_agent` against spool
- `tests/test_mention_warning.py` → no logic edits

### MOVE to `tests/cc_plugin/`

- `tests/test_persistent_observer.py` (if dashboard remains alongside CC: leave; AD-3 keeps it)
- `tests/test_silent_death_watchdog.py` — split: detector primitive stays in place, boss-tag-startup tests move

### REWRITE (in place)

- `tests/test_worker_boss_notice.py` — ~70% of assertions rewritten to spool semantics (worker writes DM → server spool → CC reads on next turn). Delete `TestOnPermRequest` (lines 181-208 — Phase 5.7 drops the underlying daemon method). **Keep** `TestRejoinOwnedTaskChannels` — review iter-3 B-2 from agent 7: `_rejoin_owned_task_channels` method SURVIVES in the bridge (per EL-1 classification "STAY-IN-BRIDGE"), only its test target moves from claude/daemon to bridge/daemon. Rewrite assertions to import from `culture.clients.bridge.daemon` instead of `culture.clients.claude.daemon`. Drop `SupervisorConfig` wiring everywhere. Also drop `test_poll_dispatch_counts_as_activation` (review iter-1 C-8 from agent 9). Also fix `#team` channel-list setups at lines 23, 37, 88, 143 to use `#task-bot` or another `#task-*` channel (review iter-2 C-5 from agent 7).
- `tests/test_task_channel_acl.py` — flip `test_regular_channel_allowed` and `test_regular_channels_unrestricted` to refused-for-#team (review iter-1 C-2 from agent 9); add explicit `#joint-*` opt-in test (per AD-3) and `#team-<project>` opt-in test
- `tests/test_culture_config.py` — drop supervisor block assertion; drop boss-tag special-meaning coupling. Clarify: SupervisorConfig deletion scope is bridge-only OR also worker daemons + 4 backends? **Plan decision:** keep SupervisorConfig dataclass alive in `culture/config.py` (worker daemons + codex/copilot/acp still use it); only the BOSS-tagged path drops it. So `tests/test_culture_config.py::test_load_server_config` keeps the supervisor-block parse test but adds an assertion that the bridge daemon ignores the block (logs warn, doesn't raise).
- `tests/test_daemon_config.py` — KEEP (workers + codex/copilot/acp still wire SupervisorConfig); add one new test asserting bridge mode ignores it
- `tests/test_daemon.py` — retarget `#general` to `#task-bot`; drop `SupervisorConfig` only in bridge-mode tests
- `tests/test_daemon_ipc.py` — add positive `#task-*` / `#joint-*` `ipc_send` tests; ALSO rewrite `test_on_mention_ignored_when_paused` to new spool semantics (review iter-1 C-7 from agent 5)
- `tests/test_boss_cli.py` — unify `CULTURE_NICK` fixtures; **ALSO drop** `_seed_ceiling` helper + `test_approve_above_ceiling_refused` + the `#team` channel-default assertions at lines 513, 526, 542, 585 (review iter-1 B-3 from agent 5 + B-2 from agent 7). NOT a light edit.
- `tests/test_boss_model_inherit.py` — rewrite line 98 which asserts `#team` IS in default channels (review iter-1 B-2 from agent 7)
- `tests/test_dashboard.py` — ~25% edits per AD-5; flat `/api/agents` shape PRESERVED (Phase 7.5 keeps backward-compat); only add NEW tests for tree view + chat panel + SSE
- `tests/test_display.py` (review iter-2 C-2 from agent 5) — culture/console renders both workers and bridge. Keep worker case (`circuit_open` field still appears for SDK-backed workers); add bridge case asserting `cc_connected` field replaces `circuit_open` for boss-tagged bridge entries.
- `tests/test_worker_boss_notice.py` — ALREADY listed above; add to its deletion list: `test_poll_dispatch_counts_as_activation` (review iter-2 C-8 from agent 9 — this test depends on the poll-loop activation path that the bridge drops; worker-side path renamed if it survives at all).

### DELETE

- `tests/test_boss_grant_ceiling.py` — covered concept (`DEFAULT_BOSS_CEILING`) removed per Rule 8 + EL-8
- `tests/test_supervisor.py` — **CORRECTION (review iter-4 B-1 from agent 5):** the `Supervisor` PRIMITIVE class survives because worker daemons (and codex/copilot/acp) still wire it. The boss-side INSTANTIATION at `daemon.py:301-313` is removed from the bridge, but `Supervisor` itself stays in `culture/clients/_supervisor.py` (or wherever it lives) for workers to use. **MOVE this entry from DELETE to KEEP.** Workers' tests pass unchanged; bridge tests don't import `Supervisor`.
- `tests/test_perm_broker_on_request.py` — entire file deleted; the `on_request` callback path is dropped in Phase 5.7 (review iter-1 C-1 from agent 5)
- Subset of `tests/test_perm_broker.py`: `test_sticky_allow_does_not_bypass_ceiling`, `test_sticky_allow_below_ceiling_still_fast_path` (review iter-1 B-1 from agents 5 and 7)
- Subset of `tests/test_boss_cli.py`: `test_approve_above_ceiling_refused` (review iter-1 B-3 from agent 5)
- Subset of `tests/test_worker_boss_notice.py`: `TestOnPermRequest` class (review iter-1 B-2 from agent 5)

### `xfail` under `tests/_deferred/` (v1 backend deferral per Rule 4)

- `tests/test_codex_daemon.py`
- `tests/test_acp_daemon.py`
- `tests/test_copilot_daemon.py`

### NEW tests (one per phase)

| ID | Test | Phase |
|---|---|---|
| NT-1 | `test_channel_architecture` — (a) JOIN `#team` returns 474 for EVERY nick (boss, worker, human) — channel is killed; (b) worker JOIN `#task-<own>` succeeds; (c) worker JOIN `#task-<other-worker>` returns 474; (d) worker JOIN `#joint-fixes` succeeds only if `culture.yaml` lists it OR after an explicit invite; (e) worker JOIN `#team-<own-project>` succeeds if invited; (f) worker JOIN `#team-<other-project>` returns 474 | Phase 1 |
| NT-2 | `test_no_polling_in_system_prompt` — load each backend's `_build_system_prompt`; assert no `irc_read`, no `irc_send`, no `periodically`, no `Check IRC` substring | Phase 1 |
| NT-3 | `test_bridge_no_sdk` — boot bridge with `skip_claude=True`; assert no `AgentRunner` instance. **The `cc_connected` field** is NET-NEW on the bridge daemon status response; NT-3 is WRITTEN ALONGSIDE Phase 2.2 (not as a precondition) since the field doesn't exist on the current `daemon.py`'s `_ipc_status` response (review iter-2 B-2 from agent 9). | Phase 2 |
| NT-4 | `test_bridge_474_handler` — simulate `ERR_BANNEDFROMCHAN` on a JOIN; assert channel REMOVED from `self.channels` (v8.19.42 carry-forward) | Phase 2 |
| NT-5 | `test_broker_pretooluse_hook_survives_split` — boot bridge + worker; spawn an actual SDK tool call; deny via worker policy; assert tool actually fails (v8.18.1 lesson) | Phase 2 (CRITICAL — blocks merge per T9) |
| NT-6 | `test_dm_spool_drain_on_reconnect` — bridge disconnects, peer sends DM, bridge reconnects, `CHATHISTORY` returns DM, CC plugin surfaces in next turn | Phase 3 |
| NT-7 | `test_dm_spool_persists_across_server_restart` — SQLite-durable | Phase 3 |
| NT-8 | `test_cc_session_start_hook` — install plugin, launch CC, assert `hookSpecificOutput.additionalContext` is non-empty + includes any spooled DMs | Phase 4 |
| NT-9 | `test_mesh_dm_tool_routes_through_bridge` — CC plugin tool `mesh dm <nick> <text>` produces IRC PRIVMSG via bridge | Phase 4 |
| NT-10 | `test_mid_turn_dm_queues` — DM arrives mid-turn; assert NOT injected as `<system-reminder>` mid-turn; assert surfaces at end-of-turn | Phase 4 |
| NT-11 | `test_mid_turn_perm_request_interrupts` — perm request arrives mid-turn; assert surfaces as `<system-reminder>` immediately (AD-1) | Phase 5 |
| NT-12 | `test_append_sticky_rule_refuses_bare_high_risk_tool` — using the real signature: `_append_sticky_rule('allow', 'Bash', {})` raises `BareStickyApproveRefusedError`. NT-12 depends on Phase 5.1a (which CREATES the exception). Plus a demote-to-once test: `_request_from_boss` catches the error and writes scope=once, worker still proceeds. **NT-12 lives in its own file `tests/test_perm_broker_bare_sticky.py` AND is committed in the same PR as Phase 5.1a + 5.1b + 5.1c** (atomic — review iter-4 B-1 from agent 9: pytest will collect the file the moment it exists, so the file and the exception MUST land together in a single commit/PR). The PR title should mention `[atomic 5.1a-c]` to make the sequencing visible in code review. | Phase 5 (5.1a + 5.1b + 5.1c + NT-12 = single atomic PR) |
| NT-13 | `test_no_grant_ceiling` — `DEFAULT_BOSS_CEILING` does not exist; `is_above_ceiling` not callable; `tests/test_boss_grant_ceiling.py` is gone | Phase 5 |
| NT-14 | `test_silent_death_watchdog_in_bridge` — worker dies without `agent_exit`; bridge's watchdog (not boss daemon) writes `silent_death_after_done` to bridge daemon-log + spools DM to CC | Phase 6 |
| NT-15 | `test_project_nick_resolution` — (a) CC in cwd with git remote `git@github.com:foo/fork-rearch.git` → boss nick `fork-rearch`; (b) `CULTURE_BOSS_NICK=mesh-design` env → boss nick `mesh-design`; (c) no git remote, no env, cwd `/tmp/x` → boss nick `x`; (d) nothing resolves → fallback `local-boss` with warning | Phase 4 |
| NT-16 | `test_multi_cc_per_host` — launch two CC sessions in different cwds (`fork-rearch` + `payment-debug`); both bridges run concurrently with distinct sockets + distinct IRC connections + distinct boss nicks on the mesh | Phase 4 |
| NT-17 | `test_dashboard_human_chat` — dashboard chat panel sends a DM from the human's nick (`edo`) to any agent on the mesh; bridge routes via IRC PRIVMSG; recipient sees `edo` as sender | Phase 7 |
| NT-18 | `test_dashboard_tree_view` — NEW endpoint `/api/agents/tree` returns hierarchical structure (project → boss → workers). **Flat `/api/agents` PRESERVED for backward-compat** (review iter-4 B-2 from agent 9 — consistent with Phase 7.5 + REWRITE bucket for `tests/test_dashboard.py`). | Phase 7 |

---

## Out of Scope

- Codex / Copilot / ACP backends becoming CC-IS-the-boss equivalents (Rule 6 — deferred to v2).
- Cross-host federation of DM spools (single-server scope for v1; the existing `agentirc` peer relay already works for channels).
- Rewriting the dashboard frontend's poll loops to SSE (per push-everywhere rule — but a polished follow-up PR; v1 keeps existing dashboard frontend for the read-side; new chat panel is SSE-shaped from day one).
- Folding the dashboard into CC entirely (kept alongside per AD-5).
- Replacing `_silent_death_watchdog`'s PID-polling with a kernel event (no cross-process push primitive exists; documented intentional poll).

**In scope (correction from earlier draft):** multi-CC-session-per-host IS supported per Rule 1 — each CC session spawns its own bridge process with a distinct project-named nick. Per-host limit is just the IRC server's connection cap (default 100+, plenty of headroom for ~5 concurrent CC sessions).

---

## Phase Breakdown

Each phase = one feature branch + one PR + a `/version-bump minor` commit. Phases sequenced by **smallest blast radius first**, then **dependency order**. Each phase has its own acceptance gate; no phase merges with a failing gate.

### Phase 0 — Pre-flight (PATH shim + critical spikes + transport-fix carryforward)

Adds three additional pre-flight tasks per review iteration 1: the UA-2 spike (Phase 4 hinge), the v8.19.42 carryforward to non-bridge transports (review iter-1 B-1 from agent 3), and macOS peercred shim verification (review iter-1 B-2 from agent 8).

| # | Task | File / area | Acceptance |
|---|---|---|---|
| 0.1 | `culture` shim on PATH (plenty's P1) | new `bin/culture` shell wrapper invoking `uv --project … run culture`; OR top-of-`culture-boss/SKILL.md` note documenting the `uv` invocation | Operator can run `culture --help` from a fresh shell |
| 0.2 | GC stale manifest entries on `culture agent status` | `culture/cli/agent.py` — `unregister` with `--all-missing` flag | `culture agent status` no longer warns on `local-prd-check-w` etc. |
| 0.3 | Reconcile `culture boss init` text with SKILL.md autonomous-vs-driven framing | `culture/cli/boss.py`, `~/.claude/skills/culture-boss/SKILL.md` | Single coherent narrative |
| 0.4 | **UA-2 spike** — write a 30-line throwaway plugin with a Stop hook that returns `decision: "block"` + a fake reason; verify CC honors the block and re-enters an assistant turn carrying the reason as context. Test on the actual CC version we ship against. **CRITICAL: include `stop_hook_active` idempotency check** (review iter-2 C-1 from agent 10) — the spike script returns `decision:block` only when `stop_hook_active==false` in the hook's stdin payload, otherwise returns no decision (prevents infinite loop on the next turn). Result determines Phase 4.5 implementation strategy. | throwaway `~/.claude/plugins/culture-stop-spike/` | Spike report committed under `docs/` confirming Stop-hook block semantics work AND idempotency under `stop_hook_active`, OR with fallback design if they don't |
| 0.5 | **v8.19.42 carryforward** — the 474 ERR_BANNEDFROMCHAN handler + server-confirmed JOIN tracking is missing from BOTH `culture/clients/claude/irc_transport.py` AND `packages/agent-harness/irc_transport.py` on this branch. Port the fix into both files (the bridge in Phase 2.3 will then inherit it cleanly). **TWO entry paths must be handled** (review iter-2 C-4 from agent 3): (a) the `join_channel` method's optimistic append; (b) `_on_welcome`'s raw JOIN sends for pre-configured channels (bypasses `join_channel`'s guard). The 474 handler must remove the rejected channel from `self.channels` regardless of which path JOINed it. | `culture/clients/claude/irc_transport.py` (`join_channel` + `_on_welcome` + `_cmd_handlers` register `'474': self._on_cannotsendtochan`), `packages/agent-harness/irc_transport.py` (mirror) | Existing transport tests green; regression test simulating `474` for both paths removes the channel from `self.channels` |
| 0.6 | **macOS peercred spike** — `SO_PEERCRED` does not exist on Darwin. Write the ctypes shim that calls `getpeereid(3)` via `libc`. Bench on the dev box. Confirms Phase 2.5 socket-auth approach. **CI-runnable test** (review iter-2 C-1 from agent 8) — unit test mocks the ctypes call and asserts the refusal logic fires on mismatched uid; no real socket connect required so it runs on the existing pytest CI. | `culture/clients/bridge/_peercred.py` (vendor) | Unit test asserts `peercred(sock_fd)` returns `(uid, gid)` on Linux (via `SO_PEERCRED`) and `peercred(sock_fd)` correctly mocked on Darwin (via `getpeereid` ctypes); refusal-on-mismatched-uid test runs in CI |
| 0.7 | **`watchdog` library smoke** — confirm `watchdog 6.0.0` Observer pattern integrates with asyncio loop via `call_soon_threadsafe`. Validates Phase 5.4 + 5.6. | spike script | Test plugin observes file create in `/tmp/test`, callback dispatched into asyncio task |

**Acceptance gate:** all existing tests green; PATH check works from fresh shell; UA-2 spike report committed; v8.19.42 fix verified in both transports; macOS peercred shim runs on Darwin.

### Phase 1 — Channel architecture cleanup + comms-prompt strip (small surface, broad effect)

Three things close in this phase: plenty's **P2** (worker IRC-tool rabbit-hole), the workers-in-`#team` leak (AD-3), and the global `#team` channel itself (AD-4).

| # | Task | File / area | Acceptance |
|---|---|---|---|
| 1.1 | Remove `#team` from all spawn / register / init defaults AND from user-facing help text. Sites: `culture/cli/boss.py:131` (argparse help text "Channel name (e.g. '#team') OR a worker suffix" — replace with `#general` or `#task-foo` example per review iter-3 B-1 from agent 5); `culture/cli/boss.py:846` (`base_channels = [_task_channel(suffix)]`); `culture/cli/boss.py:947` in `_write_boss_yaml`; `culture/dashboard/server.py:431` `_classify_channel`. NOTE: `culture/agentirc/ircd.py` does NOT have a hardcoded `#team` bootstrap site (removed from task list as erroneous). | `culture/cli/boss.py:131, 846, 947`; `culture/dashboard/server.py:431` | No new mesh writes touch `#team`; `grep -rn '"#team"' culture/` returns only test-rewrite sites + intentional historical references + the new help text example |
| 1.2 | **Unified channel-class ACL** (review iter-1 C-1 from agent 3): extend `_task_channel_acl` with per-prefix blocks rather than adding a sibling predicate. Add: `#team` returns False for ALL nicks (channel killed per AD-4); `#system`/`#boss` returns False for workers, True for bosses/humans; `#team-<project>` returns True if the joining nick is a member of that project (boss or one of its workers per manifest); `#task-*` existing logic preserved. **`#joint-*` invite gating deferred to Phase 3** — Phase 1.2 leaves `#joint-*` open per legacy behavior (no `_channel_invites` data structure exists yet; Phase 3 will add it alongside the spool DB; review iter-2 B-2 from agent 3). Identification of "worker" vs "boss" vs "human" requires extending `_load_owner_map` (review iter-2 C-3 from agent 7) — bump from `dict[worker → boss]` to a parallel `_load_role_map(): dict[nick → {'role': 'worker'|'boss'|'human', 'boss': str|None, 'project': str|None}]`. Discrimination rules: `human = nick not in role_map` (no manifest entry); `boss = role_map[nick].role == 'boss'`; `worker = role_map[nick].role == 'worker'`. ALSO update the comment block at `agentirc/client.py:46-51` to document the new channel-class ACL contract (review iter-2 C-5 from agent 3). | `culture/agentirc/client.py:147-199`, `:46-51` (comment block), `:56-129` (_load_owner_map → _load_role_map) | NT-1 passes; manifest lookup at JOIN time is the identity source |
| 1.3 | Strip polling language from all 4 backend system prompts; replace with: *"To talk to your boss, reply in your task channel. Your boss reads channel replies via the bridge. There is no IRC tool — do not search for one."* | `culture/clients/claude/daemon.py:1145-1155`, `culture/clients/copilot/daemon.py:552`, `culture/clients/codex/daemon.py:570`, `culture/clients/acp/daemon.py:577` | NT-2 passes (no `irc_read`, no `periodically`, no `Check IRC` substring in any backend prompt) |
| 1.4 | Update `~/.claude/skills/irc/SKILL.md` (worker-side skill) to drop polling guidance | skill markdown | grep `SKILL.md` for `periodically` → 0 hits |
| 1.5 | Update `packages/agent-harness/` reference (per cite-don't-import rule) | `packages/agent-harness/...` | Reference reflects new prompt |
| 1.6 | Test updates: `tests/test_task_channel_acl.py` — flip `test_regular_channel_allowed` and `test_regular_channels_unrestricted` from allowed-for-all to refused (for `#team` specifically); add `#team-<project>` opt-in cases; add `#joint-*` invite cases. ALSO update `tests/test_boss_model_inherit.py:98` which asserts `#team` IS in default channels (review iter-1 B-2 from agent 7). ALSO update `tests/test_boss_cli.py:513, 526, 542, 585` which assert `#team` is a default. | tests | green |

**Acceptance gate:** NT-1 + NT-2 green; existing `test_task_channel_acl.py` rewritten and green; manual: spawn a worker via `culture boss spawn fork-rearch-qa`, observe it JOINs only `#task-fork-rearch-qa`; attempt to JOIN `#team` as any nick → 474.

### Phase 2 — Bridge skeleton (transport-only, no SDK)

The structural core. After this phase, the boss daemon runs WITHOUT an SDK loop, fully equivalent to a passive IRC + IPC + audit + daemon-log surface.

| # | Task | File / area | Acceptance |
|---|---|---|---|
| 2.1 | New `culture/clients/bridge/` directory; copy claude daemon as starting point per cite-don't-import | `culture/clients/bridge/daemon.py`, `irc_transport.py`, `socket_server.py`, `_daemon_log.py`, `_audit.py` (copies) | Directory exists |
| 2.2 | Bridge `daemon.py`: set `skip_claude=True`; **exhaustively delete** the following methods + every reference site, leaving no orphaned `self.X` reads that would AttributeError at runtime (review iter-4 B-2/B-3/C-1/C-2 from agent 5): `_start_agent_runner` (517-571), `_delayed_restart` (1286-1289), `_evaluate_circuit_breaker` + `_circuit_open` state (161, 1192-1236), `_sleep_scheduler` (413-450), `_poll_loop` + `_process_poll_cycle` + `_send_channel_poll` (451-498) + `self._poll_task = asyncio.create_task(_poll_loop())` at line 323, `_on_agent_exit` / `_on_agent_message` / `_on_agent_usage` / `_on_turn_complete` (`~1003`) / `_on_turn_failed` (`~1014`) callbacks AND their wiring at daemon.py:523-527, `_build_system_prompt` (1145-1155), `_context_watch_*` (1031-1063), `Supervisor` instantiation (301-313) + `self._supervisor` reads at 368, 999, 1367, 1380 + `_on_supervisor_escalation` (1295-1316) + `_on_supervisor_whisper` (1300), `_maybe_rearm_watchdog` (824-834 — references the deleted runner), `mission_persistence` reads (1157-1158). **Verification:** `grep -nE 'self\._(agent_runner|supervisor|circuit_open|poll_task|sleep_task|context_watch)' culture/clients/bridge/daemon.py` returns ZERO hits after Phase 2.2 lands. | `culture/clients/bridge/daemon.py` | NT-3 passes (no `AgentRunner` instance); grep above returns 0; bridge boots without AttributeError |
| 2.3 | Bridge IRC transport: inherit v8.19.42 (474 handler + server-confirmed JOIN tracking) which already landed in Phase 0.5 in `culture/clients/claude/irc_transport.py` AND `packages/agent-harness/irc_transport.py`. Add 404 (ERR_CANNOTSENDTOCHAN) handler. | `culture/clients/bridge/irc_transport.py` (copy from claude with Phase 0.5 fix in place) | NT-4 passes |
| 2.4 | **Bridge IPC dispatch — full contract.** Preserved (13 verbs): `irc_send`, `irc_read`, `irc_join`, `irc_part`, `irc_channels`, `irc_who`, `irc_topic`, `irc_ask`, `irc_thread_create`, `irc_thread_reply`, `irc_threads`, `irc_thread_close`, `irc_thread_read`. Repurposed (1 verb): `compact` (now writes daemon-log entry only). Preserved with reshaped response (2 verbs): `status` (drops `circuit_open`, adds `cc_connected`), `shutdown`. Dropped (3 verbs): `clear`, `pause`, `resume` (no SDK loop). NET-NEW (10 verbs): `cc_session_start`, `cc_session_end`, `set_runtime_model`, `sdk_event` (audit write), `daemon_log_record`, `inbound_dm`, `inbound_mention`, `inbound_roominvite`, `perm_request`, `perm_decision`, `inbound_dm_ack` (and parallel `inbound_mention_ack`, `inbound_roominvite_ack`, `perm_decision_ack`). | `culture/clients/bridge/daemon.py:_ipc_dispatch`, `protocol/extensions/bridge-ipc.md` | IPC schema documented under `protocol/extensions/bridge-ipc.md`; every verb names its producer (bridge or CC) and consumer (the other side) with payload shape |
| 2.5 | Socket auth — adds explicit peer-uid check to `socket_server.py` which today has ZERO uid enforcement beyond `os.chmod(self.path, 0o600)` at startup (review iter-3 B-2 from agent 8 — chmod alone is insufficient because (a) symlink target permissions may not match, (b) `/tmp` fallback on macOS has world-traversable parent dir). New behavior: in `SocketServer._handle_client` (currently lines 64-72), call `_peercred(sock_fd)` immediately after accept; refuse with EPERM + log if peer uid != self uid. Also handle the macOS-specific case where `$XDG_RUNTIME_DIR` is unset and falls back to `/tmp/culture-<nick>.sock` (review iter-3 C-3 from agent 8) — explicit code path: on macOS, prefer `~/Library/Caches/culture/run/` if `$XDG_RUNTIME_DIR` is unset (user-private, not world-traversable). | `culture/clients/bridge/socket_server.py` + `_peercred.py` + (new) socket path resolution helper | NT (new): connecting as another user via `sudo -u nobody python -c "import socket; ..."` gets EPERM on both Linux and macOS; macOS unit test confirms `/tmp/` fallback NOT used |
| 2.6 | Manifest invariant on startup: refuse to start if any worker's `boss:` field points at the bridge's nick AND another manifest entry tagged `boss` claims the same nick (two CC sessions trying to use the same identity). **First-run carve-out** (review iter-2 C-2 from agent 7): if zero workers exist for this bridge's nick (brand-new project), the invariant is vacuously satisfied — the bridge starts and waits for first `culture boss spawn`. Documented as "first-run / empty mesh" case. | `culture/clients/bridge/daemon.py:start()` | NT-3 extension; first launch in a clean cwd succeeds without manifest entries |
| 2.7 | `MessageBuffer` cursor persistence at `~/.culture/bridge/cursors.json` (atomic write); reload on start | `culture/clients/bridge/message_buffer.py` | Unit test: write, restart bridge, cursor restored |
| 2.8 | Bridge shutdown order: `CHANARCHIVE` all owned `#task-*` channels before PART, then drop transport | `culture/clients/bridge/daemon.py:stop()` | Manual: stop bridge with worker still up, restart, channel still has history |
| 2.9 | **CRITICAL — T9 gate**: NT-5 (broker-via-PreToolUse-hook integration test booting worker via bridge spawn). Existing `test_make_options_wires_pretooluse_hook_when_broker_present` must stay green. | `tests/bridge/test_broker_pretooluse_survives_split.py` | NT-5 green; review per pre-push code-review choke point |
| 2.10a | **Create `tests/bridge/__init__.py` FIRST** (Phase 2's very first test-side task — review iter-3 C-1 from agent 9; tests/bridge/ does not exist on this branch). Empty placeholder file + add `tests/bridge/conftest.py` if shared fixtures are needed. Verify pytest collects from `tests/bridge/` via `pytest --collect-only tests/bridge/`. | `tests/bridge/__init__.py`, `tests/bridge/conftest.py` (if needed) | `pytest --collect-only tests/bridge/` succeeds (returns "collected 0 items" before NT files land) |
| 2.10b | Move `tests/test_irc_transport.py`, `tests/test_mentions.py`, `tests/test_mention_alias.py`, `tests/test_mention_warning.py` to `tests/bridge/` per Test Registry; rewrite DM tests to spool placeholders (real spool lands Phase 3) | tests reorg | green |

**Acceptance gate:** NT-3 + NT-4 + NT-5 all green; existing `tests/test_perm_broker*` green; manual smoke: bridge connects, joins channels, accepts IPC commands; bridge runs with NO Python `AgentRunner` instance (verifiable via `lsof | grep ‹bridge-pid› | grep claude` returning nothing).

### Phase 3 — Server-side DM spool

The fix for the `culture channel read '#team'` empty-return bug AND the structural enabler for boss-to-boss DM (Q1 binding).

| # | Task | File / area | Acceptance |
|---|---|---|---|
| 3.1 | Add `dm_spool` table to `agentirc/history_store.py` (or new `dm_spool_store.py`); schema: `(msg_id PK, sender, recipient, ts_server, payload, tags, delivered_at NULL)` | `culture/agentirc/dm_spool_store.py` | Unit test: insert, query-by-recipient, mark-delivered |
| 3.2 | IRCd hook: in `client.py:_send_to_client`, when recipient absent, write to `dm_spool_store` instead of returning False; remove `ERR_NOSUCHNICK` for spool-eligible targets (boss nicks); preserve ERR for true unknowns | `culture/agentirc/client.py:1062-1096` | Peer sends DM to disconnected `local-boss` — NO `ERR_NOSUCHNICK`; entry in `dm_spool` |
| 3.3 | IRCv3 `draft/chathistory` CAP + `CHATHISTORY=<N>` ISUPPORT advertisement; handle `CHATHISTORY` command for per-nick targets. **IDOR guard** (review iter-4 B-3 from agent 8): the handler MUST validate `requesting_nick == target_nick` for per-nick spool drains. A peer requesting `CHATHISTORY *` or `CHATHISTORY target=other-boss-nick` returns ERR_NOPRIVILEGES (or equivalent numeric reply); only the recipient may drain their own spool. Channel-history CHATHISTORY remains unchanged (governed by channel ACL — `_client_may_read_history`). | `culture/agentirc/ircd.py`, `culture/agentirc/skills/chathistory.py` (new) | Peer connects → CHATHISTORY for own nick returns spooled DMs; CHATHISTORY for any other nick is refused with ERR_NOPRIVILEGES |
| 3.4 | Bridge: on connect, issue `CHATHISTORY` to drain own spool; for each result, write IPC `inbound_dm` (already defined in Phase 2.4) to CC plugin | `culture/clients/bridge/daemon.py:_on_welcome` | NT-6 passes |
| 3.5 | Two-phase drain: bridge sends to CC, CC acks via `inbound_dm_ack(msg_id)`, bridge issues `CHATHISTORY DELETE` (or equivalent mark-delivered) | bridge + CC IPC | NT-6 covers; idempotent under CC crash mid-drain |
| 3.6 | Spool retention policy: `delivered_at IS NOT NULL` entries purged after 7 days; `delivered_at IS NULL` retained 30 days then dropped (with audit-log entry) | `culture/agentirc/dm_spool_store.py:gc()` cron-like task | Unit test: aged entries gone |
| 3.7 | Spool filesystem hardening: spool SQLite at 0o600; spool dir 0o700 | DB open path | Manual: `stat` confirms |
| 3.8 | NT-7 passes (durable across server restart) | tests | green |

**Acceptance gate:** NT-6 + NT-7 green; manual: kill bridge, peer sends 3 DMs, restart bridge, CC sees all 3 as system reminders on next turn; `culture channel read '#team'` actually returns recent messages (server-replay shape change).

### Phase 4 — CC plugin (user-settings hook installer + mesh tools + project naming)

The user-facing surface. After this phase, CC literally IS the boss, named after the project.

**Architecture pivot (review iteration 1, ext-blocker-1 + ext-blocker-2):** Claude Code plugin-scoped SessionStart hooks do NOT inject `hookSpecificOutput.additionalContext` reliably (CC bug #16538, closed-as-not-planned). And mid-conversation `<system-reminder>` injection via PreCompact has documented holes. **Revised approach:**

- The **plugin INSTALLS** SessionStart + Stop + UserPromptSubmit hooks into `~/.claude/settings.json` (user-scope, NOT plugin-scope) on first activation. User-scope hooks ARE supported as silent-injection paths.
- **SessionStart** drains the spool at launch (one-shot).
- **Stop hook with `decision: "block"`** is the end-of-turn queue-drain mechanism: when CC's assistant turn would end, the Stop hook checks the bridge IPC queue; if non-empty, it returns `decision: block` + `reason: <queued events>`, forcing CC into another assistant turn with the queue contents as the prompt. This is the canonical agentic-loop continuation pattern and IS reliably supported.
- **UserPromptSubmit** runs on any human input as a fallback drain path (and to gather any queued events the user might want to see surfaced even outside the Stop loop).
- Perm requests use a **PreToolUse** hook that blocks the next tool call with `decision: block` + the perm request — this gives perm requests interrupt priority (AD-1) without depending on the unreliable mid-turn `<system-reminder>` path.

| # | Task | File / area | Acceptance |
|---|---|---|---|
| 4.1 | New CC plugin at `culture/clients/claude/cc_plugin/`: `plugin.json` manifest, `install.py` (first-run hook installer), `tools.py` for `mesh ...` tools, the hook scripts in `hooks/`. Plugin's first activation writes a `culture-bridge` block into `~/.claude/settings.json` registering the four hooks under user scope; idempotent (overwrites only the culture-bridge block). | `culture/clients/claude/cc_plugin/` | Plugin loads in CC; first activation writes `~/.claude/settings.json` block; `mesh status` returns bridge connection status |
| 4.2 | **Project nick resolution at hook install** (AD-2 + AD-7): priority (a) explicit `CULTURE_BOSS_NICK` env or `culture boss init --name` value in `<cwd>/culture.yaml`; (b) cwd's git remote basename (`git config --get remote.origin.url` → basename, strip `.git`); (c) cwd basename; (d) legacy fallback `local-boss` with warning. Chosen nick is bridge's IRC identity + manifest `boss:` value. SessionStart hook's `additionalContext` includes: *"Session naming: I'm `<resolved-nick>` on the mesh. Override via `culture boss init --name X`."* | `cc_plugin/_nick_resolver.py` + SessionStart hook | NT-15 passes: launch CC in `~/projects/fork-rearch` → boss `fork-rearch`. Override works. |
| 4.3 | **SessionStart hook** (user-scope via plugin installer): connect to bridge socket (start bridge if not running); emit `cc_session_start(nick=<resolved>)`; drain spool for that nick; return `hookSpecificOutput.additionalContext` with mesh roster + spool entries. Idempotent across startup/resume/clear/compact firings. | `cc_plugin/hooks/session_start.py` | NT-8 passes |
| 4.4 | CC plugin tools registered as MCP tools the assistant can call: `mesh send <channel> <text>`, `mesh dm <nick> <text>`, `mesh inbox` (drain pending), `mesh who [#channel]`, `mesh status`, `mesh agents`, `mesh pending` (perm queue — moved to Phase 4 per concern C4 of iteration 1; Phase 5 no longer dupes), `mesh approve <id>`, `mesh deny <id> [reason]`, `mesh invite <worker> <#chan>`, `mesh team-channel create [topic]`, `mesh grant <worker> <tool> [input_regex] [scope]` | `cc_plugin/tools.py` | NT-9 passes |
| 4.5 | **Stop hook** (user-scope, end-of-turn queue drain): on assistant Stop, check bridge IPC for queued `inbound_dm` / `inbound_mention` / `inbound_roominvite`; if non-empty, return `decision: "block"` + `reason: <queued events as system reminder>`. CC's next turn opens with these as context. This is the canonical agentic-loop continuation pattern. | `cc_plugin/hooks/stop.py` | NT-10 passes |
| 4.6 | **UserPromptSubmit hook** (user-scope, fallback drain): on any human-typed prompt, drain queue and prepend any new events to the prompt. Belt-and-braces with the Stop hook. | `cc_plugin/hooks/user_prompt_submit.py` | Manual: type "ping" in CC, observe queued events appear |
| 4.7 | **PreToolUse hook** (user-scope, perm-request interrupt — AD-1): before any tool call, check bridge IPC for queued `perm_request`; if present, return `decision: "block"` + `reason: <perm request details>`. CC sees the perm request before its own tool fires. Approves/denies via `mesh approve`/`mesh deny`. **CRITICAL — recursion avoidance** (review iter-3 C-1 from agent 7): the hook MUST skip intercepting tool calls whose name starts with `mesh ` (the entire `mesh send`/`mesh dm`/`mesh approve`/`mesh deny` etc. tool family) — otherwise approving the perm request triggers the same hook, infinite-loops. Implementation: read tool name from stdin payload; if `name.startswith('mesh ')` or `name in {'mesh approve', 'mesh deny'}`, return no decision (pass through). Same idempotency pattern as the Stop hook's `stop_hook_active` check (Phase 0.4). | `cc_plugin/hooks/pre_tool_use.py` | NT-11 passes; recursion regression test: approving a perm request via `mesh approve` does NOT re-trigger the hook |
| 4.8 | **Worker naming** (AD-2): when CC plugin tool `mesh spawn` (or `culture boss spawn`) is called, the worker is registered as `<boss-nick>-<name>`. Auto-prepend in spawn helper. Brief template auto-includes: *"You are `<full-nick>`, working under `<boss-nick>` on …"* | `culture/cli/boss.py:_cmd_spawn` + brief template | Manual: `mesh spawn qa` while boss=`fork-rearch` → `fork-rearch-qa` registered; brief mentions it. |
| 4.9 | CC plugin starts owned workers on SessionStart (reads manifest for `boss: <session-nick>`; `culture boss spawn ...` for any pre-existing); stops on SessionEnd hook | `cc_plugin/hooks/session_start.py`, `cc_plugin/hooks/session_end.py` | Manual: launch CC → workers come up; close CC → workers stop |
| 4.10 | `set_runtime_model` IPC: plugin emits on first AssistantMessage event (via UserPromptSubmit hook tracking) with CC's resolved model; bridge writes manifest + daemon-log `model_resolved` (v8.18.6 invariant preserved) | plugin + bridge | RC-1's `model_resolved` test green |
| 4.11 | Mission persistence: SessionStart hook reads `~/.culture/mission/<nick>.md` and emits in `additionalContext`; bridge writes to it from inbound spool drain | bridge `_on_spool_drain` + plugin hook | Across restart, prior mention context survives |
| 4.12 | Create `tests/cc_plugin/__init__.py` + new tests per Test Registry | tests | green |
| 4.13 | Document the user-settings-hook installation in the plugin's README so operators understand why `~/.claude/settings.json` was modified | `culture/clients/claude/cc_plugin/README.md` | Plain-English explanation present |

**Acceptance gate:** NT-8 + NT-9 + NT-10 + NT-11 + NT-15 green; manual flow: launch CC in `~/projects/fork-rearch`, observe boss nick is `fork-rearch`; check `~/.claude/settings.json` has the culture-bridge block; spawn worker `qa`, observe `fork-rearch-qa` arrives in `#task-fork-rearch-qa`; peer DMs `fork-rearch`, CC sees it end-of-turn via Stop hook; close CC, peer DMs land in spool, relaunch CC, SessionStart drain surfaces them.

### Phase 5 — Perm broker rewiring (no ceiling + mid-turn priority)

Implements Rule 8 (no `grant_ceiling`) and AD-1 (mid-turn priority). Sequencing matters per RC-4 / T3. Note review-iter-1 corrections: BareStickyApproveRefusedError doesn't exist yet (must be CREATED in 5.1); `_append_sticky_rule` is at `_perm_broker.py:787-814` (not 717-814).

| # | Task | File / area | Acceptance |
|---|---|---|---|
| 5.1a | **CREATE `BareStickyApproveRefusedError(ValueError)`** as a new exception class in `_perm_broker.py` alongside `DecisionExistsError` / `InvalidRequestIdError` at lines ~518-524. | `_perm_broker.py` (new exception class) | Class importable from `_perm_broker` |
| 5.1b | **MUST land before 5.2**. Tighten `_append_sticky_rule(verdict, tool_name, decision)` at lines 787-814: when `verdict=='allow'` and `tool_name` matches the high-risk list (`Bash`, `Edit`, `Write`, `mcp__.*`), require `decision` to contain a non-empty `input_regex` (or equivalent narrowing key). **CRITICAL — check `tool_name` itself, NOT `decision.get('pattern')`** (review iter-3 B-1 from agent 8): the existing line 809 constructs the rule as `{"tool": decision.get("pattern") or tool_name}` — a `--pattern Foo --tool Bash` approval would currently write `{"tool": "Foo"}` and bypass the high-risk check. The guard must inspect `tool_name` regardless of whether `decision.get('pattern')` overrides the final rule's `tool` field, AND match the resulting `tool` field too (defense in depth: refuse if EITHER `tool_name` OR the resolved `decision.get('pattern')` is high-risk-without-input-regex). On miss: raise `BareStickyApproveRefusedError`. Update the single call site at `_perm_broker.py:722` (in `_request_from_boss`). | `_perm_broker.py:787-814` + `:722` | NT-12 passes (test rewritten to use real signature: `_append_sticky_rule('allow', 'Bash', {})` → raises) AND a regression test for the pattern-bypass: `_append_sticky_rule('allow', 'Foo', {'pattern': 'Bash'})` ALSO raises |
| 5.1c | **Demote-rather-than-fail UX feedback:** when `BareStickyApproveRefusedError` would fire from inside `gate()`'s `_request_from_boss` consumption path, the broker catches it specifically and writes a `scope=once` sticky rule INSTEAD (one-time approval still honored; persistent escalation refused). The demote-notice is routed through the **bridge IPC `inbound_mention` push path** (NOT via the old `on_request` callback — see review iter-2 B-2 from agent 7 / C-2 from agent 8; Phase 5.7 drops `on_request` entirely). Bridge picks up the demote-notice file (broker writes to `~/.culture/perm-demote-notices/<id>.json`), pushes IPC `inbound_mention` with tag `demote-notice`, CC sees: *"Your `--always` approval for <tool> was demoted to one-time because no `input_regex` was supplied."* | `_perm_broker.py` `_request_from_boss` + bridge `watchdog` observer + CC plugin handler | Worker still proceeds for one-shot; CC sees the demote notice next turn via bridge IPC, NOT via the old IRC-DM-to-boss path |
| 5.1d | **`write_decision` defense-in-depth:** add `tool_name` and `input_regex` parameters to `write_decision` (signature change — also propagate through `culture boss approve --tool --input-regex` CLI flags). Then add validator that raises `BareStickyApproveRefusedError` when `scope == 'always'` AND `verdict == 'allow'` AND `tool_name` is high-risk AND `input_regex` is missing/empty. Mirrors 5.1b at the lower layer so CLI / dashboard / direct file writes all benefit. (Review iter-2 B-1 from agent 8 + C-3 from agent 2 — `write_decision`'s current signature is `(verdict, scope, reason, pattern, decided_by)` with no tool/input awareness.) | `_perm_broker.py:526-574` (write_decision signature) + `culture/cli/boss.py` (approve CLI flags) + dashboard approve endpoint | Direct CLI write of a bare-Bash sticky rule fails: `culture boss approve <id> --tool Bash --scope always` (no `--input-regex`) raises |
| 5.2 | Delete `DEFAULT_BOSS_CEILING`, `_boss_policy_dir`, `boss_policy_path_for`, `write_default_boss_ceiling`, `load_boss_ceiling`, `is_above_ceiling`. Remove ceiling re-check in `gate()` at lines 649-658. Remove from `culture/cli/boss.py:32, 37, 371-379, 924`. Delete `tests/test_boss_grant_ceiling.py`. **ALSO delete** `tests/test_perm_broker.py::test_sticky_allow_does_not_bypass_ceiling` and `::test_sticky_allow_below_ceiling_still_fast_path` (review iter-1 B-1 from agent 5 — these two tests cannot survive ceiling removal). Update `_MANAGER_PROMPT` in `culture/cli/boss.py:48-72` to remove ceiling-teaching language (review iter-1 C-5 from agent 5). | `_perm_broker.py:321-395`, `cli/boss.py` (multiple sites — exhaustive grep), broker tests | NT-13 passes; `git grep -E 'BOSS_CEILING\|boss_policy\|is_above_ceiling\|write_default_boss_ceiling\|load_boss_ceiling' culture/ tests/` returns ZERO hits |
| 5.3 | (Folded into Phase 4.4 — `mesh grant` is part of the same CC plugin tool registry; no separate task here. Refusal logic per "boss can grant what boss has" stays Phase 5's responsibility because it's perm-broker-coupled.) | `cc_plugin/tools.py` (registered in 4.4); refusal predicate in `_perm_broker.py` | Manual: `mesh grant <worker> Bash 'ls .*'` works when CC has Bash; refused when CC doesn't |
| 5.4 | Bridge `watchdog`-library FS observer (per ext-concern-4 from agent 10 — `watchdog 6.0.0` is the standard cross-platform pick: inotify on Linux, FSEvents on macOS, ReadDirectoryChangesW on Windows). Watches `~/.culture/perm-queue/`; on new file event, emits IPC `perm_request` to CC. Implementation pattern: dedicated thread runs `Observer` with `PatternMatchingEventHandler`; events bridge to the asyncio loop via `loop.call_soon_threadsafe`. **Thread-safety stress test** (review iter-2 C-2 from agent 10 — `watchdog` project documents that "a full thread safety audit has not been completed"): integration test creates 50 perm-queue files in rapid succession; bridge surfaces N IPC events to CC with no drops over a 60s window on macOS + Linux. Fallback to 250ms poll if `watchdog` import fails (graceful degradation, sets `_HAS_WATCHDOG = False`). | bridge | NT-11 passes; perm request from worker surfaces to CC within ~50ms of file creation (vs today's 250ms poll); 50-rapid-create stress test green |
| 5.5 | CC plugin tool `mesh approve <id>` and `mesh deny <id> [reason]`: shell out to `culture boss approve/deny` (preserves O_EXCL race-free pattern in `write_decision`); bridge sees `perm-decisions/<id>.json` via `watchdog` observer and forwards `perm_decision` IPC (informational) | tools registered in 4.4; `culture boss approve/deny` unchanged | Manual: worker requests perm, CC sees PreToolUse-hook reminder, types `mesh approve <id>`, worker continues |
| 5.6 | Replace `_perm_broker._await_decision` 250ms file-poll with the same `watchdog` library pattern as 5.4. The loop structure changes from `while True: try_read; sleep` to `Event-driven asyncio.Future awaiting the watchdog callback`. Fallback to 250ms poll if `watchdog` unavailable (graceful degradation). | `_perm_broker.py:741-776` | Linux + macOS integration tests confirm push path; Windows / minimal-deps path uses poll fallback |
| 5.7 | Remove worker-daemon `_on_perm_request` IRC-DM-to-boss path at `daemon.py:1093-1131` — bridge watchdog observer is the new notification mechanism. **ALSO delete** `tests/test_worker_boss_notice.py::TestOnPermRequest` (lines 181-208). **ALSO delete** `tests/test_perm_broker_on_request.py` entirely. Drop the `on_request` callback parameter from `PermissionBroker.__init__`, the slow-path branch at `_perm_broker.py:685-701`, **AND from `culture/clients/claude/agent_runner.py`** where `PermissionBroker(on_request=...)` is wired, **AND from `AgentRunner.__init__` itself** where `on_perm_request=...` is a constructor parameter at `daemon.py:525` (review iter-4 B-4 from agent 5). Cascade: remove the parameter from `AgentRunner.__init__` signature, drop the daemon.py:525 wiring, drop the storage of the callback attribute, drop any call sites of that stored callback inside `agent_runner.py`. | worker daemon + tests + `agent_runner.py` + `daemon.py:525` | `git grep -E 'on_perm_request|on_request' culture/ tests/` returns ZERO hits across daemon.py, agent_runner.py, _perm_broker.py and tests (allow docs/ + CHANGELOG references) |
| 5.8 | Docs scrub: 4 files in `docs/` still teach the ceiling concept (review iter-1 C-6 from agent 5). Update or delete. `doc-test-alignment` subagent in Phase 7.7 may flag additional sites. | `docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md`, `docs/superpowers/specs/2026-05-28-boss-agent-orchestration-design.md`, and any other doc mentioning grant_ceiling | grep `docs/` for `grant_ceiling\|DEFAULT_BOSS_CEILING\|is_above_ceiling` returns either zero or only intentional historical references with "obsolete" annotations |

**Acceptance gate:** NT-11 + NT-12 + NT-13 green; full-flow test: spawn worker, worker requests a `require_approval` tool, CC sees PreToolUse-hook interrupt, CC approves with `--input-regex`, worker continues. `git grep DEFAULT_BOSS_CEILING culture/ tests/` returns zero matches. The demote-rather-than-fail path is exercised by an integration test (approve `Bash` without `--input-regex` → next gate uses scope=once not always; CC sees the demote notice).

### Phase 6 — Watchdog & resilience (silent-death moves to bridge; worker resilience per plenty)

Closes plenty's P0/P1 partially and addresses RC-1's "watchdog hardening" theme.

| # | Task | File / area | Acceptance |
|---|---|---|---|
| 6.1 | Move `_silent_death_watchdog` from worker boss daemon to bridge | `culture/clients/bridge/daemon.py` + `~/.culture/bridge/silent_death_warned.json` persistence | NT-14 passes |
| 6.2 | Watchdog auto-escalation enrichment (plenty's P1): when `stalled_in_retry_loop` fires in a worker, the worker's broker `_on_request`-style callback now ALSO emits the **failing tool name + input + last exception** to the boss DM channel (not just the bare stall message) | `culture/clients/claude/daemon.py:_stall_message` extended with failure context from `_consecutive_failed_turns` source | Manual: induce a stuck Bash retry loop in a worker, CC sees DM naming the tool + input + error within `STALL_GRACE_SECONDS` |
| 6.3 | Atomic spawn+brief (plenty's P2): `culture boss spawn` accepts `--brief <text>` flag; brief delivery happens IN-spawn (no race window where worker engages-then-idles before brief lands) | `culture/cli/boss.py` | Manual: spawn with `--brief "hello"`; worker's first audit entry includes the brief; no `never_briefed` warning fires |
| 6.4 | Crash-resilient worker checkpointing (plenty's P0b — partial): every `_on_turn_complete`, write `~/.culture/sessions/<nick>.json` with `last_session_id` + cursor; on worker daemon restart, resume from `last_session_id` instead of fresh | `culture/clients/claude/agent_runner.py` extension | Manual: kill worker mid-task, restart, worker continues from last clean turn |
| 6.5 | Update `tests/test_silent_death_watchdog.py` per Test Registry split | tests | green |

**Acceptance gate:** NT-14 green; manual induce-stall test for 6.2; manual atomic-spawn-brief test; manual checkpoint-resume test.

### Phase 7 — Cleanup + docs + dashboard adaptation

| # | Task | File / area | Acceptance |
|---|---|---|---|
| 7.1 | CHANGELOG entry for the rearch with explicit v1 carve-out from the all-backends rule | `CHANGELOG.md` | reviewer reads it |
| 7.2 | `docs/cross-project-usage.md` (was offered earlier — fits here) | docs | published |
| 7.3 | Update `docs/superpowers/specs/2026-04-28-fork-rearchitecture-design.md` (this plan supersedes parts) — header note + cross-link | docs | done |
| 7.4 | Update `~/.claude/skills/culture-boss/SKILL.md` to reflect CC-IS-boss (no more `culture agent start local-boss` — the bridge starts automatically via the plugin on SessionStart; spawn/brief/close CLI verbs unchanged) | skill markdown | skill consistent |
| 7.5 | Dashboard adaptation per AD-5: (a) tree view grouped by boss/project, collapsible per project — **preserves flat `/api/agents` for backward-compat AND adds `/api/agents/tree`** (review iter-1 B-3 from agent 7 — `tests/test_dashboard.py:60-91` asserts the flat-list shape); (b) human chat panel — the human's nick can DM any agent on the mesh; messages route through the bridge using the human's nick as PRIVMSG source; (c) SSE endpoints replacing the 3 polling intervals at `app.js:840-842` (`/api/agents/stream`, `/api/pending/stream`, `/api/channels/stream` — pattern proven by existing `/api/agents/<nick>/log/stream` SSE); (d) read bridge state (not boss-daemon state); `is_boss` flag continues to work via `tags: [boss]` on manifest; `_is_idle` resolves to bridge daemon-log full-tail (RC-8 lesson: v8.17.3 fixed-tail coupling) + CC plugin's `cc_connected` flag | `culture/dashboard/server.py`, `culture/dashboard/static/app.js`, `culture/dashboard/static/index.html` | `tests/test_dashboard.py` flat-list tests still pass; new `tests/dashboard/test_human_chat.py` covers DM-from-dashboard; new `tests/dashboard/test_tree_view.py` covers `/api/agents/tree`; SSE endpoints stream events within 200ms |
| 7.6 | Open follow-up issues for: (a) all-backends propagation of CC-IS-boss for codex/copilot/acp (Rule 4 deferral); (b) inotify replacement for `_perm_broker` (cross-platform); (c) multi-CC-session-per-host support | GitHub issues | each linked from CHANGELOG |
| 7.7 | `doc-test-alignment` subagent run on the branch diff | per CLAUDE.md | report clean |

**Acceptance gate:** all preceding phases green; dashboard test rewrites pass; doc-test-alignment reports no gaps.

---

## Self-Review Ledger (Step 8.5)

**Review iteration 1 (workflow `wchc0m105`): 19 blockers + 51 concerns + 77 notes folded.** Specifically: EL-1/EL-5/EL-10/EL-12 line-range corrections; `_idle_watchdog` / `_watchdog_tick` split; `BareStickyApproveRefusedError` creation tasked in Phase 5.1a; Phase 4 architecturally pivoted to user-settings-scoped hooks (CC bug #16538) with Stop-hook `decision:block` for end-of-turn queue drain and PreToolUse hook for perm interrupts; v8.19.42 carryforward to `claude/irc_transport.py` AND `packages/agent-harness/` in Phase 0.5; macOS peercred ctypes shim in Phase 0.6; `watchdog 6.0.0` library for Phase 5.4 + 5.6; T1 mitigation rewritten for AD-7 + multi-CC; T11 added for CVE-2026-35021/35022 supply-chain risk; `boss.py:947` second `#team` site captured; ceiling-coupled tests moved from KEEP to DELETE bucket; `_dispatch` consumer cite corrected (no `_cmd_handlers` symbol); Phase 7.5 keeps flat `/api/agents` for backward-compat + adds `/api/agents/tree` + 3 SSE endpoints; `_worker_class_acl` uses `_load_owner_map` (tags unavailable at JOIN); RC-7/RC-8/RC-9 added.

**Re-review pending — iteration 2 will execute the cross-pass discipline gates** (risk-class git, multi-callsite 5a+5b, external-issue lookup) plus pattern re-runs on the revised plan.

Patterns run by the synthesis workflow against this plan's draft:

- **Pattern 1 (writer/reader divergence):** clean — every state key in Behavior-Tracing Matrix table has a single canonical writer.
- **Pattern 5 (multi-callsite enumeration):** clean for `AgentRunner` (5 callsites enumerated), `on_mention` (1 + 6 mocks), `gate()` (1).
- **Pattern 9 (cross-layer source-of-truth):** authoritative layers named for every state concept.
- **Pattern 11 (spec-vs-impl alignment):** all 7 binding rules have enforcing tasks; gaps marked complete.
- **Pattern 13 (load-bearing literals):** 14+ literals catalogued with producer/consumer.
- **Pattern 7b (step-ordering build-break check):** Phase 5.1 BEFORE 5.2 explicitly enforced; Phase 2 manifest-invariant before bridge accepts SDK events.
- **Pattern 14 (nullable SQL gate):** N/A — no SQL gates in this plan; only the new `dm_spool` which uses `delivered_at IS NULL` semantically (NULL means "not yet drained", not a security gate).

**Findings I consciously accept as residual:**

- **UA-2 (mid-conversation system-reminder support)** is the only unverified-but-load-bearing assumption. If CC's plugin SDK doesn't expose a clean end-of-turn hook (Stop / PreCompact), Phase 4.4 needs a polling fallback OR Phase 5.4's interrupt-priority pattern extends to ALL inbound (back to AD-1's pick being the only path). Decision deferred to Phase 4 spike.
- **Dashboard frontend polling (kept per AD-3)** is a violation of Rule 7 (push everywhere) acknowledged as a known follow-up. Frontend SSE rewrite is Out of Scope for this plan.
- **`_silent_death_watchdog` polling (kept per synthesis topic 2 finding 13)** is documented intentional — PID-liveness has no push primitive across processes. Same finding kept the existing watchdog poll out of scope for the push-everywhere binding.

**Scope check:** No scope-drift between the user's binding (R1–R7) + 5 architectural decisions (AD-1 through AD-5) + this plan's 7 phases. Every phase trace-links to either a binding rule or an architectural decision. Phase 0 is the only phase not directly tied to a rule (it's plenty's P1 + minor housekeeping); kept because it's a 1-day phase that unblocks every future operator and the user explicitly asked for "everything thrown at me."

---

## Phase 9 (skill workflow) — Human approval gate

**Status: awaiting approval.** No code lands until you confirm:

1. The plan's structure / order is right (or you redirect a phase).
2. The 5 Architectural Decisions (AD-1 through AD-5) are acceptable (override any).
3. UA-1 through UA-5 are acceptable risks to verify in-phase rather than gate now.

Once approved, implementation uses `/implement docs/superpowers/specs/2026-06-03-mesh-rearchitecture-plan.md`. The implement skill takes ownership of phase sequencing, quality gates, deviation handling, and test execution order.
