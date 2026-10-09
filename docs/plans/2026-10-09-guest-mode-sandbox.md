# Build Plan — guest mode sandbox

slug: `guest-mode-sandbox` · status: `exported` · from frame: `guest-mode-sandbox`

> Visitors to culture.dev can sign in with any email, accept the Terms of Service and Privacy Policy, and chat in an isolated guest sandbox where a local model (lobes) explains culture's code and docs — while the owner's email still lands on the real mesh console and can hop into the guest sandbox

## Tasks

### t1 — \[culture\] Sandbox IRCd provisioning: an isolated, unlinked 'sbx' server on 127.0.0.1

- instruction: Reuse culture server start/install flags (`culture_core`/cli/server.py:109-140). No engine changes expected; if install lacks a flag, add it with a test. Data-dir default ~/.culture/data-sbx.
- covers: c10, h9, h25, c24, h20
- acceptance:
  - culture server install --name sbx (or a documented unit) yields a server bound to 127.0.0.1 only, with its own --port, --webhook-port and --data-dir and no --link/--mesh-config; ss -ltn shows only the loopback bind
  - a test starts sbx next to a spark server on random ports and proves no S2S handshake and no shared channel between them
  - docs/guest-sandbox.md documents the sandbox server, its data-dir, and that guest transcripts live only there

### t2 — \[culture\] Dedicated tool-less sandbox agent (sbx-ask) generalized from spark-ask

- instruction: Start from ~/.culture/ask-agent/`ask_agent.py` (proven 2026-10-09). Ship it in culture as a small module + 'culture sandbox agent' entry (or a documented script) with tests against a real agentirc server on a random port; no mocks for the server.
- covers: c6, h6, c19, h16, c20, h17, c59, h51
- acceptance:
  - every request to the gateway carries no tools field, `chat_template_kwargs`.`enable_thinking`=false, a short-answer instruction, and `max_tokens` <= 700 (unit test on the request builder)
  - single-flight queue: at most ~5 active guests are served; others receive a one-line queue-position message; asked to run a command it declines
  - answers are grounded in a read-only curated docs bundle (README, CLAUDE.md, docs/) copied into the sandbox knowledge dir; the agent reads nothing else
  - model list is configurable (cortex-spark2 today; more such as AWS gpt-6-luna addable by config) and uses its own gateway key
  - sbx-ask declines NSFW requests and flags them to the owner (flag records guest nick + message excerpt in an owner-visible log/channel)

### t3 — \[irc-lens\] Config: `guest_mode` switch and guest/sandbox settings

- instruction: Touch src/`irc_lens`/config.py and its tests only. Key names are this task's contract for later tasks; document them in docs/.
- covers: c49, h42
- acceptance:
  - config.py accepts `guest_mode`.enabled (default false), sandbox server name/host/port, guest store path, legal version URL, mail provider settings, and rate limits; unknown keys still rejected
  - with `guest_mode`.enabled false, tests/`test_auth_middleware.py` passes unchanged, including the non-allowlisted 403

### t4 — \[irc-lens\] Tier resolution: approved vs guest on one host

- instruction: Files: src/`irc_lens`/web/auth.py, web/identity.py + tests. Keep JWT verification core untouched; reuse tests/`_jwks_server.py`.
- depends on: t3
- covers: c12, h10, h11, c7, h7, c3, h3
- acceptance:
  - Identity gains a tier (approved | guest | anonymous); approved only from a verified Access JWT (header or `CF_Authorization` cookie) for an approved email
  - no client-supplied parameter, header, or non-Access cookie can yield the approved tier (negative tests)
  - `guest_mode` off: missing JWT still 401 and non-allowlisted 403 exactly as today

### t5 — \[irc-lens\] Guest store: guests, consent, tokens, bans, password hashes

- instruction: New file src/`irc_lens`/`guest_store.py` + tests only; expose a small API later tasks call (`record_consent`, `issue_token`, `verify_token`, ban, `delete_guest_inputs`, `set_password`, `check_password`).
- depends on: t3
- covers: c13, h12, c46, h38
- acceptance:
  - new `guest_store.py` (SQLite) persists guests (verified email, nick, IP), consent records (email, IP, ToS + Privacy version, timestamp), single-use tokens, bans, and approved-user password hashes; survives restart (test)
  - passwords stored only as argon2id hashes; no plaintext or reversible form anywhere (test inspects the DB)

### t6 — \[irc-lens\] Mail sender for guest tokens

- instruction: New file src/`irc_lens`/mail.py + tests. Provider is an implementation choice (v10): start with one transactional-API adapter; credentials come from env injected via grant.
- depends on: t3
- covers: c55, h48
- acceptance:
  - mail.py sends one fixed token template through a configurable provider adapter; identical subject/body shape for every address
  - a test adapter records sends; no network in tests

### t7 — \[irc-lens\] CSRF-safe guest session cookie

- instruction: New src/`irc_lens`/web/csrf.py + middleware registration in web/app.py + tests.
- depends on: t3
- covers: c47, h40
- acceptance:
  - guest sessions use a signed cookie (HttpOnly, Secure, SameSite=Strict, short expiry)
  - a cross-origin POST to /input, uploads, consent, or deletion with a valid guest or approved cookie returns 403 and sends nothing to IRC

### t8 — \[irc-lens\] Observability: guest counters and agent-offline signal

- instruction: New src/`irc_lens`/metrics.py + one owner-only route in web/routes.py + tests.
- depends on: t3
- covers: c50, h43
- acceptance:
  - counters for entries, active guest sessions, 429s, failed sign-ins, and model/agent errors exposed on an owner-only route
  - sandbox agent absence on the sandbox IRCd is detected within 60 s and exposed as a state the UI can render

### t9 — \[irc-lens\] Entry card: email, password window, Sign in or Guest mode, token verification

- instruction: New src/`irc_lens`/web/entry.py, templates/entry.html.j2, static/entry.css (+ route registration in web/app.py). Follow canvas <https://claude.ai/artifact/4bDJqM8gmU38LqDDnhAH9S> artboard 'A landing'. CSP stays script-src 'self'.
- depends on: t4, t5, t6, t7
- covers: c37, c45, h36, h37, c27, h22, h46, c14, h13, c2, h2, c30, h24
- acceptance:
  - POST email always returns the same password window; sign-in success (approved email + correct password) redirects to /login, any failure returns 'Email or password is wrong' with identical status/body and timing within 50 ms (probe test)
  - Guest mode -> nickname + Terms/Privacy consent -> token emailed -> token entry; tokens single-use, 15-min expiry; attempts rate-limited per email and IP with the same generic response (429 after limit)
  - guest nick is sbx-<nickname>, sanitized, unique on the sandbox IRCd, never derived from the email
  - entry form carries bot protection (e.g. Cloudflare Turnstile) and renders per the Option A canvas with no explanatory prose

### t10 — \[irc-lens\] Session routing: guest -> sandbox IRCd, approved -> real mesh, sandbox toggle, consent gate

- instruction: Files: src/`irc_lens`/web/sessions.py, web/routes.py (`_resolve_session` + sandbox gate), cli/`_commands`/serve.py + tests.
- depends on: t4, t5
- covers: c23, h19, c22, h18, c8, h8, c5, h5, c4, h4
- acceptance:
  - routes.`_resolve_session` picks the backend from the verified tier only; guest sessions connect only to the sandbox host:port (test with two real servers)
  - approved users can toggle into the sandbox under a distinct sbx- nick and back without re-auth
  - sandbox routes refuse/redirect a guest without a consent record for the current version

### t11 — \[irc-lens\] Owner admin CLI: passwords, bans, deletions

- instruction: New src/`irc_lens`/cli/`_commands`/guests.py (+ deletion web route in a new web/deletion.py) + tests. Password setting is CLI-only for v1 (q6); no self-service reset.
- depends on: t5, t9
- covers: c26, h21, c54, h47, h51
- acceptance:
  - irc-lens guests passwd <email> sets an approved user's password (argon2id); ban/unban <email|ip>; list
  - a banned email/IP is refused at entry and its active sandbox session drops within 1 minute (test)
  - a guest deletion request, after fresh token re-verification, removes their messages, uploads, and profile; a search for their email/nick finds nothing but the deletion record
  - a flagged guest can be blocked in one command (e.g. irc-lens guests ban --flag <id>), dropping their session within 1 minute

### t12 — \[irc-lens\] PII-stripped export of guest data

- instruction: New src/`irc_lens`/export.py + tests.
- depends on: t5
- covers: c28, h23, h45
- acceptance:
  - irc-lens guests export --redacted emits transcripts with no email, IP, or nick-to-person mapping
  - a fixture seeded with emails, phone numbers and names in free text produces output containing none of them

### t13 — \[irc-lens\] Chat UI uplift (Option A): tier badge, command palette, sandbox marker, phone layout

- instruction: Files: templates/index.html.j2, `_info`.html.j2, `_sidebar`.html.j2, static/lens.css, static/lens.js, session.py (help/command filtering). Match canvas artboards 'A chat' and 'A phone'. Use Playwright for screenshots.
- depends on: t8, t10
- covers: c36, h30, c38, h32, c39, h33, c40, h34, c42, h35
- acceptance:
  - typing / shows a tier-aware command list with 1-3 word descriptions; guests never see commands that fail in the sandbox
  - header shows nick, tier badge and active room, never nick@host:port; approved sandbox view has the amber rule, 'Guest view' and 'Back to mesh'; agent-offline state rendered
  - Lighthouse accessibility >= 90 on landing and chat; no horizontal scroll at 375 px; visible focus rings; no UI string over 4 words outside messages (consent line excepted)
  - before/after screenshots at desktop and 375 px attached to the PR for the owner

### t14 — \[katvan\] Terms of Service + Privacy Policy pages with a published version

- instruction: Claude drafts; the owner reviews final wording (c17: not legal advice). Jekyll pages under katvan/site; the lens-side version read lands in the entry task's consent step.
- covers: c16, h15, c51, h44, c58, h50
- acceptance:
  - culture.dev/terms and /privacy render with a visible version and date; /legal/version.json publishes the current version
  - Privacy names: data collected (email, IP, nick, messages), retention as long as required, model-improvement use, PII-stripped publication, deletion via verified email, and any hosted model processing guest data
  - the lens consent gate reads the version from /legal/version.json and only re-prompts once the new pages are live
  - the Terms and Privacy pages contain clauses: data-use agreement, deletion right, respectful conduct toward the owner, the system, the agents and other users, no NSFW content, and violation leads to an immediate block

### t15 — \[cloudflare\] Path-scoped Access app on chat.culture.dev/login + cookie-scope verification

- instruction: Adding the /login path app alongside the existing hostname-wide app is safe and should be done early to settle c48. Use grant run --inject `CF_TOK`/`CF_ACC`; never print tokens.
- covers: c15, h14
- acceptance:
  - an Access app for chat.culture.dev/login with the approved-email policy exists, created by a dry-run-by-default script (or cultureflare extension)
  - in a real browser after /login, a request to / carries `CF_Authorization` and irc-lens resolves the approved tier (verifies assumption c48); the new app's AUD is in the lens config
  - the hostname-wide app is removed only during rollout, after guest mode is live

### t16 — \[spark rollout\] Deploy, cut over Access, and validate end to end

- instruction: Order: units -> irc-lens upgrade (`guest_mode` off) -> verify unchanged -> flip `guest_mode` on -> swap Access apps -> E2E. Rollback = `guest_mode` off + restore hostname-wide app.
- depends on: t1, t2, t8, t9, t10, t11, t12, t13, t14, t15
- covers: c1, h1, c32, h27, c33, h28, c34, h29, h31, c25, h26
- acceptance:
  - sbx IRCd, sbx-ask agent and upgraded irc-lens run as enabled user units; `guest_mode` on; hostname-wide Access app replaced by the /login path app
  - scripted guest run: landing -> grounded answer < 2 min; first answer < 30 s p95 with 5 active guests (queue shown beyond)
  - no sandbox nick ever appears on the spark IRCd; Access seat count equals approved users; every guest session has a consent record before its first message
  - all non-culture changes landed as PRs in their repos

## Risks

- [follow_up] irc-lens tasks in the same wave each bump version/CHANGELOG — merge collisions are resolved by the main agent at merge, one bump per PR batch
- [unknown_nonblocking] If the `CF_Authorization` cookie turns out path-scoped to /login (assumption c48 fails), irc-lens must mint its own session at /login — t15 settles this before t4's design is relied on in rollout (task t15)
- [unknown_blocking] Final Terms/Privacy wording needs the owner's review before rollout (c17: drafts are not legal advice) (task t14)
- [follow_up] Guest capacity rests on one serialized model (cortex-spark2, ~7 s/answer); adding models such as AWS gpt-6-luna is a follow-up that also needs the Privacy Policy to name the hosted processor (h49 pending)
