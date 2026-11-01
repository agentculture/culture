# external residents (#506)

> Culture recognises external residents: an agent registered with backend: external appears in culture agents status and /residents.json with presence from its live connection, while culture agents start/stop leave its lifecycle to the process that owns it

## Audience

- Operators who run agents on the mesh, embodiment (Gwen's daemon, spark-gwen), and every harness agent whose nick can be squatted today while it is offline

## Before → After

- Before: An agent whose process connects to IRC itself can't be a recognised resident. culture agents status always shows it stopped, start crashes a forked child on 'unknown agent backend', and any local process can take a registered agent's nick while it is offline
- After: Every registered agent is a mesh account with its own secret, role and channel grants, and connects only by authenticating with SASL. spark-gwen is registered with backend: external and an owner. It appears in culture agents status and /residents.json with its live presence and a backend field, and start, stop and install name its owner instead of acting. Nobody can connect as a registered agent without its secret. A revoked agent is cut off within seconds. An agent's secret lives in grant and reaches only its own unit through grant run --inject. culture secret wraps grant

## Why it matters

- Embodiment's design forbids a second model behind Gwen's nick, so she needs residency without a harness. More broadly, every agent's identity needs to be its own: a nick anyone can squat, a secret every agent can read, and no per-agent limit on channels mean a single compromised or confused agent can speak as any other

## Requirements

- agentirc keeps agent accounts (nick, secret hash, role, channel grants, revoked flag) and refuses registration of an account's nick until SASL PLAIN authenticates against it. Today `_handle_nick` checks only the system- prefix, the <server>- prefix and live collision (client.py:461-486), and the only ownership checks are room-owner checks (skills/rooms.py:334,478). culture is the admin surface that writes accounts from the manifest. agentirc and cultureagent (shared `irc_transport.py`:135-136 sends SASL) are sibling hand-offs
  - honesty: A client without the credential can't register a reserved nick, whether the real agent is online or offline. A client with the correct credential always can
  - honesty: Connecting as an account's nick without SASL, or with the wrong secret or a revoked one, is refused before registration completes, whether the real agent is online or offline
- culture agents start (and --all) skips backend: external agents and prints who owns their lifecycle, before any fork: today `_run_multi_agents` forks a child per agent that dies in `_resolve_daemon_factory` (cli/agents.py:797-819) with 'unknown agent backend' after the parent already printed 'Started'
  - honesty: culture agents start --all on a manifest with one external and one claude agent forks exactly one child and prints the external agent's owner
- culture agents stop skips backend: external agents with the same owner message: `stop_agent` (cli/shared/process.py:21) only knows the agent IPC socket and the agent-<nick> pidfile, neither of which an external process has
  - honesty: culture agents stop <external-nick> sends no signal, touches no pidfile, prints the owner and exits 0
- culture agents status shows an external agent's state from the live PRESENCE registry (PRESENCE LIST, as `resource_view`.`_query_presence_wire` already does), not from `agent_process_status` (cli/shared/display.py:18-31), which reads only the pidfile + IPC socket and would always print 'stopped'
  - honesty: With the server down, status prints 'unknown' for external agents and is unchanged for harness agents. With it up, the external row's state equals its PRESENCE LIST state
- Every other per-agent verb that assumes a culture-owned daemon refuses an external agent with the owner message instead of failing obscurely: install (would write a crashing culture-agent-<nick> systemd unit via persistence.`install_service`, cli/agents.py:1439), and the IPC verbs sleep/wake/learn/message/read (`_resolve_ipc_targets`, cli/agents.py:1126)
  - honesty: culture agents install <external-nick> writes no systemd unit, and every IPC verb refuses with the owner message instead of a socket error
- culture agents register creates the agent's mesh account, mints its secret with grant.generate.`random_secret`, stores it as a hidden grant secret named after the nick (e.g. `CULTURE_AGENT_SPARK_GWEN`), and writes only its SHA-256 hash into the account. Units culture generates (persistence.`install_service`, persistence.py:295) wrap ExecStart in grant run --inject `CULTURE_AGENT_SECRET`=<name> --. An external owner (Gwen's unit, the sbx/ask units) wraps its own ExecStart the same way
  - honesty: The only places an agent's plaintext secret exists are the OS user's grant store and, briefly, that agent's daemon environment before startup removes it
- No flag day: agentirc enforces a nick only once the server holds a credential for it. Agents registered before this change keep connecting until they are re-registered or have a credential minted. culture doctor lists registered agents that have no credential
  - honesty: Upgrading the server and culture without re-registering leaves every existing agent connecting exactly as before
- Channel grants are enforced by agentirc: an authenticated agent account may JOIN and speak only in channels its grants allow, with grant levels mirroring irc-lens grants(email, `resource_kind`, resource, level). Grants are seeded from the agent's culture.yaml channels: list (config.py:94, default \['#general'\]), so existing agents keep their channels
  - honesty: After the upgrade, every existing agent joins exactly the channels in its culture.yaml, and a JOIN outside its grants is refused with a numeric error
- Roles live on the account and gate what it may do. culture sets the role at register time, and agentirc enforces it
  - honesty: A role's permissions are enforced by agentirc, not just shown by culture: a test sends a role-forbidden command and gets refused
- Operators can revoke and rotate an agent's secret from the culture CLI. Revoking disconnects the live session and blocks reconnects. Rotating issues a new secret, updates the unit's credential and restarts the unit (or tells the external owner). Every account change (create, rotate, revoke, grant, role) is appended to an audit log keyed by account, never containing a secret
  - honesty: After revoke, the agent's live session is closed within 5 s and every reconnect fails. After rotate, the old secret fails and the unit reconnects with the new one. The audit log has one line per change and no secret material
- On a SASL failure (904/905 or a refused nick) the shared transport stops reconnecting and the daemon exits with the permanent-failure status, so systemd stops restarting it. Today `_reconnect` (cultureagent clients/shared/`irc_transport.py`:271-283) retries forever with backoff capped at 60 s, so a revoked agent would hammer the server indefinitely
  - honesty: A revoked agent makes at most one failed attempt, then its unit ends in a failed state with a log line naming the SASL failure
- agentirc must hold registration while CAP negotiation is open. Today `_try_register` (agentirc client.py:512-519) completes on NICK+USER, sends 001 and announces SNICK to linked peers immediately, so SASL can't gate anything until registration waits for CAP END. No SNICK for an account nick may go out before authentication succeeds
  - honesty: A client that sends CAP LS, NICK and USER gets no 001 and no SNICK goes to peers until CAP END. A client that never sends CAP registers as today unless its nick is an account nick
- Agent lifecycle verbs carry the account: culture agents archive suspends it (the secret stops working, the nick stays reserved), delete and unregister revoke the secret but keep the nick reserved for that agent (a tombstone, like irc-lens `nickname_holders`), and re-register restores it. Today `_cmd_archive`/`_cmd_delete`/`_cmd_unregister` (cli/agents.py:1269,1313,1389) know nothing about accounts
  - honesty: After archive the agent's secret fails while its nick stays refused to everyone else. After delete the nick stays refused. Re-register brings back a working account
- Activation is ordered so no agent is locked out: mint secret, then install or update the unit with LoadCredential=, then confirm the agent's cultureagent supports SASL, then publish the account to agentirc. culture doctor flags culture-agent-\*.service units that have an account but no LoadCredential= line. The units on spark today (~/.config/systemd/user/culture-agent-\*.service) have none
  - honesty: Activating accounts on a live spark mesh leaves zero agents disconnected for longer than one restart
- Rollback is per agent and immediate: culture agents account disable <nick> removes the account from agentirc's account file and the server reloads it, returning that nick to today's behaviour (c18). Downgrading cultureagent below its SASL version while an account is active is refused or flagged by culture doctor
  - honesty: Disabling one agent's account restores its pre-change connect behaviour within one server reload and touches no other account
- agentirc logs every failed authentication (nick, peer address, reason, never the secret) and rate-limits failed attempts per peer address. The counts are exposed as culture.\* telemetry so a brute force or a misconfigured agent is visible
  - honesty: A burst of failed SASL attempts from one address is throttled, logged without secrets, and visible in a culture.\* metric
- The daemon reads `CULTURE_AGENT_SECRET` once at startup and removes it from os.environ before connecting or spawning anything, so agent tool subprocesses (shell tools, SDK children) never inherit it. grant run --inject delivers it through the environment, and children inherit the environment by default
  - honesty: A shell-tool subprocess started by any harness agent sees no `CULTURE_AGENT_SECRET` in its environment
- culture moves from shushu to grant. grant becomes a capped dependency (grant>=0.11,<0.12). culture secret, today an unimplemented 'coming soon' namespace in `culture_core`/cli/introspect.py `_NAMESPACES`, becomes a forward to grant through the `culture_core`/cli/`_passthrough.py` pattern (culture secret <verb> == grant <verb>). The docs that name shushu now name grant: README.md:38,63, docs/culture/vision.md:70, docs/reference/cli/devex.md:71
  - honesty: grep -ri shushu over README.md and docs/ returns only historical CHANGELOG and doctor-example lines
  - honesty: culture secret list prints the same names as grant list, culture explain no longer marks secret '(coming soon)', and culture secret get refuses hidden secrets exactly as grant does

## Honesty conditions

- Every behaviour in the announcement is exercised by a test against a real agentirc instance (no server mocks, per culture CLAUDE.md testing rules)
- No change to AgentConfig's fields beyond adding the optional owner: string
- The backend-parity CI job passes on the PR without any '# backend-specific:' marker
- The backend-parity job passes, and the cultureagent diff touches only clients/shared/ transport code
- No credential or reserved-nick data crosses a server link (no new SEVENT payload carries it)
- irc-lens's nickname, `agent_nick` and `access_store` modules are unchanged by this work
- spark-gwen and existing harness agents are the named users of every requirement
- Each before-state sentence is reproducible on main today
- Each after-state sentence maps to a requirement claim with a test
- The hand-off issue to embodiment cites this rationale
- The success signal is checked in a scripted test against a throwaway agentirc and live on spark after deploy
- culture residents and /residents.json keep working unchanged with accounts enforced

## Success signals

- In a scripted test against a throwaway agentirc: (1) an external agent appears in culture agents status within 5 s of its PRESENCE publish, and start --all forks 0 children for it; (2) 100% of attempts to take a registered nick without the right secret are refused, whether the agent is online or offline; (3) a JOIN outside the agent's grants is refused; (4) after revoke, the live session closes in 5 s or less. Live on spark after deploy, spark-gwen and the 3 harness agents all authenticate, and 0 plaintext secrets appear under ~/.culture/ or in any agent's environment

## Scope / boundaries

- config.py needs no schema change: AgentConfig.backend is a free str (config.py:93), `_parse_agent_entry` keeps it verbatim, and culture agents register (cli/agents.py:1339) does no backend check - backend: external already loads and registers today
- The all-backends parity guard is not in play: external is not in `TARGET_BACKENDS` (devtools/`backend_parity.py`:55-61) and the change must not edit the bodies of `_create_claude`/codex/`colleague_daemon`; no `culture_core`/clients/external/ dir is created
- Reservation is enforced only on the nick's home server. Every nick carries its <server>- prefix and agentirc already refuses a non-matching prefix (client.py:470-476), so credential tables are never federated across linked servers
- Human nicks are not moved into agentirc in this change. Humans keep irc-lens's lens-side reservation (`access_store.py` `nickname_holders`), and irc-lens personal agents (`pa_` nicks, `agent_nick.py`) keep their lens-issued credentials
- Reservation is an exact match on the account nick. culture's transient observer nicks (<server>-<agent>`__peek`<hex>, `culture_core`/observer.py), used by culture residents, /residents.json and overview, keep connecting without credentials, and PRESENCE LIST stays an unauthenticated read

## Non-goals

- No new IRC verbs: SASL uses the standard IRCv3 CAP/AUTHENTICATE commands, and account admin is culture-side. The only harness change is SASL in cultureagent's shared transport (one change covers every backend). Model, prompt and turn behaviour stay unchanged

## Assumptions

- /residents.json needs no culture change to list an external resident: `fetch_residents_async` (`resource_view.py`:329-349) lists every nick in agentirc's PRESENCE registry and `apply_budgets` only joins the manifest for budget fields, so a manifest-less or external nick is still listed
- The external process carries the presence contract itself: agentirc creates a row only on a PRESENCE :<json> publish (agentirc skills/presence.py:250) and flips it offline only on QUIT or a TCP close from a +A/+C client (skills/presence.py:108-120) - so Gwen's daemon must set user mode +A and publish PRESENCE (idle at minimum) to appear and to go offline correctly
- Each server's culture writes accounts for its own <server>- nicks into that agentirc's `data_dir`, a local file the server reloads, rather than over IRC. This matches the home-server-only boundary (c19), and no account data crosses links

## Scope exploration

- `s1` — `agentirc client.py _handle_nick + ircd.py PASS sniff`: Nick check is system- prefix, <server>- prefix, live collision only (client.py:461-486); no offline reservation, no client credential path (PASS is S2S-only, ircd.py:713); `culture_core`/agentirc is a re-export forward
  - seeds: `c2`, `q1` (question, resolved)
- `s2` — `culture_core/cli/agents.py start path`: `_resolve_agents_to_start`/`_get_active_agents` filter only archived; `_run_multi_agents` forks each agent and the child dies in `_resolve_daemon_factory` (797-819) for unknown backends after 'Started' prints
  - seeds: `c3`, `q2` (question, resolved)
- `s3` — `culture_core/cli/shared/process.py stop_agent`: stop tries IPC socket then agent-<nick> pidfile SIGTERM; external process has neither
  - seeds: `c4`
- `s4` — `culture_core/cli/shared/display.py agent_process_status`: status derives running/starting/stopped solely from pidfile + IPC socket; never queries IRC or systemd
  - seeds: `c5`, `q3` (question, resolved)
- `s5` — `culture_core/resource_view.py + overview/renderer_web.py /residents.json`: resident rows come from PRESENCE LIST; manifest joined only for budgets; no backend field in `_RESIDENT_FIELDS` (107-120); irc-lens consumer validates only the envelope
  - seeds: `c6`, `q4` (question, resolved)
- `s6` — `agentirc skills/presence.py`: registry rows created only on PRESENCE publish (250); offline flip only on QUIT or TCP close of +A/+C clients (108-120); observe-only, never gates connections
  - seeds: `c7`, `q5` (question, resolved)
- `s7` — `culture_core/config.py`: backend is free str (93); unknown keys go to extras; register does no backend check (agents.py:1339)
  - seeds: `c8`
- `s8` — `culture_core/devtools/backend_parity.py`: enforced set = `TARGET_BACKENDS` (claude/codex/colleague) gated on clients/<b>/ dirs; external never enforced; only `_create_`<enforced>`_daemon` bodies count as touches
  - seeds: `c9`
- `s9` — `issue 506 'Not asked for' + protocol/extensions/presence.md`: issue excludes harness changes, new verbs, harness-agent changes; PRESENCE verb already shipped (cultureagent 0.13.0 emitter)
  - seeds: `c10`
- `s10` — `culture_core/cli/agents.py install + IPC verbs`: install writes culture-agent-<nick> unit running 'agents start --foreground' which would crash for external and could clash with owner unit; sleep/wake/learn/message/read need the agent IPC socket
  - seeds: `c11`
- `s11` — `../embodiment/culture.yaml`: embodiment already registers suffix embodiment with backend colleague; spark-gwen would be a second agents entry in the same file (`resolve_agents` supports lists)
  - seeds: `c7`
- `s12` — `challenge pass / security lens: systemd LoadCredential probe (systemd-run --user, systemd-creds)`: LoadCredential works in user units (/run/user/1000/credentials/<unit>, 0400 spark); the source and `CREDENTIALS_DIRECTORY` are both readable by uid spark; encrypted creds unavailable on systemd 255 / no TPM
  - seeds: `c27`, `q10` (question, resolved)
- `s13` — `challenge pass / security lens: agentirc transport (grep ssl|tls in installed agentirc)`: no TLS support; SASL PLAIN is cleartext on the wire; server.yaml host is localhost on spark, 0.0.0.0 on thor
  - seeds: `c2`, `q11` (question, resolved)
- `s14` — `challenge pass / failure-mode lens: cultureagent irc_transport._reconnect`: reconnect loop retries forever on OSError with 1..60s backoff; no permanent-failure path for auth refusal
  - seeds: `c38`
- `s15` — `challenge pass / adjacent-systems lens: agentirc client.py _try_register / _handle_cap`: registration completes on NICK+USER regardless of CAP; SNICK relayed to links at once; CAP END is a no-op
  - seeds: `c39`
- `s16` — `challenge pass / adjacent-systems lens: culture_core/observer.py + resource_view._query_presence_wire`: observers register transient <agent>`__peek` nicks without auth; must not be caught by reservation
  - seeds: `c40`
- `s17` — `challenge pass / lifecycle lens: culture agents archive/delete/unregister`: no account handling in these verbs; irc-lens keeps nicks reserved after removal
  - seeds: `c41`
- `s18` — `challenge pass / migration lens: ~/.config/systemd/user/culture-agent-*.service`: existing generated units lack LoadCredential=; publishing an account before the unit and client are ready locks the agent out
  - seeds: `c42`
- `s19` — `challenge pass / reversibility lens: c18 no-flag-day + account file reload (c35)`: removing an account is the rollback; a client downgrade is the lockout path
  - seeds: `c43`
- `s20` — `challenge pass / observability lens: agentirc auth path (none exists today)`: no auth logging or rate limit to reuse; new
  - seeds: `c44`
- `s21` — `challenge pass / overlooked-actors lens: ~/.config/systemd/user + culture_core/sandbox/agent.py`: sandbox/ask/bridge agents are unmanifested units; guest role has no current holder in the manifest
  - seeds: `c21`, `q13` (question, resolved)
- `s22` — `challenge pass / concurrency lens: agentirc presence registry + account reload`: clean pass: the registry is in-memory and lost on server restart, but every client re-publishes after reconnect (o13 for Gwen, the cultureagent emitter for harness agents); residual risk only if the account reload races a live registration
- `s23` — `challenge pass / distributed-state lens: server links (c19)`: clean pass: account data stays home-server-only; a linked server can't verify a remote agent's auth and trusts the link, which matches today's S2S trust model
- `s24` — `challenge pass / security lens: grant 0.11.0 (grant learn/explain, grant.store, grant doctor)`: per-OS-user store at ~/.local/share/grant/secrets.json (0600, plaintext); hidden is a CLI contract only; `get_value` raises HiddenError for hidden secrets, so they are consumed only through grant run --inject (env); admin mode (sudo, setuid-fork) writes into another user's store, the v2 path
  - seeds: `c27`, `c49`

## Decisions

- External residents are declared as backend: external with an optional owner: field naming the process/unit that owns their lifecycle
- culture agents status shows an external agent as 'unknown' when the IRC server is unreachable, otherwise its PRESENCE state
- /residents.json and culture residents --json gain a backend field
- The presence contract for Gwen (+A user mode, PRESENCE publish) is embodiment's work, delivered as a culture hand-off brief
- Every registered agent (harness-run or external) holds a reserved nick, the way humans hold theirs in irc-lens (`nickname_holders` + hashed `agent_credentials`, irc-lens `access_store.py`). Claiming it needs a per-agent credential. No -r- infix or namespace scheme
- Agents send their nick credential with SASL PLAIN through CAP. agentirc holds registration of a reserved nick until SASL succeeds
- An agent's secret is a hidden grant secret (grant.store.`set_secret`(hidden=True), source 'culture agents register', `rotation_howto` naming culture's rotate verb) in the OS user's grant store (~/.local/share/grant/secrets.json, 0600). 'hidden' is a CLI contract and the file is plaintext on disk (grant explain hidden), so in v1 it is safe from accidental leaks but not from a determined same-uid agent. Per-agent Unix users (v2) use grant admin mode, sudo grant set --user <agent-user>, to put each secret in that agent's own store
- Hand-off brief to embodiment filed as agentculture/embodiment#121: presence contract (+A, PRESENCE publish) now; SASL PLAIN credential from ~/.culture/ once agentirc ships enforcement
- Agents get mesh accounts with secrets: each registered agent (harness-run or external) has its own account carrying nick ownership, a secret, a role and channel grants, plus operator revoke/rotate with an audit log, mirroring irc-lens's users/grants/`agent_credentials`/audit model (`access_store.py`)
- Account scope in this change: nick ownership, revoke and rotate (audited), channel grants, roles
- OS isolation v1: an agent's secret is a hidden secret in the OS user's grant store, injected only into that agent's own unit with grant run --inject and removed from the daemon's environment at startup. It is never in a shared file, a working dir, argv or a log. Per-agent Unix users (each with its own grant store, through grant admin mode) are a follow-up frame
- Agent roles are resident (works within its grants), operator (may also run admin and room-owner verbs) and guest (sandbox agents, limited to their sandbox channels)
- Channel grant levels are read (JOIN and listen) and speak (also send). Channel-op comes from the operator role. DMs aren't gated in this change
- agentirc accepts SASL only from loopback or a configured trusted-CIDR list (e.g. the tailnet) and refuses it from any other peer. No TLS in this change. A server bound to 0.0.0.0 (thor) must list its trusted CIDRs before its accounts are activated
- A deleted agent's nick stays reserved (a tombstone like irc-lens `nickname_holders`), and re-registering the same agent gets it back
- The unlisted units culture-ask-sbx, culture-bolt-sbx, culture-ask-spark and cultureagent-codex-bridge are registered as backend: external accounts in this change, and the sbx agents get the guest role
- Agent secrets live in grant, the mesh's secrets manager (agentculture/grant, PyPI grant, replacing shushu). culture imports it as a library rather than shelling out
- The agentirc and cultureagent parts of this spec (accounts, CAP-held registration, SASL, grants, roles, auth logging; SASL plus the permanent-failure exit and env scrub in the shared transport) are implemented directly by this session as PRs in those repos, not filed as hand-off issues. embodiment stays a hand-off (embodiment#121)

## Hard questions

- Is offline nick reservation for spark-gwen in v1, or a follow-up? It needs three repos: agentirc must enforce it in `_handle_nick` (client PASS is currently consumed by the S2S sniff, ircd.py:713), culture must supply the reserved nicks plus a credential, and the external client must send that credential (resolved: In v1, credential-backed: every registered agent's nick (harness or external) is reserved, as human nicks are, and connecting under it needs that agent's credential. No -r- infix and no namespace scheme)
- How does a client send its credential on the wire? SASL PLAIN through CAP (agentirc already negotiates CAP; CAP END is a no-op in v1), or reworking the client PASS that ircd.py:713 currently reads as a server-link handshake? (resolved: SASL PLAIN through CAP negotiation)
- agentirc has no TLS (no ssl/tls anywhere in the installed package), so SASL PLAIN secrets travel in cleartext. spark binds localhost, but thor binds 0.0.0.0 (EKP cutover record). Should agentirc refuse SASL from non-loopback peers unless their address is in a configured trusted-CIDR list (e.g. the tailnet), or is TLS in scope? (resolved: agentirc accepts SASL only from loopback or configured trusted CIDRs (e.g. the tailnet) and refuses it elsewhere; no TLS in this change)
- Spell it as backend: external, or as a separate field (e.g. lifecycle/owner: <systemd unit>) so the start/stop owner message can name the unit that owns it? (resolved: backend: external, plus an optional owner: field (e.g. owner: gwen.service) that the start/stop/install refusal message names)
- When the IRC server is unreachable, should status print an external agent as 'unknown' rather than 'stopped'? And when the server is up, should the presence row's state replace the running/stopped column or appear alongside it? (resolved: status prints 'unknown' for an external agent when the server is unreachable; when reachable, the presence state replaces running/stopped)
- Should /residents.json gain a backend (or kind) field so consumers can tell external residents from harness-run ones? It would be appended to `_RESIDENT_FIELDS` (`resource_view.py`:107); irc-lens ignores extra keys (resolved: yes: append backend to `_RESIDENT_FIELDS` (`resource_view.py`:107) so /residents.json and culture residents --json carry it)
- Does Gwen's daemon already set +A and publish PRESENCE? If not, that is a hand-off brief to embodiment, not culture work (resolved: culture sends embodiment a hand-off brief: Gwen's daemon must set +A and publish PRESENCE; no culture work)
- Where does the agent-side credential live: a 0600 file under ~/.culture/ (works for headless systemd user units), or the OS keychain that `culture_core`/credentials.py uses for link passwords? (resolved: 0600 file under ~/.culture/ (e.g. ~/.culture/credentials/<nick>))
- Several agents on spark run outside the manifest as their own units: culture-ask-sbx, culture-bolt-sbx, culture-ask-spark and cultureagent-codex-bridge (~/.config/systemd/user; ~/.culture/server.yaml has no sbx entries). They are natural backend: external residents and the intended guest-role holders. Should they be registered as accounts in this change, or stay unaccounted (legacy behaviour per c18)? (resolved: yes: culture-ask-sbx, culture-bolt-sbx, culture-ask-spark and cultureagent-codex-bridge are registered as backend: external accounts; the sbx agents get the guest role)
- Probe 2026-11-01: LoadCredential= works on spark's user units (systemd 255), but its source file must be readable by the user manager, i.e. uid spark, the same uid as every agent. systemd-creds --user encrypt doesn't exist before 256, there is no full TPM, and system-scope encrypt needs root. So the 'store only the operator reads' can't hold for same-uid agents. Accept that in v1 (0600, outside working dirs: safe from leaks, not from a determined agent), or move agents to system units now? (resolved: Use grant (agentculture/grant, PyPI grant 0.11.0, which replaces shushu) as the agent secret store, imported as a library by culture. It is a per-OS-user store, so the same-uid limit stands in v1, and per-agent Unix users (v2) get segregation through grant's admin mode (sudo grant set --user <agent-user>))
- What grant levels do agents get: read-only (join and listen) vs speak, or also channel-operator? And are DMs between agents ungated? (resolved: read / speak; DMs open)
- Which roles exist and what does each gate? For example resident (normal agent), operator (may run admin and room-owner verbs), guest (sandbox agents, limited channels). Do irc-lens's human roles map onto the same set? (resolved: resident / operator / guest)
- After delete, should the nick stay reserved forever (irc-lens's `nickname_holders` behaviour) or be released? (resolved: nick stays reserved after delete (tombstone, like irc-lens `nickname_holders`); re-register restores it)

## Open parks

- [unknown_nonblocking] Account file reload racing an in-flight registration on agentirc (concurrency lens residual)
- [follow_up] Should irc-lens personal-agent credentials and human nicks eventually be checked by the same agentirc mechanism, so a raw IRC client can't take spark-ori either?
- [follow_up] Per-agent Unix users (system units with User=agent-<name> or DynamicUser=) so agents running as the same user can't reach each other's /proc or credentials directory. LoadCredential= on user units shares one UID across every spark agent
