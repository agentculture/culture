# Build Plan — The spark culture mesh is always-on under CLI-provisioned units that fail fast on config errors instead of silently looping, with agentirc 9.11 agentic access and irc-lens 0.9.1 image/audio media guaranteed by pins and verified end to end through chat.agentculture.org

slug: `the-spark-culture-mesh-is-always-on-under-cli-prov` · status: `exported` · from frame: `the-spark-culture-mesh-is-always-on-under-cli-prov`

> The spark culture mesh is always-on under CLI-provisioned units that fail fast on config errors instead of silently looping, with agentirc 9.11 agentic access and irc-lens 0.9.1 image/audio media guaranteed by pins and verified end to end through chat.agentculture.org

## Tasks

### t1 — Expanduser every mesh-config consumer (culture_core/mesh_config.py load/save + CLI --mesh-config/--config paths)

- covers: c8, h1, h9
- acceptance:
  - load_mesh_config('~/.culture/mesh.yaml') with a literal tilde resolves against HOME and loads (regression test monkeypatching HOME)
  - an ExecStart-style invocation 'server start --mesh-config <literal-tilde-path>' starts: test drives the CLI arg path through the expanding loader
  - audit test: no CLI consumer opens a mesh-config path without expansion (server start/install/uninstall, mesh setup/status/join)

### t2 — Exit 78 (EX_CONFIG) from 'culture server start' on missing/unreadable/invalid config

- covers: c9, h2, c4, h8
- acceptance:
  - missing mesh-config path exits 78 with a one-line actionable error (test asserts code and message)
  - malformed YAML / invalid schema exits 78 (test)
  - transient runtime failures keep restart semantics: a refused peer link or busy port does NOT exit 78 (test)

### t3 — Provenance guard on provisioning verbs: server/console/agents install detect a non-tool (dev worktree) interpreter

- covers: c10, h3, c5, h9
- acceptance:
  - unit generated from the installed tool is byte-identical to current output (golden test)
  - install with sys.executable outside a recognized tool venv (uv tools dir / pipx) warns loudly naming the baked path, or refuses without an explicit override flag (test both branches)
  - the guard's heuristic is unit-tested against: uv tool venv, repo .venv, worktree .venv, system python

### t4 — Raise pin floors: agentirc-cli>=9.11.0,<10 and irc-lens>=0.9.1,<1.0 (+uv.lock)

- covers: c12, h12
- acceptance:
  - uv lock resolves cleanly with the new floors (lockfile committed)
  - full engine suite green via /run-tests against agentirc-cli 9.11.0 / irc-lens 0.9.1

### t5 — Docs: durable-mesh.md gains the fail-fast contract, provenance guard, media console config, and the 2026-07-03 outage postmortem

- depends on: t1, t2, t3
- covers: c3, h7, c6, h10
- acceptance:
  - docs/durable-mesh.md documents exit-78 semantics (RestartPreventExitStatus contract), the provenance guard behavior + override, and media.public_base_url as a required console setting
  - postmortem section records the 2026-07-03 outage chain (worktree venv + literal tilde + exit-1 loop) with the journal evidence
  - scope note states copilot/acp untouched and sibling releases consumed via pins only; markdownlint clean

### t6 — End-to-end always-on probe: scripted verification of the announcement on the deployed spark mesh

- depends on: t1, t2, t3, t4
- covers: c1, h5, c2, h6, c7, h11
- acceptance:
  - a repeatable probe (script under scripts/ or documented one-liner set in docs/testing.md) checks: five units active, agents connected (agentirc/colleague/culture), console 200 via CF Access service token
  - deliberate bad mesh-config start observes exit 78 and a stopped (not restarting) unit
  - image and audio round-trips through <https://chat.agentculture.org> are byte-identical with public capability URLs
  - probe recorded green on spark after the fixes deploy (uv tool upgrade + unit reinstall)

## Risks

- [follow_up] Reboot proof (three-minds t14) still requires a coordinated host reboot — linger + enablement verified 2026-07-03, full boot-order proof parked
- [unknown_nonblocking] Worktree/dev-venv detection heuristic (t3) may false-positive legitimate setups — must ship with an explicit override flag and a tested heuristic matrix
- [follow_up] If implementation fans out to the colleague workforce, dispatch drives serially — 3-way GPU contention on the local vLLM produced timeouts and malformed tool calls (2026-07-02 lesson)
- [follow_up] culture_core/clients/colleague/ ships no reference culture.yaml template while claude/codex do — parity/template gap spotted 2026-07-03, likely its own small PR
