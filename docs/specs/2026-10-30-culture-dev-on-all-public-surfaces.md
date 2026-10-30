# CULTURE.DEV on all public surfaces

> CULTURE.DEV is the visible product brand on every public surface — the CLI, the culture.dev site, the chat.culture.dev sign-in, the chat UI and its emails, and what guests see in chat — rendered consistently, so the CULTURE.DEV trademark filing has clean, consistent use to point to.
> instruction: Close out with the c28 screenshot set; each surface maps to one frame claim (c2-c10, c18).

## Audience

- Anyone who meets the product publicly — culture.dev visitors, people signing in to or chatting on chat.culture.dev (guests and members), PyPI/CLI users — plus the USPTO examiner reviewing specimens for the CULTURE.DEV mark.

## Before → After

- Before: The mark is used consistently only on the CLI (--version/--help, culture#440) and the culture.dev homepage (katvan#53/#63); the chat app, its sign-in, emails, guest agents, welcome bot, IRC greetings and PyPI page say 'culture'/'Culture'; no ™ or trademark notice exists anywhere.
- After: Every public surface — CLI, PyPI page, culture.dev site (header, hero, chat link, Terms/Privacy), chat.culture.dev sign-in, chat UI, emails, guest agents, welcome bot, IRC greeting — names the product CULTURE.DEV, with CULTURE.DEV™ on the most prominent use per surface and the notice 'CULTURE.DEV is a trademark of Ori Nachum.'

## Why it matters

- The chat/demo app is part of CULTURE.DEV, so its public surfaces are use of the mark; inconsistent lowercase/generic 'culture' weakens the mark as a source identifier and undercuts the specimens and first-use evidence a trademark filing relies on.

## Requirements

- The chat.culture.dev sign-in and guest entry pages show CULTURE.DEV as the visible brand. Today irc-lens templates/entry.html.j2:8 <title> is 'culture' and :18 hero is 'Ask culture.'; the sign-in form (entry.html.j2:51-70) carries no brand text.
  - instruction: irc-lens templates/entry.html.j2: <title> 'CULTURE.DEV', hero 'Ask CULTURE.DEV.', brand header on the sign-in form; update tests/`test_entry.py`:201,671; verify in a real browser on chat.culture.dev.
  - honesty: The live chat.culture.dev entry/sign-in page's <title> and hero both contain CULTURE.DEV (hero 'Ask CULTURE.DEV.', CULTURE.DEV™ as the prominent use), and the sign-in step shows the brand.
- The chat UI shows CULTURE.DEV in its title and header wordmark. Today irc-lens templates/index.html.j2:6 title is 'culture — {nick}' and :30 wordmark is 'culture' beside a logo with empty alt text; delete.html.j2:8 and setpw.html.j2:8 titles are 'culture'; residents.html.j2:6 leaks the tool name 'residents — irc-lens'.
  - instruction: irc-lens index.html.j2 (title 'CULTURE.DEV — {nick}', wordmark CULTURE.DEV™, logo alt), delete/setpw titles 'CULTURE.DEV', residents title 'Residents — CULTURE.DEV'; update `test_render_residents.py`:202; add title assertions.
  - honesty: No irc-lens page <title> or header wordmark renders lowercase 'culture' or 'irc-lens' as the product name; the logo has non-empty alt text 'CULTURE.DEV'.
- Sign-in and notification emails from chat.culture.dev carry the CULTURE.DEV brand: the sign-off ('The Culture team' at irc-lens mail.py:107,124,161,211,228) and the sender display name (live config `guest_mode`.mail.from = 'culture.dev <no-reply@culture.dev>') use the mark.
  - instruction: irc-lens mail.py sign-offs (:107,124,161,211,228) → 'The CULTURE.DEV team'; live config `guest_mode`.mail.from → 'CULTURE.DEV <no-reply@culture.dev>'; update `test_mail_signin.py` + `test_delete_ux.py`; send one real code email to verify.
  - honesty: A real sign-in email received from chat.culture.dev shows sender name 'CULTURE.DEV' and signs off 'The CULTURE.DEV team'; hostnames in subjects/links stay lowercase.
- Guests talking to sandbox agents in chat meet the product as CULTURE.DEV: the sandbox system prompt (`culture_core`/sandbox/agent.py:293 'the culture mesh (culture.dev)') and the `DECLINE_COMMAND` reply (agent.py:71 'questions about culture') name CULTURE.DEV.
  - instruction: `culture_core`/sandbox/agent.py `system_prompt`() :291-310 and `DECLINE_COMMAND` :70-72 → CULTURE.DEV; add/adjust unit test pinning the strings; restart sandbox agents after release.
  - honesty: Asked 'what is this?', a sandbox guest agent names the product CULTURE.DEV; its decline reply names CULTURE.DEV.
- The welcome bot that posts on every channel join carries the brand. Today `culture_core`/bots/system/welcome/bot.yaml:16 template is 'Welcome {event.nick} to {event.channel}' — the only fixed in-channel text culture owns.
  - instruction: `culture_core`/bots/system/welcome/bot.yaml:16 template → e.g. 'Welcome {event.nick} to {event.channel} on CULTURE.DEV'; adjust any test pinning the template.
  - honesty: The welcome bot's join message contains CULTURE.DEV.
- IRC connect greetings name CULTURE.DEV as the software: agentirc client.py:521-540 `_send_welcome` sends `RPL_YOURHOST` 'Your host is {name}, running culture' and `RPL_WELCOME` with no brand. This lives in the agentirc sibling (culture pins agentirc-cli>=9.12.0,<10 at pyproject.toml:27), so it is a hand-off, not a culture edit.
  - instruction: PR to agentirc (agentirc/client.py `_send_welcome`: CULTURE.DEV in `RPL_WELCOME`/`RPL_YOURHOST`, optional MOTD; keep `RPL_MYINFO` token); release agentirc-cli, then bump culture's pin.
  - honesty: An IRC client connecting to the mesh sees CULTURE.DEV in `RPL_WELCOME`/`RPL_YOURHOST` (and MOTD if added); the `RPL_MYINFO` version token and wire tags are unchanged.
- The PyPI page and README present CULTURE.DEV as the brand: pyproject.toml:4 description 'The professional workspace for agents.' omits the mark, :66 Homepage points at github.com/OriNachum/culture rather than culture.dev, and README.md:3 says 'Culture is…' right under the '# CULTURE.DEV' H1.
  - instruction: pyproject.toml description 'CULTURE.DEV — the professional workspace for agents.', \[project.urls\] Homepage <https://culture.dev> (+ Source github.com/agentculture/culture); README.md:3 'CULTURE.DEV is…', H1 'CULTURE.DEV™'.
  - honesty: The PyPI page for culture shows CULTURE.DEV in its summary and README lead, and its Homepage URL is <https://culture.dev>.
- culture.dev site header renders the mark as CULTURE.DEV. katvan site-astro/src/components/SiteHeader.astro:21 renders 'culture<span class=brand-dot>.dev</span>' while the hero (data/landing.ts:45), title, footer and `SITE_NAME` (layouts/Base.astro:20) already use CULTURE.DEV.
  - instruction: PR to katvan: site-astro/src/components/SiteHeader.astro:21 → CULTURE.DEV; reconcile retired site/`_data`/style.yml:7-9 rule.
  - honesty: The culture.dev header nav renders the brand as CULTURE.DEV (uppercase text, not CSS text-transform only).
- A trademark notice exists: a TRADEMARKS.md reserving CULTURE.DEV (and related names) separate from the Apache-2.0 code license, and a short notice line on public pages. Nothing exists today — no ™/®, no NOTICE/TRADEMARKS file in culture, irc-lens or katvan; culture#458 and katvan#55 (both open) already propose this.
  - instruction: Add TRADEMARKS.md (trademark part of culture#458/katvan#55 only); footer/README notice 'CULTURE.DEV is a trademark of Ori Nachum.'; reference both issues in PRs.
  - honesty: A TRADEMARKS.md exists in culture, irc-lens and katvan reserving CULTURE.DEV (owner Ori Nachum), stating Apache-2.0 grants no trademark rights; the notice line appears on the culture.dev footer and the chat UI.
- The culture.dev site presents the chat as the CULTURE.DEV live app: a visible link/CTA from the homepage to chat.culture.dev branded CULTURE.DEV, and the Terms/Privacy pages name the Service as CULTURE.DEV. Today katvan origin/main links chat.culture.dev only from legal/terms.md:3 and privacy.md:3 ('the guest chat at chat.culture.dev'), with 'culture.dev is a personal project' at terms.md:10.
  - instruction: katvan landing.ts/index.astro: 'Try CULTURE.DEV live' CTA → <https://chat.culture.dev>; legal/terms.md + privacy.md name the Service CULTURE.DEV (legal-version bump per data/legal.ts if wording changes).
  - honesty: The culture.dev homepage has a visible CULTURE.DEV-branded link to chat.culture.dev, and /terms and /privacy name the Service CULTURE.DEV.
- On phones the chat UI still shows CULTURE.DEV: irc-lens static/lens.css:480 hides .lens-logo and .lens-wordmark under @media (max-width: 760px), so after c3 a mobile user would see no brand in the header at all.
  - instruction: irc-lens lens.css phone breakpoint: keep the wordmark (compact) visible; verify in a real browser at mobile width.
  - honesty: At a 390px-wide viewport the chat header shows CULTURE.DEV.
- The culture.dev social-share card carries CULTURE.DEV: katvan public/assets/images/og-culture.png (og:image for every page, layouts/Base.astro:23,70,77) renders the wordmark 'Culture' with 'Human-Agent Collaboration System'. chat.culture.dev pages have no og:/description meta at all (irc-lens templates).
  - instruction: Regenerate katvan og-culture.png with CULTURE.DEV™; add og/meta tags to irc-lens base templates.
  - honesty: Sharing a culture.dev URL previews an image reading CULTURE.DEV™; chat.culture.dev has og:title/description naming CULTURE.DEV.
- Repo and package metadata name CULTURE.DEV: GitHub About for agentculture/culture ('Culture turns isolated stochastic agents…') and agentculture/irc-lens ('…AgentIRC in Culture ecosystem'), and the irc-lens PyPI summary ('…lens for the Culture mesh', pyproject.toml:8). GitHub About is set via gh repo edit, not a PR.
  - instruction: gh repo edit --description for culture + irc-lens (outward action, after PRs merge); irc-lens pyproject description → CULTURE.DEV.
  - honesty: gh repo view and PyPI show CULTURE.DEV in the culture and irc-lens descriptions.
- Each repo's CHANGELOG names the release that first exposes CULTURE.DEV on its surface, and the date that release reaches production (chat.culture.dev deploy, culture.dev deploy) is recorded — first-use evidence for the chat surface, mirroring culture#440's changelog requirement for the CLI.
  - instruction: Changelog wording per culture#440 13.3.0 entry; note chat.culture.dev and culture.dev deploy dates in the delivery record.
  - honesty: Each repo CHANGELOG entry for this work says it is the first release exposing CULTURE.DEV on its surface, and the production deploy date is recorded.
- The sandbox knowledge bundle stops teaching 'Culture' as the product name: guest agents answer from docs (`system_prompt` knowledge, `culture_core`/sandbox/agent.py), so the product-naming prose in those docs says CULTURE.DEV — else the prompt fix (c5) yields mixed naming in answers.
  - instruction: Rewrite product-naming prose across culture docs/ (and the sandbox knowledge bundle sourced from it); keep links, command names and code untouched.
  - honesty: Grep of docs/ shows no 'Culture' used as the product name (org name AgentCulture and command 'culture' excepted).
- Sandbox agents never reply to the system welcome bot: the loop guard (`culture_core`/sandbox/agent.py `_should_answer` :674-698) skips lines from system bots, so a branded join message on the sandbox server doesn't draw an agent answer per guest join.
  - instruction: Filter system-bot senders in `_should_answer` (check how the bot nick/cap is identifiable), plus a unit test.
  - honesty: A guest joining a sandbox room produces the welcome line and zero agent replies to it.

## Honesty conditions

- Every surface in the `after_state` (c27) has a delivered change or a recorded 'already compliant' check; none is silently skipped.
- After the change, the console scripts are still 'culture' and 'culture-core' and the PyPI distribution is still 'culture'.
- tests/`test_engine_identity.py` passes unchanged; no culture.dev/\* tag, culture.\* metric name or culture.yaml filename is edited.
- Every rewritten string that is a URL, hostname or email address still resolves; only product-naming prose changed case.
- Each listed audience has at least one surface in c27 that it actually sees (site, chat sign-in/UI/email, PyPI/CLI, specimen screenshots).
- The before-state is grounded in the scope entries s1-s16 (file:line evidence), not assumed.
- The rationale rests only on recorded facts (owner decision c17, katvan#53/culture#440 specimen notes), with legal judgment parked in v1.
- The after-state surface list equals the union of the requirement claims c2-c10 and c18 — no surface listed without a claim, no claim without a surface.
- The screenshot set is captured from live production surfaces after release, each with an access date.
- No ™ in the agentirc greeting, email subjects or sender name.

## Success signals

- A screenshot set — culture.dev homepage, chat.culture.dev sign-in, chat UI, a received sign-in email, a guest-agent answer, culture --version, and the PyPI page — each shows CULTURE.DEV (with ™ on the prominent use) and none presents the product as lowercase 'culture'; recorded with access dates for the filing.

## Scope / boundaries

- The command and package names stay 'culture' (and the 'culture-core' alias); this is brand presentation only, never a rename — recorded in culture#440 and katvan#53.
  - instruction: tests/`test_packaging_entrypoints.py` and `test_cli_branding.py` stay green unchanged.
- Wire and identity strings never change: telemetry tags culture.dev/traceparent and culture.dev/tracestate (`culture_core`/telemetry/context.py:16-17, mirrored in agentirc), culture.\* metric names, the culture.yaml filename, and the `RPL_MYINFO` version token — guarded by tests/`test_engine_identity.py`.
  - instruction: Grep the final diff for culture.dev/traceparent|tracestate and culture.yaml renames — expect none.
- The lowercase domain stays lowercase where it is a URL or hostname (links, chat.culture.dev in email subjects/body, `inbox@culture.dev`); only places that name the product switch to CULTURE.DEV.
  - instruction: Review diff: changed lowercase culture.dev occurrences must be prose, never inside href/mailto/hostnames.
- ™ is used only in rendered visual surfaces (site hero, chat header, sign-in page, README H1, OG image); IRC protocol lines, email subject lines and sender names use plain CULTURE.DEV without ™.
  - instruction: Grep the final diffs of agentirc and irc-lens mail.py for ™.

## Non-goals

- Org name AgentCulture and component names AgentIRC / irc-lens are not renamed — they are distinct names, not misuses of the mark.

## Assumptions

- Command-name mentions in CLI output (e.g. 'culture doctor: no drift detected' doctor.py:59, 'stopped culture console' console.py:214) are command names, not brand uses, and stay lowercase.
- Docs prose in docs/ matters as a public surface because katvan builds culture.dev from the CLI learn/explain output (docs/README.md:11, #401) and the sandbox agent answers from the docs bundle — so docs naming 'Culture' as the product leaks onto the site and into guest chat.
- Changing only the email sender display name (address stays `no-reply@culture.dev`) leaves SPF/DKIM/DMARC alignment and Resend deliverability unchanged; auth-code emails are not newly spam-filtered.

## Scope exploration

- `s1` — `irc-lens templates/entry.html.j2 (sign-in + guest entry)`: <title>culture</title> at :8, hero 'Ask culture.' at :18 (pinned by tests/`test_entry.py`:201,671); sign-in form :51-70 has no brand; Terms/Privacy links only in the guest consent block :95,134
  - seeds: `c2`
- `s2` — `irc-lens templates/index/delete/setpw/residents .html.j2`: every <title> and the wordmark are hardcoded lowercase 'culture' (or 'irc-lens' on residents); no footer, no ™, no brand config key in config.py; only `test_render_residents.py`:202 pins a title
  - seeds: `c3`
- `s3` — `irc-lens src/irc_lens/mail.py + live ~/.config/irc-lens/config.yaml`: subjects are host-based ('Your chat.culture.dev sign-in code' :53 etc.), sign-off 'The Culture team' hardcoded, sender name configurable via `guest_mode`.mail.from; pinned by tests/`test_mail_signin.py` and `test_delete_ux.py`
  - seeds: `c4`
- `s4` — `culture_core/sandbox/agent.py`: `system_prompt`() :291-310 and `DECLINE_COMMAND` :70-72 use lowercase culture/culture.dev; answers are grounded in the docs bundle, so docs product naming also flows into guest answers
  - seeds: `c5`
- `s5` — `culture_core/bots/system/welcome/bot.yaml`: single system bot, template has no brand; other bot code only relays webhook payloads
  - seeds: `c6`
- `s6` — `agentirc/agentirc/client.py (sibling, agentirc-cli 9.12.0)`: 001/002 human-readable text is the brandable spot; 004 `RPL_MYINFO` 'culture' version token is machine-parsed; no MOTD (375/372/376) support exists anywhere
  - seeds: `c7`
- `s7` — `pyproject.toml + README.md (PyPI long description)`: H1 is CULTURE.DEV (compliant), but description/keywords/urls and body prose use 'Culture' as the product noun; culture.dev lowercase in URLs is the domain and legitimate
  - seeds: `c8`
- `s8` — `katvan site-astro (culture.dev website)`: hero wordmark, Download CULTURE.DEV button, CULTURE.DEV CLI band, footer line and og:`site_name` are compliant (katvan#53, #63); gaps: header nav lowercase, no ™, no trademark notice, retired site/`_data`/style.yml:7-9 still says 'culture.dev always lowercase'
  - seeds: `c9`
- `s9` — `culture#458, katvan#55 (open legal-posture issues) + LICENSE`: both propose TRADEMARKS.md/TERMS.md, keep trademark reservation separate from code licensing, avoid calling it 'open source'; Apache-2.0 §6 grants no trademark rights; no notice exists in any of the three repos
  - seeds: `c10`
- `s10` — `culture#440 + tests/test_cli_branding.py`: CLI --version/--help already print 'CULTURE.DEV CLI' (`culture_core`/cli/`__init__.py`:129-132, CHANGELOG 13.3.0); issue fixes command/package naming as out of scope
  - seeds: `c11`
- `s11` — `culture_core/telemetry/context.py + agentirc server_link.py`: culture.dev/\* IRCv3 tags are exchanged between culture and agentirc peers; changing them breaks trace propagation across the mesh
  - seeds: `c12`
- `s12` — `README.md:50-83, irc-lens delete.html.j2:42, katvan legal/terms.md`: dozens of culture.dev hits are real links/hostnames, not brand uses; rewriting them would break URLs and blur domain vs mark
  - seeds: `c13`
- `s13` — `README.md:11, katvan SiteFooter.astro:17 '© {year} AgentCulture · culture.dev'`: AgentCulture is the org (copyright holder line) and appears deliberately separate from the product mark
  - seeds: `c14`
- `s14` — `culture_core/cli/doctor.py, console.py`: lowercase strings name the invoked command; no startup banner exists, so #440's 'banner' criterion is vacuously satisfied
  - seeds: `c15`
- `s15` — `docs/README.md + culture_core/overview/renderer_web.py:121`: docs/ is not served directly (no `_config.yml`); learn/explain output and the sandbox knowledge bundle are the real public channels; overview page <title> is 'culture overview' with no brand header
  - seeds: `c16`
- `s16` — `katvan origin/main site-astro (homepage, legal/terms.md, legal/privacy.md)`: no link from homepage/nav to chat.culture.dev on origin/main (git grep); chat appears only in legal pages, named lowercase 'the guest chat'; mesh-preview.astro is unlinked and titled 'culture.dev — living mesh'; local checkout sits on legal/v1.1-privacy-review, 2 commits behind main
  - seeds: `c18`
- `s17` — `challenge pass / missing counter-evidence lens: irc-lens static/lens.css:476-485`: phone breakpoint sets .lens-logo, .lens-wordmark {display:none}; c3's honesty (h3) only checks markup, not what renders on a narrow viewport
  - seeds: `c30`
- `s18` — `challenge pass / overlooked surfaces lens: katvan og-culture.png + irc-lens templates meta`: OG image text reads 'Culture' (viewed the PNG); irc-lens culture-logo.png and favicons are icon-only (no text) — clean; no og tags in irc-lens templates
  - seeds: `c31`
- `s19` — `challenge pass / overlooked surfaces lens: gh repo view + PyPI JSON for culture, irc-lens, agentirc-cli, katvan`: culture and irc-lens About/summary use 'Culture' as product noun; agentirc-cli summary and agentirc/katvan About don't name the product (no change needed); live culture PyPI Homepage is still github.com/OriNachum/culture (covered by c8)
  - seeds: `c32`
- `s20` — `challenge pass / operations lens: culture#440 comment + katvan#53 first-use note`: recorded filing guidance uses the deploy/release date of the branding fix as first use; nothing in this frame recorded how the chat surface's first-use date is captured
  - seeds: `c33`
- `s21` — `challenge pass / unstated assumptions lens: decision c19 + agentirc client.py _send_welcome + irc-lens mail.py subjects`: c19 lists ™ placements but never says where it must NOT go; ™ is non-ASCII in IRC numerics and RFC 2047-encoded in subjects — avoidable risk with no evidential gain
  - seeds: `c34`
- `s22` — `challenge pass / security lens: irc-lens mail.py + live guest_mode.mail.from`: domain authentication keys on the address domain, not the display name; the from value lives in live config on spark (outside any PR), so the change is an ops edit verified by one real send (h4)
  - seeds: `c35`
- `s23` — `challenge pass / adjacent systems lens: culture_core/sandbox/agent.py knowledge bundle + docs/`: c16 assumed docs prose leaks into guest chat but no claim required rewriting it; c5's honesty (h5) tests one prompt — a docs-grounded answer can still say 'Culture'
  - seeds: `c36`
- `s24` — `challenge pass / adjacent systems lens: culture_core/sandbox/agent.py:675-698 loop guard vs bots/system/welcome`: loop guard only filters peer agents; whether the welcome bot is loaded on sbx was not determined read-only — routed to a hard question on c6
- `s25` — `challenge pass / reversibility + migration + concurrency lenses: all four repos`: every change is a string/asset edit — no schema, no stored-data migration, no concurrency; PyPI/agentirc releases are irreversible but roll-forward fixes are cheap; only state touched is the live irc-lens mail.from config and the legal-version consent coupling (routed separately)
- `s26` — `challenge pass / unexamined surfaces`: NOT examined: culture-chat, culture-guide, league-of-agents, social/Discord profiles, katvan docs pages beyond homepage/header/footer/legal, irc-lens README, culture docs/ page-by-page, live rendering of ™ in each font

## Decisions

- The chat at chat.culture.dev is part of CULTURE.DEV — it is the live chat/demo app of the product — so login and in-chat surfaces count as trademark use of CULTURE.DEV, not just consistency (owner, 2026-10-30).
- Use CULTURE.DEV™ on the first/most prominent use per surface (site hero, chat header, sign-in page, README H1) while the application is pending; never ® before registration.
- The trademark owner is Ori Nachum personally; notice line reads 'CULTURE.DEV is a trademark of Ori Nachum.'
- CULTURE.DEV is the product name in all copy: chat hero 'Ask CULTURE.DEV.', chat header wordmark 'CULTURE.DEV', email sign-off 'The CULTURE.DEV team', sandbox agents introduce themselves as assistants on CULTURE.DEV.
- irc-lens carries CULTURE.DEV directly in its templates and mail (hardcoded); no per-deployment brand config key.
- Hand off to agentirc: IRC connect greetings (`RPL_WELCOME`/`RPL_YOURHOST`, and a MOTD) are CULTURE.DEV-opinionated — agentirc presents itself as the CULTURE.DEV server, not a brand-neutral ircd.
- Claude opens the PRs in every repo this needs — culture, irc-lens, katvan and agentirc — rather than handing off to sibling agents (owner, 2026-10-30).
- Naming the Service CULTURE.DEV in /terms and /privacy is a material change: bump terms/privacy in katvan data/legal.ts so the irc-lens consent gate re-prompts guests (owner, 2026-10-30).

## Hard questions

- Does the system welcome bot run on the sandbox (sbx) server? Sandbox agents answer unaddressed lines by default (`answer_unaddressed`=True, `culture_core`/sandbox/agent.py:128), and the bot nick is not a peer — so a join message newly mentioning CULTURE.DEV could draw an agent reply to every guest join. (resolved: Brand the welcome bot on the sandbox server too (owner: 'also them').)
- Which docs feed the sandbox knowledge bundle (`knowledge_dir` on the live sbx config) — rewrite only those, or all docs/ product-naming prose (large diff)? (resolved: All product-naming prose in docs/, not only the sandbox bundle (owner).)

## Open parks

- [unknown_nonblocking] What 'compliance with trademark requirements' means beyond the recorded issues is legal judgment (adjective+generic-noun usage, ™ placement, specimen adequacy per class); no attorney guidance is recorded in any repo — the frame can only implement recorded requirements plus owner decisions.
- [unknown_nonblocking] Other public surfaces may still present 'Culture' as the product (sibling repos and profiles not examined in this pass); found later, they are follow-ups, not blockers.
