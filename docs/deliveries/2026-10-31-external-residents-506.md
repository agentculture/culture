# Delivery Summary — external residents (#506)

plan: `external-residents-506` · run: `partial` · date: `2026-10-31`
baseline: `devague summary skeleton`

Run status is `partial`. Every task merged and is deployed, and the live
success signal passed 10/10 on production spark. Some of the plan's rollout
remains: accounts are active only on the `spark-colleague` canary (`d10`),
and Gwen waits on embodiment#121.

## Intent

> Culture recognises external residents: an agent registered with backend:
> external appears in culture agents status and /residents.json with presence
> from its live connection, while culture agents start/stop leave its lifecycle
> to the process that owns it

The confirmed after-state widened this into agent identities. Every
registered agent can hold a mesh account: a secret kept in grant, SASL login,
a role and channel grants. Nobody can connect as a registered agent without
its secret, and revoking cuts an agent off within seconds.

The work spans four branches in three repos:

- culture Phase A, `feat/external-residents`
- culture Phase B, `feat/agent-accounts`
- agentirc, `feat/agent-accounts`
- cultureagent, `feat/sasl-auth`

Spec: `docs/specs/2026-10-31-external-residents-506.md`. Plan:
`docs/plans/2026-10-31-external-residents-506.md`. Split (gate 2):
`docs/plans/2026-10-31-external-residents-506-split.md`.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — Phase A / culture: backend: external + owner: in agent config, plus an `is_external` / owner-message helper
- `t2` — Phase A / culture: start, stop, install and IPC verbs leave external agents to their owner
- `t3` — Phase A / culture: culture agents status shows external agents' live presence
- `t4` — Phase A / culture: /residents.json and culture residents carry a backend field
- `t5` — Phase A / culture: move from shushu to grant (dependency, culture secret forward, docs)
- `t6` — Phase A / culture: docs, version bump, PR and deploy of external residents
- `t7` — Phase B / agentirc: hold registration until CAP negotiation ends
- `t8` — Phase B / agentirc: agent accounts and SASL PLAIN authentication
- `t9` — Phase B / agentirc: enforce channel grants and roles
- `t10` — Phase B / agentirc: revoke takes effect live; failed-auth logging, rate limit and telemetry
- `t11` — Phase B / cultureagent: SASL in the shared transport, permanent exit on auth refusal
- `t12` — Phase B / cultureagent: daemon scrubs its secret from the environment at startup
- `t13` — Phase B / culture: account store — mint in grant, write agentirc's accounts file, audit log
- `t14` — Phase B / culture: account CLI verbs and lifecycle — register, archive, delete, unregister, revoke, rotate, disable
- `t15` — Phase B / culture: grant-wrapped units, doctor checks, ordered activation
- `t16` — Phase B: release all three repos, roll out on spark, verify live

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `AgentConfig.owner`; `culture_core/cli/shared/external.py` (`is_external`, `owner_message`). Drafted by colleague; the docstring was corrected in the main session. Phase A |
| `t2` | delivered | start/stop skip external agents and print the owner; install, sleep, wake and learn refuse them; `message`/`read` treat them like any nick (`d3`). Phase A |
| `t3` | delivered | `status` shows the PRESENCE state, `unknown` if the server is unreachable, `offline` if the agent never published; the detail view has an `Owner:` line; harness rows are byte-identical. Phase A |
| `t4` | delivered | `backend` field on every resident row (null for unregistered nicks); schema table updated. Colleague hit its step cap; its work was recovered from its branch and verified. Phase A |
| `t5` | delivered | `grant>=0.11,<0.12` dependency; `culture secret` forwards to grant; docs name grant instead of shushu (frozen records exempt, `d1`); two wrong doc examples fixed. Phase A |
| `t6` | partial | `docs/external-residents.md`, a docs-coverage audit with every gap closed, 14.12.0 with changelog, and the mesh fix (no units for external residents). **Not done:** PR merge, deploy, live registration of spark-gwen and the sbx/ask/bridge units (`d4`) |
| `t7` | delivered | agentirc holds `001`/`SNICK` after `CAP LS` until `CAP END`; 9.14.0 |
| `t8` | delivered | agentirc `accounts.py` (file and reload), SASL PLAIN, trusted peers, nick reclaim, no account data over links; hardened further by `h1`/`h2` (`d6`) |
| `t9` | delivered | read/speak grants on JOIN/PRIVMSG/NOTICE; operator-only room verbs; guest channel prefixes; extended by `h1` to HISTORY/BACKFILL/EVENTSUB/THREAD/TOPIC/EVENTPUB |
| `t10` | delivered | live revoke via a 1 s watcher plus SIGHUP; failed-auth logging; rate limit; `culture.auth.failures`; then `h1` (critical/high) and `h2` (medium) review fixes (`d6`) |
| `t11` | delivered | `IRCTransport(sasl_secret=)`, `AuthenticationRefused`, exit 78; resolved l1 (the old code reconnected every ~1 s forever); fixed a fake-server teardown hang that only showed on Python 3.12 |
| `t12` | delivered | `BaseDaemon` pops `CULTURE_AGENT_SECRET` at construction; covers all 5 backends via `super().__init__`. Colleague; its junk "WIP" commit (uv.lock only) was dropped |
| `t13` | delivered | `culture_core/accounts.py`: hidden grant secrets, the agentirc accounts-file format, audit log; later hardened by `hc` (`d7`) |
| `t14` | delivered (amended) | `account revoke/rotate/disable/show/list`, `unarchive`, lifecycle hooks. **register no longer creates an account** (`d7`) |
| `t15` | delivered (amended) | grant-wrapped units, `RestartPreventExitStatus=78 64`, doctor class 5; activation reworked into opt-in `account activate` with rollback (`d7`); pins deferred (`d5`, `d8`); host secret-store test guard |
| `t16` | partial | releases prepared (agentirc 9.14.0, cultureagent 0.16.0, culture 14.13.0, all with changelogs); end-to-end success-signal test `tests/test_success_signal_506.py`. **Not done:** publishing, raising the pins plus the uv.lock refresh (`d8`), rollout on spark, the live success signal (`d4`) |

16 of 16 plan tasks accounted for: 14 delivered (two of them amended), 2
partial, 0 dropped, 0 blocked.

Three tasks were added outside the plan (`d6`, `d7`):

| Task | Repo | Scope |
|------|------|-------|
| `h1` | agentirc | critical and high review findings |
| `h2` | agentirc | medium review findings |
| `hc` | culture | opt-in activation plus 13 culture review findings |

## Mid-work Decisions

Approved deviations, quoted from the delivery store:

- `d1` — t5's shushu grep criterion exempts frozen design records (docs/specs, docs/plans, docs/superpowers, including this spec and plan and the 2026-05-05 org-ecosystem-registry docs); README and the live docs name grant — the records describe history; the user approved the exemption on 2026-11-01.
- `d2` — wave 2 tasks (t2, t3, t8) started as soon as their own dependencies merged rather than after all of wave 1 — colleague runs one task at a time; wave-2 files were disjoint from t4/t12.
- `d3` — `message` and `read` don't refuse external agents — `message` is an IRC DM, not IPC; decided via /assess (`allow_both` 0.82).
- `d4` — t6/t16's "PR merged" and "deployed live" criteria move after human gate 3 — the user's goal orders validate and summarize before any PR, and merging is the user's gate.
- `d5` — t15 doesn't raise the dependency pins; t16 does — the SASL-capable versions are unreleased.
- `d6` — two agentirc hardening tasks (`h1`, `h2`) added and blocking the release; low findings filed as agentirc#65 — the independent security review showed the delivered code didn't yet keep c2/c23/c32 and the malformed-file promise; /assess `critical_high_medium` 0.81, block release 0.97.
- `d7` — account activation becomes explicit and opt-in (`culture agents account activate`); `hc` fixes culture review findings H1–H5 and M1–M8; mesh setup/update skip external residents — automatic activation on register would lock agents out once the pins land; /assess `opt_in_activate` 0.97, fix high+medium 0.87. **This changes confirmed claim c17 and t14's "register creates the account".**
- `d8` — culture Phase B's pins and uv.lock refresh come as the last commit on its PR, after agentirc and cultureagent publish — uv can't lock unpublished versions.

Decisions taken without a deviation record:

- **Nick reclaim (t8).** A correct SASL login evicts any other local holder of the nick. This is my reading of "right secret gets 001 whether or not the real agent is online".
- **`h1` and `h2` design choices.**
  - All clients get 451 for commands before registration.
  - Local nicks must be printable ASCII.
  - Grants and guest prefixes match channel names exactly.
  - An unreadable accounts file fails closed. If no reserved nick can be recovered at startup, all local registrations are refused until the file is fixed.
  - Accepted as security-consistent; recorded as deltas `b3`–`b6`.
- **`hc` design choices.**
  - Activating a culture-owned agent requires an installed unit.
  - macOS and Windows refuse activation.
  - Archive disables the unit.
  - Secret names gain a 10-hex suffix of the exact nick.
- **Python 3.12 for every gate.** The worktree venvs defaulted to 3.13, which caused every reported "pre-existing failure" (`l10`).
- **Host cleanup.** An unisolated t14 test wrote a test account and grant secret onto the live host. Both were removed, and an autouse guard now blocks it (`l11`). There was no live impact: spark runs agentirc 9.13.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t5` (`d1`) | Frozen records describe history, including the shushu-to-grant move itself; rewriting them would falsify it. The user approved the exemption on 2026-11-01 | acceptable |
| `t8` (`d2`) | Colleague runs one task at a time; wave-2 files were disjoint from t4/t12, so parallel still equals serial | acceptable |
| `t2` (`d3`) | `agents message` sends over IRC, not the daemon socket; refusing it would block operators from DMing Gwen | acceptable |
| `t6` (`d4`) | Validate and summarize come before any PR; merging and deploying are the user's gate | needs-follow-up |
| `t15` (`d5`) | The SASL-capable cultureagent and accounts-capable agentirc are unreleased, so pinning in t15 would break culture's lockfile and CI | acceptable |
| `t10` (`d6`) | The independent security review found the delivered agentirc code didn't yet meet c2/c23, c32 and the malformed-file guarantee | needs-follow-up |
| `t15` (`d7`) | Automatic activation on register would publish accounts before daemons hold their secret, leave units wrapped around missing secrets, collide secret names and exit 1 everywhere | needs-follow-up |
| `t14` (`d7`) | "register creates the account" replaced by the explicit, opt-in `account activate` | needs-follow-up |
| `t16` (`d8`) | uv can't lock unpublished versions; the pins land after agentirc and cultureagent publish | acceptable |
| `t16` (`d4`) | Rollout on spark and the live success signal wait for PR review and merge | needs-follow-up |

The `needs-follow-up` entries are follow-ups for the PR reviewer. The confirmed
claim c17 and t14's first criterion no longer describe what ships. The spec
should be amended to match `d7` once the user accepts it.

## Evidence

All runs were on Python 3.12 (CI's version) on 2026-11-01. Each integration
branch was tested with its sibling integration branches installed editable.

- **Integration branch heads.**
  - culture Phase A: `da8ee8f`
  - culture Phase B: `ae4b599`
  - agentirc: `c042738`
  - cultureagent: `cb605f5`
- **Full suites.**

  | Branch | Passed | Skipped | Failed |
  |---|---|---|---|
  | culture Phase A | 2072 | 1 | 0 |
  | culture Phase B | 2179 | 1 | 0 |
  | agentirc | 1037 | — | 0 |
  | cultureagent | 1572 | 13 | 0 |

- **Backend parity:** `python -m culture_core.devtools.backend_parity --base origin/main` reports `PASS — no backend-specific surface touched` on Phase A and B.
- **End-to-end success signal (c25):** `tests/test_success_signal_506.py`, 4 tests, 5 of 5 consecutive runs green.
  - Status latency after the PRESENCE publish: 0.006–0.011 s.
  - `start --all` forks 0 for the external agent.
  - 46 of 46 nick-takeover attempts refused, offline and online.
  - JOIN outside grants refused with 474; read-only PRIVMSG refused with 404.
  - Revoke to socket closed: 0.482–0.486 s, including the CLI call.
- **Cross-repo SASL probe:** real agentirc against cultureagent's transport, re-run after h1 and after h2.
  - Right secret: registers.
  - Wrong secret: `AuthenticationRefused` (904).
  - Account nick with no secret: refused.
  - No account: registers as before.
- **Evidence and deltas.**
  - Evidence records `e1`–`e17`: all pass, all proposed, awaiting adjudication.
  - Behavioural deltas `b1`–`b10`: proposed.
  - Obligation `o13` (Gwen's presence contract) has **no evidence**: unchecked.
- **Independent reviews.**
  - agentirc security review: 1 critical, 3 high, 7 medium and 4 low findings. Critical, high and medium are fixed in `h1`/`h2` (61 + 36 new red-first tests); low are filed as agentirc#65.
  - culture review: 5 high, 8 medium and 7 low findings. High and medium are fixed in `hc`; low are filed as culture#513.
- **Docs-coverage audits.** Phase A had 3 doc gaps and 3 stale statements; Phase B had 8 doc gaps. All closed; markdownlint reports 0 errors on the touched files.
- **Host safety.** After every full suite, `~/.culture/data` held no `accounts.json` and `grant list` had no `CULTURE_AGENT_*` entries.
- **Issues.**
  - embodiment#121: hand-off brief plus two corrections.
  - agentirc#65 and culture#513: low review findings.
  - cultureagent#57: an unrelated SonarCloud CI ask, already done upstream in #58.

## Delivery Claims

The evidence records are still `proposed` (filed by the agent, awaiting the
user's adjudication). Confidence below reflects tests that actually ran green,
capped where a lapse or missing check applies.

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c3` — start (and --all) skips external agents and names the owner, before any fork | high | `e1`: `tests/test_cli_agents.py::TestExternalResidents`, `tests/test_success_signal_506.py::test_1_…` @ `ae4b599` |
| `c4` — stop skips external agents with the owner message | high | `e2` @ `ae4b599` |
| `c5` — status shows an external agent's live PRESENCE state | high | `e3`: `tests/test_status_external.py` (real agentirc) @ `ae4b599` |
| `c6`/`c14` — `/residents.json` and `culture residents --json` list external residents with a `backend` field | high | `e4`: `tests/test_residents_endpoint.py`, `tests/test_residents_cli.py` |
| `c11` — install/sleep/wake/learn refuse external agents; mesh setup writes no unit for them; message/read per `d3` | high | `e5` @ `ae4b599` |
| `c2` — account nicks are credential-gated at the IRC layer on every registration path | high | `e6`: agentirc `test_accounts_sasl.py`, `test_accounts_hardening*.py` @ `c042738`; culture e2e test 2 (46/46) |
| `c18` — no flag day: nicks without an account behave as before | high | `e7` @ `c042738` (full agentirc suite green) |
| `c19` — account data never crosses server links | high | `e8`: linked-pair S2S recording test @ `c042738` |
| `c32` — read/speak grants enforced on every read and post path | high | `e9` @ `c042738`; culture e2e test 3 |
| `c33` — roles gate operator-only verbs; guests limited to sandbox channels | high | `e10` @ `c042738` |
| `c34` — revoke closes the live session in ≤ 5 s (measured 0.48 s); rotate works; audit log has no secrets | high | `e11`: culture `test_agent_accounts_cli.py`, e2e test 4; agentirc `test_accounts_revoke.py` |
| `c17` (amended by `d7`) — the secret lives in grant and reaches only its unit via `grant run --inject`; activation is opt-in with rollback | high | `e12`: `tests/test_agent_account_activation.py`, `tests/test_account_activation_hardening.py` @ `ae4b599` |
| `c50` — `culture secret` forwards to grant; docs moved off shushu | high | `e13`: `tests/test_cli_secret.py` |
| `c38` — permanent exit 78 on auth refusal, no reconnect loop | high | `e14`: cultureagent `tests/test_irc_transport_sasl.py` @ `cb605f5` + cross-repo probe; resolves `l1` |
| `c39` — registration held until CAP END after CAP LS | high | `e15`: agentirc `tests/test_cap_registration_hold.py` |
| `c44` — failed-auth logging without secrets, rate limit, metric | medium | `e16` @ `c042738`. Capped: a local process can still throttle one named agent for up to 60 s (documented residual of the per-(address, account) limit) |
| `c49` — daemon removes its secret from the environment before spawning | medium | `e17`: cultureagent `tests/test_base_daemon_secret.py`. Capped: the "all 5 backends" part is verified by reading, not a per-backend test; `/proc/<pid>/environ` still holds it for the same user (culture#513) |
| `c7` — Gwen's daemon sets `+A` and publishes PRESENCE | unverified | (no evidence — embodiment#121 hand-off; obligation `o13` unchecked) |
| Live: spark-gwen and the sbx/ask/bridge units registered as external residents on spark | unverified | (not done — `d4`) |
| Live success signal on spark after deploy; agents authenticate with 0 plaintext secrets on disk | unverified | (not done — `d4`, `d8`) |

Lapse ledger: `l1` is approved and now resolved by `e14` (the reconnect loop
was real, every ~1 s). `l2`–`l11` are pending approval and are not cited as
evidence. `l10` (Python 3.13 in worktrees) and `l11` (host pollution, cleaned)
are the material ones for the reviewer.

## Post-PR review round (`d9`)

After the PRs opened, the user's `/code-review` of all four found 20
issues. All of them are fixed on the PR branches before merge, as deviation
`d9`.

**agentirc#66:**

- Channel-less events are scoped for accounts; QUIT channel lists are
  filtered.
- `EVENTPUB *` is allowed for operators and residents only (/assess
  `operator_and_resident` 0.83).
- ROOMINVITE is read-gated with an anti-probe reply.
- The rate limiter is a bounded LRU, which also closes agentirc#65 L1.
- Lookups no longer stat the file. Reloads happen only from the watcher,
  SIGHUP and registration, so a grant or role change reaches connected
  sessions within about 1 s.

**cultureagent#59:**

- No dead transport after a mid-SASL close on reconnect.
- A 433 is transient (150 s grace).
- Backoff doubles until a `001`.
- A SASL timeout or a 906 is a failed attempt; a 902 is permanent; a 907
  proceeds.
- AUTHENTICATE payloads are chunked at 400 bytes.
- No orphaned futures.
- New public API: `BaseDaemon.auth_refusal` and `wait_auth_refused()`.
- New `docs/sasl.md`; the CHANGELOG "Fixed" entry is now true.

**culture#514/#515:**

- `culture server start` passes the `accounts:` block to agentirc
  (new `--config`).
- culture validates every accounts.json write exactly as agentirc does,
  including agentirc's own parser when it is installed.
- `disable` and `revoke` roll back their unit changes on failure.
- A wrapped unit always gets `RestartPreventExitStatus` including 64.
- The CodeQL clear-text-logging alerts are fixed.
- Tests that need agentirc 9.14 or cultureagent 0.16 skip on CI's older
  versions until the d8 pin commit.
- The runner uses cultureagent's public refusal API.

Suites after the round, on Python 3.12 with the integration branches:

| Repo | Passed | Skipped | Failed |
|------|--------|---------|--------|
| culture Phase B | 2215 | 1 | 0 |
| agentirc | 1061 | 0 | 0 |
| cultureagent | 1586 | 13 | 0 |

The cross-repo SASL probe was re-run after each repo's fixes, with the
same four outcomes. A full suite passing after a fix is execution-level
evidence. The filed evidence `e1`–`e17` predates this round and still
reflects the earlier heads.

## Live deploy (2026-11-01, after the user approved merge and deploy)

**Merged and published:**

| PR | Release |
|----|---------|
| agentirc#66 | agentirc-cli 9.14.0 |
| cultureagent#59 | cultureagent 0.16.0 |
| culture#514 | culture 14.12.0 |
| culture#515 | culture 14.13.0, including the d8 pin commit |

**Pin commit.** The culture suite ran green against the *published* agentirc
9.14.0 and cultureagent 0.16.0: 2225 passed, 1 skipped (Playwright). All
version-marked tests ran.

**Deployed on spark:**

- Ran `uv tool install --force --refresh --python 3.12
  'culture[all-backends]==14.13.0'`.
- Restarted, in order: both servers, all four harness agents, the sandbox
  agents, the codex bridge and the console.
- Every unit is active.
- The four harness agents show `idle` in `culture residents` (PRESENCE over
  9.14).
- The console answers HTTP 200.
- The spark server listens on `127.0.0.1:6667`, so no trusted CIDRs are
  needed for local agents.

**Live success signal, 10/10.** Run against a throwaway external account
`spark-probe` (secret injected with `grant run`, never printed), which was
removed afterwards (evidence `e18`–`e21`):

| Check | Result |
|---|---|
| External PRESENCE shows in `status` | 0.16 s |
| `start` forks nothing and names the owner | ✓ |
| Takeovers without the secret, agent online | 12/12 refused |
| The real session is unaffected | ✓ |
| JOIN outside grants | 474 |
| Revoke closes the live session | 0.84 s, including the CLI |
| After revoke | old secret refused, nick stays reserved |

**Canary account: spark-colleague** (/assess `probe_then_one_canary` 0.61,
canary `spark-colleague` 0.81; evidence `e22`):

- Activation wrapped its unit, restarted it and verified it reconnected.
- Its child processes don't have the secret.
- A no-SASL takeover of its nick is refused with "SASL authentication
  required".

**External residents registered** (`d10`):

| Nick | Owner | Status |
|---|---|---|
| `spark-gwen` | `embodiment-gwen.service` | `offline` until it publishes PRESENCE (embodiment#121) |
| `spark-ask` | `culture-ask-spark.service` | `offline` until it publishes PRESENCE |

`cultureagent-codex-bridge` was dropped: it is an HTTP bridge, not a
resident. `sbx-ask` and `sbx-bolt` were deferred: they live on the sbx
server's manifest.

## Remaining Work / Follow-up

- **Done 2026-11-01:** the four PRs are merged in order and deployed
  (see Live deploy). The original list follows for reference.
- **Open the four PRs, then merge in order** (`d8`):
  1. agentirc `feat/agent-accounts` → publishes 9.14.0;
  2. cultureagent `feat/sasl-auth` → publishes 0.16.0;
  3. culture Phase A `feat/external-residents` (14.12.0);
  4. culture Phase B `feat/agent-accounts` (14.13.0), plus a final commit raising `agentirc-cli>=9.14.0` and `cultureagent~=0.16.0` with a `uv.lock` refresh.

  Next step: main session at gate 3; the user reviews and merges.
- **Deploy and register (t6/t16 live, `d4`).**
  - `uv tool install --force --python 3.12 'culture[all-backends]'` on spark, then restart culture-console.
  - Register spark-gwen and culture-ask-sbx, culture-bolt-sbx, culture-ask-spark and cultureagent-codex-bridge as `backend: external` with `owner:`; sbx agents get the guest role.
  - Activate accounts agent by agent with `culture agents account activate`, keeping `account disable` ready as rollback.
  - Verify the success signal live.
- **Activate the remaining harness agents** one at a time, once the
  spark-colleague canary has run cleanly: `culture agents account activate
  spark-agentirc`, then `spark-associate`, then `spark-culture`.
- **sbx-ask / sbx-bolt:** register them through the sbx server's manifest.
- **thor**, which is bound to `0.0.0.0`: set `accounts.trusted_cidrs` (or bind the tailnet or loopback) before any account there is activated.
- **embodiment#121:** Gwen's `+A` and PRESENCE now; SASL with `grant run --inject` once agentirc 9.14 is live. Obligation `o13` stays unchecked until then.
- **Spec amendment.** Re-export the spec with claim c17 (and t14's criterion) amended to the opt-in activation of `d7`, once the user accepts it.
- **Adjudication (the user).** Confirm or reject evidence `e1`–`e17`, deltas `b1`–`b10` and lapses `l2`–`l11`.
- **Follow-up issues.**
  - agentirc#65: rate-limiter bound, CAP LS unicode crash, unregistered-connection reaping, reload signature.
  - culture#513: fsync and atomic unit rewrite, `/proc` environ note, hand-edited units, rename, doctor icon, hidden-secret note.
- **Parked follow-ups from the spec.**
  - `v1`: humans' nicks under the same agentirc check.
  - `v2`: per-agent Unix users. Same-uid agents can still read each other's grant store.
  - `v3`: account reload racing an in-flight registration.
- **Residual risks to know at review.**
  - A local process can throttle one named agent's logins for up to 60 s.
  - An unreadable `accounts.json` at agentirc startup with no recoverable nicks locks out all local registrations until fixed.
  - Activation needs an installed systemd unit, so macOS and Windows can't activate culture-owned agents.
