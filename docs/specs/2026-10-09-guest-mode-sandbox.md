# guest mode sandbox

> Visitors to culture.dev can sign in with any email, accept the Terms of Service and Privacy Policy, and chat in an isolated guest sandbox where a local model (lobes) explains culture's code and docs — while the owner's email still lands on the real mesh console and can hop into the guest sandbox

## Audience

- Visitors curious about culture (any email address) — plus the owner, who must be able to enter the guest sandbox too

## Before → After

- Before: chat.culture.dev admits only the owner's email; there is no way for an outsider to try culture or ask about it
- After: chat.culture.dev is one site for everyone: approved users sign in via SSO and land on the real console (the owner can switch into the sandbox); guests enter an email + nickname, accept ToS + Privacy Policy, and land in the sandbox chat

## Requirements

- Guests must accept a Terms of Service (behave, be respectful) and a Privacy Policy (owner may use data guests provide) before getting any sandbox access
  - honesty: No sandbox route (stream, send, join, upload) responds to a guest session lacking a consent record for the current ToS + Privacy version
- In the sandbox, an available local model (lobes) replies to guests and explains culture.dev — the code and the docs
  - honesty: Asked 'what is the all-backends rule?', the sandbox agent answers consistently with culture's CLAUDE.md within 30s
- Approved users (emails on the approved list — today the owner and one approved collaborator) sign in via SSO and get the real site (the live mesh console), unchanged; the list itself lives in the lens config and the Access policy, never in the spec
  - honesty: An approved email signing in via SSO lands on the real spark console with the same channels and agents as before guest mode shipped
- The owner can hop into the guest sandbox to see what guests see
  - honesty: An approved user can switch into the sandbox and back without re-authenticating, appearing under a distinct, recognizable sandbox nick
- irc-lens recognizes two tiers on one site: 'approved' (Cloudflare Access JWT, email on the approved list) and 'guest' (no JWT; self-declared email + nickname, identified by IP + email); the role decides which IRCd a session connects to
  - honesty: irc-lens enforces the tier itself: every real-mesh route (stream, send, join, residents, overview, media upload) returns 401/403 without a verified Access JWT for an approved email — because the edge now protects only a path, not the whole host
  - honesty: Tier is derived only from a verified JWT (header or `CF_Authorization` cookie); no client-supplied parameter, cookie, or header can select the real mesh
- A guest's ToS + Privacy acceptance is recorded persistently (email, ToS/Privacy version, timestamp) and enforced before any sandbox route; a version bump re-prompts
  - honesty: Consent records survive an irc-lens restart and record email, IP, ToS version, Privacy version, timestamp
- Guest nicks on the sandbox IRCd come from the guest's chosen nickname (prefixed <sbx>-, sanitized, unique — a taken nickname is refused at entry), never from the email; the owner's sandbox nick is distinct and recognizable
  - honesty: A requested nickname already in use on the sandbox IRCd is refused at entry; the guest's email never appears in their nick
- Cloudflare Access gates only chat.culture.dev/login (path-scoped app, approved-email policy); the front site sends a user there only after a correct password for an approved email; guests never reach Access, so they consume no seats
  - honesty: Guests are never redirected to Cloudflare login; only the SSO path triggers Access, and the Access app's policy is the approved-email list
- Terms of Service and Privacy Policy are published, versioned pages on culture.dev (katvan site), linked from the consent gate
  - honesty: culture.dev/terms and culture.dev/privacy load with a visible version and date, and the consent form links both
- Guest model traffic goes through the lobes gateway with thinking disabled and a short-answer instruction: cortex-spark2 today, with more models to be added (e.g. AWS gpt-6-luna) to spread load; its own gateway key, single-flight per model, capped `max_tokens`, per-guest (IP + email) rate limits, at most ~5 active guests with a visible queue
  - honesty: With 5 guests asking at once, guest requests queue single-flight with `max_tokens` <= 700, and the owner's agents still get gateway responses
- One irc-lens site routes per role: approved → real spark IRCd, guest → sandbox IRCd; approved users get a toggle to enter the sandbox under their sandbox nick
  - honesty: Backend selection happens in one place (routes.`_resolve_session`) keyed on the verified tier; an approved user's sandbox toggle never lets a guest-tier session reach the real IRCd
- Guest conversations stay in the sandbox IRCd's own data-dir, separate from the mesh, retained as long as required; the owner may use them to improve models
  - honesty: Guest transcripts are written only under the sandbox data-dir; the spark data-dir holds no guest content
- The owner can ban a guest by email and/or IP in the guest store, blocking re-entry and dropping their active sandbox session, without touching the mesh or Cloudflare
  - honesty: Banning an email or IP blocks re-entry at the form and drops any active sandbox session for it within 1 minute
- Guest entry is the c44 card: email, then 'Guest mode' under the password field, then nickname + Terms/Privacy acceptance, then a one-time token emailed to that address must be entered before sandbox access; no SSO; a guest is identified by their verified email (+ IP for rate limits and bans)
  - honesty: The entry form validates email syntax and nickname rules and stores IP + email as the guest's identity
  - honesty: A guest who cannot read the address they typed never reaches the sandbox; the token is single-use, expires within 15 minutes, and is rate-limited per email and per IP
- Published guest data has user PII removed (email, IP, and anything identifying them)
  - honesty: A publish/export step strips email, IP, and the nick-to-person mapping; a fixture seeded with PII produces output containing none of it
  - honesty: PII stripping covers free text guests type (emails, phone numbers, names they volunteer), not only the stored email/IP metadata
- Because the guest surface is public (no Access), it carries its own abuse controls: per-IP rate limits on entry and messages, a cap on concurrent guest sessions, and bot protection on the entry form
  - honesty: One IP exceeding the entry or message rate limit gets HTTP 429; the entry form carries bot protection (e.g. Cloudflare Turnstile, which uses no Access seats)
- General uplift of the site (irc-lens on chat.culture.dev): both experience and visuals — commands, clarity, etc.
  - honesty: Every item in the uplift (landing, command discovery, clarity, visual pass) is shown to the owner as a before/after screenshot set at desktop and 375px widths before it ships
- A guest-first landing on chat.culture.dev (Option A): heading 'Ask culture.', a real sandbox transcript as the hero, and the c44 entry card (email → Continue → password with 'Sign in' / 'Guest mode' → SSO or nickname + consent) — no explanatory prose
  - honesty: A first-time visitor, given no instructions, reaches a sandbox answer using only what the landing page says (checked in the scripted guest run behind c32)
- Commands are discoverable without memorizing them: a visible help control and inline slash-command suggestions as you type, each with a one-line description; the list is tier-aware (guests see only what works in the sandbox)
  - honesty: Typing '/' in the input lists available commands with descriptions; a guest never sees a command that would fail in the sandbox tier
- Clarity without prose: the header shows nick, a tier badge (Guest / Real mesh / Sandbox preview) and the active room instead of nick@host:port; the approved user's sandbox view carries an amber top rule plus 'Guest view' and 'Back to mesh'; empty states are a single action
  - honesty: The header never shows a raw host:port; tier and active channel are always visible; each empty state has a next-step hint
- Visual pass: keep culture.dev's dark-terminal palette, but refine typography, spacing and hierarchy, add visible focus states, and make the layout usable on a phone (375px wide), not just desktop
  - honesty: Lighthouse accessibility score >= 90 on the landing and chat views; no horizontal scroll at 375px; all interactive elements show a visible focus ring
- The site uses little to no prose: one-word headings, short labels, 1-3 word command descriptions; the real conversation is the explanation
  - honesty: Every visible string on the landing and chat views is a heading, label, nick, command or message — no explanatory sentences; the longest UI string outside chat messages is <= 4 words (Terms/Privacy consent line excepted)
- The entry flow does not reveal membership: unknown emails, approved emails, and wrong passwords get the same window, the same single error ('Email or password is wrong'), and indistinguishable response timing; password attempts are rate-limited per IP and per email
  - honesty: A scripted probe of 1 approved and 1 unknown email (and a wrong password) sees byte-identical windows and errors, with median response times within 50 ms of each other
  - honesty: After 5 failed password attempts in 15 minutes from one IP or for one email, further attempts get the same generic error and a delay, not a different message
- Approved users have a site password stored only as a slow salted hash (e.g. argon2id) in the front site's store; the password is a pre-check before SSO — Cloudflare Access on /login remains the real gate for the real mesh
  - honesty: The store holds no plaintext or reversible passwords; a correct password without a valid Access login still cannot open any real-mesh route (h10)
- Guest sessions are carried by a server-issued signed cookie (HttpOnly, Secure, SameSite=Strict, short expiry); every state-changing route (POST /input, uploads, consent) rejects cross-site requests via SameSite plus an Origin/CSRF-token check — for guests and approved users alike
  - honesty: A cross-origin form POST to /input carrying a valid guest or approved cookie is rejected (403) and sends nothing to IRC
- Guest mode ships behind a config switch (default off); switching it off restores exactly today's behavior — approved-only access, guest routes 404 — without a redeploy of the approved path, and doubles as the kill switch during abuse
  - honesty: With the switch off, the existing auth tests (tests/`test_auth_middleware.py`, including the non-allowlisted 403) pass unchanged; flipping it on and off needs only a config reload/restart
- The guest surface is observable: counts of entries, active guest sessions, 429s, failed sign-ins, and model errors are logged/exported, and when the sandbox agent or spark2 is down guests see a one-line 'agent offline' state instead of silence
  - honesty: Stopping the sandbox agent shows 'agent offline' to an open guest session within 60 s; the counters are visible to the owner without reading raw logs
- One source of truth for the current Terms/Privacy version: the katvan pages publish it, and the lens consent gate reads the same value — a version bump re-prompts guests only after the new pages are live
  - honesty: Bumping the version without the new pages live is refused (or ignored) by the lens; the version the consent record stores always matches a published page
- A verified guest can request deletion of the data they input: after re-verifying their email with a fresh token, their messages, uploads, and consent-linked profile are removed from the sandbox store within a stated period; the consent record of the request itself is kept as proof
  - honesty: After a deletion request completes, a search of the sandbox data-dir and guest store for that guest's email or nick finds none of their messages or uploads
- Token emails go out for every address that chooses Guest mode, with identical wording and timing whether or not the address is approved, so the token step leaks no membership (c45)
  - honesty: Choosing Guest mode with an approved email and with an unknown email produces the same screen and the same email template

## Honesty conditions

- A private browser window with no Cloudflare session can open chat.culture.dev, submit email + nickname + consent, and get an answer from the sandbox agent
- The guest flow needs nothing but a browser and an email address — no Cloudflare or IdP account, no email round-trip
- Today an unlisted email is blocked: the Access policy chat.culture.dev-allow lists only approved emails and irc-lens 403s anyone else (auth.py:234-239)
- The same hostname serves both tiers: a request with no Access JWT renders guest entry; a request with a valid JWT for an approved email renders the real console
- The sandbox IRCd listens on 127.0.0.1 only and has no links configured: ss -ltn shows only the loopback bind; its log shows no S2S handshake
- Guests on a shared IRCd could /join any channel — agentirc has no join ACL (client.py:449-459) — so isolation must come from the separate server, not channel modes
- The sandbox agent's model requests carry no tools (or only read-only tools rooted in the sandbox knowledge directory); asked to run a command, it declines
- No guest-tier session ever opens a connection to :6667; WHO on the spark IRCd lists no sandbox nicks
- Every non-culture change lands as a hand-off issue/brief in its repo (irc-lens, katvan, cultureflare), not as code written from culture
- Measured by a scripted guest run: timestamps from form submit to first agent line, and gateway latency over 5 parallel guests
- Verified from spark IRCd connection logs (no sandbox-prefixed nicks) and the Access seat count staying at the approved-user count
- Verified by a consent-store query joined against sandbox session starts: no session without a matching record
- Entering any email always shows the password window with both buttons; 'Guest mode' works for every email, including approved ones (the owner's sandbox hop-in, c8)
- Verified once in a real browser before build: after /login, a request to / carries `CF_Authorization` and irc-lens resolves the approved tier; if the cookie is path-scoped, /login must instead hand off via a lens-issued session

## Success signals

- A first-time guest goes from landing on chat.culture.dev to a grounded answer in < 2 minutes; a guest's first answer arrives in < 30 s p95 with up to 5 active guests (more wait in a visible queue showing their position)
- 0 guest sessions ever reach the real spark IRCd (:6667) and 0 Cloudflare Access seats are consumed by guests over the first 30 days
- 100% of guest sessions have a consent record for the current ToS + Privacy version before their first message is accepted

## Scope / boundaries

- Guests never connect to the real spark IRCd: the sandbox is a separate, unlinked culture IRCd bound to 127.0.0.1 — agentirc has no join ACL, ban/invite/key modes, opers, or flood control, so confinement cannot be done inside a shared IRCd
- The sandbox agent has no bash/shell, write, or network tools; it may use read-only tools, but only over knowledge held inside the sandbox (a curated culture docs/code copy), never the host filesystem or the real mesh
- The real console stays reachable only by approved users through SSO; guest traffic never reaches culture-server-spark (:6667) or the real mesh
- Culture-side work is the sandbox provisioning (sandbox IRCd, sandbox agent, units) + docs; irc-lens (tiers, entry card, password + token login, consent gate, routing, token emails), cultureflare (path-scoped Access app, if automated), and katvan (ToS/Privacy pages) changes land as direct PRs to those repos

## Non-goals

- Answer quality is v1 — the guest model's knowledge and behavior will improve later; v1 does not need to be excellent
- Claude-drafted ToS/Privacy text is not legal advice; the owner reviews and owns the final wording before it goes live
- No frontend-framework rewrite: the uplift stays within irc-lens's htmx + SSE + static-JS stack and its script-src 'self' CSP

## Assumptions

- The sandbox IRCd is a second 'culture server start --name <sbx> --port <p> --webhook-port <w> --data-dir <d> --host 127.0.0.1' with no --link/--mesh-config, so it is federation-isolated by default
- The sandbox answerer is a new, dedicated agent on the sandbox IRCd (not an existing resident), seeded from spark-ask (~/.culture/ask-agent/`ask_agent.py`): tool-less, cortex-spark2 via the lobes gateway, thinking off
- v1 grounds answers by giving the model a curated culture docs bundle (README, docs/, CLAUDE.md) in context or via its read-only checkout — retrieval/RAG is a later improvement
- After SSO on the path-scoped /login Access app, the `CF_Authorization` cookie is set for the whole chat.culture.dev host (Path=/) and carries a JWT whose AUD irc-lens is configured to accept — so approved users are recognized on public paths

## Scope exploration

- `s1` — `agentirc IRCd ACL (installed agentirc/client.py)`: JOIN only checks archived rooms (client.py:449-459); channel modes are just +R/+S/+o/+v; no +i/+k/+m/+b, no opers, no rate limiting beyond bot `fires_event` — a guest on the real IRCd could /join any channel
  - seeds: `c10`
- `s2` — `culture_core/cli/server.py`: start takes --name/--port/--webhook-port/--data-dir/--link/--mesh-config (server.py:109-140); links default to \[\] (server.py:672,697) and pidfiles key on server-<name>, so a second named server coexists isolated; default port/webhook/data-dir collide and must be overridden
  - seeds: `c11`
- `s3` — `irc-lens src/irc_lens/web/auth.py + identity.py`: `_authorize_principal` 403s any email not in auth.`allowed_emails` (auth.py:234-239, verified); Identity is a 3-field NamedTuple with no role (identity.py:12-15); tests/`test_auth_middleware.py`:122 asserts the 403 and would flip
  - seeds: `c12`
- `s4` — `irc-lens state/consent (sessions.py, routes.py, app.py)`: no consent, terms, cookie-setting or DB exists — state is in-memory only (sessions.py:30 registry); gate must precede `get_index` (routes.py:249) with /tos,/accept exempt in auth.py:313-318; CSP is script-src 'self' (app.py:36-40) so the consent page needs no inline JS
  - seeds: `c13`
- `s5` — `irc-lens identity.py derive_nick + agentirc nick rules`: `derive_nick` = `<server>-<email local part>` (identity.py:18-37) so `a@x.com` and `a@y.com` collide (433 at agentirc client.py:380); IRCd enforces the `<servername>-` prefix (client.py:363, verified)
  - seeds: `c14`
- `s6` — `cultureflare/_remote_login/_access_policy.py + _access_app.py`: `build_include` only emits email/`email_domain` includes and refuses empty lists (`_access_policy.py`:9-26, verified) — no 'everyone' include; `ensure_allow_policy` never updates an existing policy (39-62); `find_app` keys one Access app per hostname, no path apps (`_access_app.py`:8-13)
  - seeds: `c15`
- `s7` — `katvan/site (culture.dev apex, Jekyll on Cloudflare Pages)`: culture.dev is built from agentculture/katvan (culture CHANGELOG.md:367; katvan/site/`_worker.js`:1-10); git grep for 'privacy policy|terms of service|code of conduct' found nothing in katvan, culture, cultureflare, culture-guide — pages must be written from scratch
  - seeds: `c16`
- `s8` — `ToS/Privacy authorship`: no existing legal text anywhere in the workspace (same grep as katvan entry); drafts would be LLM-proposed and need owner sign-off
  - seeds: `c17`
- `s9` — `docs/reference/harnesses/colleague.md + culture.yaml`: colleague needs only backend: colleague, engine: vllm-openai, `base_url` (colleague.md:~50-70); culture/culture.yaml already does this; unknown keys pass via AgentConfig.extras (`culture_core`/config.py:115,285-294); one bounded engine.work turn per message, no git/PR path
  - seeds: `c18`
- `s10` — `colleague tools.py / engines (installed colleague package)`: colleague supports tool allowlists + a read-only role (tools.py:572-574,702-712) and a no-tools completion (engines/`vllm_openai.py`:298-303), but no culture.yaml key was found that selects them for a resident — must be verified in cultureagent clients/colleague/config.py
  - seeds: `c19`
- `s11` — `lobes deployment (~/.lobes, lobes-cli, nvidia-smi)`: gateway localhost:8001 requires a Bearer key; compose caps --max-num-seqs=2 ('4 OOMs', ~/.lobes/docker-compose.yml:137); one GB10 with ~86GB held by a vLLM EngineCore; prior memory colleague-fanout-serialize-gpu: concurrent colleague drives caused 600s turns and model eviction
  - seeds: `c20`
- `s12` — `grounding options (eidetic, knowledgebase-cli, model context)`: eidetic has public-scope recall usable for RAG but its embedder lane isn't local now; knowledgebase-cli is AWS Bedrock-only; Qwen3.8-27B advertises 262k ctx and nemotron 393k, enough for a docs bundle
  - seeds: `c21`
- `s13` — `chat.culture.dev live deployment (~/.config/irc-lens/config.yaml, memory reference_chat_agentculture_console)`: lens config header names chat.culture.dev and media.`public_base_url` <https://chat.culture.dev> (verified); tunnel cloudflared-chat -> 127.0.0.1:8765; owner-only `allowed_emails` + one service token
  - seeds: `c22`
- `s14` — `culture_core/cli/console.py + docs/reference/cli/console.md`: console builds an irc-lens serve argv; pidfiles key per web port console-<port> (console.py:57-61); conflict check keys on (`server_name`,nick,host,`irc_port`) (console.py:64,201-208); docs name side-by-side --web-port runs as supported
  - seeds: `c23`
- `s15` — `sandbox data handling (culture server --data-dir, irc-lens MediaStore)`: server data-dir defaults to ~/.culture/data (server.py) and must be overridden for the sandbox; irc-lens media uploads go to media.dir — guest uploads would need their own dir or be disabled
  - seeds: `c24`
- `s16` — `repo ownership across the change`: role/consent code lives in irc-lens src/`irc_lens`/web; Access policy code in cultureflare; site pages in katvan; resident tool config in cultureagent — per the cross-repo hand-off convention, culture implements only its side
  - seeds: `c25`
- `s17` — `Cloudflare Access plan + IdP (agentculture team, unverified)`: cultureflare has no reference to OTP or an 'everyone' include; Access counts each authenticated user as a seat; plan tier and OTP enablement not checked (no CF API reads made during scope)
  - seeds: `c15`
- `s18` — `moderation + privacy terms`: no ban/kick ACL in agentirc (client.py JOIN path); no existing privacy policy to inherit retention terms from — retention/usage terms are an owner decision
  - seeds: `c26`, `q3` (question, resolved)
- `s19` — `culture_core resident presence (/residents.json, PRESENCE)`: residents view queries a single IRCd (`renderer_web.py`:199,238; docs/resident-presence.md) — pointing a copy at the sandbox IRCd would show guests whether the culture-explainer agent is up; optional for v1, not required
- `s20` — `irc-lens auth middleware exemptions (auth.py:313-318)`: today every non-static path requires a CF JWT; a public guest area means the middleware must allow JWT-less requests on guest routes and treat a present, valid JWT as the approved tier — the 401 on missing JWT (tests/`test_auth_middleware.py`) changes meaning
  - seeds: `c12`, `c30`
- `s21` — `spark-ask (~/.culture/ask-agent/ask_agent.py, culture-ask-spark.service)`: a stdlib IRC client sending chat completions with no tools field answered a culture question correctly and refused 'run ls /' over DM on 2026-10-09; no culture backend offers a no-tools knob (cultureagent claude `agent_runner.py`:144-151 bypassPermissions; colleague harness engine.work offers full tools)
  - seeds: `c18`, `c19`
- `s22` — `irc-lens UI (templates/*.j2, static/lens.css, lens.js, mesh.js)`: help is a static <dl> reached only via /help (`_info`.html.j2:5-24); header shows raw nick@host:port (index.html.j2:25); 3-column lens-grid, monospace 14px, palette mirrors culture.dev dark-terminal (lens.css:1-14); input placeholder 'Type a message or /command…'; htmx + sse.js vendored, CSP script-src 'self' (app.py:36-40)
  - seeds: `c36`, `c37`, `c38`, `c39`, `c40`, `c41`
- `s23` — `uplift design canvas (https://claude.ai/artifact/4bDJqM8gmU38LqDDnhAH9S)`: three directions mocked (A terminal sharpened, B mesh-as-room, C ask-first) as landing + tier-switchable chat + phone artboards; user chose A and asked for little to no prose; A's artboards are the reference for c37-c40
  - seeds: `c36`, `c37`, `c38`, `c39`, `c40`
- `s24` — `challenge pass / cheap-probe lens: lobes gateway cortex-spark2 under 5 concurrent requests`: requests serialize on spark2; per-answer ~7 s at this prompt size; c32's 30 s p95 at 5 concurrent is not met (36 s) — routed to a blocking hard question on c32
  - seeds: `c32`
- `s25` — `challenge pass / security lens: irc-lens web/auth.py + routes.py (cookie handling)`: today irc-lens sets no cookies and relies on Cloudflare Access for every path (auth.py:142-150 reads `CF_Authorization`, never sets one); a public site with cookie-identified guests, and approved users identified by the `CF_Authorization` cookie on public paths, opens a CSRF surface on POST /input that Access previously fronted
  - seeds: `c47`
- `s26` — `challenge pass / security lens: Cloudflare Access path-scoped app (q5 decision) + irc-lens aud pinning (auth.py:153-179)`: not probed — creating the path app would mutate live Access config; Access injects Cf-Access-Jwt-Assertion only on protected paths, so recognition on / rests entirely on the cookie's scope and on the new app's AUD being in the lens config
  - seeds: `c48`
- `s27` — `challenge pass / adjacent-systems lens: real mesh agents + approved-user tier (c7, c31)`: the frame treats 'approved' as one tier, but agentirc has no join/ban ACL (client.py:449-459) and tool-capable agents answer any mention; nothing distinguishes the owner from an approved collaborator — routed to a user question
- `s28` — `challenge pass / operations lens: deployment units + rollback path`: the frame specifies no rollback, kill switch, or monitoring for a newly public surface; the existing deployment is hand-written systemd units (memory `reference_chat_agentculture_console`) — seeded the switch and observability requirements
  - seeds: `c49`, `c50`
- `s29` — `challenge pass / hidden-dependency lens: katvan /terms,/privacy (c16) + lens consent store (c13)`: ToS text deploys from katvan (Cloudflare Pages) and consent is enforced in irc-lens on spark — separately deployed; nothing in the frame keeps their version numbers in step
  - seeds: `c51`
- `s30` — `challenge pass / overlooked-actors lens: guest data lifecycle (c24, c28)`: retention is 'as long as required' and publication strips PII, but the frame has no path for a guest asking to be forgotten — routed to a user question
- `s31` — `challenge pass / unexamined: legal adequacy of ToS/Privacy`: not examined — c17 already records drafts are not legal advice; jurisdiction-specific obligations (e.g. GDPR for EU guests) were not researched
- `s32` — `challenge pass / concurrency lens: guest consent store + session registry (sessions.py:30)`: clean for v1 scale — single irc-lens process, in-memory registry plus one consent store; residual risk only if irc-lens is ever run as multiple workers
- `s33` — `challenge pass / dependency lens: outbound email for guest tokens`: git grep for smtplib/sendmail/resend/mailgun/sendgrid/postmark/`send_email` across irc-lens, culture, cultureflare, katvan, grant found no mail-sending code (only false hits: a vendored htmx bundle and one plan doc mention); the token decision adds a new outbound-email dependency — parked blocking (v8) until a sender is chosen
  - seeds: `c54`, `c55`

## Decisions

- The SSO path is for approved users only; guests and approved users share one site, and Cloudflare Access seats are spent only on approved users
- Approved (SSO) users today: the owner and one approved collaborator; the real site carries a tool-less Q&A agent (spark-ask) that only answers questions
- Access protects only chat.culture.dev/login (path-scoped app, set up once via API); the site shows a visible Login button that takes approved users through SSO and back
- Visual direction is Option A, terminal sharpened (canvas <https://claude.ai/artifact/4bDJqM8gmU38LqDDnhAH9S>): culture.dev dark-terminal palette, IBM Plex Mono + Sans, amber marks the sandbox, real transcript as the landing hero, inline slash-command palette with a tier-aware list
- Entry flow: (1) everyone enters an email and continues; (2) every email gets the identical next window — a password field with two buttons under it, 'Sign in' and 'Guest mode'. A correct password for an approved email leads to SSO; 'Guest mode' leads to nickname + Terms/Privacy consent. The window never differs by email, so nobody can probe which emails are in the system
- Approved users (owner and approved collaborators) get full access to the real mesh, including addressing tool-capable agents — approval is the trust boundary
- A guest's email is verified with an emailed token before sandbox access, so nobody can pretend to be someone else; the verified identity lets a guest request deletion of the data they input
- The login owner (irc-lens, which serves the chat.culture.dev entry card) owns guest token emails; work may land as direct PRs to any repo needed (irc-lens, katvan, cultureflare, cultureagent, culture) rather than hand-off briefs
- Guest answers are short with no thinking; more models will be added soon, and AWS gpt-6-luna may be used alongside cortex-spark2

## Hard questions

- Which culture.yaml / cultureagent knob confines a colleague resident to read-only (or no) tools — and if none exists, is that a cultureagent hand-off that blocks v1? (resolved: A dedicated, unique sandbox agent (not an existing resident). No bash. Read-only tools are allowed but scoped to sandbox-held knowledge only)
- One hostname or two? (a) guest.culture.dev → sandbox console :8766 with an any-email OTP Access app, owner reaches it by visiting it too; or (b) a single chat.culture.dev irc-lens that routes owner→real IRCd and guest→sandbox IRCd per role (routes.`_resolve_session`, routes.py:76-80), with an owner toggle. Access apps are per-hostname and can't route by email. (resolved: (b) one site for all: chat.culture.dev serves everyone; approved users SSO in, guests enter email+nickname; role routes to real mesh vs sandbox)
- How does one hostname split public and SSO areas? Cloudflare Access supports path-scoped self-hosted apps (e.g. protect chat.culture.dev/mesh/\*, leave / public), but cultureflare only creates hostname-wide apps (`_access_app.py`:8-13) — extend cultureflare, or configure the path app once by hand/API? (resolved: Path-scoped Cloudflare Access app on chat.culture.dev/login, configured once via API; the rest of the host is public; a visible Login button on the site sends approved users to /login (SSO) and back; the hostname-wide `CF_Authorization` cookie then marks them approved; irc-lens enforces the tier in-app; teaching cultureflare path-scoped apps is a follow-up)
- Privacy Policy terms: how long are guest transcripts retained, may they be used to improve the guest model / docs, and may they be published? (resolved: Retain as long as required. Owner may use guest data to improve models and may publish it with user PII removed)
- risk: Guest email is self-declared and unverified, so anyone can type someone else's email; ToS acceptance and bans keyed on it are only as strong as IP + a claimed email (resolved: Resolved by email verification: an emailed token proves the guest controls the address)
- Probe 2026-10-09: 5 concurrent spark-ask-shaped requests (4572 prompt tokens, 73-318 completion) to cortex-spark2 completed at 10.6/19.0/26.8/33.2/35.8 s — effectively serialized, p95 ~36 s, so '< 30s p95 with 5 concurrent guests' fails at today's capacity. Lower the target (e.g. < 45 s), cap concurrent active guests (e.g. 3), or show queue position? (resolved: Option (c) approved: keep < 30 s for a guest's first answer, show queued guests their position, cap active guests at ~5; the sandbox agent is instructed to answer briefly with thinking off; more models (e.g. AWS gpt-6-luna) will be added to spread load)
- How are approved-user passwords set and reset — a CLI on spark (e.g. a lens admin verb), or a self-service flow? A self-service reset by email would itself leak membership unless it also responds identically for every email

## Open parks

- [unknown_nonblocking] Whether a second culture server/config home coexists cleanly with ~/.culture/server.yaml and its agents manifest (pidfile.py / config.py HOME handling unverified)
- [unknown_nonblocking] Prompt-injection resilience of the sandbox agent beyond 'no tools' (e.g. leaking its system prompt or being steered into abusive replies) was not probed
- [unknown_nonblocking] Which mail provider irc-lens uses for token emails (transactional API such as Resend/Postmark, SMTP relay, or Cloudflare Email Workers) — an implementation choice, credentials via grant
- [follow_up] Whether a deletion request also reaches already-published, PII-stripped material — stripped data is no longer linkable to the guest, so likely out of reach; confirm when the publication pipeline exists

## Resolved vagueness

- [unknown_blocking] Which model endpoint serves guests: the lobes primary (model-gear-vllm-primary) is currently exited/unhealthy, the gateway's cortex is proxied from peer spark2, and nemotron-120B on :8010 is up but keyless and memory-heavy — resolved: Guests are served by the spark2 cortex via the lobes gateway for now, with thinking disabled
- [unknown_blocking] Cloudflare Zero Trust plan/seat cap: on the free plan every authenticated guest consumes a seat (50 max) — unverified which plan the agentculture team is on — resolved: Seats are spent only on approved users: a public front site sits before Cloudflare Access; guests never authenticate through Access
- [unknown_nonblocking] Whether the One-time PIN identity provider is already enabled on the agentculture Access team — needs a dashboard/API read — resolved: Moot: guests no longer authenticate through Cloudflare Access (c29), so the One-time PIN IdP is not needed
- [unknown_nonblocking] Abuse handling beyond ToS: banning a misbehaving guest email, and who moderates — agentirc has no ban modes, so it would live in the consent store / Access policy — resolved: Bans live in the guest store by email and/or IP, owner-moderated (c26)
- [follow_up] Visual direction details (type scale, layout of the guest landing, sandbox vs real-mesh visual distinction) — settle with mockups during planning via a design pass — resolved: Option A chosen on the uplift canvas; remaining detail settles during implementation against those artboards
- [unknown_blocking] Which outbound email sender the front site uses for tokens (SMTP relay, a transactional service, or Cloudflare Email) — no mail-sending path was found in the explored repos; needed before the token step can ship — resolved: Token emails are sent by the component that owns the login: irc-lens (chat.culture.dev entry card), or the culture.dev site if login moves there; the concrete mail provider is picked during implementation
