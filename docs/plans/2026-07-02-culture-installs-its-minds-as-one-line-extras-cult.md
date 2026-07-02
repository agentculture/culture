# Build Plan — Culture installs its minds as one-line extras — culture[claude,codex,colleague] — presents one clear agentfront-derived interface to agents and humans alike, and answers both locally and at chat.agentculture.org

slug: `culture-installs-its-minds-as-one-line-extras-cult` · status: `exported` · from frame: `culture-installs-its-minds-as-one-line-extras-cult`

> Culture installs its minds as one-line extras — culture[claude,codex,colleague] — presents one clear agentfront-derived interface to agents and humans alike, and answers both locally and at chat.agentculture.org.

## Tasks

### t2 — PR2b — unknown-backend hard error: remove the silent _BACKEND_DAEMON_FACTORIES fallback to claude (observed 2026-07-02: spark-colleague ran the claude daemon unnoticed); unknown backend exits with an actionable error naming the valid set

- covers: c5, h12
- acceptance:
  - culture agents start of an agent with backend: bogus exits non-zero listing valid backends; regression test covers it

### t5 — PR1 — agentfront migration: replace the afi-cli>=0.3,<0.4 dep with agentfront, update culture_core/cli/afi.py (afi.cli -> agentfront.cli), keep the passthrough verbs; verifies the assumption that agentfront 0.20 keeps the main(argv)->int entry

- covers: c8, h3
- acceptance:
  - Full engine suite passes with afi-cli absent from the venv and agentfront installed
  - culture afi explain/overview/learn forward to agentfront and exit 0
  - Version policy for the new dep (cap vs float) decided and recorded in CHANGELOG

### t6 — Cross-repo hand-off: file the cultureagent issue for clients/colleague wrapping colleague[culture] — brief covers the resident-harness seam (ColleagueHarness on agent-lifecycle), the backend-colleague extra, and the smoke-test contract; no sibling code vendored in-tree

- covers: c12, h13
- acceptance:
  - Issue open on agentculture/cultureagent with the signed brief; grep confirms no colleague/agentfront/cultureagent source copied into culture by this effort

### t7 — PR3a — parity narrowing: backend_parity devtool + CI enforce claude/codex/colleague; CLAUDE.md and docs all-backends rule text updated

- covers: c10, h5
- acceptance:
  - Negative test: a PR touching a single backend directory fails backend-parity naming the missing backends of the NEW three-backend set

### t9 — PR4a — agentfront App registry: culture declares its docs and tools once in an agentfront App; the CLI surface derives from it while every existing culture command name keeps working

- depends on: t5
- covers: c18, h7
- acceptance:
  - agentfront cli doctor (the rubric gate) passes on culture's CLI surface
  - The existing CLI test suite passes unchanged — command names and behavior preserved

### t10 — PR4b — MCP surface from the same App registry (minimal tool menu, behind agentfront's mcp extra), documented in docs/

- depends on: t9
- covers: c2, h9
- acceptance:
  - MCP server exposes the registry-derived tool menu; a docs/ page describes connecting an agent to it

### t11 — PR4c — HTTP surface from the same App registry: markdown pages + sitemap navigable by any agent with a fetch tool, documented in docs/

- depends on: t9
- covers: c2, h9
- acceptance:
  - HTTP surface serves registry-derived pages + sitemap; the same content a human reads in docs/ — no drift between surfaces by construction

### t12 — Tracking issue on culture: the PR ladder (PR1 agentfront -> PR2 colleague -> PR3 parity/deprecation -> PR4 App registry -> PR5 provisioning+verification) linking spec and plan; each PR independently green; carries the before-state evidence

- covers: c4, h11, c3, h10
- acceptance:
  - Issue open listing the PR sequence with spec/plan links and the on-host before-state evidence (extras list, afi pin, 1033 observation of 2026-07-02)

### t13 — PR5a — durable-mesh provisioning verbs: culture server install/uninstall and culture console install (plus documented tunnel-unit pattern with 0600 token file), symmetric with the existing culture agents install; idempotent; agent units gain After=/Wants= ordering on the server unit. Covers the user requirements that the WHOLE mesh survives restarts and that provisioning is CLI-supported for others/machine moves (frame c21/c22, h17)

- covers: c9, c22, h17, c21
- acceptance:
  - On a machine with none of the units, the documented CLI flow alone provisions server+console+agents units enabled for reboot survival; running it twice is a no-op
  - spark's hand-written server/console/tunnel units are replaced by CLI-provisioned ones (the verification baseline)

### t14 — Verification — unattended reboot test on CLI-provisioned units: reboot spark; chat.agentculture.org returns 200 through Access, culture agents status shows every registered agent running, zero manual steps; transcript recorded (frame h16)

- depends on: t13
- covers: c14, h4, h8, h15, c1, h16
- acceptance:
  - Post-reboot with zero manual steps: console 200 through Access, all registered agents running and joined, install headline re-verified; transcript recorded before declaring done

### t15 — PR3b — stale marking for copilot/acp (supersedes the rejected deprecation task): docs + pyproject extras comments mark both backends STALE with the future re-validation path; no deprecation warnings, no removal, no major bump; parity exemption verified

- depends on: t7
- covers: c23, h18
- acceptance:
  - pip install culture[copilot] and culture[acp] still resolve and those backends still start
  - docs mark copilot/acp stale with the re-validation path; no deprecation warning emitted anywhere
  - backend-parity CI does not fire on a copilot/acp-only change (exemption test)

### t1 — PR2a — culture-side colleague wiring: 'colleague' extra in pyproject (cultureagent[backend-colleague]), _create_colleague_daemon factory + registry entry, culture.yaml template and docs page

- depends on: t2
- covers: c6, c7
- acceptance:
  - With the extra installed, backend: colleague resolves the colleague daemon; without it, CultureError names the missing extra (mirrors _require_backend_sdk)
  - docs/ page describes the colleague backend; doc-test-alignment finds no gap

### t4 — PR2c — clean-install smoke test: fresh venv, install culture[claude,codex,colleague] from published indexes only; colleague agent joins a local mesh and answers a mention; transcript recorded

- depends on: t1
- covers: c13, h1, h2, h14
- acceptance:
  - Install resolves entirely from published package versions (no git/path deps)
  - Colleague agent passes the same connect-join-respond smoke the other backends pass; transcript recorded before declaring done

## Risks

- [unknown_nonblocking] cultureagent release timing gates the clean-install smoke test — culture[colleague] resolves only after cultureagent ships backend-colleague (task t4)
- [unknown_nonblocking] App-registry adoption is the largest refactor; command-name compatibility is the contract — regressions surface via the unchanged CLI suite (task t9)
- [unknown_nonblocking] Tunnel provisioning depends on Cloudflare account state (token re-issue on machine move) — CLI documents the pattern, cannot mint the token (task t12)
- [follow_up] copilot/acp removal timeline undecided (parked v3 in the frame) — deprecation ships now, removal is a future major
