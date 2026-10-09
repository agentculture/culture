# Delivery Summary — guest mode sandbox

plan: `guest-mode-sandbox` · run: `partial` · date: `2026-10-09`
baseline: `devague summary skeleton`

## Intent

> Visitors to culture.dev can sign in with any email, accept the Terms of
> Service and Privacy Policy, and chat in an isolated guest sandbox where a
> local model (lobes) explains culture's code and docs — while the owner's
> email still lands on the real mesh console and can hop into the guest
> sandbox

After: chat.culture.dev is one site for everyone: approved users sign in via
SSO and land on the real console (the owner can switch into the sandbox);
guests enter an email + nickname, accept ToS + Privacy Policy, and land in the
sandbox chat.

The run executed the converged plan `docs/plans/2026-10-09-guest-mode-sandbox.md`
(16 tasks, 5 waves) across three repos — culture, irc-lens, katvan — via
`/assign-to-workforce`, with the split approved at gate 2
(`docs/plans/2026-10-09-guest-mode-sandbox-split.md`). The run is **partial**:
everything up to a loopback staging rollout is built, merged on integration
branches and validated; the public cutover of chat.culture.dev is deferred
until after the PRs merge (approved deviation `d5`).

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — \[culture\] Sandbox IRCd provisioning: an isolated, unlinked 'sbx' server on 127.0.0.1
- `t2` — \[culture\] Dedicated tool-less sandbox agent (sbx-ask) generalized from spark-ask
- `t3` — \[irc-lens\] Config: `guest_mode` switch and guest/sandbox settings
- `t4` — \[irc-lens\] Tier resolution: approved vs guest on one host
- `t5` — \[irc-lens\] Guest store: guests, consent, tokens, bans, password hashes
- `t6` — \[irc-lens\] Mail sender for guest tokens
- `t7` — \[irc-lens\] CSRF-safe guest session cookie
- `t8` — \[irc-lens\] Observability: guest counters and agent-offline signal
- `t9` — \[irc-lens\] Entry card: email, password window, Sign in or Guest mode, token verification
- `t10` — \[irc-lens\] Session routing: guest -> sandbox IRCd, approved -> real mesh, sandbox toggle, consent gate
- `t11` — \[irc-lens\] Owner admin CLI: passwords, bans, deletions
- `t12` — \[irc-lens\] PII-stripped export of guest data
- `t13` — \[irc-lens\] Chat UI uplift (Option A): tier badge, command palette, sandbox marker, phone layout
- `t14` — \[katvan\] Terms of Service + Privacy Policy pages with a published version
- `t15` — \[cloudflare\] Path-scoped Access app on chat.culture.dev/login + cookie-scope verification
- `t16` — \[spark rollout\] Deploy, cut over Access, and validate end to end

## Actual Delivery

Integration branches (not yet pushed): culture `spec/guest-mode-sandbox`,
irc-lens `feat/guest-mode`, katvan `feat/terms-privacy`.

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `culture server install --standalone --name sbx` (loopback, own ports/data-dir, never `--link`/`--mesh-config`), `docs/guest-sandbox.md`; culture `3a699bd`, merged `05f9c27` |
| `t2` | delivered | `culture_core/sandbox/agent.py` + `culture sandbox agent`: no tools on the wire, thinking off, short answers, `max_tokens` ≤ 700, single-flight queue (max 5), NSFW decline + flag log, configurable models; culture `7cde62e`, merged `ab9f200`; staging fixes `ba1eec5`, `24cff92`, `408e234` |
| `t3` | delivered | `guest_mode` config section (off by default, unknown keys rejected), `docs/guest-mode-config.md`; irc-lens `0fcda66`, merged `78cb44d` (finished by the main agent, `d1`) |
| `t4` | delivered | approved / guest / anonymous tiers, deny-by-default `allows_anonymous`, tier only from a verified Access JWT; merged `3f0e26a` |
| `t5` | delivered | SQLite `GuestStore` (guests, consents, hashed single-use tokens, bans, flags, inputs, deletions, argon2id passwords, attempt counters); merged `6e8d394` (native subagent per `d2`) |
| `t6` | delivered | `mail.py`: one fixed token template, Resend adapter (stdlib urllib), recording + none adapters; merged `3160394` (native subagent per `d3`) |
| `t7` | delivered | signed `lens_guest` cookie (HttpOnly, Secure, SameSite=Strict) + CSRF middleware on every non-GET; merged `f9b82a7` |
| `t8` | delivered | `metrics.py` counters + `AgentPresence` (60 s), owner-only `/owner/metrics`; merged `5e71925` (native subagent per `d3`) |
| `t9` | delivered | `/entry` card (email → identical password window → Sign in / Guest mode → nickname + consent → emailed token), timing floor, rate limits, Turnstile-when-configured, `legal.py`; merged `7ef73f6` |
| `t10` | delivered | tier-only `_resolve_session`, guest → sandbox only, approved sandbox toggle, consent gate; merged `b52ea43` with seam fixes (shared store, `/consent`, test isolation) |
| `t11` | delivered | `irc-lens guests passwd/ban/unban/list/flags`, `ban --flag`, ban sweeper (≤ 30 s), `/delete` flow; merged `7aa44dd` |
| `t12` | delivered | `irc-lens guests export --redacted` with random per-export pseudonyms and PII scrubbing; merged `293a676` (native subagent per `d3`) |
| `t13` | delivered | Option A chat UI (tier badge, palette, amber sandbox marker, phone drawer), `/presence`, plus `d4` guest command allowlist + message rate limit; merged `25bf7de`; screenshots in irc-lens `docs/screenshots/guest-mode/` |
| `t14` | delivered | `/terms/`, `/privacy/`, `/legal/version.json` from `site/_data/legal.yml` (v1.0, effective 2026-10-09); katvan `188d1b4`. Contains a `[contact address]` placeholder for the owner |
| `t15` | partial | `scripts/cf_access_login_path.py` (narrow/restore the existing Access app to `/login`, pins host-wide cookie) merged `edc8587`; **not applied** to Cloudflare and the browser cookie-scope check not done — both move to the cutover |
| `t16` | partial | (a) loopback staging rollout done and validated (see Evidence); (b) public cutover not done — deferred by `d5` |

## Mid-work Decisions

- `d1` — t3 was completed by the main agent: colleague's drive stopped after the RED tests + LensConfig fields (WIP on stop); the main agent wrote the loader and docs against colleague's tests — colleague (local Qwen) ran out of steps before implementing; salvage per the colleague-tiering practice kept the confirmed contract and tests unchanged
- `d2` — t5 owner changes from colleague to a native subagent (sonnet) that implements `guest_store.py` against colleague's salvaged RED tests — colleague's second drive (t5) also exhausted its step budget with no implementation (exit 2)
- `d3` — Remaining colleague tasks (t6, t8, t12) move to native subagents (sonnet), keeping each task's contract unchanged — colleague completed 0 of 2 drives within budget; keeping it on the critical path stalls waves 3-4
- `d4` — t13 additionally enforces a server-side guest command allowlist and the per-guest /input message rate limit — t10 left guest /input unrate-limited and guest slash commands unrestricted; c30/h24 and c38 need server-side enforcement
- `d5` — t16 splits in two: (a) a local staging rollout before the PRs, where the E2E probes run for /validate-delivery; (b) the public cutover after the PRs merge — the owner's goal requires validation and summary before PRs, and the cutover would expose unreviewed code
- Access narrowing edits the **existing** chat.culture.dev app's destination instead of creating a second path app — keeps the AUD the lens already pins; `path_cookie_attribute` was read as unset on the live app, so the login cookie stays host-wide (behavioral delta `b6`).
- Integration fixes made by the main agent at merge time (no deviation record; contracts unchanged): t9/t10 seam — one shared `GuestStore`, a `GET /consent` route (`b5`), tests stubbing `irc_lens.legal` as a package attribute, and an autouse `XDG_DATA_HOME` fixture after guest-mode tests wrote `~/.local/share/irc-lens/guests.db` (the stray, empty file was deleted).
- Staging found three gaps no unit test caught; each was fixed test-first: sandbox sessions joined no room, so guests could not reach the agent (`b3`, irc-lens `2ae7914`); `sbx-ask` ignored unaddressed questions (`b1`, culture `24cff92`); the "curated" bundle was 2.5 MB and made the first answer 82 s (`b2`, culture `408e234`). The documented `culture sandbox agent --config/--bundle-from` CLI also failed (culture `ba1eec5`).

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t3` (`d1`) | colleague (local Qwen) ran out of steps before implementing; salvage per the colleague-tiering practice kept the confirmed contract and tests unchanged | acceptable |
| `t5` (`d2`) | colleague's second drive (t5) also exhausted its step budget with no implementation (exit 2); two of two colleague drives incomplete | acceptable |
| `t8` (`d3`) | colleague completed 0 of 2 drives within budget; keeping it on the critical path stalls waves 3-4; native subagents completed every wave-1/2 task first try | acceptable |
| `t13` (`d4`) | t10 landed routing but left guest /input unrate-limited and guest slash commands unrestricted; c30/h24 (message rate limits) and c38 (tier-aware commands) need server-side enforcement, and session.py/input handling sit in t13's files | needs-follow-up |
| `t16` (`d5`) | the owner's goal requires /validate-delivery and /summarize-delivery before opening PRs, while t16's public cutover would expose unreviewed code and its 'landed as PRs' criterion can only hold after merge | needs-follow-up |
| `t15` | the narrowing script landed but was not applied, and the browser cookie-scope verification (assumption c48) was not done; both ride with the t16(b) cutover — no record covers this beyond `d5` | needs-follow-up |
| `t2` | three post-merge fixes from staging (CLI flags, `answer_unaddressed`, curated budgeted bundle) changed shipped behavior relative to the first merge; contract unchanged | acceptable |
| `t10` | merged with main-agent seam fixes (shared store, `/consent`, test isolation); the sandbox room auto-join was added after staging | acceptable |

## Evidence

- tests: culture full suite `bash .claude/skills/run-tests/scripts/test.sh -p -q` at `408e234` — pass (1947 passed, 1 skipped)
- tests: irc-lens full suite `uv run pytest -q` at `2ae7914` — pass (996 passed, 18 deselected)
- tests: obligation re-runs during /validate-delivery — irc-lens 53 + 5 passed at `2ae79148d3`, culture 7 passed at `408e234e86` (evidence `e1`–`e15`, all `pass`, proposed)
- tests: irc-lens `-m playwright` — 17 passed (t13 run, chat UI browser tests)
- accessibility: Lighthouse 13.5.0 — 100 on landing, guest chat and approved chat (mobile and desktop; t13 run)
- build: katvan `bundle exec jekyll build --config _config.base.yml,_config.culture.yml --strict_front_matter` — ok; `/legal/version.json` = `{"terms":"1.0","privacy":"1.0","effective":"2026-10-09"}`
- staging E2E (loopback: sbx IRCd 6668, sbx-ask on cortex-spark2, irc-lens 8766): 1 guest first answer 14.2 s; 5 concurrent guests answered 9.9 / 14.5 / 21.0 / 25.9 / 29.9 s; staging lens TCP peers all `127.0.0.1:6668`, none `:6667`; 10/10 guests hold v1.0 consent; NSFW declined + flagged, `guests ban --flag j1` dropped the session after 26 s (evidence `e16`–`e21`, proposed)
- probe: cortex-spark2 latency, no prefix-cache benefit — ~7 s at ~10.7k prompt tokens, ~28 s at ~66k
- commits: culture `0600e10..408e234`; irc-lens `origin/main..2ae7914` on `feat/guest-mode`; katvan `origin/main..188d1b4`
- deltas: `b1`–`b7` (proposed)
- PRs / issues: none yet — PRs open after this summary

## Delivery Claims

Evidence records `e1`–`e21` and deltas `b1`–`b7` are `llm`-origin and still
**proposed** (pending owner adjudication); confidence below reflects tests the
main agent re-ran at the named commits, not adjudicated records.

| Claim | Confidence | Evidence |
|-------|------------|----------|
| `c22` — guest traffic never reaches the spark IRCd | high | test `test_session_routing.py::test_guest_connects_only_to_sandbox` @`2ae7914` · staging `e16` (no `:6667` peer) |
| `c12` — tier comes only from a verified Access JWT; everyone else is guest/anonymous | high | `tests/test_auth_tiers.py` (spoof/forgery negatives) @`2ae7914` · `e1` |
| `c45` — the entry flow does not reveal membership (body + timing) | high | `test_entry.py::test_sign_in_timing_probe_approved_vs_unknown`, `::test_sign_in_failures_identical` · `e4` |
| `c27` — guests verify their email with a single-use 15-minute token | high | `test_entry.py::test_token_single_use`, `::test_token_expires_after_15_minutes` · staging token flow · `e6` |
| `c5` / `c34` — no sandbox access without current-version consent | high | `test_session_routing.py -k consent` (5) · staging 10/10 consents `e18` |
| `c47` — cross-site POSTs are refused with no side effect | high | `tests/test_csrf_guest_cookie.py` (15) · `e9` |
| `c49` — guest mode is off by default and off restores today's behavior | high | `test_auth_tiers.py::test_guest_mode_off_*` · `test_session_routing.py::test_guest_mode_off_sandbox_routes_404_and_auth_unchanged` · `e10` |
| `c19` / `c20` — sbx-ask has no tools, thinking off, short capped answers, queue at 5 | high | `test_sandbox_agent.py::test_every_wire_request_is_toolless` · staging `e19`, `e20` |
| `c6` — the local model answers guests about culture | high | staging: 5/5 guests got a correct all-backends answer (`e20`); fixes `b1`–`b3` |
| `c32` — first answer < 30 s p95 with up to 5 active guests | medium | staging max 29.9 s at 5 concurrent (single run, n=5; serialized model — thin margin) |
| `c26` / `c59` — owner can ban (incl. by NSFW flag) and the session drops within 1 minute | high | `test_guest_admin.py::test_ban_by_flag_drops_session` · staging 26 s `e21` |
| `c54` — a verified guest can delete their inputs | medium | `test_guest_admin.py::test_deletion_flow_removes_everything_but_the_record` (`e13`); not exercised on staging |
| `c46` — approved passwords stored only as argon2id; password alone opens nothing | high | `test_guest_store.py::test_password_stored_as_argon2id_hash_not_plaintext` · `e7` |
| `c55` — token email identical for every address | high | `test_mail.py::test_o5_same_mail_for_approved_looking_and_unknown_address` · `e5` |
| `c28` — published guest data has PII removed | low | `tests/test_guest_export.py` (`e14`); scrubbing is heuristic — third-party names in prose, lowercase names, obfuscated emails are not caught |
| `c10` — the sandbox IRCd is loopback-only and unlinked | medium | `test_sandbox_server.py::test_sbx_and_spark_do_not_federate` · staging `e17`; capped by pending lapses `l1` (never mutation-checked), `l2` (weak log grep) |
| `c16` / `c58` — Terms and Privacy carry the required clauses and a published version | medium | katvan `188d1b4` build output; wording pending owner review (r3) and `[contact address]` placeholder |
| `c36`–`c40`, `c42` — Option A chat UI, tier-aware palette, ≤ 4-word strings, phone layout | high | `tests/test_chat_ui.py` (50) · Playwright 17 · Lighthouse 100 · `docs/screenshots/guest-mode/` |
| `c1` — visitors to chat.culture.dev can use guest mode | unverified | not live — public cutover deferred (`d5`) |
| `c15` — Access gates only `/login` | unverified | script exists (`edc8587`), not applied |
| `c33` — 0 guests on spark and 0 Access seats over 30 days | unverified | needs 30 days in production |

Lapse ledger evidence:

pending approval (not yet evidence): `l1`, `l2`, `l3`

## Remaining Work / Follow-up

- `t16` (b) / `t15` — public cutover after the PRs merge: install `culture-server-sbx` + sbx-ask units, upgrade the live irc-lens, set `IRC_LENS_GUEST_COOKIE_SECRET`, a mail provider key (via `grant`), optional Turnstile keys, sbx-ask's **own** gateway key (staging reused the shared lobes key), run `cf_access_login_path.py --apply`, then verify in a real browser that the login cookie covers `/` (c48) — owner + main agent.
- `t14` — owner reviews the Terms/Privacy wording (r3) and fills `[contact address]` before the cutover.
- `c32` capacity — the 5-guest margin is 0.1 s on one serialized model; add a second model (e.g. AWS gpt-6-luna) or lower `max_active`; naming a hosted processor in the Privacy Policy needs `h49` decided first.
- `c28` — the PII scrub is heuristic; a review step (or NER) before any publication.
- `c33` — measure guests-on-spark and Access seat count over the first 30 days after cutover.
- Lapses `l1`, `l2` (t1 isolation tests never mutation-checked; weak log check) and `l3` (evidence `--contract` holds pointers, not snapshots) — owner adjudication; `l1` can be closed by a mutation run that links the two servers.
- Owner adjudication of evidence `e1`–`e21` and deltas `b1`–`b7` (`devague evidence --confirm`, `devague delta --confirm`).
- Colleague drafted good RED tests but finished 0 of 3 drives (memory note updated); plan future splits with a native finisher.
