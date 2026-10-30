# Build Plan — CULTURE.DEV on all public surfaces

slug: `culture-dev-on-all-public-surfaces` · status: `exported` · from frame: `culture-dev-on-all-public-surfaces`

> CULTURE.DEV is the visible product brand on every public surface — the CLI, the culture.dev site, the chat.culture.dev sign-in, the chat UI and its emails, and what guests see in chat — rendered consistently, so the CULTURE.DEV trademark filing has clean, consistent use to point to.

## Tasks

### t1 — irc-lens templates carry CULTURE.DEV (titles, hero, wordmark, sign-in header, meta, notice)

- instruction: Repo ../irc-lens. Touch only src/`irc_lens`/templates/\*.j2 and their tests. ™ only on the rendered prominent use (hero/header), never in <title>. Keep chat.culture.dev hostnames lowercase.
- covers: c2, h2, c3, h3
- acceptance:
  - templates/entry.html.j2: <title> contains CULTURE.DEV, hero reads 'Ask CULTURE.DEV.', sign-in step renders a CULTURE.DEV™ brand header
  - index.html.j2 title 'CULTURE.DEV — {nick}', wordmark 'CULTURE.DEV™', logo alt='CULTURE.DEV'; delete/setpw titles CULTURE.DEV; residents title 'Residents — CULTURE.DEV'
  - base templates carry og:title / og:description / meta description naming CULTURE.DEV, and the chat UI shows 'CULTURE.DEV is a trademark of Ori Nachum.'
  - tests assert each page title/hero (update `test_entry.py`:201,671 and `test_render_residents.py`:202; add title assertions for index/delete/setpw); no URL/hostname string changes case

### t2 — irc-lens phone layout keeps the CULTURE.DEV wordmark visible

- instruction: Repo ../irc-lens, file src/`irc_lens`/static/lens.css only (plus an e2e assertion if tests/`test_e2e_playwright.py` supports viewport). Keep header from overflowing: shrink font, drop identity first.
- covers: c30, h21
- acceptance:
  - static/lens.css @media (max-width: 760px) no longer hides .lens-wordmark (a compact form is fine); .lens-logo may stay hidden
  - a Playwright/browser check at 390px width finds visible text CULTURE.DEV in the chat header

### t3 — irc-lens emails sign off as The CULTURE.DEV team

- instruction: Repo ../irc-lens, src/`irc_lens`/mail.py + its tests only. Also update the documented example for `guest_mode`.mail.from (README/docs/config example) to 'CULTURE.DEV <no-reply@culture.dev>'.
- covers: c4, c34, h25
- acceptance:
  - every body in src/`irc_lens`/mail.py ends 'The CULTURE.DEV team'; subjects still use the lowercase hostname chat.culture.dev; no ™ anywhere in mail.py
  - tests/`test_mail_signin.py` and `test_delete_ux.py` updated and green

### t4 — irc-lens release: PyPI summary, TRADEMARKS.md, README notice, first-use CHANGELOG, version bump

- instruction: Repo ../irc-lens. Follow its CLAUDE.md PR flow; sign PR body per repo nick. TRADEMARKS.md text identical across culture/irc-lens/katvan/agentirc.
- depends on: t1, t2, t3
- covers: c32, c33, h24, c10
- acceptance:
  - pyproject.toml description names CULTURE.DEV (e.g. 'Web chat console for CULTURE.DEV, built on AgentIRC'); package/command name stays irc-lens
  - TRADEMARKS.md reserves CULTURE.DEV (owner Ori Nachum), states Apache-2.0 grants no trademark rights; README has the notice line
  - CHANGELOG entry states this is the first irc-lens release exposing CULTURE.DEV branding on chat.culture.dev; minor version bump; PR opened in agentculture/irc-lens

### t5 — irc-lens deploy to chat.culture.dev + live mail.from + live verification

- instruction: Ops step after merge+release. Restart culture-console after the venv upgrade (known gotcha). Do browser checks with Playwright before calling it done.
- depends on: t4
- covers: h4
- acceptance:
  - irc-lens release installed on spark and the console service restarted; ~/.config/irc-lens/config.yaml `guest_mode`.mail.from = 'CULTURE.DEV <no-reply@culture.dev>' (backup kept)
  - a real sign-in code email is received showing sender CULTURE.DEV and sign-off 'The CULTURE.DEV team'
  - real-browser click-through of entry, sign-in, chat (desktop + 390px), delete and setpw pages shows CULTURE.DEV; deploy date recorded for first-use

### t6 — Sandbox guest agents name CULTURE.DEV and ignore the system welcome bot

- instruction: culture repo, `culture_core`/sandbox/agent.py + tests/`test_sandbox`\*.py only. Find how system bots are named on the wire (`culture_core`/bots, agentirc bots/`virtual_client`) before choosing the filter.
- covers: c5, h5, c38, h20
- acceptance:
  - `culture_core`/sandbox/agent.py `system_prompt`() calls the product CULTURE.DEV and `DECLINE_COMMAND` reads '…questions about CULTURE.DEV…'; unit test pins both
  - `_should_answer` returns False for lines from the system welcome bot (identified by nick/bot cap, not text); unit test: a welcome-bot line in a sandbox room yields no reply while human lines still answer

### t7 — Welcome bot greets with CULTURE.DEV

- instruction: culture repo, bot.yaml + its tests only. Keep the peek-nick filter unchanged.
- covers: c6, h6
- acceptance:
  - `culture_core`/bots/system/welcome/bot.yaml template contains CULTURE.DEV (e.g. 'Welcome {event.nick} to {event.channel} on CULTURE.DEV'), no ™; any test pinning the template updated

### t8 — docs/ prose names the product CULTURE.DEV

- instruction: culture repo, docs/\*\* only (not README, not CHANGELOG, not docs/specs or docs/plans historical records). Prose 'Culture is…' → 'CULTURE.DEV is…'; leave historical specs/plans/deliveries untouched.
- covers: c36, h26
- acceptance:
  - grep of docs/ finds no 'Culture' used as the product name (AgentCulture, command 'culture', culture.yaml, URLs and code blocks excepted)
  - no link, command, path or code block changes; markdownlint-cli2 passes on touched files

### t9 — culture release: PyPI metadata, README, TRADEMARKS.md, first-use CHANGELOG, version bump

- instruction: culture repo. Run /version-bump minor, /run-tests, black/isort before commit, doc-test-alignment, then cicd flow. Include uv.lock if pyproject changes it.
- depends on: t6, t7, t8
- covers: c8, h8, c11, h11, c12, h12, c13, h13, h10
- acceptance:
  - pyproject.toml description names CULTURE.DEV; \[project.urls\] Homepage <https://culture.dev> plus Source <https://github.com/agentculture/culture>; scripts still culture + culture-core, dist name culture
  - README H1 'CULTURE.DEV™', README.md:3 'CULTURE.DEV is…', README notice 'CULTURE.DEV is a trademark of Ori Nachum.'; TRADEMARKS.md added
  - tests/`test_engine_identity.py`, `test_cli_branding.py`, `test_packaging_entrypoints.py` pass unchanged; diff contains no culture.dev/trace\* or culture.yaml rename; every changed culture.dev string outside prose is untouched
  - CHANGELOG names this as the first release exposing CULTURE.DEV in guest chat (sandbox agents, welcome bot); /version-bump minor; /run-tests green; PR opened

### t10 — agentirc greets connecting clients as CULTURE.DEV (welcome + MOTD) and releases

- instruction: Repo ../agentirc. Follow its CLAUDE.md. MOTD text fixed in code (CULTURE.DEV-opinionated), optionally overridable by config later — not required.
- covers: c7, h7
- acceptance:
  - agentirc/client.py `_send_welcome`: `RPL_WELCOME` and `RPL_YOURHOST` name CULTURE.DEV (no ™); `RPL_MYINFO` version token unchanged; culture.dev/\* message tags unchanged
  - MOTD numerics 375/372/376 sent on registration (and MOTD command) with a CULTURE.DEV line; `ERR_NOMOTD` path not regressed; tests cover welcome + MOTD text
  - protocol/docs updated, TRADEMARKS.md added, CHANGELOG first-use note, minor bump, PR merged and agentirc-cli released to PyPI

### t11 — culture pins the CULTURE.DEV-greeting agentirc-cli release

- instruction: culture repo, after t9 merged. pyproject.toml + uv.lock + CHANGELOG only.
- depends on: t10, t9
- covers: h7
- acceptance:
  - pyproject.toml agentirc-cli floor raised to the t10 release and uv.lock updated; full /run-tests green; separate culture PR with patch bump

### t12 — culture.dev site chrome: header CULTURE.DEV, chat CTA, OG image, footer notice

- instruction: Repo ../katvan (branch from origin/main, not the local legal/v1.1 branch). site-astro/src/components/\*, data/landing.ts, pages/index.astro, public/assets/images/og-culture.png. Keep the hero/download specimen layout from #53/#63 intact.
- covers: c9, h9, c31, h22, c18
- acceptance:
  - SiteHeader.astro renders literal text CULTURE.DEV (not CSS text-transform); retired site/`_data`/style.yml lowercase rule reconciled
  - homepage shows a 'Try CULTURE.DEV live' link to <https://chat.culture.dev>
  - public/assets/images/og-culture.png regenerated reading CULTURE.DEV™; footer shows 'CULTURE.DEV is a trademark of Ori Nachum.'; astro build passes

### t13 — culture.dev Terms/Privacy name the Service CULTURE.DEV with a legal-version bump

- instruction: Repo ../katvan, site-astro/src/legal/\*.md + data/legal.ts only. See docs/legal-version.md for the bump procedure.
- covers: h14
- acceptance:
  - legal/terms.md and legal/privacy.md name the Service CULTURE.DEV (hostname chat.culture.dev and `inbox@culture.dev` stay lowercase)
  - data/legal.ts terms and privacy bumped (1.1 → 1.2) with new effective date, so /legal/version.json changes and irc-lens re-prompts guests

### t14 — katvan release: TRADEMARKS.md, first-use CHANGELOG, version bump, PR + deploy

- instruction: Repo ../katvan, follow its CLAUDE.md PR flow.
- depends on: t12, t13
- covers: c10
- acceptance:
  - TRADEMARKS.md (same text as other repos) added; CHANGELOG states first release with CULTURE.DEV in header/OG/chat CTA/legal; version bumped; PR merged and culture.dev deployed (deploy date recorded)

### t15 — GitHub About for culture and irc-lens names CULTURE.DEV

- instruction: gh repo edit --description, after the owner OKs the exact wording (outward-facing).
- depends on: t9, t4
- covers: h23
- acceptance:
  - gh repo view agentculture/culture and agentculture/irc-lens show descriptions naming CULTURE.DEV; irc-lens PyPI summary shows CULTURE.DEV after its release

### t16 — Live verification and dated specimen screenshot set

- instruction: Save screenshots under a dated folder the owner chooses (not committed if private). Record deploy/first-use dates in the delivery summary.
- depends on: t5, t11, t14, t15
- covers: c1, h1, c24, h15, c25, h16, c26, h17, c27, h18, c28, h19
- acceptance:
  - culture upgraded on spark, sandbox agents + culture-console restarted; a live guest-agent answer names CULTURE.DEV and a sandbox join draws no agent reply
  - screenshots with access dates: culture.dev homepage, chat sign-in, chat UI (desktop + phone), received email, guest-agent answer, IRC connect greeting, culture --version, PyPI pages; each surface in c27 checked off or recorded as already compliant

## Risks

- [unknown_nonblocking] Whether the system welcome bot is actually loaded on the sandbox (sbx) server is unverified; t6's guard makes the outcome safe either way, t16 observes it live. (task t6)
- [unknown_nonblocking] How og-culture.png was originally produced (tool/source file) is unknown; t12 may need to recreate it from the logo + text by hand. (task t12)
- [follow_up] Legal-version bump (t13) re-prompts every guest for consent as soon as culture.dev deploys — independent of the irc-lens deploy order; acceptable by owner decision c37. (task t13)
