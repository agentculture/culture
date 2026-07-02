# Culture 14.1: the mesh grows itself from culture — a forwarded guild verb group provisions new sibling agents end-to-end (scaffold repo, run daemon, appear in the relationship overview, exchange messages), external sibling pins are drift-guarded so they can never silently lag again, and backend SDKs are optional extras so a slim install stays slim

> Culture 14.1: the mesh grows itself from culture — a forwarded guild verb group provisions new sibling agents end-to-end (scaffold repo, run daemon, appear in the relationship overview, exchange messages), external sibling pins are drift-guarded so they can never silently lag again, and backend SDKs are optional extras so a slim install stays slim

## Audience

- Culture operators (humans and mesh agents) who provision, run, and maintain sibling agents from the culture CLI; and installers of the culture dist who want a slim footprint

## Before → After

- Before: Provisioning exists only in guild-cli used standalone; culture's core deps hard-require claude-agent-sdk+anthropic although culture_core/ never imports either (only cultureagent does, and it already models them as backend-* extras); nothing guards the external sibling pins against silently lagging — the exact failure (culture-core~=0.5.0 vs engine 0.17.0) that motivated the #462 merge-back
- After: From culture, an operator provisions a new sibling agent end-to-end — guild create scaffolds the repo from culture-agent-template, culture agents create/start runs its daemon, it appears in the relationship overview and exchanges messages — while external sibling pins are drift-guarded and backend SDK deps are optional extras

## Why it matters

- The merge-back was justified by the capability goals it unblocks; without Phase C the single-repo consolidation delivers only half its value, and the pin-drift failure mode that motivated it can recur with the remaining siblings (agentirc-cli, cultureagent, steward-cli, guild-cli)

## Requirements

- Pin guild-cli in pyproject (capped like the other siblings) and forward a provisioning verb group to guild.cli:main, mirroring the _maybe_forward_to_steward/_maybe_forward_to_agentirc REMAINDER-forwarding pattern so --help reaches guild's parser
  - honesty: Forwarded guild verbs behave exactly as standalone guild: exit codes, --json output, dry-run-by-default on create, and --help reaching guild's own parser are all preserved through the REMAINDER forward (proven by tests like the existing steward-forward tests)
  - honesty: The guild-cli pin lands with a validated cap range like the other siblings (>=0.18,<1.0) and its rationale comment, and 'culture guild' collides with no existing top-level command
- A drift guard covers the external sibling pins (agentirc-cli, cultureagent, steward-cli, guild-cli): it detects when a pin's allowed range lags the latest published release and surfaces it visibly, so a pin can never silently lag again
  - honesty: The guard demonstrably catches the motivating failure: pointed at a deliberately staled pin (or replaying culture-core~=0.5.0 vs 0.17.0) it flags the lag; pointed at current pins it stays quiet
- claude-agent-sdk and anthropic leave culture's core dependencies and become an optional extra; the extras surface is symmetric across all four backends per the all-backends rule (codex needs no SDK), and running a backend whose SDK extra is missing fails with a remediation hint naming the exact install command
  - honesty: A slim install (no extras) imports culture, runs the CLI, and passes the non-SDK test suite; each backend daemon start on a missing SDK fails with a remediation hint naming the exact 'pip install culture[<extra>]' command, symmetric across claude/codex/copilot/acp
  - honesty: CI and the dev environment keep the SDKs via the dev dependency group (tests/conftest.py and the integration runner import claude_agent_sdk), so the full suite still runs green at >=90% coverage
- The culture-core name survives only where intended: the compat console-script alias (if kept) and historical docs/CHANGELOG; no dependency or install-metadata reference remains (verified clean today in pyproject, uv.lock, repo venv, uv-tool venv)
  - honesty: A repo-wide sweep finds culture-core only in intended places: the compat alias (per its decision), CHANGELOG history, historical specs/docs, and the Phase B hand-off references

## Honesty conditions

- The end-to-end demo actually runs on the mesh: a sibling agent provisioned from culture (scaffold -> create -> start) shows up in the overview and exchanges a channel message — the #462 Phase C acceptance flow, scripted or transcript-evidenced
- The provisioning flow is drivable non-interactively (mesh agent) as well as by a human at a terminal: no interactive prompt on the happy path; guild create stays dry-run by default with --apply for execution
- Verified 2026-07-02 in-repo: culture_core/ has zero claude_agent_sdk/anthropic imports (only tests/ do); cultureagent 0.4.x publishes backend-claude/backend-acp/backend-copilot/all-backends extras; .github/workflows has no drift guard; pyproject/uv.lock/venvs carry no culture-core dist
- Each of the three tracks lands as its own PR with tests and a docs/ page (provisioning flow doc, extras/install doc, drift-guard doc), and the e2e flow is reproducible from the docs alone
- The motivating failure is the benchmark: replaying the culture-core~=0.5.0-vs-0.17.0 situation through the drift guard flags it — the guard closes the exact hole that justified the merge-back
- The culture-side diff for provisioning is confined to CLI forwarding + parser registration + pyproject pin (+ tests/docs); zero new provisioning or relationship business logic lands in culture_core
- Every listed signal is mechanically checkable in CI or a scripted smoke: slim-install import smoke, extra-install backend smoke, forwarded --help snapshot, staled-pin guard test, coverage >=90, backend-parity green
- cultureagent 0.4.x's backend-claude extra resolves to bounds compatible with the engine's expectations (anthropic>=0.40, claude-agent-sdk>=0.1) — verified against the published dist metadata

## Success signals

- On a clean box a slim 'uv tool install culture' works without claude-agent-sdk; 'culture[claude]' (or chosen extra name) pulls it via cultureagent's backend extra; the forwarded guild verbs reach guild-cli's own parser (--help included); a deliberately-staled pin makes the drift guard flag it; full suite green at >=90% coverage; backend-parity job passes

## Scope / boundaries

- No culture-side re-implementation: provisioning forwards verbatim to guild-cli (as doctor/show/overview forward to steward-cli); relationship gaps become new steward verbs, not culture code; Phase B (archiving the culture-core repo/dist) is a hand-off to culture-core#28, not work in this repo

## Non-goals

- No auto-bumping of the capped dependencies: the caps encode validated breakage (afi/agex renames, OTel 1.42, copilot-sdk 0.2.3+), so the drift guard reports lag — it never opens bump PRs that fight the caps

## Assumptions

- cultureagent's existing backend-claude extra (anthropic>=0.40 + claude-agent-sdk>=0.1) is the right vehicle: culture's extra maps to cultureagent[backend-claude] rather than re-pinning the SDKs directly

## Decisions

- Forwarded provisioning verbs land under a NEW top-level 'culture guild <verb>' group (mirroring the agentirc top-level forward), not under 'culture agents' — guild's create/show/overview would collide with the existing steward-forwarded show/overview and the native agents create
- Backend extras are named after the backends for symmetry with the existing [copilot] extra: culture[claude], culture[acp], culture[copilot], plus culture[all-backends] as the everything meta-extra
- Drift guard mechanism: a scheduled weekly GitHub Actions job that compares each sibling pin's allowed upper bound against the latest PyPI release and opens/updates a single tracking issue naming the lagging pins — report-only by design (no auto-bump PRs, per the caps rationale)
- The culture-core console-script compat alias stays in 14.x — dropping a shipped CLI command is a breaking change (major bump); its removal is deferred to the next planned major
- Ship as three separate PRs in dependency order: (1) dependency slimming + extras restructure + culture-core sweep, (2) guild-cli pin + forwarded provisioning group + e2e provisioning doc, (3) drift-guard workflow — unrelated changes never pile onto one PR

## Open / follow-up

- docs/ tree trim (2.6MB) and CHANGELOG compaction (153KB) — deferred until the post-merge-back shape settles, per the #462 parked list
- Phase B execution — final culture-core pointer release, repo archive, sibling-ledger updates — hands off to culture-core#28; culture only verifies no dist reference remains
