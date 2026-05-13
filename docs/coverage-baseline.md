# Coverage baseline

This doc tracks culture's coverage floor and the per-domain growth path through Phase 0a (cultureagent extraction).

## Baseline (locked 2026-05-09)

- **Project-wide:** 56.86% (7556/13289 lines)
- **Integration-only baseline (`tests/test_integration_layer5.py`):** 15.95%
- **Harness-module-only:** 56% of 5304 lines

The baseline was measured by `uv run pytest -n auto --cov=culture --cov-report=xml:coverage.xml` against commit `4595fc7`. The full per-behavior audit (mapping each harness unit test file to its production-code coverage delta) lands in the follow-up audit-doc PR; this baseline doc is intentionally self-contained so the rationale for `fail_under = 56` is on `main` immediately.

## Locks

- **pytest CI gate:** [`[tool.coverage.report] fail_under = 56`](../pyproject.toml) in `pyproject.toml`. Drops below 56% fail the PR locally / in CI before SonarCloud reports back.
- **SonarCloud Quality Gate:** `sonar.qualitygate.wait=true` in [`sonar-project.properties`](../sonar-project.properties) blocks CI on the gate decision. Gate threshold managed in SonarCloud's project settings (out-of-tree).
- **CI scanner:** `.github/workflows/tests.yml` runs `SonarSource/sonarqube-scan-action` after pytest, uploading `coverage.xml` to the SonarCloud project (`agentculture_culture`). Fork PRs without `SONAR_TOKEN` skip the scan cleanly.

## Per-domain growth path (Phase 0a)

| Domain | Baseline | Phase 0a (projected) | Phase 0a (measured) | Gating done by |
|---|---|---|---|---|
| `culture/clients/shared/` | ~58% | ~95% | **80%** | Tasks 2, 3, 4, 5, 6 |
| `culture/clients/claude/` | ~85% | ~90% | **72%** | Tasks 6, 8 |
| `culture/clients/codex/` | ~25% | ~75% | **~43%** | (Task 8 narrowed to claude) |
| `culture/clients/copilot/` | ~25% | ~75% | **~43%** | (Task 8 narrowed to claude) |
| `culture/clients/acp/` | ~25% | ~70% | **~43%** | (Task 8 narrowed to claude) |
| **Project-wide** | **56.86%** | **~73%** | **56.96%** | (see below) |

The project-wide number barely moved because the harness unit tests in `tests/harness/test_*.py` still cover the same code paths the new integration tests exercise — once Phase 1 deletes those unit tests, the integration tests become the *sole* coverage source and the project-wide number reflects what's actually testable through the daemon → IRC → harness chain. Phase 0a's real delivery is the **+22pp on `culture/clients/shared/`** (58% → 80%): the shared-by-import tier that all four backends use is now covered through real `agentirc.IRCd` instances, not just through harness unit tests.

The non-claude backends (`codex`, `copilot`, `acp`) didn't move because Task 7 (supervisor) was dropped during the audit revision and Task 8 (agent_runner timeout) narrowed to claude-only — each non-claude backend has a distinct SDK injection point that warrants its own integration-test PR. Cross-backend integration coverage is a follow-up.

## Why "Coverage on New Code" alone isn't enough here

SonarCloud's default gate is "Coverage on New Code ≥ 80%" — protects new lines from being added without tests. That stays on as a regression preventer, but it doesn't lock today's overall floor: a delete that removes tested code can raise overall percentage while leaving real coverage gaps. The pytest `fail_under = 56` is the tighter floor.

## Closeout — Phase 0a complete (2026-05-09)

Phase 0a's six integration-test PRs all merged:

- [#364](https://github.com/agentculture/culture/pull/364) — Task 2 — attention behaviors
- [#365](https://github.com/agentculture/culture/pull/365) — Task 3 — message buffer overflow
- [#366](https://github.com/agentculture/culture/pull/366) — Task 4 — IRC transport traceparent propagation
- [#367](https://github.com/agentculture/culture/pull/367) — Task 5 — webhook HTTP fanout
- [#368](https://github.com/agentculture/culture/pull/368) — Task 6 — harness telemetry (connect span + transition counter)
- [#369](https://github.com/agentculture/culture/pull/369) — Task 8 — claude agent_runner timeout

`fail_under` stays at **56** (matching `floor(56.96)`); see the comment in `pyproject.toml`. SonarCloud gate choice: **Path B** (in-tree only — no Sonar UI changes; rely on the in-tree pytest floor for project-wide and on Sonar's default "Coverage on New Code ≥ 80%" for regression prevention).

**Known follow-ups** (each will be its own small PR, not part of Phase 0a):

1. **Task 8.5 — skill_client integration test.** `culture/clients/claude/skill/irc_client.py` shows 54% under integration-only tests; needs a small `tests/test_integration_skill_client.py` before Phase 1 deletes `tests/test_skill_client.py`, otherwise the Phase 1 delete drops the floor.
2. **Cross-backend integration timeout tests** for `codex`, `copilot`, `acp` (Task 8 narrowing).
3. **Product fix — `AgentDaemon.stop()` should cancel/await `_background_tasks`** (PR #369 review #2 follow-up).

## Coverage ratchet to 90% — phase log (started 2026-05-13)

Plan: `/home/spark/.claude/plans/we-re-at-60-coverage-refactored-lark.md` — phased PR plan to raise the floor from 56 to 90.

### Phase 1 — Coverage plumbing + console removal (2026-05-13)

**Measured: 60.99%** (6260 statements, 2442 missing) → `fail_under = 60`.

Changes:

1. **Parallel-coverage merging fixed.** `[tool.coverage.run]` now sets `parallel = true`, `concurrency = ["thread", "multiprocessing"]`, `sigterm = true`. The test runner (`.claude/skills/run-tests/scripts/test.sh`) now runs `coverage combine` between pytest-xdist and the final report. Before this, xdist worker `.coverage.*` files were merged unsafely and the reported number drifted from reality.
2. **`culture/console/` deleted.** The Textual TUI is superseded by sibling repo [`irc-lens`](https://github.com/agentculture/irc-lens); `culture console` already passthrough-launches it via `culture/cli/console.py` (kept). Removed: `culture/console/` package, eight `tests/test_console_*.py` files, `textual>=1.0` from `pyproject.toml`.
3. **`exclude_lines` extended** with `raise NotImplementedError`, `...` (literal ellipsis), and `if sys.platform == "win32":` to drop legitimately-untestable lines from the denominator.

Notable findings (deferred to later phases):

- **`culture/transport/client.py` (569 stmts, 25% covered) is largely dead post-extraction.** `agentirc.ircd.IRCd` uses agentirc's own `Client` class (`.venv/lib/python3.12/site-packages/agentirc/client.py`), not culture's. Only four telemetry tests still import `culture.transport.client.Client` directly. Phase 5 should reconsider: delete the unused code rather than backfill tests for it.
- **`culture/protocol/commands.py` (35 stmts, 0% covered)** — addressed in Phase 2 as planned.

### Phase 2 — `cli/shared/process.py` + `protocol/commands.py` (2026-05-13)

**Measured: 62.91%** (6260 statements, 2322 missing) → `fail_under = 62`. Both target files moved from low/zero coverage to 100%:

- `culture/cli/shared/process.py` — 12% → **100%** (97 statements, 0 missing). `tests/test_cli_shared_process.py` (24 tests) covers all four functions (`stop_agent`, `_try_ipc_shutdown`, `_try_pid_shutdown`, `server_stop_by_name`) including every branch: IPC success, IPC failure, IPC raises, missing socket, corrupt/stale/non-culture PID, SIGTERM success, SIGTERM `ProcessLookupError`, SIGKILL escalation, and the "aborts kill when PID no longer culture" guard. All OS primitives monkeypatched — no real `os.kill` / `os.fork` / sockets.
- `culture/protocol/commands.py` — 0% → **100%** (35 statements, 0 missing). `tests/test_protocol_commands.py` (3 tests) discovery-style: iterates `vars(commands)` for uppercase string constants and asserts `value == name`. Adding a new verb does not require a test edit; deletion is caught by the RFC-baseline smoke test.

### Phase 3a — `cli/shared/mesh.py` + `cli/bot.py` + `cli/channel.py` (2026-05-13)

**Measured: 67.89%** (6260 statements, 2010 missing) → `fail_under = 67`. All three target files moved to near-100% coverage:

- `culture/cli/shared/mesh.py` — 25% → **100%** (48 statements, 0 missing). `tests/test_cli_shared_mesh.py` (14 tests) covers all four functions (`parse_link`, `resolve_links_from_mesh`, `generate_mesh_from_agents`, `build_server_start_cmd`) including malformed-spec rejection, password-with-colons preservation, peer-without-credential skip with warning, and the missing-DEFAULT_CONFIG fallback path.
- `culture/cli/bot.py` — 33% → **99%** (199 statements, 2 missing). `tests/test_cli_bot.py` (35 tests) covers all seven `_bot_*` handlers + the `_should_include_bot` / `_load_and_filter_bots` helpers. Filesystem isolated via tmp_path-backed `BOTS_DIR`; `load_config_or_default` patched. The 2 missing lines are in a small `_bot_list` empty-state branch.
- `culture/cli/channel.py` — 44% → **99.6%** (260 statements, 1 missing). 21 new tests appended to `tests/test_channel_cli.py` covering every `_cmd_*` handler (list/read/message/who/join/part/ask/topic/compact/clear), `_interpret_escapes`, `_is_connection_error`, the dispatch wrapper for OSError, and the `_topic_read` daemon-unreachable exit path. IPC is stubbed at the `_try_ipc` / `_require_ipc` boundary (the `_cmd_*` handlers call `asyncio.run` internally, so a test-owned event loop would dead-lock the real Unix-socket-mock pattern).

### Phase 3b — `cli/mesh.py` + `cli/server.py` (2026-05-13)

**Measured: 73.40%** (6260 statements, 1665 missing) → `fail_under = 73`. Two more CLI handlers covered:

- `culture/cli/mesh.py` — 53% → **99.3%** (282 statements, 2 missing). `tests/test_cli_mesh.py` (54 tests) covers every dispatched handler (`_cmd_overview`, `_cmd_setup`, `_cmd_update`, `_cmd_console`) plus every reachable helper: `_collect_mesh_data` exit paths, `_find_upgrade_tool`, `_upgrade_timeout_hint`, `_run_upgrade` (timeout + non-zero-exit), `_upgrade_culture_package` (skip / dry-run / no-tool / exec-reach), `_wait_for_server_port` (accept / refuse / non-culture PID), `_restart_single_service` (service path + subprocess fallback + timeout swallow), `_restart_mesh_services`, `_resolve_mesh_for_server`, `_restart_running_servers`, `_restart_from_config`. `_install_mesh_services` is excluded (systemd; Linux + root only); `os.execvp` re-exec is reached by the tests but its body is excluded as an end-of-life branch.
- `culture/cli/server.py` — 21% → **76%** (396 statements, 96 missing). `tests/test_cli_server.py` (54 tests) covers `_resolve_server_name`, `dispatch` (including the `agentirc.cli.dispatch` passthrough for forwarded verbs), `_cmd_default`, `_server_rename` (every branch), `_check_server_archived`, `_check_already_running`, `_resolve_server_links`, `_wait_for_graceful_stop`, `_force_kill` (POSIX SIGKILL + win32 SIGTERM + `ProcessLookupError` swallow), `_server_stop`, `_server_status`, `_validate_config_name`, `_update_single_bot_archive`, `_set_bots_archive_state`, `_server_archive`, `_server_unarchive`, plus the `_server_start` dispatcher. The 96 missing lines are concentrated in three deliberately-skipped integration-territory functions: `_run_foreground`, `_daemonize_server`, `_run_server` — exercised by `tests/test_integration_irc_transport.py` against a real IRCd, not unit tests.

### Phase 3c — `cli/agent.py` (2026-05-13)

**Measured: 77.30%** (6260 statements, 1421 missing) → `fail_under = 77`. The last CLI module is now covered:

- `culture/cli/agent.py` — 47% → **88.8%** (579 statements, 65 missing). `tests/test_cli_agent.py` (104 tests, heavily parametrized) covers `dispatch`, the four backend `_create_*_config` factories (parametrized across claude/codex/copilot/acp), `_parse_acp_command` (all branches incl. JSON parse, fallback split, rejection), `_check_existing_agent` (clean / archived / active duplicate), `_to_manifest_agent`, `_save_agent_to_directory`, `_cmd_create` (per-backend + collision), `_cmd_join`, every resolution helper (`_get_active_agents`, `_resolve_by_nick`, `_resolve_auto`, `_resolve_agents_to_start`, `_resolve_agents_to_stop`), `_cmd_start` dispatcher routing, the backend daemon factories (`_create_codex_daemon`, `_create_copilot_daemon`, `_create_claude_daemon`), `_coerce_to_acp_agent`, `_make_backend_config`, `_cmd_stop`, `_cmd_status` (all branches), `_cmd_rename`/`_cmd_assign` (every branch), the IPC dispatcher chain (`_resolve_ipc_targets`, `_argparse_error`, `_send_ipc`, `_ipc_to_agents`), the IPC verb wrappers (`_cmd_sleep`, `_cmd_wake`), `_cmd_learn` (nick / no-nick / cwd-match / no-match), `_cmd_message` (empty target / empty text / send), `_cmd_read`, `_cmd_archive`/`_cmd_unarchive`/`_cmd_delete`, `_cmd_unregister`. The 65 missing lines are concentrated in 5 deliberately-skipped integration-territory functions (`_probe_server_connection`, `_start_foreground`, `_start_background`, `_run_single_agent`, `_run_multi_agents`) — exercised by `tests/test_integration_agent_runner.py` against a real IRCd.

### Phase 4a — `persistence.py` + `config.py` + `telemetry/audit.py` (2026-05-13)

**Measured: 79.68%** (6260 statements, 1272 missing) → `fail_under = 79`. The pure config / OS-services / audit layer is now covered:

- `culture/persistence.py` — 54% → **97.95%** (146 statements, 3 missing). `tests/test_persistence.py` extended with 26 new tests covering `_run_cmd` (success / timeout), `install_service` (macOS plist + Windows .bat + unsupported-platform raise), `uninstall_service` (every platform), `list_services` (macOS / Windows / unsupported-empty), and `restart_service` (every platform + missing-unit + Windows-query-timeout). The 3 missing lines are trivial path-builders (`_systemd_user_dir` / `_launchd_dir` / `_windows_service_dir`) that don't exercise platform-specific branches under test.
- `culture/config.py` — 75% → **90.11%** (445 statements, 44 missing). `tests/test_manifest_config.py` extended with 9 new tests covering `archive_manifest_server` (happy path + skip-already-archived + missing-culture-yaml + extra-unrelated-agent-in-dir), `unarchive_manifest_server` (round-trip + empty + missing-yaml), and `_load_legacy_config` (full body + empty-agents fallback). The 44 remaining missing lines are scattered tiny error paths in YAML I/O helpers and rename validators.
- `culture/telemetry/audit.py` — 82% → **91.89%** (185 statements, 15 missing). New `tests/telemetry/test_audit_rotation.py` (25 tests) covers `_write_all` (full / short-write retry / zero-bytes-raise), `_target_for` (every branch incl. non-dict data), `build_audit_record` (federated origin, extra_tags, actor-kind/remote_addr propagation), `AuditSink.submit` (before start + disabled), `start()` idempotency, `shutdown()` no-op paths + fd-close OSError swallow, `_pick_rotation_path` (suffix increment + stat-error fallback), `_maybe_rotate` open-failure-keeps-old-fd, and `init_audit` / `reset_for_tests` warning paths.

Note: this PR landed 0.3pp short of the original 80% target. The gap is absorbed by the existing Phase 7 sweep.

### Phase 4b — `observer.py` + `overview/collector.py` + `bots/*` (2026-05-13)

**Measured: 83.93%** (6260 statements, 1006 missing) → `fail_under = 83`. The four IRCd-integrated modules picked up substantial coverage; the project floor jumps 4pp in a single PR and hits the original Phase 4b target (83) exactly, while soaking up most of the Phase 4a 0.3pp shortfall as a side effect:

- `culture/observer.py` — 28% → **92%** (183 statements, 14 missing). New `tests/test_observer.py` (28 tests) covers the pure parsers (`_parse_history_line` / `_parse_who_line` / `_parse_list_line` table-driven), the registration/query state machines (`_process_registration_line`, `_process_query_line`, `_drain_query_buffer` exercised with a `_RecordingWriter` + canned bytes), and the full public API (`list_channels` / `who` / `read_channel` / `send_message`) round-tripped against the real `server` fixture. The CRLF-in-target injection test pins the strip behavior — the smuggled `JOIN` collapses into the channel name rather than starting a second protocol line. The remaining 14 missing lines are the `_disconnect` writer-close swallow paths and a recv-empty-line fall-through that fires only on socket close.
- `culture/overview/collector.py` — 71% → **98%** (255 statements, 6 missing). 23 new tests appended to `tests/test_overview_collector.py` covering `_inject_stopped_agents` (manifest archived skip + already-present pass-through + non-list channels fallback), `_handle_registration_line` (PING / 001 / 433-retry / unknown), `_recv_until` (stop-command termination + PING absorption + blank-line skip + timeout), `_query_roommeta` (every key parsed: `room_id` / `owner` / `purpose` / `tags` CSV / `persistent` truthy + ERR_UNKNOWNCOMMAND short-circuit), `_query_tags` (parsed list + ERR_NOSUCHNICK empty), `_collect_bots` (missing dir / missing yaml / malformed yaml swallow), and `_enrich_via_ipc` against a real tmp_path Unix socket — happy status, `circuit_open` / `paused` status flips, stranger-socket skip, foreign-server skip, malformed-not-a-socket connection-refused swallow, and `ok: False` response no-mutation. End-to-end `collect_mesh_state` injects stopped manifest agents.
- `culture/bots/bot.py` — 62% → **98%** (155 statements, 3 missing). 21 new tests appended to `tests/test_bot.py` covering `_DynamicEventType.__str__`, `_check_rate` (burst + release-after-window with pinned `time.monotonic`), `_render_data_values` (templated strings + non-string passthrough + sandbox-rejected dunder fall-through), `start()` idempotency, `handle()` empty-message short-circuit, `_resolve_channels` (dynamic-from-event + empty fallback + non-dict ctx), `_deliver` re-join branch when the channel was dropped, the full `_maybe_fire_event` matrix (happy enum-typed emit / dynamic-typed emit / no-spec early-return / invalid type regex skip / rate-limited skip / emit-raises log-and-swallow), and `_run_custom_handler` against a real `importlib.util` load (handler-on-disk happy / no `handle()` fallback / handler raises fallback / handler returns `None` → empty). The 3 missing lines are inside the `relative_to`-fails security guard that can't be reached without forging an out-of-tree path.
- `culture/bots/bot_manager.py` — 65% → **99%** (175 statements, 1 missing). 24 new tests appended to `tests/test_bot_manager.py` covering `start()` listener wiring + crash-safe teardown when `load_bots` raises + `OSError` listener-bind swallow, `stop()` exception-tolerant listener teardown, `load_bots` (missing dir / dir-without-yaml / archived skip / invalid filter / malformed yaml exception swallow), `register_bot` filter compile-error raise, `_try_start_bot` (mid-start re-entrancy guard + start-fails log-and-return-false), `_matches_event` (non-event triggers / no compiled filter / evaluate raises), `_dispatch_to_bot` (try-start-fails short-circuit + handle-raises span error), `on_event` event matching, `start_bot` load-from-disk fallback + unknown-name raise, `stop_all` per-bot exception swallow, and `load_system_bots` (collision skip + register raises + server-config forwarding). The one remaining missing line is a single guard branch inside `_dispatch_to_bot`'s span context.

### Phase 5 — Delete `culture/transport/` (2026-05-13)

**Measured: 89.13%** (5669 statements, 616 missing) → `fail_under = 89`. Pivoted from backfill to deletion after auditing the file: `culture.transport.client.Client` was never instantiated in production (`agentirc.ircd.IRCd._handle_connection` imports `agentirc.client.Client`, not culture's copy), and the four `tests/telemetry/test_*.py` files that imported it were all isolation-style unit tests against the dead code — none of them exercised the production `IRCd` connection path.

Pre-deletion audit confirmed `agentirc.client.Client` (in the installed `agentirc-cli` package) carries the same instrumentation hooks the deleted culture tests covered: `_submit_parse_error_audit` (line 182 upstream), the `irc.parse_error` span event (163), and traceparent injection on outbound `send` (95–116). The instrumentation is alive in production, just on the upstream side.

`culture/transport/remote_client.py` was also deleted — `RemoteClient` is now `agentirc.remote_client.RemoteClient` and culture's copy was unused. The whole `culture/transport/` package is removed in this PR.

Deletions:

- `culture/transport/client.py` (569 statements, dead in production)
- `culture/transport/remote_client.py` (18 statements, superseded by `agentirc.remote_client`)
- `culture/transport/__init__.py` (4 statements)
- `tests/telemetry/test_parse_error.py` (1 test)
- `tests/telemetry/test_audit_parse_error.py` (4 tests)
- `tests/telemetry/test_dispatch_span.py` (3 tests)
- `tests/telemetry/test_outbound_inject.py` (4 tests)

Net effect: project drops by 591 statements (6260 → 5669) and 12 tests (1292 → 1280); coverage jumps +5.2pp because the deleted slice was at 25% (424 missing / 569 stmts on `client.py` alone). The Phase 5 → 6 step is now a single +5.2pp move instead of two +3pp moves; Phase 6 picks up only the final +0.87pp to reach 90.

### Phase 6 — Final sweep to 90 (2026-05-13)

**Measured: 90.28%** (5669 statements, 551 missing) → `fail_under = 90`. The 60→90 ratchet is **complete**. 64 new tests across the long-tail candidates lifted the project floor the last 1.15pp.

Per-file:

- `culture/credentials.py` — 69% → **100%** (32 statements, 0 missing). 16 new tests in `tests/test_credentials.py` parametrize the FileNotFoundError tool-name lookup across all 4 platform branches (linux/darwin/win32/freebsd) and cover `store_credential` / `lookup_credential` / `delete_credential` on each platform (security on darwin; powershell + PS-script interpolation on win32; secret-tool with stdin-piped password on linux).
- `culture/pidfile.py` — 65% → **98%** (104 statements, 2 missing). 17 new tests in `tests/test_pidfile.py` cover port files (write_port/read_port/remove_port + invalid-int swallow), read_pid invalid-int branch, is_process_alive PermissionError, default_server file (read/write + missing/empty/OSError), rename_pid (both files / pid-only / missing / OSError swallow), and list_servers (empty dir / missing dir / running culture servers / dead PIDs / non-culture / port fallback / unreadable PID). The 2 missing lines are inside `is_process_alive`'s ProcessLookupError/PermissionError catch which can't be safely exercised without forging an OS state.
- `culture/cli/shared/ipc.py` — 74% → **98%** (42 statements, 1 missing). New `tests/test_cli_shared_ipc.py` (10 tests) covers `ipc_request` against a real `asyncio.start_unix_server` in tmp_path (happy response + missing-socket + server-drops swallow), `ipc_shutdown` (ok / not-ok / missing-socket), `get_observer` config-driven factory (with / without / empty `CULTURE_NICK`), and `agent_socket_path` runtime-dir routing. The 1 missing line is the deadline-exhausted return-None path on line 40 (hard to force deterministically without monkeypatching the event loop clock).
- `culture/cli/shared/formatting.py` — 0% → **100%** (2 statements). Trivial re-export test in `tests/test_cli_afi_devex.py` confirms `culture.cli.shared.formatting.relative_time is culture.formatting.relative_time`.
- `culture/cli/afi.py` — 73% → **93%** (15 statements, 1 missing). 5 tests in `tests/test_cli_afi_devex.py` cover `dispatch` (forwards argv + handles None args), `_entry` (raises SystemExit(2) when afi-cli not installed), `register` (adds REMAINDER-typed subparser), and the NAME/register/dispatch protocol attributes. The 1 missing line is the `# pragma: no cover` branch already excluded by Phase 1's exclude_lines.
- `culture/cli/devex.py` — 73% → **100%** (15 statements). Same shape as afi.py — 5 tests cover all paths.

### Phase target table — 60 → 90 ratchet COMPLETE

| Phase | Floor | Measured | PR | Status |
|---|---|---|---|---|
| 1 | 60 | 60.99% | [#383](https://github.com/agentculture/culture/pull/383) | ✅ merged |
| 2 | 62 | 62.91% | [#384](https://github.com/agentculture/culture/pull/384) | ✅ merged |
| 3a | 67 | 67.89% | [#385](https://github.com/agentculture/culture/pull/385) | ✅ merged |
| 3b | 73 | 73.40% | [#386](https://github.com/agentculture/culture/pull/386) | ✅ merged |
| 3c | 77 | 77.30% | [#387](https://github.com/agentculture/culture/pull/387) | ✅ merged |
| 4a | 79 | 79.68% | [#388](https://github.com/agentculture/culture/pull/388) | ✅ merged |
| 4b | 83 | 83.93% | [#389](https://github.com/agentculture/culture/pull/389) | ✅ merged |
| 5 | 89 | 89.13% | [#390](https://github.com/agentculture/culture/pull/390) | ✅ merged |
| 6 | 90 | 90.28% | (this PR) | in flight |

The plan started 2026-05-13 with the project at 60.01% and `fail_under = 56`; today's PR closes it at 90.28% with `fail_under = 90`. From here, the SonarCloud "Coverage on New Code ≥ 80%" gate continues to prevent regressions on new code, and the pytest floor at 90 prevents wholesale backsliding.
