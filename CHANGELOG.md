# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [10.1.0] - 2026-05-05

### Added

- **`culture console` now detects same-port conflicts before invoking irc-lens.** When the web port (default `8765`) is already bound by a culture-owned console, the CLI looks at the recorded pidfile/sidecar instead of letting irc-lens fail with `[Errno 98] address already in use`:
  - **Same target, same port** → prints `culture console is already running for 'spark' at http://127.0.0.1:8765/` and exits `0`. No more spurious "port busy" when you re-run from a second terminal.
  - **Different target** → prints a 3-bullet hint (open the existing URL, `culture console stop && culture console <new>`, or `--web-port 8766` for side-by-side) and exits `1`. Culture never auto-kills another console — that decision stays with you.
  - **Foreign irc-lens without a pidfile** → prints a recovery hint pointing at `ss`/`lsof` so you can find and stop the owner manually.
  - **Foreign non-irc-lens owner** → falls through to irc-lens's own `cannot bind web port` error, the right message for arbitrary processes.
  - **Stale pidfile** (dead PID, or PID belongs to a non-culture process) → silently cleaned up before the new console starts.
- **`culture console stop`** — culture-owned verb that gracefully stops the locally-running console (`SIGTERM` with a 5-second grace, `SIGKILL` fallback). Idempotent on a missing pidfile, refuses to signal a process whose `/proc/<pid>/cmdline` doesn't include "culture".
- **State files under `~/.culture/pids/`** — `console.pid`, `console.port`, and a `console.json` sidecar (with `pid`, `server_name`, `nick`, `host`, `irc_port`, `web_port`) are written at start and removed at exit. They reuse the same layout every other culture daemon (servers, agents) already uses, so future tooling that lists culture-owned processes can include the console for free.
- **`docs/reference/cli/console.md`** — full reference for the new conflict UX, the `stop` verb, and the on-disk state layout.

## [10.0.2] - 2026-05-05

### Changed

- **Restructured public-facing docs** — README leads with the canonical positioning paragraph; new `docs/culture/ecosystem-map.md` page renders the AgentCulture org map from two YAML data files (`_data/agentculture_repos.yml`, `_data/culture_subcommands.yml`).
- **Documented planned rename of `culture afi` → `culture contract`** in all public copy. The CLI still exposes `culture afi` as a passthrough to `afi-cli` today; the rename itself ships in a later release.

### Added

- **Org-wide repo registry** at `_data/agentculture_repos.yml` with category, maturity, description, package, and docs link for every public repo in `agentculture/`.
- **Subcommand registry** at `_data/culture_subcommands.yml` with `ready`/`planned` status and the sibling repo backing each `culture <verb>`.
- **Reference doc** at `docs/resources/registry.md` documenting the schemas and naming steward + ghafi as registry-hygiene owners.

## [10.0.1] - 2026-05-05

### Changed

- **`culture mesh update` now streams uv/pip output** instead of capturing it. A long-running upgrade (e.g. fresh major-version install pulling in `claude-agent-sdk` ~73 MiB plus the OpenTelemetry stack) is no longer indistinguishable from a hang — uv's progress bars and download messages show through to the terminal in real time.
- **Default upgrade timeout raised from 120s to 600s.** The old ceiling spuriously fired mid-download on slow links during major-version bumps even though the upgrade was making progress.

### Added

- **`culture mesh update --upgrade-timeout SECONDS`** flag to override the default 600s ceiling for sites with very slow or very fast networks.
- **Expanded timeout hint.** When the upgrade does time out, the CLI now suggests three recovery paths instead of one: run `uv tool upgrade culture` (or `pip install --upgrade culture`) directly, rerun with a larger `--upgrade-timeout`, or `--skip-upgrade` to restart services without upgrading.

## [10.0.0] - 2026-05-05

### Changed (breaking)

- **Reverted `culture chat` → `culture server`.** The IRC-mesh subcommand is `culture server` again. The brief 9.0.0 detour through `culture chat` conflated server lifecycle (`start` / `stop` / `status` / `default` / `rename` / `archive` / `unarchive`) with chat operations — they're distinct nouns. `culture server` is now the canonical home for both the lifecycle verbs and the agentirc passthrough verbs (`restart` / `link` / `logs` / `version` / `serve`). **Migration:** replace any `culture chat <verb>` invocation in scripts, skills, and service definitions with `culture server <verb>`. The 9.1.0 stderr deprecation warning on `culture server` already promised this would land in 10.0.0.

### Removed (breaking)

- **`culture chat` is gone.** Argparse rejects it with `invalid choice`. There is no deprecation alias this round; the 9.1.0 → 10.0.0 cycle is the migration window.

### Added

- **`communicate` skill** — vendored from [agentculture/steward](https://github.com/agentculture/steward) (per [#324](https://github.com/agentculture/culture/issues/324)). Ships under `culture/skills/communicate/` and is materialized into each backend's skills root by `culture skills install <backend>` alongside the existing `irc` and `culture` skills. Two scripts: `post-issue.sh` (cross-repo GitHub issues, auto-signed `- culture (Claude)`) and `mesh-message.sh` (Culture mesh channel messages, unsigned — IRC nick is the speaker).
- **`culture agent learn` references the vendored skill.** A new "Cross-Repo + Mesh Communication via Vendored Skill" section in the learn prompt points at `<skill-dir>/communicate/scripts/{post-issue,mesh-message}.sh` and explains how the vendored skill (signs as `- culture (Claude)`) differs from the per-agent walkthrough (signs as `- <nick> (<harness>)`).

## [9.1.0] - 2026-05-05

### Added

- **`culture console`** — top-level passthrough wrapper around [`irc-lens`](https://github.com/agentculture/irc-lens), the reactive web console for AgentIRC. `culture console <server>` resolves the culture server name to host/port/nick before delegating; explicit irc-lens verbs (`serve`, `learn`, `explain`, `overview`, `cli`) flow through unchanged. Universal verbs (`culture explain console`, `culture overview console`, `culture learn console`) wire through `culture.cli._passthrough.register_topic`. Spec: `docs/superpowers/specs/2026-05-05-culture-console-design.md`. Subprocess-vs-in-process tradeoff tracked in [#322](https://github.com/agentculture/culture/issues/322).

### Deprecated

- **`culture mesh console`** — emits a stderr warning and forwards to `culture console`. Removal target: 10.0.
- **`culture/console/` Textual TUI package** — module-level `DeprecationWarning`. Removal target: 10.0.

## [9.0.0] - 2026-05-04

### Changed (breaking)

- **Renamed `culture server` → `culture chat`.** The new noun fits what culture actually is — an agent chat mesh, not a generic server. The 7 culture-owned verbs (`start`, `stop`, `status`, `default`, `rename`, `archive`, `unarchive`) keep their behavior under the new noun. New agentirc verbs (`restart`, `link`, `logs`, `version`, `serve`) land under `culture chat` for free via a passthrough into `agentirc.cli.dispatch`. The legacy `culture server` keeps working through 9.x as a deprecation alias that prints a stderr warning and routes to the same handlers; removed in 10.0.0.
- **Bundled IRCd deleted.** `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py`, `culture/agentirc/skills/`, `culture/agentirc/__main__.py` are gone. Culture now embeds `agentirc.ircd.IRCd` from the [`agentirc-cli`](https://pypi.org/project/agentirc-cli/) PyPI package directly (the path A2 set up). ~3,800 LOC of bundled fork removed.
- **`python -m culture.agentirc` is removed.** Use `agentirc` (CLI) or `python -m agentirc` instead. Reachable through `culture chat <verb>` for the lifecycle verbs.
- **`culture/agentirc/{client,remote_client}.py` moved to `culture/transport/`** (`git mv` preserves blame). Code that imported them under the old path needs to update — `culture/bots/*` and `culture/clients/*/daemon.py` are already updated.
- **`parse_room_meta` moved to `culture/clients/shared/rooms.py`.** All four client daemons (`claude`, `codex`, `copilot`, `acp`) updated to import from the new location. The function lived in the bundled `culture/agentirc/rooms_util.py` before A3.
- **Test harness migrated to `agentirc.ircd.IRCd`.** `tests/conftest.py`, `tests/telemetry/*`, and the bot/event/welcome/transport-tag tests now construct the public class instead of the bundled fork. Agentirc-internal tests (federation, rooms, threads, history, skills, events_*) deleted from culture's test tree — they live in agentirc's repo now.

### Kept (transitional)

- **`culture/agentirc/config.py`** remains as a re-export shim over `agentirc.config` through the 9.x line; removed in 10.0.0. Existing `from culture.agentirc.config import ServerConfig` keeps resolving.
- **`culture/agentirc/__init__.py`** re-exports `ServerConfig` / `LinkConfig` / `TelemetryConfig` from the same shim, so `from culture.agentirc import ServerConfig` keeps working.
- **`culture/agentirc/docs/`** is the source for the AgentIRC section of culture.dev — CI still copies it to `docs/agentirc/` before the Jekyll build. Can be revisited once culture's docs and agentirc's own docs visibly diverge.

### Migration notes for users

- Skills, scripts, and agent prompts that say `culture server` keep working with a stderr warning. Update them when you next touch them.
- Code importing `culture.agentirc.{ircd,channel,events,...}` is broken. There is no replacement under `culture.*`; depend on `agentirc-cli` and import from `agentirc.*` directly.
- Code importing `culture.agentirc.{client,remote_client}` — switch to `culture.transport.{client,remote_client}`.
- Code importing `culture.agentirc.rooms_util.parse_room_meta` — switch to `culture.clients.shared.rooms.parse_room_meta`.
- Code importing `culture.agentirc.config` — still works through the shim; remove in 10.0.0.

### Notes

- Final phase of the agentirc extraction. A1 (config dataclasses, 8.8.0, #309) and A2 (bot framework rewrite, 8.10.0, #319) preceded this. Spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md`. Plan: `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a3.md` plus the verb-ownership and noun-rename decisions from this PR.
- Pre-existing bot-load race ([#317](https://github.com/agentculture/culture/issues/317)) is independent of this PR; tracked for a follow-up patch.

## [8.10.0] - 2026-05-03

### Changed

- `culture server start` now embeds `agentirc.ircd.IRCd` in-process directly, via the public class promoted in agentirc 9.6.0 ([agentculture/agentirc#22](https://github.com/agentculture/agentirc/issues/22) / PR #23). The bundled IRCd in `culture/agentirc/` is now orphaned in the production path; it is reached only by the test suite and is slated for deletion in Phase A3.
- Rewrote `culture/bots/virtual_client.py` as a thin subclass over the public `agentirc.virtual_client.VirtualClient` (also promoted in agentirc 9.6.0). The 231-LOC file collapsed to 31 LOC; user-visible bot behavior (channel JOINs, `@`-mention notices, `send_dm`, `broadcast_to_channel`) is unchanged. Culture-specific glue (BotConfig, template engine, `fires_event` chaining, owner DM) lives in `culture/bots/bot.py`, not in VirtualClient.
- Webhook listener (`webhook_port`) ownership moved from the bundled `culture/agentirc/ircd.py` to `culture/bots/bot_manager.py`. agentirc 9.5+ stopped binding the port itself; consumers (culture) now own the listener. `BotManager` gains `start()` / `stop()` lifecycle methods that wrap bot loading and HttpListener bind/unbind.
- `_run_server` in `culture/cli/server.py` wraps the post-`ircd.start()` body in a `try/finally` so `bot_manager.stop()` and `ircd.stop()` always run on shutdown — even if bot teardown raises.
- Bumped dep floor `agentirc-cli>=9.4,<10` → `agentirc-cli>=9.6,<10`.
- `Event` / `EventType` imports retargeted from `culture.agentirc.skill` to `agentirc.protocol` in the three sites that survive Phase A3 (`culture/bots/virtual_client.py`, `culture/bots/bot.py`, `culture/telemetry/audit.py` — TYPE_CHECKING). The bundled `culture.agentirc.skill` re-exports stay intact for the test suite, which still drives the bundled IRCd via `tests/conftest.py` until A3.

### Notes

- Plan: `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a2.md`. Spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md` ("Implementation status" table, A2 row).
- One deviation from the canonical plan: Task 10 ("Test harness migration" — switching `tests/conftest.py` and bot tests from the bundled IRCd to `agentirc.ircd.IRCd`) is **deferred to Phase A3**. The full test suite (1157 tests) passes unchanged on the bundled IRCd, which is structurally compatible with the public class — migrating now would be diff churn without behavioral risk reduction. The smoke test exercises the production agentirc IRCd + culture BotManager path end-to-end.
- A pre-existing bot-load race (`Bot.start()` "Nick already in use" when one bot's `user.join` event triggers another bot's lazy-start during `BotManager.load_bots`) was surfaced in A2's smoke test and tracked at [#317](https://github.com/agentculture/culture/issues/317) for a follow-up patch. The error log is misleading but bot behavior is unaffected; this race exists identically on the bundled IRCd path and was not introduced by A2.

## [8.9.0] - 2026-05-03

### Changed

- Renamed the `coordinate` skill to `communicate` and broadened its scope to cover the agent's full communication toolkit — both in-mesh chat (`culture channel` CLI: message, read, who, ask, join, part, list, plus collaboration patterns @mentions / `[FINDING]` / `#general` / `#knowledge` / `#ops` / DMs) AND cross-repo hand-off briefs (`post-issue.sh`). The two surfaces now share one SKILL.md with a decision table for picking between them. Same `post-issue.sh` script (now at `.claude/skills/communicate/scripts/post-issue.sh`); same auto-signature `- culture (Claude)`; same conventions for self-contained briefs and title format.
- `culture agent learn` (and the top-level `culture learn`) prompt now ends with a "Cross-Repo Coordination" section that teaches the `communicate` skill — when to use in-mesh chat vs cross-repo issues, the `post-issue.sh` invocation, and the don't-double-post rule. The `learn` CLI verb itself is unchanged: it remains the agent self-teaching prompt; what changed is what it teaches (now covers both halves of agent communication).
- `CLAUDE.md` "Sibling alignment" section updated to reference `communicate` instead of `coordinate` in the example skill list.

### Migration

- `bash .claude/skills/coordinate/scripts/post-issue.sh ...` invocations need to be updated to `bash .claude/skills/communicate/scripts/post-issue.sh ...`. No other interface change — same flags, same signature, same behavior. No back-compat shim at the old path; if scripts in your environment still reference `coordinate/`, they'll fail.

### Notes

- This is a SKILL rename + scope expansion, not a CLI rename. `culture agent learn` and `culture learn` keep their names — the user clarified that "learn is for agents to learn how to use" and the skill name "communicate" is the right noun for the broader communication toolkit. The CLI verb teaches the skill; both names are now correct for what they do.
- The rename mirrors steward 0.8's `coordinate` → `communicate` rename. Culture's signature literal stays `- culture (Claude)`.

## [8.8.1] - 2026-05-03

### Changed

- Renamed `.claude/skills/pr-review/` → `.claude/skills/cicd/` to align with steward 0.7's skill-name convention (per [agentculture/culture#314](https://github.com/agentculture/culture/issues/314)). The `name:` field in `SKILL.md` becomes `cicd`; the description preserves the existing trigger phrases ("create PR", "review comments", "address feedback", "resolve threads") plus adds "use pr-review" / "use cicd" so existing automation keeps resolving here. The script names inside (`create-pr-and-wait.sh`, `wait-and-check.sh`) stay as-is.
- Doc references to the old skill name updated across `coordinate/SKILL.md` (4 mentions), the in-flight A2/A3 extraction plans (3 mentions), `docs/culture/reflective-development.md`, and `docs/superpowers/specs/2026-04-08-reflective-development-deepening-design.md`. Forward-facing slash-command references in three historical plans (`mesh-events.md`, `otel-foundation.md`, `agentirc-extraction-track-a.md`) updated to `/cicd` with an inline note about the rename, so future readers don't get mixed instructions. CHANGELOG entries about historical PRs that mentioned `pr-review` at the time are intentionally not rewritten (those are diffs of past releases). Global-fallback paths (`~/.claude/skills/pr-review/scripts/pr-status.sh`) in three older otel plans are intentionally preserved — those refer to the user's global skills directory which still uses the old name.
- The two project-local scripts (`create-pr-and-wait.sh`, `wait-and-check.sh`) retain their `~/.claude/skills/pr-review/scripts/pr-comments.sh` global fallback — steward's rename is in-repo only, the user's global skills directory still holds the trio (`pr-comments.sh`, `pr-reply.sh`, `pr-batch.sh`) under the old name. Header comments note this lag.

### Added

- `## Sibling alignment` section in project `CLAUDE.md` pointing at [agentculture/steward](https://github.com/agentculture/steward) as the alignment hub for AgentCulture skills (per culture#314 Ask 2). New contributors editing skills here now have an in-repo signal that there's a sibling tracking skill evolution.

## [8.8.0] - 2026-05-01

### Added

- `agentirc-cli>=9.4,<10` runtime dependency — the IRCd config dataclasses (`ServerConfig`, `LinkConfig`, `TelemetryConfig`) are now sourced from the published `agentirc-cli` PyPI distribution (imported as `agentirc.config`) instead of culture's local copy. First step of the Track A cutover described in [agentculture/culture#308](https://github.com/agentculture/culture/issues/308).

### Changed

- `culture/agentirc/config.py` is now a re-export shim over `agentirc.config` — `from culture.agentirc.config import ServerConfig` continues to work and resolves to the same class as `from agentirc.config import ServerConfig` (no parallel-dataclass identity hazard). The shim will be deleted alongside the rest of `culture/agentirc/` in Phase A3 once the bot-runtime story is settled (see agentculture/agentirc#15).
- Canonical call sites retargeted to import directly from `agentirc.config`: `culture/cli/shared/mesh.py`, `culture/config.py`, `culture/telemetry/{metrics,tracing,audit}.py`, `tests/conftest.py`. Bot-runtime call sites (`culture/bots/*`, `culture/agentirc/ircd.py`, `culture/cli/server.py:_run_server`, `culture/clients/*/daemon.py`) intentionally retain their `culture.agentirc.*` imports — those reach internal IRCd/Event/parse_room_meta surfaces that are not yet exposed in agentirc's public API.

### Removed

- Bootstrap-era `agentirc-cli` and `agentirc` alias publish steps from `.github/workflows/publish.yml`. `agentirc-cli` is now owned by [agentculture/agentirc](https://github.com/agentculture/agentirc) and published from there; culture's TestPyPI/PyPI workflow no longer co-publishes itself under those names (the OIDC trust policy was transferred along with the package). Culture continues to publish itself as `culture` on both indices.
- `agentirc-cli` / `agentirc` fallback chain in `culture/__init__.py` `__version__` resolution. The fallback would silently report agentirc's installed version as culture's version now that the alias names refer to a separate package.

## [8.7.1] - 2026-04-27

### Added

- `tests/test_socket_path_convergence.py`: parametric regression test asserting all 8 daemon+skill resolvers agree with the CLI's `agent_socket_path()` for both `XDG_RUNTIME_DIR` set and unset.
- `tests/test_constants.py`: unit tests pinning `culture_runtime_dir()` contract (env precedence, `~/.culture/run/` fallback, mode 0700).

### Changed

- `culture channel {message,list,read}`: when `CULTURE_NICK` is set but the daemon IPC is unreachable or rejects the request, the CLI now prints a stderr warning naming the nick, the socket path, and the GitHub issue tracker before falling back to the peek-nick observer. The fallback itself is preserved for human use (no `CULTURE_NICK`).

### Fixed

- Converged all socket-path resolvers (4 backend daemons, 4 skill irc_clients, overview collector, harness package reference impl) on `culture_runtime_dir()` so the CLI and daemon agree on the IPC socket path when `XDG_RUNTIME_DIR` is unset (macOS default). Previously the daemon listened on `/tmp/culture-<nick>.sock` while the CLI looked in `~/.culture/run/`, causing `culture channel message` to silently fall back to an anonymous `_peek<random>` nick (#302, regression of #203).

## [8.7.0] - 2026-04-26

### Added

- OTEL bot instrumentation (Plan 7): bot.event.dispatch and bot.run spans, culture.bot.invocations counter, culture.bot.webhook.duration histogram, aiohttp-server auto-instrumentation on the webhook listener.

### Changed

- docs/agentirc/telemetry.md updated with the 8.7.0 section; harness-telemetry.md cross-link updated.

## [8.6.0] - 2026-04-26

### Added

- Harness-side OTEL: 3 spans (harness.irc.connect, harness.irc.message.handle, harness.llm.call) and 4 LLM metrics (culture.harness.llm.tokens.input/output, call.duration, calls).
- W3C traceparent injection on outbound IRC + extraction on inbound — single trace_id now spans server, federation, and harness in the cross-process tree.
- Per-backend telemetry citation across claude/codex/copilot/acp with all-backends parity test (24 tests across 6 dimensions) locking down drift.
- docs/agentirc/harness-telemetry.md — new operator guide for the harness OTEL pillar.

### Changed

- packages/agent-harness/{telemetry.py,config.py,culture.yaml,irc_transport.py,daemon.py} — reference module for the citation pattern.
- culture/clients/{claude,codex,copilot,acp}/{telemetry.py,config.py,culture.yaml,irc_transport.py,daemon.py,agent_runner.py} — telemetry citation, harness.llm.call span wrap, record_llm_call invocation.
- tests/harness/ — 70 new tests (24 parity + 46 module/runner/transport/daemon).

### Fixed

- Code-quality fixes from review: zero-token usage extraction (0 no longer silenced), tracer-name from constant (no hardcoded strings), module-top imports of record_llm_call across all 4 backends.

## [8.5.0] - 2026-04-25

### Added

- `culture/telemetry/audit.py` — `AuditSink` with bounded `asyncio.Queue` + dedicated writer task + daily/size rotation + `0600`/`0700` perms.
- Public `culture.telemetry.AuditSink`, `init_audit`, `build_audit_record`, `utc_iso_timestamp`.
- `TelemetryConfig.audit_enabled` (default `True`), `audit_dir`, `audit_max_file_bytes`, `audit_rotate_utc_midnight`, `audit_queue_depth` — independent of `telemetry.enabled` (audit fires even with OTEL off).
- `culture/protocol/extensions/audit.md` — JSONL record schema as a stable contract.
- `docs/agentirc/audit.md` — operator guide.
- Audit metrics extend the Plan-3 `MetricsRegistry`: `culture.audit.writes` (Counter, labels `outcome=ok|error`) and `culture.audit.queue_depth` (UpDownCounter).
- `IRCd.__init__` creates the sink; `IRCd.start()` awaits `sink.start()`; `IRCd.stop()` awaits `sink.shutdown()` so SERVER_WAKE / SERVER_SLEEP both land in the JSONL.
- `IRCd.emit_event` submits one record per event after the `irc.event.emit` span; `trace_id` / `span_id` captured inside the span for cross-pillar joins.
- `Client._process_buffer` submits `PARSE_ERROR` records for malformed inbound lines.
- Federation audit: federated `message` events arrive on the receiver with `origin=federated`, `peer=<peer_name>`. Federated lifecycle events (JOIN/PART/QUIT) are deferred — see #296.

## [8.4.0] - 2026-04-25

### Added

- `culture/telemetry/metrics.py`: `init_metrics(config)` + `MetricsRegistry` dataclass for all 15 server-side instruments — mirrors `tracing.py`'s idempotency + no-op pattern.
- Public `culture.telemetry.MetricsRegistry` and `culture.telemetry.init_metrics`.
- `TelemetryConfig.metrics_enabled` (default `True`) and `metrics_export_interval_ms` (default 10000).
- Message-flow metrics: `culture.irc.bytes_sent`, `culture.irc.bytes_received`, `culture.irc.message.size`, `culture.privmsg.delivered`.
- Events metrics: `culture.events.emitted`, `culture.events.render.duration`.
- Federation metrics: `culture.s2s.messages` (inbound), `culture.s2s.relay_latency`, `culture.s2s.links_active`, `culture.s2s.link_events`.
- Client metrics: `culture.clients.connected`, `culture.client.session.duration`, `culture.client.command.duration`.
- `culture.trace.inbound` counter — closes Plan 2's deferral.
- `tests/conftest.py` `metrics_reader` fixture parallel to `tracing_exporter`.
- `tests/telemetry/_metrics_helpers.py` — `get_counter_value`, `get_histogram_count`, `get_up_down_value`.

## [8.3.0] - 2026-04-25

### Added

- `irc.s2s.session` span over ServerLink connection lifetime.
- `irc.s2s.<VERB>` per-verb spans on inbound federation messages with traceparent extraction and the inbound mitigation rules from `culture/protocol/extensions/tracing.md`.
- `irc.s2s.relay` span on outbound relay enforcing the re-sign-per-hop rule.
- `irc.client.session` span over Client connection lifetime (#290).
- `irc.join` and `irc.part` spans (#290).
- Public `culture.telemetry.context_from_traceparent` and `culture.telemetry.current_traceparent` helpers.
- Single traceparent injection choke point at `ServerLink.send_raw`.
- End-to-end propagation tests proving one `trace_id` spans federated client → server → relay → server hops.

### Changed

- `Client._dispatch` span name and `irc.command` attribute now uppercase, matching `ServerLink._dispatch` convention.

### Fixed

- `_replay_event` uses the hasattr-guarded comparison so string-typed federated `event.type` no longer skips the typed fast path. (#291)

## [8.2.0] - 2026-04-24

### Added

- OpenTelemetry foundation: `culture/telemetry/` package with TracerProvider bootstrap, W3C trace context extract/inject helpers for IRCv3 tags, and `TelemetryConfig` block in `server.yaml`.
- Protocol extension: `culture.dev/traceparent` and `culture.dev/tracestate` IRCv3 tags (`culture/protocol/extensions/tracing.md`).
- Server-side tracing: `IRCd.emit_event`, `Client._dispatch`, `Client._process_buffer` (with parse-error compensation), and PRIVMSG dispatch/delivery paths now emit spans.
- Outbound traceparent injection on `Client.send` and `Client.send_raw` when a span is active.
- Operator docs at `docs/agentirc/telemetry.md` and starter collector config at `docs/agentirc/otelcol-template.yaml`; `docs/reference/server/config.md` documents the new `telemetry` block.
- Dependencies: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-semantic-conventions`.

## [8.1.0] - 2026-04-23

### Added

- `culture afi` namespace — passthrough to the standalone `afi-cli` (Agent First Interface scaffolder) with argparse-REMAINDER argument forwarding, parallel to `culture devex`.
- Universal verbs register the `afi` topic; `culture explain afi` / `culture overview afi` / `culture learn afi` all route through `afi-cli` 0.3+ and `culture explain` no longer marks `culture afi` as coming-soon.
- `afi-cli>=0.3,<1.0` as a library dependency (0.3 added the `overview` verb + sixth rubric bundle per agentculture/afi-cli#5).
- `culture/cli/_passthrough.py` — shared plumbing for `culture <ext>` subcommands that embed a sibling CLI. Supplies `run()`, `capture()`, and `register_topic()` so each passthrough module stays a thin adapter.

### Changed

- `culture/cli/devex.py` refactored onto the new shared passthrough helper. Behaviour is preserved (explain/overview/learn argv and typer app invocation unchanged); the module shrinks to a package-specific entry adapter and a single `register_topic` call. When agex-cli adopts the agent-first CLI contract (`main(argv) -> int`, tracking: agentculture/agex-cli#30), the adapter collapses to a direct delegation.

## [8.0.0] - 2026-04-22

### Added

- `culture afi`, `culture identity`, and `culture secret` surfaced as (coming soon) namespaces in `culture explain` output. `culture identity` will wrap a future standalone `zehut-cli`; `culture secret` will wrap `shushu-cli`; `culture afi` shares its name with the standalone `afi-cli`.

### Changed

- BREAKING: Renamed `culture agex` to `culture devex` (developer experience) — more familiar terminology and visually distinct from `culture agent`. The upstream `agex-cli` / `agent_experience` library is unchanged; only culture's public command name differs.
- Positioning docs, README, and site chrome refreshed to reflect the new identity: culture is the framework of agreements that makes agent behavior portable, inspectable, and effective, surfacing explain/overview/learn at every CLI level.

## [7.4.0] - 2026-04-22

### Added

- culture agex passthrough to the standalone agex CLI (new subcommand embedding agex via typer)
- Universal verbs culture explain, culture overview, culture learn at the CLI root, with a per-topic handler registry (culture/cli/introspect.py)
- agex-cli>=0.13,<1.0 as a library dependency

## [7.3.0] - 2026-04-21

### Added

- `sitemap-agentirc.html`: Liquid-templated sub-sitemap emitted at `/agentirc/sitemap.xml`, listing only pages under `/agentirc/*`. `sitemap.xml` index (from 7.2.3) extended to list it alongside `/sitemap-main.xml` and `/agex/sitemap.xml`.
- AgentIRC section in the main just-the-docs nav (`has_children: true`, `nav_order: 10`) grouping the four runtime pages.

### Changed

- Fold AgentIRC into the main Culture Jekyll build under `/agentirc/*` permalinks. Retire `_config.agentirc.yml` and the separate `_site_agentirc/` target. Completes the one-origin consolidation plan: `culture.dev`, `culture.dev/agex/`, and `culture.dev/agentirc/` all live on a single origin now.

### Removed

- `_config.agentirc.yml` — no longer a separate Jekyll build target.
- `_plugins/site_filter.rb` — dropped the per-build `sites:` filter; single-build semantics make it a noop. Existing `sites:` front matter on docs is now harmless metadata.
- `.github/workflows/docs-check.yml`: second Jekyll build step and the `sites:` front-matter presence check — single build, no filter.
- `.gitignore` and `_config.base.yml`: `_site_agentirc/` entries.
- `rel=related` links to `site.data.sites.agentirc` in `_includes/head_custom.html` — same-origin now, no longer semantically meaningful; agex `rel=related` retained.

### Fixed

- `docs/culture/features.md` and `docs/culture/index.md`: eliminated double-slash bug from `{{ site.data.sites.agentirc }}` concatenations (the data value now ends in a trailing slash from culture 7.2.1) by switching those cross-references to same-origin `relative_url` links.

## [7.2.3] - 2026-04-21

### Added

- `sitemap.xml` (sitemap index, gated on `site.build_site`).
- `sitemap-main.xml` (enumerates only HTML pages in the current build; opts out via `sitemap: false` front matter).

### Changed

- Replace `jekyll-sitemap` auto-generation with an explicit sitemap index (`/sitemap.xml`) plus a Liquid-templated sub-sitemap (`/sitemap-main.xml`). On the culture build the index points at `culture.dev/sitemap-main.xml` + `culture.dev/agex/sitemap.xml`; on the agentirc build it points at `agentirc.dev/sitemap-main.xml`. Sets the stage for adding `culture.dev/agentirc/sitemap.xml` once AgentIRC folds under `culture.dev/agentirc` (Phase 4 of the one-origin consolidation).

## [7.2.2] - 2026-04-21

### Changed

- Supersedes PR #274, which tried to solve cross-site flicker via `preconnect` hints across the three-subdomain topology. The one-origin consolidation (`culture.dev/agex`, `culture.dev/agentirc`) makes those preconnects moot; only the dark-paint CSS and `aux_links_new_tab` change carry forward.

### Fixed

- `_includes/head_custom.html`: add `color-scheme` meta + inline dark-paint `<style>` to eliminate the white flash on cold-cache first paint.
- `_config.culture.yml` + `_config.agentirc.yml`: `aux_links_new_tab: false` so sibling-site clicks stay in-tab (one-site feel after the consolidation). Power users can still Ctrl/Cmd-click for a new tab.

## [7.2.1] - 2026-04-21

### Changed

- `_data/sites.yml`: agex URL → `https://culture.dev/agex/` (was `https://agex.culture.dev`)
- `_config.culture.yml` + `_config.agentirc.yml`: aux_links and footer_content agex links retargeted to `https://culture.dev/agex/`

## [7.2.0] - 2026-04-18

### Added

- Console: full CommonMark markdown rendering in chat panel — bold/italic/inline code/strikethrough, OSC 8 hyperlinks, fenced code blocks with syntax highlighting, headings, lists, blockquotes, and tables (#233)
- Console: docs/reference/console.md reference page covering chat-panel markdown rendering

### Changed

- Console: ChatPanel.add_message now writes a header line ([ts] icon nick:) followed by a Rich Markdown body, instead of a single Rich-markup string — this also closes a latent footgun where bracketed text in agent messages could be reinterpreted as Rich markup

## [7.1.5] - 2026-04-18

### Added

- `jekyll-redirect-from` plugin (Gemfile + `_config.base.yml`) so `/why-culture/` redirects cleanly to `/what-is-culture/` (#267)
- `docs/resources/positioning.md`: canonical positioning snippets (paragraph + reference points + usage notes). Source of truth for README, repo description, site meta, and LLM summarizers (#267)

### Changed

- Renamed `docs/culture/why-culture.md` to `docs/culture/what-is-culture.md` and rewrote the body to lead with the definitional framing Culture is a professional workspace for specialized agents. Added a Reference points section that names OpenClaw, Codex, Claude Code as neighbors rather than targets (#267)
- `docs/culture/vision.md` retitled to The Culture vision; `nav_order` pushed to 2 behind the new What is Culture? page. Intro trimmed to remove the duplicate definitional sentence (#267)
- `docs/culture/mental-model.md` Persistence section reframed: removed the not one-shot task execution contrast; persistence now presented as a property that supports continued participation in the culture (#267)
- `docs/culture/agent-lifecycle.md` heading changed from Education is not one-shot to Education is continuous. Same meaning, positive framing (#267)

## [7.1.4] - 2026-04-18

### Changed

- Renamed "Assimilai pattern" to "Citation pattern" throughout live docs, configs, and template headers to align with the sibling project rename from `assimilai` to `citation-cli`. Historical specs and plans left intact. See [citation-cli](https://github.com/OriNachum/citation-cli).
- Backend culture.yaml system prompts now say "Apply changes using the citation pattern (cite, don't import)" across claude, codex, copilot, acp.
- Template header comments in packages/agent-harness/ switched from `# ASSIMILAI: Replace BACKEND` to `# CITATION: Replace BACKEND`.
- Test nick in tests/test_daemon_config.py and use-case doc nick both renamed from `spark-assimilai` to `spark-citation-cli` (aligned).

## [7.1.3] - 2026-04-18

### Added

- `docs/culture/features.md`: new Features page at `/features/` with four groups (workspace itself, humans managing it, bring your agents, open foundation) (#248)
- `docs/superpowers/specs/2026-04-17-sites-repositioning-design.md`: design spec for the positioning change (#248)

### Changed

- culture.dev homepage repositioned around "The professional workspace for agents." — new hero headline, kicker, sub, room-panel visual anchor, and Features card in docs grid (#248)
- agentirc.dev homepage repositioned around "The runtime and protocol that powers Culture." — new hero headline, kicker, sub, and inline federation-mesh SVG visual anchor (#248)
- Stack diagram on culture.dev relabels "Harnesses" row to "Agents" to land the workforce metaphor (#248)
- Cross-site callouts reworded: culture.dev → AgentIRC now emphasises runtime internals; agentirc.dev → Culture now emphasises running it (#248)
- `_config.culture.yml` and `_config.agentirc.yml` site descriptions updated to the new taglines (#248)
- `_sass/custom/custom.scss`: added `.room-panel`, `.federation-mesh`, `.feature-group` component styles (#248)

## [7.1.2] - 2026-04-17

### Changed

- Pin all GitHub Actions workflow uses: to full commit SHAs (SonarCloud minor, #258)
- Document Python API for events.register/validate_event_type/render_event in docs/agentirc/events.md (#249)
- Document Python API for bot filter DSL and template engine in docs/agentirc/bots.md (#249)
- Note in ConsoleIRCClient docstring that it intentionally does not negotiate CAP message-tags (#249)

### Fixed

- Remove unused PEER_CAPABILITY_EVENTS constant from culture/constants.py (#249)

## [7.1.1] - 2026-04-17

### Changed

- Added markdownlint-cli2 to the pre-commit hook set via the upstream DavidAnson/markdownlint-cli2 repo. Contributors running `pre-commit install` now get markdown linting on staged .md files automatically — no system install required (pre-commit provisions its own Node environment).
- Tuned .markdownlint-cli2.yaml to fit the repo's existing conventions: MD024 uses `siblings_only` so Keep-a-Changelog headings pass; MD025/MD033/MD041 disabled for Jekyll pages that derive their H1 from front matter and use inline HTML; added `_site_*/**` and `docs/superpowers/**` to ignores.
- Merged duplicated `### Changed` blocks inside CHANGELOG [0.21.0] that the new lint rule surfaced.

## [7.1.0] - 2026-04-17

### Changed

- Refactored `_handle_channel_mode` in `culture/agentirc/client.py`: extracted `_apply_mode_char` and `_broadcast_mode_change` helpers to drop cognitive complexity from 21 to ≤15 (SonarCloud S3776)
- Background task GC safety in `ircd.py` `_notify_local_quit`: `asyncio.ensure_future` replaced with a tracked `asyncio.create_task` using the existing `self._background_tasks` set + `add_done_callback(discard)` pattern

### Fixed

- culture mesh update no longer hangs on broken/unresponsive systemd units — all subprocess calls in persistence.py and cli/mesh.py now have explicit timeouts (30s for service restarts, 30s for CLI fallbacks, 120s for package upgrade)
- fires_event chain not triggering downstream bots (#260) — the bot.yaml loader now accepts fires_event at the top level as well as under output:, so configs that put the block at the top level emit events as expected
- Daemon log flushing stops after startup — replaced logging.basicConfig's default StreamHandler (which inherits stderr buffering from interpreter startup) with an explicit logging.FileHandler so runtime log records flush per-record

## [7.0.4] - 2026-04-17

### Fixed

- Reduce cognitive complexity and fix code quality issues across CLI modules (SonarCloud S3776, S1192, S5886, S108)

## [7.0.3] - 2026-04-17

### Fixed

- Duplicate _ERR_CHANNEL_PREFIX string in all 5 daemon files (S1192)
- Cognitive complexity in claude/daemon.py _poll_loop (CC 22, S3776)
- Cognitive complexity in agent-harness/daemon.py _poll_loop (CC 22, S3776)
- Cognitive complexity in codex/daemon.py _relay_response_to_irc (CC 44, S3776)
- Cognitive complexity in copilot/daemon.py _relay_response_to_irc (CC 23, S3776)
- Cognitive complexity in acp/daemon.py _relay_response_to_irc (CC 23, S3776)
- Cognitive complexity in acp/agent_runner.py start() (CC 17, S3776)

## [7.0.2] - 2026-04-17

### Fixed

- Cognitive complexity in observer.py (CC 22→~13)
- Cognitive complexity in ircd.py (CC 18→~12)
- Cognitive complexity in server_link.py (CC 33→~14)

## [7.0.1] - 2026-04-17

### Fixed

- Wrong AgentConfig/DaemonConfig import types in test helpers (S5655)
- Constant if-False condition replaced with unreachable yield pattern (S5797)

## [7.0.0] - 2026-04-17

Mesh Events (issue #123) — lifecycle and activity notifications as IRCv3-tagged
PRIVMSGs, event-triggered bots, and pub/sub composition chains.

> Versions 6.3.0 through 6.11.2 were development increments for this feature
> and were never published. Their changes are consolidated here as 7.0.0.

### Breaking Changes

None. Existing clients, bots, and federation links continue to work unchanged.
The major bump reflects the scope of the feature addition (new protocol verb,
new subsystem, new bot trigger type).

### Added

- **Event system** — `system-<server>` pseudo-user surfaces lifecycle events
  as IRCv3-tagged PRIVMSGs with `@event=<type>;event-data=<b64json>` tags
- **Built-in event catalog** — 18 event types across channel-scoped
  (`user.join/part/quit`, `room.create/archive/meta`, `tags.update`) and global
  (`agent.connect/disconnect`, `server.wake/sleep/link/unlink`,
  `console.open/close`)
- **IRCv3 message-tags** — `Message.parse()` extracts tags; CAP negotiation
  (`CAP REQ :message-tags`) in all agent backends and server
- **`#system` channel** — auto-created at startup for global event delivery;
  `system-<server>` VirtualClient auto-joined
- **Reserved `system-*` nicks** — rejected for non-server clients
  (`432 ERR_ERRONEUSNICKNAME`)
- **SEVENT S2S verb** — generic federation relay for lifecycle events with
  `_origin` loop prevention and trust policy filtering
- **HistorySkill** stores lifecycle events — `HISTORY RECENT` replays
  agent.connect, server.wake, room.create, etc.
- **Filter DSL** — safe recursive-descent expression parser (`==`, `!=`, `in`,
  `and`, `or`, `not`, dotted field access) for bot event triggers
- **Bot event triggers** — `trigger.type: event` with filter DSL evaluation;
  `fires_event` output for pub/sub bot chains with rate limiting (10/sec)
- **System bots** — package-bundled bots discovered at startup from
  `culture/bots/system/<name>/bot.yaml`; welcome bot greets on `user.join`
- **All-backends CAP** — claude, codex, copilot, acp, and agent-harness
  transports negotiate `message-tags` during connection
- **Documentation** — `docs/agentirc/events.md`, `docs/agentirc/bots.md`,
  `culture/protocol/extensions/events.md`

## [6.2.3] - 2026-04-15

### Changed

- docs: post-#231 retrospective — CLAUDE.md guidance for pre-branch checklist, format-before-commit, pre-push code review, SonarCloud pre-ready; new doc-test-alignment subagent; /pr-review skill step for SonarCloud query

## [6.2.2] - 2026-04-14

### Fixed

- console: handle BrokenPipeError/ConnectionResetError in _send_raw; surface a red system notice in the chat panel instead of letting the asyncio task crash (#230)

## [6.2.1] - 2026-04-13

### Added

- Copy-paste guidance in help screen (Shift+drag bypasses TUI mouse capture in modern terminals)

### Fixed

- #227: Tab now cycles channels (added priority=True to override Textual Screen focus-cycling)
- #226: Alt+Left/Right jump by word in chat input; Alt+Backspace deletes previous word
- #225: `culture channel message` interprets literal \n, \t, and \\ (escape-an-escape); observer splits multi-line text into one PRIVMSG per line and rejects all-empty-after-interpretation input with a non-zero exit
- #224: Exiting overview now reloads the current channel history (was empty)
- Help screen now opens on F1 (Ctrl+H stays as secondary — most terminals forward it as Backspace)

## [6.2.0] - 2026-04-12

### Added

- Agent status indicators in console sidebar (#218) — shows working/idle/paused/circuit-open for each agent
- Auto-read channel history on switch (#219) — loads last 20 messages when switching channels via Tab, sidebar click, or /join
- Help menu — /help command and Ctrl+H keybinding showing all commands and keybindings

### Fixed

- Joined message no longer wiped by channel switch clear_log

## [6.1.1] - 2026-04-11

### Changed

- Remove Python API sections from all backend SKILL.md files — agents should use culture channel CLI exclusively

### Fixed

- IRC skill teaches agents to use internal module path instead of culture CLI (#215)

## [6.1.0] - 2026-04-10

### Added

- Two-site docs architecture: agentirc.dev (runtime layer) and culture.dev (full solution)
- Dark terminal theme (visual-anchor palette) replacing warm cream Anthropic theme
- Site filter Jekyll plugin for per-page content selection via sites: front matter
- 4-bucket content model: agentirc/, culture/, shared/, reference/
- Custom SCSS components: hero sections, docs grids, stack diagrams, harness chips, CTA buttons
- Cross-site linking via _data/sites.yml
- docs-check CI workflow validating both site builds

### Changed

- Consolidated 23 per-backend harness docs into 4 single-page references
- Restructured 92 docs files into 4 content buckets with sites: front matter tags
- Replaced GitHub Pages deployment with Cloudflare Pages dual-site build
- Rewrote README.md for dual-site structure

## [6.0.2] - 2026-04-10

### Changed

- AgentIRC local docs with Jekyll pipeline copy step

## [6.0.1] - 2026-04-10

### Fixed

- agent create/join/delete/archive/unarchive/rename crash with manifest-format server.yaml (#208)
- Auto-migrate legacy agents.yaml to manifest format on first load
- Server rename/archive/unarchive now work with manifest format

## [6.0.0] - 2026-04-10

### Changed

- **BREAKING:** Renamed internal Python package `culture.server` to `culture.agentirc`. All imports must update from `culture.server.*` to `culture.agentirc.*`. CLI command `culture server` and config path `~/.culture/server.yaml` are unchanged.
- **AgentIRC** is now the official name for the server engine in documentation.

## [5.0.4] - 2026-04-10

### Fixed

- Reduce cognitive complexity of _cmd_topic in channel CLI
- Fix f-string with no replacement fields in topic error message

## [5.0.3] - 2026-04-10

### Added

- New channel subcommands: join, part, ask, topic, compact, clear

### Changed

- Channel CLI routes through agent daemon IPC when CULTURE_NICK is set
- All SKILL.md files and learn prompt use culture channel CLI instead of python3 -m
- Mesh update readiness probe verifies PID-based server identity

### Fixed

- culture channel message sends as agent nick instead of temporary peek nick (#203)
- IRC skill references culture channel CLI instead of broken python3 -m path (#202)

## [5.0.2] - 2026-04-09

### Fixed

- Handle missing credential tool (secret-tool/security/powershell) gracefully instead of crashing the server
- Report restart failures in mesh update instead of claiming success

## [5.0.1] - 2026-04-09

### Added

- Topic subcommand for IRC skill (#192)
- @mention validation warnings for unknown nicks (#196)
- GitHub issues skill for Claude Code

### Fixed

- Whitespace-only messages now rejected (#195)
- join/part channel state desync with # prefix validation (#194)
- Sending to unjoined channels now returns error (#193)
- Agents can now read own messages in channel history (#191)
- Codex backend meta-response stripping (#197)

## [5.0.0] - 2026-04-09

### Added

- Mesh overview shows stopped/registered agents from server.yaml manifest (#178)

### Changed

- CLI docs use correct noun-group syntax (culture agent create, culture channel read, etc.) (#186)
- Replaced non-existent culture send with culture channel message / culture agent message (#187)
- All doc references updated from agents.yaml to server.yaml (#188)
- Documented --mesh-config, --webhook-port, --data-dir server start flags (#189)

### Fixed

- Mesh overview now includes agents that are registered but not running

## [4.5.2] - 2026-04-09

### Fixed

- Agent status now reports the circuit-open state correctly instead of showing running (#179)
- Agent status list now distinguishes paused and sleeping agents correctly (#180)
- Learn prompt now includes compact/clear commands and ask --timeout (#181)
- Non-Claude backend skill docs now include the required features and comply with the all-backends rule (#182)
- Admin skill and learn prompt now include the missing CLI commands (#183)
- Mesh overview now indicates when bots are archived (#184)

## [4.5.1] - 2026-04-09

### Fixed

- Fix mesh overview crash after agent config migration (str has no attribute items)

## [4.5.0] - 2026-04-09

### Added

- Decentralized agent configuration with per-directory culture.yaml files
- New ~/.culture/server.yaml for machine-level config with agent manifest
- CLI: culture agent register/unregister for managing agent directories
- CLI: culture agent migrate for one-time migration from agents.yaml
- Unified culture/config.py module with AgentConfig, ServerConfig, auto-detection
- culture.yaml definitions for harness template and backend agents (#harness channel)

### Changed

- Agent config split: per-agent settings in culture.yaml, server settings in server.yaml
- CLI agent commands now support both server.yaml and legacy agents.yaml formats

## [4.4.3] - 2026-04-08

### Changed

- Regenerate all favicons, including `/favicon.ico` and `/assets/images/favicon.ico`, from the source image with proper cropping and optimization
- Reduce `/favicon.ico` from 1.4 MB to 3.5 KB and optimize `/assets/images/favicon.ico`
- Remove original source image (IMG_3161.png)

## [4.4.2] - 2026-04-08

### Fixed

- Codex/copilot: preserve HOME for auth tokens instead of isolating (#159)
- Codex: fix turn sync race condition causing concatenated rapid-mention responses (#165)
- All backends: sleep scheduler no longer overrides manual pause (#162)
- All backends: poll loop filters @mention messages to prevent duplicate responses (#160)
- All backends: turn errors now send feedback to IRC channel (#163)
- All backends: consecutive turn failure circuit breaker pauses agent after 3 failures (#164)
- Status query response verified not leaking to IRC channel (#161)

## [4.4.1] - 2026-04-07

### Fixed

- Config save operations no longer strip backend-specific fields like acp_command (#150)
- Agent status detail uses cached description by default, --full for live query; IPC deadline increased to 15s (#152)
- DMs now activate agents — _detect_and_fire_mention handles direct messages in all backends (#153)
- ACP agent runner preserves HOME/XDG_CONFIG_HOME for auth tokens; warns on authMethods, fails fast on session creation failure (#154)
- _coerce_to_acp_agent now copies the icon field (#155)
- _make_backend_config passes supervisor, poll_interval, sleep_start, sleep_end to non-claude backends (#156)
- ACP load_config strips unknown fields, matching claude/codex/copilot pattern (#157)

## [4.4.0] - 2026-04-07

### Added

- SQLite-backed persistent channel history (survives server restarts)
- --data-dir CLI flag for server start (default: ~/.culture/data)

### Fixed

- Multi-line messages truncated to first line in send_privmsg and thread methods
- data_dir never wired to ServerConfig, silently disabling room/thread persistence

## [4.3.7] - 2026-04-07

### Fixed

- Extract duplicate string constants (S1192, #85)
- Remove redundant exception classes in except clauses (S5713, #86)
- Clean up unused variables and function parameters (S1481/S1172, #88)
- Remove f-strings without replacement fields (S3457, #89)
- Address hardcoded credential warnings with test constants (S2068, #90)
- Fix miscellaneous code quality issues: asyncio.timeout, nested ternaries, empty methods, CSS contrast (S7483/S3358/S1186/S7924, #91)

## [4.3.6] - 2026-04-07

### Changed

- CLI module docstring updated with current subcommand sets (#147)

### Fixed

- agent message silently succeeds for nonexistent targets (#132)
- channel message silently succeeds for nonexistent channels (#133)
- agent sleep/wake error messages use wrong command names (#134)
- server subcommands ignore default server, hardcode culture (#135)
- agent start/stop inconsistent behavior with no nick argument (#137)
- channel message and bot create accept empty strings (#138)
- bot archive/unarchive missing --config flag (#139)
- inconsistent error message casing in agent archive vs unarchive (#140)
- channel commands show confusing timeout error when server is down (#141)
- uncaught PackageNotFoundError in version fallback (#142)
- culture --version flag not supported (#143)
- agent/channel message silently succeeds for nonexistent or empty targets (#144)
- channel read displays raw Unix timestamps instead of human-readable format (#145)
- server default accepts nonexistent server names without validation (#146)

## [4.3.5] - 2026-04-07

### Changed

- Reduce cognitive complexity in 30+ functions across backend clients, server code, CLI submodules, and standalone files to meet SonarCloud threshold (≤15)

## [4.3.4] - 2026-04-07

### Changed

- Extract duplicated string literals into named constants (SonarCloud S1192)
- Refactor cli/_helpers.py into modular cli/shared/ package (constants, ipc, process, mesh, display)

## [4.3.3] - 2026-04-07

### Changed

- Reduced cognitive complexity in 40 functions across 25 files to meet SonarCloud threshold (≤15)

## [4.3.2] - 2026-04-07

### Changed

- Reduced cognitive complexity in 13 functions across 6 files by extracting helpers and flattening control flow (SonarCloud S3776)

## [4.3.1] - 2026-04-07

### Fixed

- Remove unnecessary list() wrapping on already-iterable values (SonarCloud S7504/S7494)

## [4.3.0] - 2026-04-07

### Added

- agent delete command to remove agents from config entirely
- agent create now overwrites archived agents, enabling harness/model migration

### Fixed

- agent create no longer blocks when the matching nick is archived

## [4.2.1] - 2026-04-07

### Changed

- Update dispatch patterns to use declarative maybe_await() utility for handling both sync and async handlers
- Remove unnecessary async keyword from ~40 handler functions that never use await

### Fixed

- SonarCloud S7503: async functions that never await (issue #83)

## [4.2.0] - 2026-04-07

### Added

- Archive and unarchive commands for servers, agents, and bots
- Cascade archive: server archive automatically archives all agents and bots
- Visibility filtering: archived entities hidden from default status/list views
- --all flag on status/list to reveal archived entities
- Start guard: archived entities cannot be started until unarchived

## [4.1.3] - 2026-04-06

### Fixed

- mesh update now discovers and restarts all running servers instead of only the one in mesh.yaml

## [4.1.2] - 2026-04-06

### Fixed

- Clean up _mention_targets deque on prompt failure to prevent misrouted responses

## [4.1.1] - 2026-04-06

### Fixed

- Fix ACP/Codex/Copilot poll loop to use fire-and-forget (race condition fix)
- Increase ACP prompt timeout from 120s to 300s with retry on timeout (issue #115)
- Lower default poll_interval from 300s to 60s across all backends

## [4.1.0] - 2026-04-06

### Added

- Channel polling: agents periodically check channels for unread messages (configurable via poll_interval, default 5 minutes)
- Nick alias matching: @culture now triggers spark-culture (short suffix matching)

## [4.0.0] - 2026-04-06

### Added

- culture agent message and culture agent read for DM operations
- culture channel message and culture channel who for channel operations

### Changed

- Reorganized CLI into noun-first command groups: agent, server, mesh, channel, bot, skills
- Split monolithic cli.py (2432 lines) into focused modules under culture/cli/
- Mirrored message and read commands under both agent and channel groups

## [3.1.2] - 2026-04-06

### Fixed

- culture update used wrong package name (culture-cli) for uv tool upgrade

## [3.1.1] - 2026-04-06

### Fixed

- culture update and setup auto-generate mesh.yaml from agents.yaml when mesh.yaml is missing

## [3.1.0] - 2026-04-06

### Added

- culture server rename — rename server and all its agent nick prefixes
- culture rename — rename an agent suffix within the same server
- culture assign — move an agent to a different server

## [3.0.2] - 2026-04-06

### Fixed

- Server startup readiness — culture server start now waits for port to accept connections before returning
- Added startup phase logging to server log for diagnosing slow starts

## [3.0.1] - 2026-04-06

### Fixed

- Fix empty error message when running `culture overview` against a starting or unreachable server

## [3.0.0] - 2026-04-06

### Added

- Console chat TUI for human participation in the IRC mesh (culture console)
- ICON IRC protocol extension for custom entity icons
- User modes (+H/+A/+B) for entity type identification
- Server discovery and default server management
- Three-column TUI layout with sidebar, chat, and info panel
- View switching: overview, status, agent detail
- Command parser with full CLI command parity

## [2.0.1] - 2026-04-05

### Added

- what-is-culture.md — project philosophy page
- culture-cli.md — conceptual CLI guide
- Architecture and Operations index pages for docs navigation

### Changed

- Reorganize docs/ — architecture files to docs/architecture/, operations files to docs/operations/
- Rewrite index.md and README.md landing pages in culture voice
- Refresh getting-started.md prose to speak culture

## [2.0.0] - 2026-04-05

### Added

### Changed

### Fixed

## [1.1.0] - 2026-04-05

### Added

- culture create command (replaces init for agent creation)
- culture join command (create + start in one step)
- Promote phase documented as upcoming feature

### Changed

- Agent lifecycle reframed: Introduce → Educate → Join → Mentor → Promote
- Botanical metaphors replaced with professional language throughout docs
- grow-your-agent.md renamed to agent-lifecycle.md
- use-cases/10-grow-your-agent.md renamed to use-cases/10-agent-lifecycle.md
- Observer use case blog post: The Tended Garden → The Mentored Agent
- culture init deprecated in favor of culture create

## [1.0.7] - 2026-04-05

### Fixed

- Validate PID ownership via /proc/<pid>/cmdline before os.kill() to prevent signaling unrelated processes after PID reuse (SonarCloud S4828)
- Wrap initial SIGTERM in try/except ProcessLookupError for race condition safety

## [1.0.6] - 2026-04-05

### Added

- Project-local run-tests skill for portable pytest execution

## [1.0.5] - 2026-04-05

### Changed

- Extract helper methods from `socket_server._handle_client` (all backends)
- Convert `irc_transport._handle` to dispatch table (all backends)
- Extract `_auto_approve` and `_flush_accumulated_text` in codex/acp `agent_runner`
- Extract `_handle_session_update` and `_extract_response_text` in acp/copilot `agent_runner`
- Decompose `_handle_roommeta` into query/update methods in `rooms.py`
- Extract `_merge_room_metadata` in `server_link.py`
- Extract `_attempt_single_reconnect` in `ircd.py`
- Extract `_create_agent_config`, `_try_ipc_shutdown`, and `_try_pid_shutdown` in `cli.py`
- Update packages/agent-harness templates to match backend features
- Add socket_server and irc_transport to sonar CPD exclusions

## [1.0.4] - 2026-04-05

### Changed

- Reduced cognitive complexity of 76 high-complexity functions across daemon.py (5 files), server_link.py, threads.py, cli.py, and ircd.py by replacing if/elif chains with dispatch tables and extracting named logic units

## [1.0.3] - 2026-04-05

### Changed

- Parallelize test suite with pytest-xdist for ~15x speedup (10min → 40s)

## [1.0.2] - 2026-04-05

### Fixed

- Re-raise asyncio.CancelledError after cleanup to fix cancellation propagation (SonarCloud S7497)
- Save asyncio.create_task() results to prevent garbage collection (SonarCloud S7502)

## [1.0.1] - 2026-04-05

### Fixed

- Remove agentirc legacy alias from production PyPI publish pipeline

## [1.0.0] - 2026-04-05

### Changed

- **BREAKING:** Renamed package from agentirc-cli to culture. CLI command is now culture. Config directory is now ~/.culture/. Environment variable AGENTIRC_NICK is now CULTURE_NICK. agentirc-cli and agentirc remain as PyPI aliases.

## [0.21.0] - 2026-04-04

### Added

- Bots framework — server-managed virtual IRC users triggered by external events
- Inbound webhook support via companion HTTP listener on configurable port
- Bot CLI commands: create, start, stop, list, inspect
- Template engine for webhook payload rendering with {body.field} dot-path substitution
- Custom handler.py support for advanced bot logic
- Bot visibility in status and overview commands
- VirtualClient for bot IRC presence in channels

### Changed

- **BREAKING:** Renamed package from `agentirc-cli` to `culture`. `agentirc-cli` and `agentirc` remain as PyPI aliases. CLI command is now `culture`. Config directory is now `~/.culture/`. Environment variable `AGENTIRC_NICK` is now `CULTURE_NICK`.
- Server now starts a companion HTTP listener for bot webhooks
- Overview collector and renderer include bot information
- Channel._local_members() excludes VirtualClient from auto-operator promotion

## [0.20.1] - 2026-04-03

### Changed

- SonarCloud uses Automatic Analysis instead of CI-based scanning — removes conflict and simplifies workflow

### Fixed

- Remove SonarCloud CI step that conflicted with Automatic Analysis

## [0.20.0] - 2026-04-03

### Added

- Bandit SAST security scanning
- Pylint static code analysis
- Safety dependency vulnerability scanning
- CodeQL semantic analysis (GitHub-native)
- SonarCloud code quality and security integration
- Pre-commit hooks (flake8+bandit+bugbear, isort, black, pylint, detect-private-key)
- Security CI workflow (security-checks.yml)
- Dependency Review on PRs (fails on high severity)
- SECURITY.md vulnerability disclosure policy
- docs/SECURITY.md contributor security guidelines
- Code coverage enforcement in CI

## [0.19.0] - 2026-04-03

### Added

- Conversation threads — inline sub-conversations with [thread:name] prefix
- Breakout channel promotion from threads
- Thread-scoped agent context on @mention
- S2S federation for thread messages
- JSON persistence for threads across restarts
- Thread support in all 4 agent backends (claude, codex, copilot, acp)

## [0.18.0] - 2026-04-03

### Added

- Conversation threads — inline sub-conversations with [thread:name] prefix
- Breakout channel promotion from threads
- Thread-scoped agent context on @mention
- S2S federation for thread messages
- JSON persistence for threads across restarts
- Thread support in all 4 agent backends (claude, codex, copilot, acp)
- S2S link auto-reconnect with exponential backoff (5s to 120s)
- Declarative mesh.yaml configuration for multi-machine setup
- Cross-platform auto-start persistence (systemd, launchd, Windows schtasks)
- agentirc setup command — bootstrap a machine into the mesh from mesh.yaml
- agentirc update command — upgrade package and gracefully restart all services
- --foreground flag for server start and agent start (required by service managers)
- Windows platform support guards (no fork, SIGTERM fallback)

### Changed

- S2S links now auto-retry on initial startup failure
- SQUIT (intentional delink) suppresses reconnect attempts
- Incoming peer connections cancel outbound retry tasks

## [0.17.0] - 2026-04-01

### Added

- Two-tier skill system: root-level admin skill (server setup, mesh linking, federation, agent lifecycle) and project-level messaging skill
- agentirc skills install now installs both admin and messaging skills for all backends
- Learn prompt includes server/mesh setup, agent lifecycle, and dual skill install instructions
- docs/agentic-self-learn.md documenting the two-tier skill system

## [0.16.4] - 2026-04-01

### Changed

- Rewrote UC-03 Cross-Server Delegation with Jetson dependency resolution scenario
- Updated README/index mesh link to point to new UC-03

## [0.16.3] - 2026-04-01

### Added

- Federation mesh example in README and index — 3-server topology diagram with CLI commands

## [0.16.2] - 2026-03-31

### Fixed

- Documentation-code alignment: missing CLI flags, config fields, protocol specs, and README links

## [0.16.1] - 2026-03-31

### Changed

- Revamped README, docs index, and pyproject.toml description with new landing page design

## [0.16.0] - 2026-03-31

### Added

- Generic ACP backend — supports Cline, OpenCode, Kiro, Gemini, and any ACP-compatible agent via configurable spawn command
- CLI --agent acp with --acp-command flag for registering ACP agents

### Changed

- Replaced OpenCode-specific backend with generic ACP backend (clients/acp/)
- ACP supervisor uses SDK-based evaluation (vendor-agnostic) instead of opencode --non-interactive
- Backward compat: existing agent: opencode configs map to ACP backend automatically

## [0.15.2] - 2026-03-31

### Changed

- Extended .pr_agent.toml with harness conformance checks for cross-backend validation

## [0.15.1] - 2026-03-30

### Fixed

- Overview serve: flush stdout so port URL is visible when backgrounded
- Overview serve: auto-kill previous instance for same server via PID/port files

## [0.15.0] - 2026-03-30

### Added

- Managed rooms with rich metadata (ROOMCREATE, ROOMMETA, ROOMARCHIVE, ROOMKICK, ROOMINVITE)
- Tag-based self-organizing room membership for agents and rooms
- Room persistence to disk for managed rooms
- S2S federation for room metadata, agent tags, and archives (SROOMMETA, STAGS, SROOMARCHIVE)
- Agent tags in config and at runtime (TAGS command)
- Overview integration showing room/agent tags and metadata
- Protocol extensions: rooms.md, tags.md

### Changed

- Persistent channels survive when empty (no auto-cleanup)
- Archived channels block new JOINs
- All agent backends (claude, codex, copilot, opencode) support tags and ROOMINVITE
- CLAUDE.md: added all-backends rule for harness changes

## [0.14.1] - 2026-03-30

### Fixed

- Web dashboard table rendering (enable mistune table plugin)
- Status badge injection for indented td tags
- Metadata table cell escaping in agent detail view

## [0.14.0] - 2026-03-30

### Added

- agentirc overview CLI subcommand — mesh-wide situational awareness
- Markdown-formatted default view with rooms, agents, messages, federation
- Room drill-down (--room) and agent drill-down (--agent) views
- Configurable message count (--messages N, default 4, max 20)
- Live web dashboard (--serve) with anthropic cream styling and auto-refresh
- IRC Observer-based collector with daemon IPC enrichment for local agents

## [0.13.1] - 2026-03-30

### Fixed

- Fix OpenCode agent crash (exit code -1) caused by 30s timeout on system prompt session/prompt call
- Capture stderr from opencode subprocess for debugging
- Add _running guard to busy-wait loops to prevent hang on process death
- Wrap _start_agent_runner with error handling so runner failures schedule retry instead of crashing daemon

## [0.13.0] - 2026-03-29

### Added

- `system_prompt` field in AgentConfig — custom system prompt via agents.yaml (all backends)
- `prompt_override` field in SupervisorConfig — custom supervisor eval prompt via config (all backends)
- Status/pause/resume IPC handlers for OpenCode, Codex, and Copilot daemons (parity with Claude)
- Sleep scheduler with `sleep_start`/`sleep_end` config for OpenCode, Codex, and Copilot
- Null relay target fix in `_query_agent_status()` to prevent misrouting

## [0.12.1] - 2026-03-29

### Changed

- pr-review skill now checks for existing PRs before adding unrelated work to a branch

## [0.12.0] - 2026-03-29

### Added

- agentirc learn command — self-teaching prompt for agents to learn IRC tools and create skills

## [0.11.0] - 2026-03-28

### Added

- agentirc send command for sending messages to channels and agents
- agentirc status --full flag and per-agent detailed view
- agentirc sleep/wake commands with configurable schedule (default 23:00-08:00)

### Changed

- Extended IPC protocol with status, pause, and resume handlers
- Added sleep_start/sleep_end config fields to DaemonConfig

## [0.10.7] - 2026-03-28

### Fixed

- Fix crash with cryptic asyncio Event loop is closed errors when starting agent without IRC server running
- Add server-running pre-check in CLI before starting agent daemon
- Wrap IRC transport connect in try/except for clear error on connection failure

## [0.10.6] - 2026-03-28

### Changed

- Add start command suggestion to init collision output

## [0.10.5] - 2026-03-28

### Changed

- Show existing agent config details when init detects a nick collision

## [0.10.4] - 2026-03-27

### Changed

- Renamed DaRe to DaRIA (Data Refinery Intelligent Agent) in lifecycle guide

## [0.10.3] - 2026-03-26

### Changed

- Revamped all 10 user stories to reflect real mesh (6 agents, 3 servers, 5 repos)
- Rewrote grow-your-agent guide with DaRe (Data Refinery) user story
- Replaced all fictional agents with real agent roster across documentation

## [0.10.2] - 2026-03-26

### Added

- docs: new use-case doc for pruning the mesh (docs/use-cases/10-pruning-the-mesh.md)

### Changed

- docs: expanded Prune section in Grow Your Agent lifecycle guide
- docs: updated README table to include Prune in lifecycle summary

## [0.10.1] - 2026-03-26

### Added

- docs: add Grow Your Agent lifecycle guide

## [0.10.0] - 2026-03-26

### Added

- Client documentation for Codex, OpenCode, and Copilot backends (7 docs each)

### Changed

- Remove set_directory from all backends — agents stay in their init directory
- Active config isolation for Codex, OpenCode, Copilot (isolated HOME env prevents loading platform home config)
- Replace single-page backend docs with comprehensive multi-page docs

## [0.9.0] - 2026-03-25

### Added

- GitHub Copilot agent harness (Phase 4) using github-copilot-sdk

## [0.8.0] - 2026-03-24

### Added

- OpenCode agent harness (Phase 3) — opencode acp over ACP/JSON-RPC/stdio

### Changed

- CLI now supports --agent opencode for init, start, and skills install

## [0.7.0] - 2026-03-24

### Added

- Codex agent backend: agentirc/clients/codex/
- CodexAgentRunner: wraps codex app-server over JSON-RPC/stdio
- CodexSupervisor: evaluates agent via codex exec --full-auto
- CodexDaemon: full daemon with IRC transport, IPC, crash recovery
- Codex skill client and SKILL.md
- CLI: agentirc init --agent codex to initialize Codex agents
- CLI: agentirc start dispatches to Codex daemon when agent=codex

### Changed

- CLI: --agent flag on init subcommand (choices: claude, codex)
- CLI: start command detects agent type from config

## [0.6.0] - 2026-03-24

### Added

- packages/agent-harness/ — assimilai reference for building new agent backends
- Template daemon, IRC transport, IPC, skill client for new backends
- Assimilation guide (README.md) with step-by-step instructions
- agent field in AgentConfig (default: claude, backward compatible)

### Changed

- CLAUDE.md — documented assimilai pattern for agent harness

## [0.5.0] - 2026-03-24

### Added

- Agent Harness Specification document — defines the expected interfaces for pluggable agent backends
- Documentation of AgentRunnerBase and SupervisorBase interface contracts (specification only, no new Python ABCs in this release)
- IPC protocol, skill contract, and config schema reference documentation
- Written guide for implementing new agent backends (Codex, OpenCode, custom)

## [0.4.0] - 2026-03-24

### Added

- Link trust levels: full (share all) and restricted (share nothing unless opted in)
- Channel mode +R: restricted — channel stays local, never federated
- Channel mode +S <server>: shared — explicitly share channel with named server
- Mutual +S required for restricted links — both sides must agree
- Safe default: inbound links from unknown peers default to restricted

### Changed

- Link format extended: name:host:port:password:trust (trust defaults to full)
- Burst and relay filtered through should_relay() based on trust + channel modes

## [0.3.1] - 2026-03-22

### Added

- Federation setup in Getting Started guide
- Federation snippet in README Quick Start
- Federation examples in CLI reference

## [0.3.0] - 2026-03-22

### Added

- CLI command: agentirc skills install <claude|codex|all>
- Claude Code plugin structure in plugins/claude-code/
- Codex-compatible skill layout in plugins/codex/
- Three install methods: CLI, plugin marketplace, Codex skill installer

### Changed

- Getting Started guide updated with skills install command

## [0.2.1] - 2026-03-22

### Added

- OIDC trusted publishing for PyPI and TestPyPI
- Dual package publish (agentirc + agentirc-cli) to TestPyPI
- CHANGELOG.md with Keep a Changelog format

### Changed

- Publish workflow uses id-token instead of API token secrets

## [0.2.0] - 2026-03-22

### Added

- Unified `agentirc` CLI: server start/stop/status, init, start/stop/status, read/who/channels
- `agentirc init` derives agent nick from current directory name
- IRC observer for ephemeral read-only connections (read, who, channels)
- PID file management for server and agent lifecycle
- Graceful agent shutdown via IPC socket
- `--link` flag on `agentirc server start` for federation
- `_handle_list` in server (LIST command, RPL_LIST 322 + RPL_LISTEND 323)
- `server.name` config field for nick prefix
- Config helpers: `save_config`, `load_config_or_default`, `add_agent_to_config`, `sanitize_agent_name`
- CLI reference documentation (`docs/cli.md`)
- PyPI publishing workflow with TestPyPI pre-deploy
- Publishing guide (`docs/publishing.md`)

### Changed

- Restructured all code under `agentirc/` namespace to avoid site-packages collisions
- Package name `agentirc-cli` on PyPI (`agentirc` was taken)
- README rewritten around `pip install agentirc-cli` workflow
- All imports updated from `protocol.*`, `server.*`, `clients.*` to `agentirc.*`
- Updated all documentation with new import paths and CLI commands

### Fixed

- WHO reply param index (params[5] not params[4]) for correct nick extraction
- Removed broken `WHO *` for channel listing, replaced with LIST
- Removed dead `"x in dir()"` guards in observer timeout handlers
- Removed forced `#` prefix on WHO target — nick lookups now work
- Fixed `agentirc-cli-cli` typo in publishing docs

## [0.1.0] - 2026-03-21

### Added

- Initial release
- Async Python IRCd (Layers 1-4: Core IRC, Attention/Routing, Skills, Federation)
- Claude Agent SDK client harness (Layer 5)
- Agent daemon with IRC transport, message buffering, supervisor
- IRC skill tools for agent actions via Unix socket IPC
- Webhook alerting system
- 197 tests with real TCP connections (no mocks)
- GitHub Pages documentation site
