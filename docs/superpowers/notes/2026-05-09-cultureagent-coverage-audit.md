# cultureagent extraction — Phase 0a coverage audit

**Date:** 2026-05-09
**Spec:** [`docs/superpowers/specs/2026-05-09-cultureagent-extraction-design.md`](../specs/2026-05-09-cultureagent-extraction-design.md)
**Plan:** [`docs/superpowers/plans/2026-05-09-cultureagent-extraction-phase-0a.md`](../plans/2026-05-09-cultureagent-extraction-phase-0a.md)

---

## Status update — 2026-05-09 (post-PR-#362)

Two top-line findings in this audit have been **resolved** by [PR #362](https://github.com/agentculture/culture/pull/362):

- ✅ **SonarCloud scanner wired into CI.** The `SonarSource/sonarqube-scan-action@fd88b7d` step lands in `.github/workflows/tests.yml`, `qualitygate.wait=true` blocks CI on the gate decision, and SonarCloud's automatic analysis was disabled so CI is the sole analysis path.
- ✅ **Project key migrated** from `OriNachum_culture` to `agentculture_culture` (`sonar.organization=agentculture` added).
- ✅ **Coverage baseline locked** via pytest's `[tool.coverage.report] fail_under = 56` (rather than the 95% project-wide target the original audit warned was unrealistic). [`docs/coverage-baseline.md`](../../coverage-baseline.md) documents the per-domain growth path through Phase 0a.
- ⏳ **Per-domain enforcement** (e.g., `--cov=culture/clients --cov-fail-under=80`) deferred to Task 9 closeout per the audit's recommendation #3a.

The "Pre-existing finding" and "Recommendations for plan updates" sections below are kept as the historical audit record. Items already resolved are flagged inline.

---

## TL;DR

- **Baseline coverage (full suite):** **56.86%** (7556/13289 lines covered) — **far below the 95% gate Phase 0a closeout (Task 9) plans to flip on**.
- **Baseline coverage (integration-only, `tests/test_integration_layer5.py`):** **15.95%** (2120/13289 lines covered).
- **Delta the harness unit tests close (full vs. integration-only):** **40.91 percentage points** — but most of that delta lives in *non-harness* code (CLI, server, agentirc, console, transport) that integration tests can't realistically substitute for.
- **Harness-module-only coverage today (full suite):** **56%** of 5304 lines (`culture/clients/{shared,acp,claude,codex,copilot}/{daemon,agent_runner,supervisor,config,constants}.py` + `culture/clients/shared/*`).
- **Harness-module-only coverage (integration-only):** **16%** of 5304 lines — the cite-don't-import non-claude backends (`acp`, `codex`, `copilot`) get **0%** from `test_integration_layer5.py` because that test only spins up the `claude` daemon.

**Top-line finding (RESOLVED in PR #362):** SonarCloud was configured but not wired into CI — fixed in [PR #362](https://github.com/agentculture/culture/pull/362). See "Status update — 2026-05-09 (post-PR-#362)" above for the full resolution scope.

**Secondary finding (RESOLVED — different shape than originally proposed):** The original audit suggested scoping `--cov-fail-under` to `culture/clients/` because 95% project-wide was unrealistic. PR #362 took a different shape — kept project-wide scope but locked `fail_under = 56` (today's measured floor) and grows it per-task toward ~73%. Per-domain enforcement is deferred to Task 9 closeout.

---

## Pre-existing finding: SonarCloud scanner not wired to CI

> **✅ RESOLVED in PR #362.** What follows is the original audit text, kept as historical record. The recommended remediation has been applied; see "Status update — 2026-05-09 (post-PR-#362)" at the top for the full resolution.

This finding is **not caused by Phase 0a work** but the audit surfaced it and it directly affects Task 9's remediation steps.

### Symptoms

1. `sonar-project.properties` exists and looks complete — it sets `sonar.projectKey=OriNachum_culture`, `sonar.python.coverage.reportPaths=coverage.xml`, and crucially `sonar.qualitygate.wait=true`.
2. `.github/workflows/tests.yml` runs `pytest --cov=culture --cov-report=xml:coverage.xml`, producing `coverage.xml`, and then **discards it** — there is no `sonar-scanner` step or `SonarSource/sonarqube-scan-action` invocation.
3. The SonarCloud project for `OriNachum_culture` has **zero analyses ever** (controller verified via `/api/project_analyses/search?project=OriNachum_culture&ps=3` → `analyses: []`).
4. `pr-comments.sh` Section 4 (vendored from steward) queries SonarCloud's API for new issues — and correctly reports nothing, because the scanner has never run.
5. `SONAR_TOKEN` is present locally and as a GitHub secret, but no workflow step references it.

### Why this matters for Task 9

The Phase 0a plan's Task 9 ("Coverage gate flip — closeout") has these steps:

> Step 4 — Update SonarCloud Quality Gate at https://sonarcloud.io/project/quality_gate?id=OriNachum_culture
> Step 5 — Bump version (minor)
> Step 6 — Run tests locally to confirm the new gate passes

This assumes SonarCloud is uploading analyses on every PR. **It isn't.** Flipping the gate's threshold has no observable effect until the scanner is wired up.

### Recommended remediation (insert into Task 9 as a new Step 0)

Wire `sonar-scanner` into `.github/workflows/tests.yml` as a job step that runs after `pytest` produces `coverage.xml`. The standard pinned-action pattern is:

```yaml
      - name: SonarCloud scan
        uses: SonarSource/sonarqube-scan-action@<pinned-sha>  # v5.x
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Pin the action SHA (CICD policy). Run a throwaway PR to confirm:

1. The action picks up `sonar-project.properties` and `coverage.xml`.
2. SonarCloud receives the analysis (visible in the project's "Analyses" list).
3. `sonar.qualitygate.wait=true` blocks the workflow until the gate decides.
4. `pr-comments.sh` Section 4 surfaces real findings.

**Only after this is verified** does Step 4's UI/API gate-threshold flip become meaningful.

### Suggested split

If the scanner-wiring change is non-trivial (e.g., needs a SonarCloud admin to allow the action's outbound token), do it as a separate PR *before* Task 9 — a `chore/wire-sonarcloud-scanner` PR. Then Task 9 becomes purely about flipping the threshold and running the closeout. Both options are acceptable; the audit doc just needs to make sure the wiring step doesn't get skipped.

---

## Methodology

1. Ran `uv run pytest -n auto --cov=culture --cov-report=term-missing --cov-report=html --cov-report=xml -q` to get the **full-suite baseline** — captured to `/tmp/culture-tests/coverage-audit.log`.
2. Ran `uv run pytest tests/test_integration_layer5.py --cov=culture --cov-report=term --no-cov-on-fail -q` to get the **integration-only baseline** — captured to `/tmp/culture-tests/integration-baseline.log`.
3. Ran `uv run coverage report --include='culture/clients/...'` after each to get **per-module breakdowns** (`/tmp/culture-tests/harness-coverage.txt`, `/tmp/culture-tests/harness-integration-baseline.txt`).
4. For each harness unit test file, ran `uv run pytest <file> --cov=culture --cov-report=term --no-cov-on-fail -q` standalone to measure its *standalone* coverage (`/tmp/culture-tests/per-test-deltas.log`). The standalone number minus the integration-only number is a rough proxy for the test's unique contribution; for some tests (e.g. `test_daemon_telemetry.py`, which reads source files as text) the standalone delta is essentially 0 because the test doesn't actually execute the production code it claims to cover.
5. For specific shared modules (`attention`, `message_buffer`, `irc_transport`, `webhook`, `telemetry`, `socket_server`, `ipc`), ran the unit test → module pair with `--cov-report=term-missing` to get **real line ranges** (`/tmp/culture-tests/per-module-deltas.log`, `/tmp/culture-tests/per-test-module-deltas.log`).

All commands are reproducible from this branch.

---

## Per-behavior gap list

Line ranges are taken from `--cov-report=term-missing` output captured in the methodology step. "Unique contribution" is the delta between standalone-unit-test coverage of the named module and integration-only coverage of the same module.

| # | Behavior | Unit test source | Production code uniquely covered | Module delta (unit→integration baseline) | Decision |
|---|---|---|---|---|---|
| 1 | Attention band transitions, mention bumps, idle decay | `tests/harness/test_attention.py` (16 tests) | `culture/clients/shared/attention.py` — covered lines closed: 91-93, 99, 130-137, 146, 156-163, 181-194, 198-199, 214, 232-245 | unit: 97% / int: 58% / **+39 pp** | **ADD integration** (Task 2) |
| 2 | Dynamic attention levels (per-channel, per-band config) | `tests/harness/test_attention_config.py` (11 tests) | Same module, same line span; supersedes #1's coverage of dynamic-config code paths | (rolled into #1) | **ADD integration** (Task 2, second test in same file) |
| 3 | Message buffer overflow + retention semantics | `tests/test_message_buffer.py` | `culture/clients/shared/message_buffer.py` — lines 39, 53, 63-67, 70-76 (drain logic, dedupe, overflow eviction); 76% standalone vs. 72% integration | unit: 76% / int: 72% / **+4 pp** | **ADD integration** (Task 3) — small absolute gap but the missing lines are the overflow path itself, which is the behavior under test |
| 4 | IRC transport tag propagation (IRCv3 `@tag` parsing/forwarding) | `tests/harness/test_irc_transport_propagation.py` + `tests/test_irc_transport_tags.py` | `culture/clients/shared/irc_transport.py` — lines 106, 123-124, 158, 184, 191, 216, 231, 251-262, 271-272, 288, 294, 313, 364, 375-377, 382-384 | combined: 75% / int: 63% / **+12 pp** | **ADD integration** (Task 4, first test) |
| 5 | IRC transport reconnect after server bounce | `tests/test_irc_transport.py` | Same module — reconnect path lines 251-262, 271-272 (subset of #4) | (rolled into #4) | **ADD integration** (Task 4, second test — already a `pytest.skip` placeholder in plan) |
| 6 | HTTP webhook fanout (POST to capture URL on trigger) | `tests/test_webhook.py` | `culture/clients/shared/webhook.py` — lines 37, 42, 53, 59-71 (HTTP send path + retries) | unit: 91% / int: 69% / **+22 pp** | **ADD integration** (Task 5, first test) |
| 7 | Webhook IRC alert channel (config + on-trigger PRIVMSG to alert chan) | `tests/harness/test_webhook_config_shared.py` | `culture/clients/shared/webhook_types.py` (alert config); `webhook.py` lines 49-50, 55-56 (trigger formatter) | small (webhook_types is 100% in both) but the **trigger formatter** lines 49-56 are exercised | **ADD integration** (Task 5, second test — `pytest.skip` placeholder in plan) |
| 8 | Telemetry counter + span emission during real ops | `tests/harness/test_telemetry_module.py` (init/getters) | `culture/clients/shared/telemetry.py` — lines 88-92, 115-127, 313-320 (counter creation, metric reader binding) | unit: 64% / int: 49% / **+15 pp** | **ADD integration** (Task 6, single end-to-end test using `metrics_reader` + `tracing_exporter` fixtures) |
| 9 | Daemon telemetry initialization wiring (asserts daemon imports `init_harness_telemetry` and calls it at start) | `tests/harness/test_daemon_telemetry.py` | **None executed** — this test reads `daemon.py` source as text and asserts on its content (parity-style) | unit standalone produced **0% coverage** of `culture/clients/claude/daemon.py` (the test doesn't run the code) | **ACCEPT** — this is a structural lint, not a runtime test. Phase 1: moves to cultureagent or replaced by an in-cultureagent equivalent. Task 6 still adds the *runtime* telemetry integration coverage. |
| 10 | ~~Supervisor restart-on-crash via real subprocess lifecycle~~ — **misread in original audit**: `culture/clients/<backend>/supervisor.py` is the LLM verdict evaluator (`Supervisor` class with `evaluate()`, `SupervisorVerdict.parse()`, `make_sdk_evaluate_fn`), not a process supervisor. There is no subprocess lifecycle to test. | `tests/test_supervisor.py` | `culture/clients/<backend>/supervisor.py` — verdict parsing, rolling window, whisper-on-correction, escalation-after-threshold, SDK evaluate-fn wrapping (all unit-testable) | claude: 93% standalone vs. ≈42% integration / **+51 pp**; non-claude backends drop from ≈40% to **0%** integration-only | **ACCEPT** — unit tests in `tests/test_supervisor.py` are the right shape; move to cultureagent in Phase 1. No integration substitute. |
| 11 | agent_runner per-turn timeout — claude | `tests/harness/test_agent_runner_claude.py` | `culture/clients/claude/agent_runner.py` — uncovered after this test runs alone: lines 31-39, 99-102, 111-121, 124, 128, 132, 146, 148, 155, 159-161, 180-181, 221, 239-253, 258 (≈64% standalone) | unit: 64% / int: 21% / **+43 pp** for claude | **ADD integration** (Task 8, parameterized over backend, claude row) |
| 12 | agent_runner per-turn timeout — codex | `tests/harness/test_agent_runner_codex.py` | `culture/clients/codex/agent_runner.py` — drops from full-suite 35% to integration **0%** | full: 35% / int: 0% / **+35 pp** | **ADD integration** (Task 8, codex row) |
| 13 | agent_runner per-turn timeout — copilot | `tests/harness/test_agent_runner_copilot.py` | `culture/clients/copilot/agent_runner.py` — drops from 51% to 0% | full: 51% / int: 0% / **+51 pp** | **ADD integration** (Task 8, copilot row) |
| 14 | agent_runner per-turn timeout — acp | `tests/harness/test_agent_runner_acp.py` | `culture/clients/acp/agent_runner.py` — drops from 47% to 0% | full: 47% / int: 0% / **+47 pp** | **ADD integration** (Task 8, acp row) |
| 15 | LLM-call recording / observation logging | `tests/harness/test_record_llm_call.py` | Per-backend `agent_runner.py` recording paths; mostly subsumed by #11–14 if those exercise the recording side-effect | small (recording is on the hot path of the runner) | **EVALUATE** — likely covered by Task 8 once that test runs an actual turn. If the recording path stays uncovered after Task 8, file as a follow-up; do not block Task 9. |
| 16 | All-backends parity (asserts byte-equivalence of cited files between backends) | `tests/harness/test_all_backends_parity.py` | **None executed** — this is a structural diff test on source files | standalone produces 0 coverage of harness modules | **ACCEPT** — moves to cultureagent in Phase 1; the post-extraction analog is "all backends still cite from packages/agent-harness/ in cultureagent". No integration test substitute. |
| 17 | No-per-backend-copy guard for shared-tier modules | `tests/harness/test_no_per_backend_copy_of_shared_modules.py` | **None executed** — this asserts that `culture/clients/<backend>/` does NOT contain a file that should live in `clients/shared/` | standalone produces 0 coverage of harness modules | **ACCEPT** — architectural lint. Phase 1: either moves to cultureagent (asserting the same about its own layout) or is replaced by an explicit shim-layout test in culture asserting `cultureagent.clients.<backend>` is what culture reaches for. |
| 18 | Daemon config schema validation | `tests/test_daemon_config.py` | `culture/clients/claude/config.py` — lines around enum/path/url validators | small but includes error-path branches | **ACCEPT** — schema-level. Phase 1: moves to cultureagent. The runtime contract (a malformed config crashes the daemon at start) is already covered by `test_integration_layer5.py` indirectly. |
| 19 | Daemon IPC primitives (request/response framing on the unix socket) | `tests/test_daemon_ipc.py` | `culture/clients/shared/ipc.py` — drops from 100% (full suite) to 88% (integration-only); the missing 3 lines are error/edge cases on `recv_message` | full: 100% / int: 88% / **+12 pp** | **ALREADY COVERED** at the chain level by `test_integration_layer5.py` (every SkillClient call goes through ipc). The 12 pp delta is in error branches that an integration test won't hit reliably. **ACCEPT** the small loss. |
| 20 | Skill client commands (irc_send, irc_read, etc.) | `tests/test_skill_client.py` | `culture/clients/claude/skill/irc_client.py` — drops from 57% (full) to ≈26% (integration-only-ish) | unit only: 53% / int: ≈26% / **+27 pp** | **ALREADY COVERED** at the chain level — `test_integration_layer5.py` exercises `SkillClient.irc_send` and `irc_read`. The remaining lines are command surfaces (history/whois/lookup) that should be exercised by the new integration tests in Tasks 4–6 anyway. **ACCEPT** with a verification step in Task 9: re-measure after Tasks 4-6 land and confirm `irc_client.py` ≥ 80% under integration-only. |
| 21 | Socket server (unix socket accept + message framing) | `tests/test_socket_server.py` | `culture/clients/shared/socket_server.py` — same line span (32, 41-42, 62-67, 89-92, 105, 110-115, 121-128) under both unit-only and integration-only — i.e., **the unit test contributes 0 unique lines** | unit: 69% / int: 57% / **+12 pp** but the missing lines under unit-only are the same as under integration-only (just slightly different overlap) | **ALREADY COVERED** by integration chain. Lines 110-128 (the cleanup/shutdown paths) are uncovered under both — true gap, but neither approach closes it. **ACCEPT** the gap; flag for follow-up. |

### Decision summary (updated post-#363 review)

- **ADD integration** rows: 1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14 → **12 rows mapping to Plan Tasks 2–6 + Task 8** (Task 7 dropped — see row #10 update below).
- **ACCEPT** rows: 9, 10, 16, 17, 18, 19, 21 → **7 rows** (added row #10 — supervisor.py was misread as process supervisor; it's the LLM verdict evaluator, unit-testable, no integration substitute).
- **ALREADY COVERED** rows: 20 → **1 row** (with verification step deferred to Task 9 pre-flight).
- **EVALUATE** rows: 15 → **1 row** (resolved by Task 8 in practice; revisit at closeout).

---

## Recommendations for plan updates

Based on what the audit surfaced:

1. **Task 9: insert SonarCloud-scanner-wiring step** ✅ **RESOLVED in [PR #362](https://github.com/agentculture/culture/pull/362).** Original recommendation kept below as historical record: ~~(or split into prerequisite PR). See §"Pre-existing finding" above. **High priority** — without this, Task 9 has no observable effect on the Quality Gate.~~

2. **Task 7: parameterize over all 4 backends, not just claude.** **MOOT post-#363 review.** Task 7 is dropped entirely — `culture/clients/<backend>/supervisor.py` is the LLM verdict evaluator, not a process supervisor; the original "subprocess kill + restart" framing was a misread. The unit tests in `tests/test_supervisor.py` are the right shape and move to cultureagent in Phase 1 with no integration substitute. The four-backend parameterization pattern lives in Task 8 (agent_runner) instead. Original recommendation kept below as historical record: ~~The plan's Task 7 (`tests/test_integration_supervisor.py`) currently launches `python -m culture.clients.claude.supervisor` only. Per row #10, the integration-baseline coverage of `culture/clients/{codex,copilot,acp}/supervisor.py` is **0%**. If Task 7 doesn't parameterize, the closeout coverage will show three supervisor.py files at 0% and the gate will fail (or have to be set artificially low). Suggest the test mirror Task 8's `BACKEND_MODULES` pattern.~~

3. **Task 9: the 95% threshold is unrealistic at the project level.** ✅ **RESOLVED in [PR #362](https://github.com/agentculture/culture/pull/362) — different shape than originally proposed.** PR #362 locked `[tool.coverage.report] fail_under = 56` (today's measured floor) and grows it per-task toward ~73% project-wide; per-domain enforcement deferred to Task 9 closeout. Original recommendation kept below: Current full-suite coverage is 56.86%. Even if Tasks 2–8 close every gap above (≈+40 pp on the harness modules, ≈5304 of 13289 total lines), project-wide coverage rises by at most ≈16 pp to ≈73%. Three options for closeout:
   - **3a (recommended):** Scope the gate to `culture/clients/` only — `--cov=culture/clients --cov-fail-under=95`. This matches what Phase 0a is actually testing (the extraction targets) and is achievable.
   - **3b:** Lower the closeout threshold to a number Phase 0a can actually hit (e.g., 70% project-wide, 95% on `culture/clients/`).
   - **3c:** Add backfill-coverage tasks for `culture/transport/`, `culture/cli/`, `culture/console/`, `culture/agentirc/server.py` etc. before the closeout — multi-week effort, not in Phase 0a's scope.
   - The plan's spec already acknowledges this (line 180): "If 95% project-wide is unreachable without large-scale legacy backfill, the closeout PR documents the gap and the user decides…" — flag this **at audit time** rather than at closeout time so the user can pick before Tasks 2–8 are written. **Recommendation: 3a.**

4. **Task 6 should assert specific metric/span names.** ✅ **APPLIED IN THIS PR** (see plan Task 6 — added Step 1.5 that reads `culture/clients/shared/telemetry.py` for canonical constant names; assertions use exact names, not substring match). Original recommendation: The plan's Task 6 has the test assert `any("send" in n.lower() for n in metric_names)`. After this audit I confirmed the relevant uncovered lines in `telemetry.py` (88-92, 115-127, 313-320) — once you write the integration test, pull the actual constant names from `telemetry.py` (e.g. the counter prefix `culture.harness.*`) and assert them by name. The fuzzy substring match is fine as a bootstrapping placeholder but should harden before the closeout.

5. **Row #20 (skill client) needs a Task 9 pre-flight verification.** ✅ **APPLIED IN THIS PR** (see plan Task 9 Step 0a — re-measures `irc_client.py` coverage; if <80% under integration-only, adds a Task 8.5 before the gate flip). Original recommendation: After Tasks 2–8 land, re-run `uv run pytest tests/test_integration_*.py --cov=culture/clients/claude/skill/irc_client.py --cov-report=term`. If the coverage of `irc_client.py` is below 80% under integration-only, add a follow-up Task 8.5 to add a Skill-client-focused integration test before flipping the gate. The audit assumes this verification will pass; if it doesn't, it's a real gap.

---

## Acceptance criteria for closing this audit

- [x] Every row marked **ADD integration** has a Phase 0a task assigned (Tasks 2, 3, 4, 5, 6, 7, 8). The agent_runner timeout rows (#11–14) all map to Task 8's parameterization.
- [x] Every row marked **ACCEPT** has a one-sentence justification.
- [x] Every row marked **ALREADY COVERED** has a pointer to the existing test that covers it (`test_integration_layer5.py`).
- [x] The pre-existing SonarCloud-CI finding is documented and surfaced as a Task 9 prerequisite.
- [x] Plan-update recommendations are listed for the user to decide on before Tasks 2–8 are dispatched.

## Reproducing this audit

```bash
git checkout chore/cultureagent-extraction-coverage-audit

# Full-suite baseline
uv run pytest -n auto --cov=culture --cov-report=term-missing --cov-report=html --cov-report=xml -q 2>&1 | tee /tmp/culture-tests/coverage-audit.log
uv run coverage report --include='culture/clients/shared/*,culture/clients/*/daemon.py,culture/clients/*/agent_runner.py,culture/clients/*/supervisor.py,culture/clients/*/config.py,culture/clients/*/constants.py' > /tmp/culture-tests/harness-coverage.txt

# Integration-only baseline
uv run pytest tests/test_integration_layer5.py --cov=culture --cov-report=term --no-cov-on-fail -q 2>&1 | tee /tmp/culture-tests/integration-baseline.log
uv run coverage report --include='culture/clients/shared/*,culture/clients/*/daemon.py,culture/clients/*/agent_runner.py,culture/clients/*/supervisor.py,culture/clients/*/config.py,culture/clients/*/constants.py' > /tmp/culture-tests/harness-integration-baseline.txt
```

## Closeout (post-merge)

Phase 0a complete on **2026-05-09**. Final measured coverage:

- **Project-wide:** **56.96%** (vs 56.86% baseline — barely moved because harness unit tests still cover the same code paths; they delete in Phase 1).
- **`culture/clients/shared/`:** **80%** (vs ~58% baseline — **+22pp**, Phase 0a's real delivery).
- **`culture/clients/claude/`:** **72%** (vs ~85% baseline — measurement appears tighter under the post-Phase-0a full-suite run; the audit's 85% figure may have been optimistic).
- **`culture/clients/{codex,copilot,acp}/`:** ~43% each (essentially unchanged — Task 7 dropped, Task 8 narrowed to claude only).

Gate stays at **`fail_under = 56`** (matching `floor(56.96)`); SonarCloud gate kept at default Path B. See [`docs/coverage-baseline.md`](../../coverage-baseline.md) for the full per-domain table and follow-up list.

Phase 0a's six integration-test PRs (#364, #365, #366, #367, #368, #369) all merged. Ready to kick off Phase 0b (cultureagent buildup brief — separate writing-plans session).

**Audit-driven follow-ups** (out of Phase 0a; tracked for the cutover and beyond):

- **Task 8.5 — skill_client integration test.** Audit row #20's "ALREADY COVERED" assumed `test_integration_layer5.py` was sufficient; actual integration-only coverage of `culture/clients/claude/skill/irc_client.py` is 54%, below the in-repo plan's 80% threshold. A small `tests/test_integration_skill_client.py` is needed before Phase 1 deletes `tests/test_skill_client.py`.
- **Cross-backend integration timeout tests** for `codex`, `copilot`, `acp` (Task 8 narrowing).
- **Product fix — `AgentDaemon.stop()` should cancel/await `_background_tasks`** (PR #369 review #2 follow-up — currently mitigated test-locally by monkeypatching `_on_agent_exit`).
