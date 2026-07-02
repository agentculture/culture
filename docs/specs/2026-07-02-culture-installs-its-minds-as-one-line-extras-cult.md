# Culture installs its minds as one-line extras — culture[claude,codex,colleague] — presents one clear agentfront-derived interface to agents and humans alike, and answers both locally and at chat.agentculture.org

> Culture installs its minds as one-line extras — culture[claude,codex,colleague] — presents one clear agentfront-derived interface to agents and humans alike, and answers both locally and at chat.agentculture.org.

## Audience

- Mesh operators (humans at the CLI) and AI agents (in-mesh and driving the culture CLI) — both install, learn, and drive culture through the same surfaces

## Before → After

- Before: Today: extras are culture[claude/acp/codex/copilot] — no colleague backend anywhere (engine factories: claude, codex, acp+opencode, copilot); culture pins the retired afi-cli>=0.3,<0.4 and imports 'from afi.cli import main' (agentfront is afi-cli's renamed successor, now an importable runtime); chat.agentculture.org returns Cloudflare 1033 because the IRCd is down, the console has no systemd unit (was nohup), and the token-run tunnel died at reboot
- After: uv tool install 'culture[claude,codex,colleague]' yields working backends in one line; the colleague coder-agent harness is a first-class culture backend; culture's agent-first surfaces ride the agentfront package (afi-cli imports gone); chat.agentculture.org serves the console again and survives reboots via systemd units

## Why it matters

- Accessibility is the product: one obvious install line, one interface registry that cannot drift between CLI/MCP/HTTP, and a remote console that is actually up — for agents and humans equally

## Requirements

- A 'colleague' extra exists: culture[colleague] installs whatever the colleague backend needs, symmetric with the existing per-backend extras (pip already supports the comma union culture[claude,codex,colleague])
  - honesty: On a clean venv, pip install 'culture[colleague]' resolves entirely from published package versions (no git/path deps)
- A colleague backend is wired end-to-end: culture.yaml 'backend: colleague', a _create_colleague_daemon factory, and daemon resolution — implemented where the other backends live (cultureagent.clients.<backend>.daemon), with culture-side wiring here and a hand-off brief/issue on the sibling repo
  - honesty: A colleague-backed agent passes the same connect-join-respond smoke test the other backends pass
- Culture migrates afi-cli -> agentfront: the pyproject pin afi-cli>=0.3,<0.4 is replaced by agentfront, culture_core/cli/afi.py's 'from afi.cli import main' import is updated, and the passthrough verb keeps working
  - honesty: The full engine test suite passes with afi-cli absent from the venv and agentfront installed; culture afi passthrough verbs (explain/overview/learn) still work
- chat.agentculture.org is restored and durable: IRCd + console + cloudflared tunnel each run under a systemd unit that survives reboot, and the 1033 is gone
  - honesty: Reboot test: systemctl reboot, wait, then chat.agentculture.org loads the console with no manual bring-up
- The all-backends parity rule and its CI job are updated to whatever the new supported backend set is — colleague counts as a backend for parity
  - honesty: A test PR touching only one backend directory fails backend-parity CI naming the missing backends of the NEW set
- Copilot and acp are deprecated: parity CI + all-backends rule narrow to claude/codex/colleague, docs and extras mark the two as deprecated, and the release is a MAJOR version bump; actual removal timeline is parked
  - honesty: On the release: pip install culture[copilot] still works but warns/documents deprecation; backend-parity CI enforces exactly claude/codex/colleague; CHANGELOG carries a major-version breaking entry
- Culture's own interface is agentfront-derived: one App registry declares culture's docs and tools once, and the CLI, MCP server, and HTTP surfaces are derived from it (the surfaces cannot drift apart); the existing culture CLI command names keep working
  - honesty: agentfront's own rubric gate (agentfront cli doctor) passes on culture's CLI surface, and the existing CLI test suite passes unchanged (command names and behavior preserved)
- The ENTIRE mesh survives host restarts (user requirement 2026-07-02): not just IRCd/console/tunnel — every registered agent (spark-agentirc, spark-colleague, future registrations) runs under an enabled systemd user unit with linger, and rejoins the mesh after an unattended reboot
  - honesty: After the unattended reboot test, culture agents status shows every registered agent running and joined, with zero manual steps
- Durable-mesh provisioning is a CLI-supported product capability, not hand-ops (user steer 2026-07-02): culture agents install exists today; server, console, and tunnel provisioning must be equally supported so others — or a machine move — reach a reboot-surviving mesh from documented commands alone. This is part of the offering that is culture
  - honesty: On a fresh machine or after a machine move, the documented culture CLI flow alone — no hand-written unit files — yields the reboot-surviving mesh; spark's hand-written server/console/tunnel units get replaced by CLI-provisioned ones as the verification

## Honesty conditions

- Every headline is demonstrable on release: the extras line installs from PyPI, one agentfront App registry backs the CLI/MCP/HTTP surfaces, and chat.agentculture.org answers after an unattended reboot
- Both audiences are served by the same artifacts: a human can install and run from the docs alone; an agent can drive the same flows via the learn/explain affordances — no human-only steps
- The before-state is evidenced, not asserted: the extras list and afi-cli pin are in pyproject.toml, the afi.cli import is in culture_core/cli/afi.py, and the down IRCd/console/1033 were observed on-host at spec time (2026-07-02)
- Every after-state clause maps to a requirement carrying its own confirmed honesty condition (c6, c7, c8, c9, c18) — nothing in the after-state is uncovered
- The accessibility claim is testable: a fresh agent or human goes from zero to a mesh-joined backend using only published packages and documented commands
- No PR in this effort vendors colleague/cultureagent/agentfront code in-tree or modifies irc-lens; upstream gaps appear as filed issues instead
- The clean-install + colleague-answers-a-mention smoke test is actually executed and its transcript recorded before the effort is declared done
- The unattended-reboot test and the parity-CI negative test are both executed and recorded before the effort is declared done

## Success signals

- On a clean machine: uv tool install 'culture[claude,codex,colleague]' succeeds and culture agents create/start with backend: colleague joins the mesh and answers a mention — the same smoke test the existing backends pass
- After a host reboot with zero manual steps, chat.agentculture.org serves the console behind Cloudflare Access — no error 1033; and the backend-parity CI job names colleague in its enforced set

## Scope / boundaries

- Not a console/web rewrite: irc-lens remains the chat console; agentfront's HTTP/MCP surfaces present culture-the-tool, not the chat UI. And no re-vendoring: colleague, cultureagent, and agentfront stay external deps — cite issues upstream, don't absorb code

## Assumptions

- colleague's resident mode is the integration point: colleague ships a [culture] extra whose ColleagueHarness adapts the bounded work loop onto agent-lifecycle's Harness Protocol over the agentirc-cli wire — the culture backend wraps this, it does not build a new harness
- agentfront 0.20.0 (on PyPI) keeps a main(argv)->int-compatible entry at agentfront.cli:main, so culture's passthrough adapter carries over with an import rename (afi.cli -> agentfront.cli)

## Decisions

- Cross-repo changes follow the standing hand-off convention: culture implements its own side; gaps in colleague, cultureagent, or agentfront are filed as issues on those repos, not patched in-tree here
- The colleague adapter is cultureagent's clients/colleague backend wrapping colleague[culture]; culture's extra becomes colleague = [cultureagent[backend-colleague]]; hand-off issue filed on cultureagent
- Staging (user decision): one converged spec, several PRs in dependency order — agentfront migration; colleague backend + extras; parity narrowing + copilot/acp deprecation (major bump); App-registry adoption; ops/systemd durability. Each PR independently green. This resolves the hard question on c18.

## Hard questions

- risk: Full registry adoption touches all of culture_core/cli/ (9 command groups); riding it alongside the backend-set change makes one large release — mitigation: stage as separate PRs under one spec
