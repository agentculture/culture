# Build Plan — Culture 14.1: the mesh grows itself from culture — a forwarded guild verb group provisions new sibling agents end-to-end (scaffold repo, run daemon, appear in the relationship overview, exchange messages), external sibling pins are drift-guarded so they can never silently lag again, and backend SDKs are optional extras so a slim install stays slim

slug: `culture-14-1-the-mesh-grows-itself-from-culture-a` · status: `exported` · from frame: `culture-14-1-the-mesh-grows-itself-from-culture-a`

> Culture 14.1: the mesh grows itself from culture — a forwarded guild verb group provisions new sibling agents end-to-end (scaffold repo, run daemon, appear in the relationship overview, exchange messages), external sibling pins are drift-guarded so they can never silently lag again, and backend SDKs are optional extras so a slim install stays slim

## Tasks

### t1 — PR1/t1 — pyproject extras restructure: move anthropic + claude-agent-sdk out of core deps; add extras claude=[cultureagent[backend-claude]], acp=[cultureagent[backend-acp]], keep copilot==0.2.0 pin, add all-backends meta-extra; add the SDKs to the dev dependency group so the suite keeps them; regenerate uv.lock

- covers: c11, c3, h5
- acceptance:
  - A no-extras resolve of culture contains neither claude-agent-sdk nor anthropic; culture[claude] resolves both via cultureagent[backend-claude] (verified against published cultureagent 0.4.x metadata); culture[all-backends] pulls github-copilot-sdk==0.2.0
  - Dev environment still provides the SDKs (tests/conftest.py imports keep working); full suite green at >=90% coverage; uv.lock staged with pyproject.toml

### t2 — PR1/t2 — missing-SDK remediation hints: wrap the backend SDK imports in the _create_<backend>_daemon factories (culture_core/cli/agents.py) so a missing SDK fails with the exact 'pip install culture[<extra>]' command, symmetric across claude/codex/copilot/acp (codex: documented no-SDK no-op)

- depends on: t1
- covers: h4
- acceptance:
  - With the SDK import monkeypatched away, starting each backend exits non-zero with a hint naming its extra (test per backend); backend-parity CI job passes (all four factories touched)

### t3 — PR1/t3 — slim-install smoke + culture-core sweep guard: test that culture_core/ contains zero claude_agent_sdk/anthropic imports (keeps the engine slim by construction); slim-install smoke (import culture + culture --version without SDKs, e.g. a no-extras CI step or tox-like venv test); repo-wide sweep asserting culture-core appears only in intended places (compat alias, CHANGELOG/specs history, Phase B hand-off refs)

- depends on: t1
- covers: c8, h14, c12, h6, h10
- acceptance:
  - A guard test fails if any culture_core module imports claude_agent_sdk or anthropic; a smoke proves slim install imports and runs the CLI; the sweep lists intended culture-core sites and fails on any new one

### t4 — PR1/t4 — install/extras doc + PR1 version bump: docs/ page for the slim-vs-extras install matrix (slim, [claude], [acp], [copilot], [all-backends]) incl. the changed default UX (claude backend now needs its extra) and remediation-hint behavior; /version-bump minor

- depends on: t1
- covers: h11
- acceptance:
  - Doc page explains every extra, maps each backend to its install command, and states the slim default; CHANGELOG entry present; version-check CI green

### t5 — PR2/t5 — pin guild-cli in pyproject: guild-cli>=0.18,<1.0 with a rationale comment like the other siblings; regenerate uv.lock

- depends on: t4
- covers: h2
- acceptance:
  - guild-cli resolves in a fresh install; pin carries the cap-rationale comment; uv.lock staged with pyproject.toml

### t6 — PR2/t6 — 'culture guild' top-level forward group: new culture_core/cli/guild.py (NAME='guild', REMAINDER passthrough parser) + `_maybe_forward_to_guild` in `culture_core/cli/__init__.py`, mirroring the steward/agentirc forwards so argv reaches guild.cli:main verbatim and --help reaches guild's parser

- depends on: t5
- covers: c9, c6
- acceptance:
  - culture guild --help prints guild's own parser output; culture guild create stays dry-run by default with exit code/output identical to standalone guild create; 'guild' collides with no existing top-level command (parser registration test)

### t7 — PR2/t7 — guild-forward test suite: tests/test_guild_forward_cli.py mirroring the existing steward-forward tests — verbatim argv passthrough, exit-code propagation, --json passthrough, no argument mangling; asserts the culture-side diff is confined to forwarding + registration

- depends on: t6
- covers: h1, h13
- acceptance:
  - Forward tests cover help/exit-code/--json/dry-run passthrough and pass; no provisioning business logic exists in culture_core (import/AST-level check or review checklist in test docstring)

### t8 — PR2/t8 — end-to-end provisioning doc + demo + PR2 version bump: docs/provisioning.md walking scaffold (culture guild create --apply) -> culture agents create/start -> agent appears in overview -> exchanges a channel message; scripted dry-run e2e where CI-able, transcript-evidenced full run once; non-interactive (mesh-agent) path documented; /version-bump minor

- depends on: t6
- covers: c1, h8, c2, h9, c4, h11
- acceptance:
  - Doc alone is sufficient to reproduce the flow; dry-run e2e scripted; one full provisioning transcript recorded (or linked); no interactive prompt on the happy path; CHANGELOG entry present

### t9 — PR3/t9 — pin-drift checker: culture_core/devtools/pin_drift.py parses the sibling pins (agentirc-cli, cultureagent, steward-cli, guild-cli) from pyproject, queries PyPI for latest releases, and reports pins whose allowed range excludes latest; unit tests with offline fixtures incl. a replay of culture-core~=0.5.0 vs 0.17.0 (flags) and current pins (quiet)

- covers: c10, h3, c5, h12
- acceptance:
  - Replayed motivating failure is flagged; current pins stay quiet; network layer injectable so tests run offline; exit codes distinguish clean/lagging/error

### t10 — PR3/t10 — weekly drift workflow: .github/workflows/pin-drift.yml scheduled cron running the checker and opening/updating a single tracking issue naming the lagging pins (report-only; no bump PRs)

- depends on: t9
- covers: c10
- acceptance:
  - Workflow runs the checker on schedule and on workflow_dispatch; creates the tracking issue when lag is found and updates (not duplicates) it on subsequent runs; does nothing when clean

### t11 — PR3/t11 — drift-guard doc + PR3 version bump: docs/pin-drift.md describing the guarded pins, the report-only rationale (caps encode validated breakage), and how to act on a drift issue (bump alongside adaptation code, re-run suite); /version-bump minor

- depends on: t9
- covers: h11
- acceptance:
  - Doc names all guarded pins and the response playbook; CHANGELOG entry present; version-check CI green

## Risks

- [unknown_nonblocking] Full guild create --apply needs gh auth + org permissions on the operator box — CI exercises dry-run only; the transcript-evidenced full run is operator-gated
- [unknown_nonblocking] Slim default changes install UX: 'uv tool install culture' no longer runs the claude backend out of the box — accepted by design, mitigated by remediation hints + install doc
- [unknown_nonblocking] PyPI rate limits / transient network on the scheduled drift job — mitigate with retries and treating network failure as 'error', never 'clean'
