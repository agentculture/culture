# cultureagent extraction — agent harness moves to a sibling repo

**Status:** Phase 1 cutover landed in culture 11.0.0; cultureagent 0.4.0 consumed
**Date:** 2026-05-09 (Phase 1 closeout: 2026-05-11)
**Author:** Ori Nachum + Claude (culture-side)

**Tracking:**

- Phase 0a integration test PRs (closed): #364, #365, #366, #367, #368, #369, #370, #371, #372, #373, #374, #375
- Phase 0b kickoff brief: [agentculture/cultureagent#3](https://github.com/agentculture/cultureagent/issues/3) — closed; cultureagent shipped 0.1.0 → 0.4.0 between 2026-05-09 and 2026-05-10 (full dedup arc — `__main__` + `skill/irc_client` + `Supervisor` to shared, `packages/agent-harness/` retired, `BaseDaemon` + `QueuedBaseDaemon` template-method introduced)
- Phase 1 cutover PR (this work): culture 11.0.0

**Two surprises vs. this spec, captured for posterity:**

1. **Subprocess retarget was a no-op.** The spec assumed `python -m culture.clients.<B>` was the daemon subprocess target. In reality culture's CLI launches daemons in-process via direct class instantiation in `culture/cli/agent.py`; the only `python -m culture.clients.<B>.skill.irc_client` strings in the tree were docstrings inside the per-backend `skill/irc_client.py` files (now deleted). systemd `ExecStart` is `culture agent start <name>`, which forks daemons in-process inside one Python process. Subprocess retarget step in §Phase 1 became a verification-only pass.
2. **Daemons are private in cultureagent's 0.4.0 stable surface but culture imports them directly.** Culture's CLI does `from cultureagent.clients.<B>.daemon import <Daemon>` to instantiate in-process. Hedged with `cultureagent~=0.4.0` (compatible-release pin blocks accidental minor bumps). Follow-up brief asks cultureagent to promote daemon classes to the stable surface; once shipped, culture relaxes the pin to `>=0.4,<1.0`.

## Goal

Extract culture's agent harness — `culture/clients/{shared,claude,codex,copilot,acp}/` plus the citation-pattern reference at `packages/agent-harness/` — into the sibling repo `cultureagent`. Culture keeps the integrated experience (operator CLI, IRCd integration, `culture agent <verb>`) by depending on `cultureagent` and re-exporting through thin shims; users who want a lighter install can `uv tool install cultureagent` and get the agent runtime alone.

The end state mirrors the [agentirc extraction](2026-04-30-agentirc-extraction-design.md) at the next layer of the stack: agentirc owns the IRCd, cultureagent owns the agent runtime, culture owns the integration.

## Non-goals

- We do **not** introduce a `cultureagent.api` façade. Culture imports raw modules through a declared stable surface (matching agentirc's 9.6.0 pattern). Façade design is tracked as a follow-up issue, deferred indefinitely.
- We do **not** change behavior in any harness module during the cutover. Improvements ride in separate PRs in cultureagent before or after, not bundled with the move.
- We do **not** retire `culture agent` as a verb. It survives long-term as the integrated entry point; users opt out by installing `cultureagent` alone.
- We do **not** retire the citation pattern *inside* cultureagent. `packages/agent-harness/` moves with the rest, and the cite-don't-import seam between cultureagent's own backend trees is preserved.
- We do **not** bundle the agentirc 10.0 cleanup (removing `culture/agentirc/config.py` shim) into this work. Separate concern, separate PR.

## Background

The agent harness today lives in two layers inside culture:

- **`culture/clients/shared/`** — modules with no backend-specific behavior (`attention`, `ipc`, `irc_transport`, `message_buffer`, `rooms`, `socket_server`, `telemetry`, `webhook`, `webhook_types`). Every backend imports these directly. Established by [the shared harness modules spec](2026-05-09-shared-harness-modules-design.md) (PR #357).
- **`culture/clients/<backend>/`** — per-backend working copies, cited from `packages/agent-harness/`. Each backend owns `agent_runner.py`, `supervisor.py`, `daemon.py`, `config.py`, `constants.py`, `culture.yaml`, and a `skill/` subdirectory.

Culture's operator CLI reaches into `clients/` from several places:

- `culture/cli/agent.py` — imports per-backend `AgentConfig`/`DaemonConfig` to validate user-provided YAML
- `culture/cli/shared/ipc.py` and `culture/overview/collector.py` — use `culture.clients.shared.ipc` for whisper-protocol primitives
- `culture/mesh_config.py` — uses shared modules
- `python -m culture.clients.<backend>` — subprocess `ExecStart` target for `culture agent start <name>`

`cultureagent` was bootstrapped at the AFI-CLI frame (v0.1.0, [agentculture/cultureagent#2](https://github.com/agentculture/cultureagent/pull/2)) with a documented migration playbook in its `CLAUDE.md`. The repo is ready to receive the harness; the question this spec answers is *how* the move happens without breaking culture's integrated experience or violating the cross-repo handoff rule.

## Constraints

- **Read-across-edit-within.** Culture-side agent only edits culture; cultureagent-side agent only edits cultureagent. Cross-repo communication is `/communicate post-issue.sh` only. Either side can read the other's tree; neither writes to it.
- **All-backends rule.** No phase leaves the four backends in mismatched states. They move together.
- **Test-as-if-yours, fix-upstream.** Culture keeps integration tests forever. When an integration test fails on the cutover, the brief goes upstream to cultureagent.
- **Behavior preservation.** Functionality unchanged across the move. "Maybe improved" lives in cultureagent PRs separate from receive-the-code PRs.
- **Coverage growth tracked per-domain** — culture's pytest `fail_under` ratchets from PR #362's locked baseline (`56`) toward the post-Phase-0a projection (~73 project-wide; ~85 on `culture/clients/`); cultureagent commits to ≥80% on harness internals it owns.

## Architecture

### End-state layout

```
~/git/culture/
├── culture/
│   ├── agentirc/            (unchanged — A1 shim still here)
│   ├── cli/                 (operator CLI)
│   ├── transport/           (culture's user of agentirc)
│   ├── bots/                (BotManager, virtual_client subclass)
│   └── clients/
│       ├── shared/          (re-export shims: from cultureagent.clients.shared.X import *)
│       ├── claude/          (re-export shims for config/constants only)
│       ├── codex/           (same)
│       ├── copilot/         (same)
│       └── acp/             (same)
├── packages/                (DELETED — agent-harness lives in cultureagent now)
└── tests/                   (integration-only; harness unit tests gone)

~/git/cultureagent/
├── cultureagent/
│   ├── cli/                 (existing AFI frame)
│   └── clients/
│       ├── shared/          (canonical shared tier)
│       ├── claude/          (canonical: agent_runner, supervisor, daemon, config, constants, culture.yaml, skill/)
│       ├── codex/
│       ├── copilot/
│       └── acp/
├── packages/agent-harness/  (canonical reference impl)
├── docs/api-stability.md    (declares the stable import surface)
└── tests/                   (harness unit tests)
```

Culture's `pyproject.toml` gains `cultureagent>=X.Y,<X.(Y+1)` as a runtime dep. Two install modes coexist long-term:

- `uv tool install culture` → integrated experience (pulls cultureagent transitively, full operator CLI)
- `uv tool install cultureagent` → lighter install, agent runtime only, no IRCd, no `culture agent` verb

### Stable import surface

cultureagent's `docs/api-stability.md` declares these importable and behavior-stable through 0.x; breaking changes only in major bumps. Culture imports only from this list (through re-export shims, see §"Re-export shim mechanics").

**Shared tier (Python imports):**

- `cultureagent.clients.shared.attention.{AttentionTracker, Band, ...}`
- `cultureagent.clients.shared.message_buffer.MessageBuffer`
- `cultureagent.clients.shared.ipc.{decode_message, encode_message, make_request, make_response, ...}`
- `cultureagent.clients.shared.irc_transport.IRCTransport`
- `cultureagent.clients.shared.socket_server.SocketServer`
- `cultureagent.clients.shared.telemetry.init_harness_telemetry`
- `cultureagent.clients.shared.webhook.{AlertEvent, WebhookClient}`
- `cultureagent.clients.shared.webhook_types.WebhookConfig`
- `cultureagent.clients.shared.rooms.parse_room_meta`

**Per-backend (Python imports — schema only, no behavior):**

- `cultureagent.clients.<backend>.config.{AgentConfig, DaemonConfig}` for each of `claude`, `codex`, `copilot`, `acp`
- `cultureagent.clients.<backend>.constants.*`

**Per-backend (subprocess entry points — used by culture's `culture agent start`):**

- `python -m cultureagent.clients.<backend>` (daemon entry)
- `python -m cultureagent.clients.<backend>.skill.irc_client` (whisper IPC client)

Everything else (`agent_runner.py`, `supervisor.py`, `daemon.py` internals, helper functions, etc.) is **private**. cultureagent can refactor at will without majoring.

### Re-export shim mechanics

After cutover, every `culture/clients/...` file that culture itself imports becomes a re-export shim. Example for `culture/clients/shared/attention.py`:

```python
"""Re-export shim — see cultureagent.clients.shared.attention.

This module is kept so that existing `from culture.clients.shared.attention
import AttentionTracker` calls in culture's CLI and tests keep working.
The implementation lives in cultureagent; bug reports go upstream.
"""
from cultureagent.clients.shared.attention import *  # noqa: F401, F403
from cultureagent.clients.shared.attention import (  # noqa: F401
    AttentionTracker,
    Band,
)
```

The explicit-named re-imports keep `pylint`/`flake8` happy and make the surface visible to readers. Files needing shims:

- `culture/clients/shared/{attention,ipc,irc_transport,message_buffer,rooms,socket_server,telemetry,webhook,webhook_types}.py` (9 files)
- `culture/clients/<backend>/{config,constants}.py` for each of `claude`, `codex`, `copilot`, `acp` (8 files)

Files **deleted** from culture (no shim, not imported by culture):

- `culture/clients/<backend>/{agent_runner,supervisor,daemon}.py` (12 files)
- `culture/clients/<backend>/culture.yaml` (4 files — moved to cultureagent)
- `culture/clients/<backend>/skill/` (4 directories — moved to cultureagent)
- `culture/clients/<backend>/__main__.py` if present (subprocess target moves)
- `packages/agent-harness/*` (entire directory)

### Subprocess retarget

`culture/cli/agent.py` spawns agent daemons via subprocess. Today's `ExecStart` is `python -m culture.clients.<backend>`. After cutover: `python -m cultureagent.clients.<backend>`. Same applies to systemd unit emission in `culture/mesh/` (the templated `ExecStart` strings) and to the skill helper subprocess targets (`python -m culture.clients.<backend>.skill.irc_client` → `python -m cultureagent.clients.<backend>.skill.irc_client`).

Audit during Phase 1:

```bash
grep -rn "python -m culture.clients" --include='*.py' --include='*.yaml'
```

Every match becomes `python -m cultureagent.clients`. The shims in `culture/clients/<backend>/__main__.py` are *not* added — culture stops being a subprocess target for agent daemons.

## Phasing

Three phases, one of which produces zero culture PRs.

### Phase 0a — culture pre-cutover test reinforcement

Audit which behaviors are currently proven only by harness unit tests. For each, add an integration test in culture that starts a real `agentirc.IRCd`, registers an agent, and asserts behavior end-to-end through the cross-package import chain.

Behaviors needing integration coverage (audit may find more):

- Attention state transitions (mention bumps band, idle decay, dynamic levels) — currently in `tests/harness/test_attention.py` + `test_attention_config.py`
- Message buffer overflow and drain — `tests/test_message_buffer.py`
- IRC transport tag propagation, reconnect — `tests/test_irc_transport.py`, `test_irc_transport_tags.py`
- Webhook fanout (HTTP + IRC alert) — `tests/test_webhook.py`, `test_webhook_config_shared.py`
- Telemetry counter emission — `tests/harness/test_daemon_telemetry.py`, `test_telemetry_module.py`
- ~~Supervisor restart-on-crash — `tests/test_supervisor.py`~~ — **dropped post-#363 review.** `supervisor.py` is the LLM verdict evaluator (`Supervisor` class with `evaluate()`, `SupervisorVerdict.parse()`, `make_sdk_evaluate_fn`), not a process supervisor. Unit tests are the right shape; no integration substitute. Moves to cultureagent in Phase 1.
- Per-backend agent_runner timeout, parity — `tests/harness/test_agent_runner_*.py`, `test_all_backends_parity.py`

Each new integration test stays in culture forever. Multiple small culture PRs are fine; this phase doesn't need to ship as one PR.

**Phase 0a closeout** activates two enforcement gates that PR #362 partially established:

- **pytest CI (`pyproject.toml`):** ratchet `[tool.coverage.report] fail_under` from PR #362's locked baseline (`56`) to the post-Phase-0a measured floor (~73 project-wide; ~85 on `culture/clients/`).
- **SonarCloud Quality Gate (culture project, optional):** raise the overall coverage condition (or add a `culture/clients/**`-scoped condition per `docs/coverage-baseline.md`'s growth path). Set in SonarCloud project settings; `sonar.qualitygate.wait=true` already blocks CI on the gate. `SonarSource/sonarqube-scan-action` was wired into `tests.yml` by PR #362, so analyses upload on every PR.

The "95% project-wide" target the original audit warned was unrealistic has been **dropped** in favor of this locked-floor-and-ratchet shape. The audit measured today's project-wide coverage at 56.86%; even closing every harness gap (Tasks 2–8) lifts that by ~16 pp to ~73%. `docs/coverage-baseline.md` documents the per-domain growth path and locks the floor as Phase 0a progresses.

Phase 0a does not require version bumps beyond patch-per-PR.

### Phase 0b — cultureagent buildup

**Zero culture PRs.** A single comprehensive brief is posted to cultureagent's GitHub Issues via `/communicate post-issue.sh`. Brief content described in §"Brief format".

cultureagent's agent receives, lays out the files at the target paths, gets unit tests green, ships releases. Internal phasing inside cultureagent is its choice (it may ship `0.2.0` with shared tier first, then `0.3.0` with backends; or one large `0.3.0`). Culture is unchanged through all of this.

The "ready" criteria culture waits for:

- Tagged release on PyPI matching the target dep pin (e.g., `cultureagent==0.3.0`)
- `docs/api-stability.md` in cultureagent declares the surface from §"Stable import surface"
- ≥80% coverage gate active on cultureagent's CI (harness internals)
- All harness unit tests green

When cultureagent posts back ("ready, here's the tag"), Phase 1 begins.

### Phase 1 — culture cutover

One culture branch, one PR. May involve N round-trips with cultureagent inside the PR window before going green.

**Cutover branch tasks:**

1. Add `cultureagent>=X.Y,<X.(Y+1)` to `pyproject.toml`; refresh `uv.lock`
2. Replace `culture/clients/shared/*.py` with re-export shims (9 files)
3. Replace `culture/clients/<backend>/{config,constants}.py` with re-export shims (8 files)
4. Delete `culture/clients/<backend>/{agent_runner,supervisor,daemon}.py` (12 files)
5. Delete `culture/clients/<backend>/culture.yaml`, `skill/`, `__main__.py` (per-backend)
6. Delete `packages/agent-harness/`
7. Retarget subprocess strings (`python -m culture.clients` → `python -m cultureagent.clients`)
8. Delete harness unit tests: `tests/harness/*`, `tests/test_daemon*.py`, `tests/test_supervisor.py`, `tests/test_{codex,acp,copilot}_daemon.py`, `tests/test_message_buffer.py`, `tests/test_irc_transport*.py`, `tests/test_socket_server.py`, `tests/test_skill_client.py`, `tests/test_webhook.py`, `tests/test_attention*.py`, `tests/test_agent_runner*.py`, `tests/test_telemetry*.py` (audit during execution)
9. Update `docs/` to reflect the split
10. Run integration tests + SonarCloud gate

**Round-trip loop (when CI fails):**

```
push → CI red → /communicate brief to cultureagent → wait for fix/pushback/instruct → push again
```

Three resolution shapes from cultureagent:

- **Fix:** new release shipped, culture bumps the pin, re-pushes
- **Pushback:** "the contract was never X" — culture adapts the test/usage
- **Instruct:** "use Y.foo() instead of X.bar()" — culture rewrites the import/call

**Version bump:** `11.0.0` major — deletes are breaking even though shims preserve most imports (the deletes of `agent_runner`/`supervisor`/`daemon` modules are observable for any external caller importing them, and the runtime dep change itself is a major event for packagers).

## Brief format

Two flavors of brief. Both posted to cultureagent's GitHub Issues via `/communicate post-issue.sh`, signed `- culture (Claude)`. Reciprocal briefs from cultureagent come back the same way to culture's repo.

### Initial migration brief (Phase 0b kickoff)

```text
Title: Migration: receive harness from culture (Phase 0b)

Body:
- Spec: <link to this design doc on culture's main>
- Source paths to read in ../culture (you can read these directly):
  - culture/clients/shared/{attention,ipc,irc_transport,message_buffer,
    rooms,socket_server,telemetry,webhook,webhook_types}.py
  - culture/clients/{claude,codex,copilot,acp}/{agent_runner,supervisor,
    daemon,config,constants}.py + culture.yaml + skill/
  - packages/agent-harness/* (canonical reference impl)
  - tests/harness/* + tests/test_{daemon,supervisor,
    {codex,acp,copilot}_daemon,...}.py (unit tests follow the code)
- Target layout in cultureagent: see spec §Architecture
- Stable surface to commit to: see spec §Stable import surface
- Acceptance for "ready": tagged release with docs/api-stability.md
  declaring the surface; ≥80% coverage gate on cultureagent CI (harness internals);
  all harness unit tests green.
- Internal phasing inside cultureagent is your call; culture flips
  when you tag the ready release.

— culture (Claude)
```

### Round-trip brief (during Phase 1 cutover)

```text
Title: <module>.<symbol>: <one-line summary>

Body:
- What I tried: <action / import / call>
- What broke: <pytest output or stack trace>
- What I expected: <the contract from §Stable import surface or my reading>
- Your call: fix / pushback / instruct — no preference

— culture (Claude)
```

cultureagent's reply is one of:

- **Fix:** "Fixed in `cultureagent 0.X.Y+1`. Bump your pin." — culture bumps and re-pushes.
- **Pushback:** "This isn't a bug; the contract was never X, here's why. Adapt your test/usage." — culture adapts.
- **Instruct:** "You're calling it wrong. Use `Y.foo(...)` instead of `X.bar(...)`." — culture rewrites the import/call.

## Testing

### Coverage gate

Coverage gates are asymmetric — culture grows project-wide via per-task ratchet; cultureagent commits to a domain-specific floor.

- **Culture side (this spec, Phase 0a):** project-wide coverage measured against `sonar.sources=culture` (configured by PR #362). Enforced via `[tool.coverage.report] fail_under` in `pyproject.toml` (PR #362 locked at `56`; Task 9 closeout ratchets to the post-Phase-0a measured floor, ~73). SonarCloud's Quality Gate (`sonar.qualitygate.wait=true`) is the upstream enforcement; the scanner uploads on every PR via `SonarSource/sonarqube-scan-action` since PR #362.
- **cultureagent side (in the brief):** ≥80% gate on cultureagent's own CI for harness internals it owns. cultureagent already has the cicd skill vendored from steward, so SonarCloud setup follows the same pattern. cultureagent's agent owns the wiring details.

### Test movement

Per the rule **"Anything we serve, we must test; unit tests follow the code"**:

- **Stay in culture:** integration tests that exercise the agent-on-mesh stack (IRCd + agent + operator CLI together). Examples: `test_integration_layer5.py`, `test_archive.py`, mention pipeline tests, channel CLI tests.
- **Move to cultureagent (via brief):** unit tests of harness internals. Examples: all of `tests/harness/`, `tests/test_daemon*.py`, `tests/test_supervisor.py`, `tests/test_{codex,acp,copilot}_daemon.py`, `tests/test_message_buffer.py`, `tests/test_irc_transport*.py`, `tests/test_socket_server.py`, `tests/test_skill_client.py`, `tests/test_webhook.py`, `tests/test_attention*.py`, `tests/test_agent_runner*.py`, `tests/test_telemetry*.py`.
- **Borderline cases** are resolved by the rule: does this test exercise something culture *serves* (i.e., uses the integrated stack), or does it exercise harness internals only? Borderline cases default to moving (per the rule "if code is no longer here, it's pointless to test it").

Mechanically, "move" means: brief tells cultureagent to copy the test files; the same Phase 1 PR deletes them from culture. Both sides happen in the same release pair.

## Follow-up issues (opened on culture's repo)

Two GitHub issues opened on `agentculture/culture` to track future work culture wants from upstream sibling repos:

1. **"Façade design for cultureagent (`cultureagent.api`)"** — Once the raw-import surface stabilizes, design a named-entry-point façade: `cultureagent.api.start_agent(nick, backend)`, `cultureagent.api.parse_agent_config(backend, path)`, etc. Cite this spec's Section "Stable import surface" choice (Model 1 chosen now, Model 2 deferred). Stays open indefinitely; converts into a brief to cultureagent when culture is ready to consume a façade.
2. **"Façade design for agentirc (extends 9.6.0 stable surface)"** — Symmetric ask for agentirc. Today culture imports flat modules (`agentirc.config`, `agentirc.cli`, `agentirc.protocol`, `agentirc.ircd`, `agentirc.virtual_client`) and reaches into `agentirc.channel.Channel` for type hints. A façade with explicit verbs would let culture stop reaching into internal modules. Cite the agentirc 9.6.0 stable surface as the starting point. Stays open indefinitely.

Both issues are tracking, not committed work. When the time comes to actually build either façade, culture writes a brief from the issue to the relevant sibling repo.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| **Coverage gap surfaces only at cutover** — Phase 0a misses an integration path, Phase 1 PR fails CI for behavior reasons | Round-trip brief loop is designed for this. Failure during Phase 1 is normal, not exceptional. |
| **cultureagent and culture diverge on subprocess entry-point semantics** (e.g., env vars, working dir, signals) | Phase 0a integration test must launch real subprocess via `culture agent start` and assert lifecycle. Catches divergence before Phase 1. |
| **Re-export shims confuse `pylint` / `mypy`** | Use explicit re-imports (named symbols) in addition to `from … import *`. Pattern proven in `culture/agentirc/config.py` (A1 shim). |
| **Phase 0b takes longer than expected; culture work blocks waiting for cultureagent** | Phase 0a is independent of Phase 0b. Culture can keep shipping integration-test PRs (and any other normal work) while cultureagent builds. |
| **All-backends rule violation if cultureagent ships shared tier first, then backends, and culture flips between releases** | Culture only flips once, on cultureagent's "ready" release that includes all four backends. Mid-build cultureagent releases are not consumed. |
| **Deleted unit tests reduce overall coverage below the locked floor after Phase 0a integration tests are added** | The pytest `fail_under` value at Phase 0a closeout reflects current measured coverage (PR #362 locked at 56; Task 9 ratchets to post-Phase-0a floor ~73). The cutover PR (Phase 1) re-runs against that locked value with the deletes applied. If it fails, Phase 0a needs more integration tests before retry. |
| **`cultureagent` lockfile drift from culture** | culture pins `cultureagent>=X.Y,<X.(Y+1)`; future bumps are normal dep-bump PRs. Same pattern as agentirc-cli. |

## What this spec produces vs. what execution produces

**This spec produces:**

- The design (this document), committed to culture
- Two follow-up tracking issues opened on culture's repo (façade trackers)
- An implementation plan for Phase 0a (next step after spec approval, via `writing-plans` skill)

**Execution produces (deferred to implementation):**

- Phase 0a integration test PRs in culture (one or several)
- The Phase 0b initial migration brief, sent at the end of Phase 0a
- The Phase 1 cutover branch and PR in culture
