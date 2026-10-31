# Build Plan — external residents (#506)

slug: `external-residents-506` · status: `exported` · from frame: `external-residents-506`

> Culture recognises external residents: an agent registered with backend: external appears in culture agents status and /residents.json with presence from its live connection, while culture agents start/stop leave its lifecycle to the process that owns it

## Tasks

### t1 — Phase A / culture: backend: external + owner: in agent config, plus an `is_external` / owner-message helper

- instruction: Touch only `culture_core`/config.py (owner field, keep backend a free str per config.py:93) and a new `culture_core`/cli/shared/external.py plus tests/`test_external_agent_config.py`. Do not touch cli/agents.py; t2 consumes the helper.
- covers: c8, h11
- acceptance:
  - AgentConfig gains an optional owner: str (default None); backend: external round-trips through `load_culture_yaml` and `_serialize` unchanged; no other AgentConfig field changes
  - `culture_core`/cli/shared/external.py exposes `is_external`(agent) and `owner_message`(agent) -> 'owned by <owner>' (or 'owned by an external process' when owner is unset), unit-tested

### t2 — Phase A / culture: start, stop, install and IPC verbs leave external agents to their owner

- instruction: All edits in `culture_core`/cli/agents.py: filter in `_resolve_agents_to_start` (before `_probe_server_connection` and any fork), `_resolve_agents_to_stop`, `_cmd_install` and `_resolve_ipc_targets`. Tests extend tests/`test_cli_agents.py`. Never add an 'external' entry to `_BACKEND_DAEMON_FACTORIES`.
- depends on: t1
- covers: c3, h3, c4, h4, c11, h6, c9, h12
- acceptance:
  - culture agents start --all on a manifest with one external and one claude agent forks exactly one child, writes no agent-<external-nick> pidfile, and prints the owner message for the external agent; exit 0
  - culture agents stop <external-nick> sends no signal and no IPC, touches no pidfile, prints the owner message and exits 0
  - culture agents install <external-nick> writes no systemd unit; sleep/wake/learn/message/read on an external nick exit non-zero with the owner message, not a socket error
  - python -m `culture_core`.devtools.`backend_parity` --base origin/main passes with no '# backend-specific:' marker, and no `_create_`<backend>`_daemon` body is edited

### t3 — Phase A / culture: culture agents status shows external agents' live presence

- instruction: Edit `culture_core`/cli/shared/display.py (`agent_process_status` / `_agent_overview_row` / `print_agent_detail`) and reuse `resource_view`.`fetch_residents_async` with one PRESENCE LIST query per status call, not one per agent. Real-server test per CLAUDE.md (no server mocks): tests/`test_status_external.py`.
- depends on: t1
- covers: c5, h5
- acceptance:
  - With a real agentirc up, an external agent's status row shows the state from PRESENCE LIST (e.g. idle) within 5 s of its PRESENCE publish
  - With the server unreachable, external rows show 'unknown'; harness-agent rows are byte-identical to before (snapshot test)

### t4 — Phase A / culture: /residents.json and culture residents carry a backend field

- instruction: Append backend to `_RESIDENT_FIELDS` in `culture_core`/`resource_view.py` (107-120) and set it in `apply_budgets`' manifest join. Files: `resource_view.py`, tests/`test_residents_endpoint.py`, docs/resident-presence.md. The observer `__peek` path stays unauthenticated and untouched (c40).
- covers: c40, h27
- acceptance:
  - Every resident row in /residents.json and culture residents --json has a backend key: the manifest backend for registered nicks, null otherwise; the envelope {supported, `generated_at`, residents} is unchanged
  - tests/`test_residents_endpoint.py` covers backend for a registered external nick, a harness nick and an unregistered nick; the docs/resident-presence.md schema table lists backend

### t5 — Phase A / culture: move from shushu to grant (dependency, culture secret forward, docs)

- instruction: New `culture_core`/cli/secret.py following `culture_core`/cli/afi.py's passthrough registration. Docs: README.md:38,63, docs/culture/vision.md:70, docs/reference/cli/devex.md:71, plus a new docs/secret.md. Tests use a temporary HOME so the real grant store is never read.
- covers: c50, h32, h33
- acceptance:
  - pyproject.toml declares grant>=0.11,<0.12 and uv.lock is updated in the same commit
  - culture secret <verb> forwards to grant through `culture_core`/cli/`_passthrough.py`: culture secret list prints exactly what grant list prints; culture secret get on a hidden secret is refused exactly as grant does; culture explain no longer marks secret '(coming soon)'
  - grep -ri shushu README.md docs/ returns only historical CHANGELOG lines and the doctor.md example

### t6 — Phase A / culture: docs, version bump, PR and deploy of external residents

- instruction: Every before-state sentence in c22 gets a test reproducing it on main first (red), then green on the branch. Run doc-test-alignment before the first push. Deployment follows the uv tool upgrade + restart culture-console procedure.
- depends on: t2, t3, t4, t5
- covers: c22, h14
- acceptance:
  - docs/external-residents.md describes backend: external, owner:, what start/stop/status/install do, and the presence contract (+A, PRESENCE) an owner process must speak
  - /version-bump minor; CI green, including backend-parity and the SonarCloud gate; PR merged
  - On spark after deploy: spark-gwen (after embodiment#121 part 1), culture-ask-sbx, culture-bolt-sbx, culture-ask-spark and cultureagent-codex-bridge are registered as backend: external with owner: and appear in culture agents status with live state

### t7 — Phase B / agentirc: hold registration until CAP negotiation ends

- instruction: Repo ../agentirc (direct PR, decision c51). Edit agentirc/client.py `_handle_cap` / `_try_register` (installed copy lines 430, 512-519). Version bump + CHANGELOG per agentirc's CLAUDE.md.
- covers: c39, h25
- acceptance:
  - A client that sends CAP LS, NICK and USER gets no 001 and sends no SNICK to linked peers until CAP END; a client that never sends CAP registers exactly as today
  - Existing agentirc test suite passes unchanged

### t8 — Phase B / agentirc: agent accounts and SASL PLAIN authentication

- instruction: Repo ../agentirc. New agentirc/accounts.py (store + reload) and SASL handling in client.py; advertise the sasl cap. Compare hashes with hmac.`compare_digest`. Real-server tests with two linked instances for the S2S assertion.
- depends on: t7
- covers: c2, h2, h19, c18, h8, c19, h9
- acceptance:
  - Accounts load from a file in the server `data_dir` (nick, sha256 secret hash, role, grants, state) and reload on change or SIGHUP without a restart
  - NICK of an account nick never gets 001 without successful SASL PLAIN; a wrong, revoked or suspended secret gets 904 and a closed connection; the right secret gets 001, whether or not the real agent is online
  - SASL is accepted only from loopback or a configured trusted-CIDR list; from any other peer it fails with 904
  - A nick with no account registers exactly as today (prefix + collision checks only); exact-match only, so <agent>`__peek` observer nicks are unaffected
  - No account, secret-hash, grant or role data appears in any S2S message (SNICK/SEVENT), asserted with a two-server linked test

### t9 — Phase B / agentirc: enforce channel grants and roles

- instruction: Repo ../agentirc. Gate in the JOIN/PRIVMSG handlers and next to the existing owner checks (skills/rooms.py:334,478). Grant levels are read and speak; channel-op comes from the operator role.
- depends on: t8
- covers: c32, h20, c33, h21
- acceptance:
  - An authenticated account's JOIN to a channel with no grant is refused with a numeric; a read grant joins but its PRIVMSG to the channel is refused; a speak grant succeeds; DMs are not gated
  - Operator-only verbs (admin and room-owner verbs in skills/rooms.py) are refused for resident and guest accounts and succeed for operator; a guest grant outside the guest's sandbox channels is rejected at load
  - Clients without an account keep today's JOIN/PRIVMSG behaviour

### t10 — Phase B / agentirc: revoke takes effect live; failed-auth logging, rate limit and telemetry

- instruction: Repo ../agentirc. The reload hook from t8 walks live clients; logging and rate limit live in accounts.py/client.py; the metric name follows the culture.\* telemetry identity rule (never `culture_core`.\*).
- depends on: t8, t9
- covers: c44, h30
- acceptance:
  - An account revoked or suspended in the accounts file has its live session closed within 5 s of the reload
  - Every failed authentication logs nick, peer address and reason (never secret material); failed attempts are rate-limited per peer address; a culture.\* counter metric counts failures

### t11 — Phase B / cultureagent: SASL in the shared transport, permanent exit on auth refusal

- instruction: Repo ../cultureagent (direct PR, decision c51). `irc_transport.py`:135-136 (registration) and 271-283 (`_reconnect`). First trace how a server refusal surfaces today (lapse l1): whether it reaches `_reconnect` as an OSError. Write that red test first.
- depends on: t8
- covers: c38, h24
- acceptance:
  - When `CULTURE_AGENT_SECRET` was supplied, the shared transport negotiates CAP sasl + AUTHENTICATE PLAIN before CAP END; without one it connects exactly as today
  - On 904/905 or a refused nick the transport stops reconnecting and the daemon exits with the permanent-failure status, so the systemd unit ends failed after one attempt with a log line naming the SASL failure
  - backend-parity passes: the change is confined to cultureagent/clients/shared/

### t12 — Phase B / cultureagent: daemon scrubs its secret from the environment at startup

- instruction: Repo ../cultureagent. Do it in the shared BaseDaemon startup (clients/shared/), not per backend, so the all-backends rule holds by construction. File-disjoint from t10: touch the daemon base, not `irc_transport.py`.
- covers: c49, h31
- acceptance:
  - The shared daemon base reads `CULTURE_AGENT_SECRET` once, removes it from os.environ before connecting or spawning anything, and hands it to the transport in memory
  - A test spawning a subprocess from the daemon (the path agent shell tools take) shows no `CULTURE_AGENT_SECRET` in the child environment, for every backend

### t13 — Phase B / culture: account store — mint in grant, write agentirc's accounts file, audit log

- instruction: New `culture_core`/accounts.py plus tests/`test_accounts.py`. Write the accounts-file format agentirc t8 reads. No CLI wiring here (that's t14).
- depends on: t8
- covers: c17, h34
- acceptance:
  - `culture_core`/accounts.py mints a secret with grant.generate.`random_secret`, stores it with grant.store.`set_secret`(hidden=True, source='culture agents register', `rotation_howto` naming culture's rotate verb) under `CULTURE_AGENT_`<NICK>, and writes only its sha256 hash into the agentirc accounts file
  - Role defaults to resident and grants are seeded from culture.yaml channels: as speak; each server's culture writes only its own <server>- nicks
  - Every account change appends one line to an audit log keyed by nick with no secret material; tests use a temporary HOME so the real grant store is never touched

### t14 — Phase B / culture: account CLI verbs and lifecycle — register, archive, delete, unregister, revoke, rotate, disable

- instruction: Edit `culture_core`/cli/agents.py (`_cmd_register`, `_cmd_archive` 1269, `_cmd_delete` 1313, `_cmd_unregister` 1389, plus a new 'account' subcommand group). Integration tests run against a real agentirc built from the t8-t10 branch.
- depends on: t13, t10
- covers: c41, h28, c43, h29, c34, h22
- acceptance:
  - register creates the account; archive suspends it (the secret fails, the nick stays reserved); delete and unregister revoke the secret but keep a tombstone so the nick stays reserved; re-register restores it
  - culture agents account revoke closes the live session within 5 s; rotate makes the old secret fail and restarts a culture-owned unit, or prints the owner instruction for an external one; account disable returns the nick to legacy behaviour within one reload without touching other accounts

### t15 — Phase B / culture: grant-wrapped units, doctor checks, ordered activation

- instruction: Files: `culture_core`/persistence.py (the \[Service\] block around line 295), `culture_core`/doctor/, `culture_core`/accounts.py (activation order only). The grant pin and the cultureagent SASL floor go into pyproject.toml + uv.lock.
- depends on: t13, t11, t12
- covers: c42, h26
- acceptance:
  - persistence.`install_service` wraps ExecStart as grant run --inject `CULTURE_AGENT_SECRET`=<name> -- <cmd> for agents with an account
  - culture doctor flags: agents with an account whose unit lacks the inject wrapper; an installed cultureagent below its SASL version; a server bound to 0.0.0.0 with accounts but no trusted CIDRs; registered agents with no account
  - Account activation runs mint, unit update, client-version check, then publish to agentirc, and refuses to publish while any earlier step fails

### t16 — Phase B: release all three repos, roll out on spark, verify live

- instruction: Activate agent by agent in t15's order and keep 'culture agents account disable' ready as rollback. Gwen's activation waits on embodiment#121 part 2. Run /validate-delivery before /summarize-delivery.
- depends on: t9, t14, t15
- covers: c1, h1, c20, h10, c21, h13, c23, h15, c24, h16, c25, h17
- acceptance:
  - agentirc, cultureagent and culture each released with a version bump; culture's pins raised to the new floors in the same PR
  - Scripted success-signal test against a throwaway agentirc passes: external agent in status within 5 s, start --all forks 0 for it, 100% of wrong-secret attempts refused, JOIN outside grants refused, revoke closes the session in 5 s or less
  - Live on spark: spark-gwen (once embodiment ships SASL), the sandbox/ask/bridge units and every harness agent authenticate; 0 plaintext secrets in ~/.culture or any agent child environment; the irc-lens diff is empty
  - Delivery record written via /summarize-delivery; the 'Rollout on thor' step lists its trusted CIDRs before activation

## Risks

- [unknown_nonblocking] embodiment#121 (Gwen's +A/PRESENCE now, SASL later) is a hand-off with no committed date; Gwen's live verification in t6 and t16 waits on it
- [follow_up] Same-uid agents can still read each other's grant store; per-agent Unix users are follow-up v2
