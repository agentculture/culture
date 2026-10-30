# Delivery Summary — CULTURE.DEV on all public surfaces

plan: `culture-dev-on-all-public-surfaces` · run: `partial` · date: `2026-10-30` · updated: 2026-10-30 13:35 UTC (final: all PRs merged and deployed, sbx IRCd restarted, email received)
baseline: `devague summary skeleton`

## Intent

> CULTURE.DEV is the visible product brand on every public surface — the CLI,
> the culture.dev site, the chat.culture.dev sign-in, the chat UI and its
> emails, and what guests see in chat — rendered consistently, so the
> CULTURE.DEV trademark filing has clean, consistent use to point to.

The run executed the converged plan across four repos — culture, irc-lens,
agentirc, katvan — on a `feat/culture-dev-brand` branch in each. A first version of this
summary was written before the PRs opened (owner instruction; see `d2`). This
version is the post-merge re-run: the four main PRs are merged, released and
deployed, and so are the follow-ups irc-lens#82 (`d4`, 0.16.1) and culture#496
(t11, 14.10.1). All plan code is shipped and live, the guest sandbox IRCd was
restarted, and a real sign-in email was received. The run stays `partial` only
because the specimen set lacks a signed-in chat UI screenshot (see Remaining
Work).

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — irc-lens templates carry CULTURE.DEV (titles, hero, wordmark, sign-in header, meta, notice)
- `t2` — irc-lens phone layout keeps the CULTURE.DEV wordmark visible
- `t3` — irc-lens emails sign off as The CULTURE.DEV team
- `t4` — irc-lens release: PyPI summary, TRADEMARKS.md, README notice, first-use CHANGELOG, version bump
- `t5` — irc-lens deploy to chat.culture.dev + live mail.from + live verification
- `t6` — Sandbox guest agents name CULTURE.DEV and ignore the system welcome bot
- `t7` — Welcome bot greets with CULTURE.DEV
- `t8` — docs/ prose names the product CULTURE.DEV
- `t9` — culture release: PyPI metadata, README, TRADEMARKS.md, first-use CHANGELOG, version bump
- `t10` — agentirc greets connecting clients as CULTURE.DEV (welcome + MOTD) and releases
- `t11` — culture pins the CULTURE.DEV-greeting agentirc-cli release
- `t12` — culture.dev site chrome: header CULTURE.DEV, chat CTA, OG image, footer notice
- `t13` — culture.dev Terms/Privacy name the Service CULTURE.DEV with a legal-version bump
- `t14` — katvan release: TRADEMARKS.md, first-use CHANGELOG, version bump, PR + deploy
- `t15` — GitHub About for culture and irc-lens names CULTURE.DEV
- `t16` — Live verification and dated specimen screenshot set

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | irc-lens `4191983` (+ `261d863` by main; hero ™ in 0.16.1, `d4`): titles, "Ask CULTURE.DEV™." hero, wordmark, logo alt, og/description meta, trademark notice in chat UI |
| `t2` | delivered | irc-lens `5e3521f`: wordmark stays visible ≤760px; Playwright phone tests green after t1 merged |
| `t3` | delivered | irc-lens `c5d1910` (colleague draft, salvaged + verified by main): "The CULTURE.DEV team" sign-offs, documented sender `CULTURE.DEV <no-reply@culture.dev>` |
| `t4` | delivered | irc-lens#81 merged; 0.16.0 on PyPI |
| `t5` | delivered | 0.16.0 deployed 10:05:22 UTC, then 0.16.1 (irc-lens#82, `d4`) at 13:08:16 UTC; live `mail.from` set to `CULTURE.DEV <no-reply@culture.dev>`; live entry page shows "Ask CULTURE.DEV™." (desktop + 390px, `e30`); a real sign-in email arrived from "CULTURE.DEV", signed "The CULTURE.DEV team" (`e34`) |
| `t6` | delivered | culture `fd05235`, `e917576`, `7536e23` (main: prompt wording): prompt/decline name CULTURE.DEV; `_should_answer` skips `system-*` senders |
| `t7` | delivered | culture `748dffa` (colleague draft, salvaged + verified by main): "Welcome … on CULTURE.DEV" |
| `t8` | delivered | culture `779dd19`, `de887b4` (44 files) + `e457e53`, `51b92e9` (doc-audit fixes by main) |
| `t9` | delivered | culture#495 merged; 14.10.0 on PyPI (summary + Homepage verified live); README leftover fixed in culture#496 |
| `t10` | delivered | agentirc#63 merged; agentirc-cli 9.13.0 on PyPI; live on the main mesh (the IRCd restarted itself at 10:11 UTC, probe `e28`) and on the guest sbx IRCd (restarted by the agent on the owner's request at 13:30:46 UTC, probe `e33`) |
| `t11` | delivered | culture#496 merged; 14.10.1 on PyPI and installed on spark with agentirc-cli 9.13.0 (`e31`) |
| `t12` | delivered | katvan `df9c1d2`: header CULTURE.DEV, hero CULTURE.DEV™, "Try CULTURE.DEV live" CTA, footer notice, regenerated OG image + `make-og.py` |
| `t13` | delivered | katvan `5ea9675`: Terms/Privacy name the Service CULTURE.DEV; legal 1.2, effective 2026-10-30 |
| `t14` | delivered | katvan#72 merged; culture.dev live with header, ™ hero, chat link, footer notice and Terms 1.2 by 10:05:57 UTC |
| `t15` | delivered | About descriptions set for culture and irc-lens (wording via `/assess`, leans 0.503 / 0.567; revert with `gh repo edit --description`) · `e27` |
| `t16` | partial | `~/culture-dev-specimens/2026-10-30/` (not committed): culture.dev home/terms + chat entry at desktop and 390px with access log, guest-agent answer, `culture --version`, PyPI metadata. plus the main-mesh IRC greeting (`e28`), GitHub About (`e27`), the 0.16.1 entry page (`e30`) and 14.10.1 versions (`e31`), the sbx greeting + welcome (`e32`, `e33`) and the received email, code redacted (`e34`). Missing: signed-in chat UI |

## Mid-work Decisions

`d1`–`d3` were **approved by the owner** at the merge decision (2026-10-30);
`d4` was approved by the owner after the merge of irc-lens#82. Each was
weighed with the mesh decider (`/assess`).

- `d1` (approved) — add a katvan task (t12b): rewrite product-naming body copy
  across the culture.dev site to CULTURE.DEV — after t12/t13 the site body
  still said "Culture"; c27 requires every surface (decider: add now 0.956).
- `d2` (approved) — split delivery around the PR gate: validate + summarize
  before the PRs; t5/t11/t15/t16 after merge — those tasks need merged PRs and
  published packages (decider: 0.986).
- `d3` (approved) — add a culture task: `culture explain/overview/learn`
  output names CULTURE.DEV — found during validation; katvan builds culture.dev
  from that output (c16) (decider: add now 0.971).
- `d4` (approved) — irc-lens 0.16.1: entry hero becomes 'Ask CULTURE.DEV™.' —
  the live check showed the sign-in page's first screen without the ™ that
  decision c19 requires (decider: patch now 0.962). After code review the ™
  is a small superscript, and the password step's separate "CULTURE.DEV™"
  line is removed so the page shows the mark once. Ledger note: the first
  `d4` record (culture `5b10076`) was lost when a stale ledger copy
  overwrote the file in `0bc704a` (same failure as the `e20` id collision);
  it was re-filed as `d4` with the same content plus the review outcome.
- Live check found the guest agents still said "The product is called
  culture." (evidence `e20`, **fail**): the knowledge bundle was a read-only
  snapshot from 2026-10-10. Rebuilt with `culture sandbox agent --bundle-from`
  (after restoring write permission — the rebuild hits its own 0555 dirs, a
  small bug) and restarted; the agent then answered "This chat is powered by
  CULTURE.DEV…" (`e21`, pass). Delta `b4`.
- Decider on IRCd restarts: restart the main mesh IRCd now (0.973), leave the
  sandbox IRCd to the owner (0.984, matches the recorded never-restart rule).
  The agent's restart of the main IRCd was blocked by Claude Code's auto-mode
  classifier, but the main IRCd had already restarted itself at 10:11 UTC
  after the upgrade and serves the new greeting (`e28`) — no owner restart is
  needed there. Merging the follow-up PRs was also blocked for the agent; the
  merges and the sbx IRCd restart are the owner's.
- Supersession `s1` (`e25` → `e26`, a corrected console-restart timestamp)
  was filed by the agent; the CLI recorded it as user-origin by default. The
  owner confirmed it afterwards.
- Colleague drafted t3 and t7; both self-checks failed on environment imports,
  so main salvaged the files from the colleague branches and ran the tests
  (rated 4/5 each).
- r1 resolved in passing: `handle()` already dropped `system-*` senders, so the
  welcome bot never drew replies; t6's `_should_answer` check is defence in
  depth.
- agentirc review fixes (`a70e60d`): ASCII dash instead of em dash in the MOTD
  (legacy-charset clients), unused `ERR_NOMOTD` dropped, test helpers drain to
  376, exports sorted.
- Re-confirmations: c13, c35 and t13 had backticks added around email
  addresses (markdown lint) — wording unchanged — and were re-confirmed by the
  main agent; probe obligation `o1` was filed by mistake and rejected.

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t12` (`d1`, approved) | site body copy also rewritten (t12b, katvan `92f730f`) | acceptable |
| `t8` (`d3`, approved) | CLI introspection text renamed too (culture `d08fd25`) | acceptable |
| `t5`, `t11`, `t15`, `t16` (`d2`, approved) | run after the PR gate, not before | acceptable |
| `t1` (`d4`, approved) | entry hero gains the ™ in 0.16.1 | acceptable |
| `t16` | no signed-in chat UI screenshot in the specimen set | needs-follow-up |
| `t1` | the ™ moved from a password-step brand line to the hero (0.16.1, `d4`); setpw title test reads template source | acceptable |
| `t10` | MOTD line uses an ASCII `-` rather than the em dash in the task's example | acceptable |

## Evidence

Test runs at the branch heads (culture `8d58c4c`, irc-lens `c855644`,
agentirc `a70e60d`, katvan `b02d847`):

- culture: `/run-tests -p` — 2007 passed, 1 skipped (Playwright console test)
- irc-lens: `uv run --all-extras pytest -q` — 1331 passed, 25 deselected;
  `pytest -m playwright tests/test_e2e_playwright.py` — 10 passed
- agentirc: `uv run pytest -n auto` — 843 passed
- katvan: `uv run pytest` — 199 passed; `npm run build` — 26 pages
- devague evidence `e1`–`e19` (all `pass`, proposed) against obligations
  `o2`–`o20`; open obligations `o21` (c28), `o22` (c32), `o23` (c1)
- behavioral delta `b1` (agentirc MOTD added); deltas for `d1` and `d3` wait
  for those deviations to be approved
- reviews: doc-test-alignment (2 gaps, fixed in `e457e53`); agentirc code
  review (no blockers; fixes in `a70e60d`)
- PRs merged: agentirc#63, irc-lens#81, katvan#72, culture#495; follow-ups: irc-lens#82 (0.16.1, `d4`), culture#496 (t11, this PR)
- irc-lens#82 after review fixes (`dedc960`): `uv run --all-extras pytest -q` — 1331 passed; Playwright — 10 passed · evidence `e29`
- live: evidence `e20`–`e28` (`e20` fail, `e24` partial-fail, `e25` superseded by `e26`; `e27` GitHub About, `e28` main-mesh greeting)
- owner confirmed `d4`, `e20`–`e29` and `s1`, then `e1`–`e19`, `e30` (live
  0.16.1 entry page) and `e31` (14.10.1 installed) (2026-10-30; the owner wrote
  "e3", read as `e31` since `e3` is in `e1`–`e19`)
- guest sbx IRCd restarted 13:30:46 UTC on the owner's request; both sandbox
  agents re-registered within 10 s; probe `e32` (welcome bot) and `e33`
  (001/002 + MOTD), proposed
- received sign-in email (owner's screenshot, code redacted): `e34`, proposed
- merged follow-ups: irc-lens#82 (`3855a1a`), culture#496 (`756f63c`); deploy
  13:08:16 UTC — culture 14.10.1, irc-lens 0.16.1, sandbox bundle rebuilt from
  `756f63c`, console and both sandbox agents restarted
- outside the plan: SonarCloud unresolved-issue gate added to katvan#73 and
  agentirc#64 (owner request), both green

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| chat.culture.dev pages (entry, sign-in, chat, delete, setpw, residents) name CULTURE.DEV in code | high | irc-lens `4191983` · `e1`, `e2` |
| the live sign-in page's first screen shows "Ask CULTURE.DEV™." | high | `chat-entry-0.16.1-*.png` · `e30` |
| the chat header shows CULTURE.DEV at phone width | high | Playwright `test_phone_header_*` · `e3` |
| emails sign off "The CULTURE.DEV team" | high | `tests/test_mail_signin.py` · `e4` |
| live emails show sender CULTURE.DEV | high | received email, `signin-email-received-redacted.png` · `e34` |
| guest agents name CULTURE.DEV and ignore system bots | high | `test_sandbox_agent.py` · `e6`, `e7` |
| a live guest-agent answer says CULTURE.DEV | high | `e21` (after bundle rebuild; `e20` failed before it) |
| welcome bot greets "… on CULTURE.DEV" | high | `tests/test_welcome_bot.py` · `e8` |
| agentirc greets as CULTURE.DEV with a MOTD; wire tokens unchanged | high | `tests/test_motd.py` · `e9`, `e5` |
| the main mesh serves the new greeting + MOTD | high | live probe `irc-greeting-spark.txt` · `e28` |
| the guest sbx IRCd serves the new greeting/welcome | high | live probe `irc-greeting-sbx.txt` · `e32`, `e33` |
| culture.dev header, CTA, footer, legal 1.2 and OG image carry CULTURE.DEV | high | live access log 10:05:57 UTC · `e12`–`e14` |
| CLI `explain`/`learn` name CULTURE.DEV | high | `tests/test_cli_introspect.py` · culture `d08fd25` |
| command/package names and wire strings unchanged | high | `test_packaging_entrypoints.py`, `test_engine_identity.py` · `e10`, `e11` |
| TRADEMARKS.md + notices in all four repos | medium | files at branch heads · `e15` |
| PyPI pages show CULTURE.DEV | high | `pypi-metadata.json` · `e23` |
| GitHub About shows CULTURE.DEV | high | `gh repo view` · `e27` |
| dated specimen screenshot set exists | medium | partial set `~/culture-dev-specimens/2026-10-30/` · `e24`, `e30`, `e34` (signed-in chat UI missing) |

## Remaining Work / Follow-up

- t15 — done; owner may reword the About text (decider only leaned on wording).
- t16 — add a signed-in chat UI screenshot to the specimen set to close `o21`;
  adjudicate `e32`–`e34` and the deltas.
- Follow-ups: `culture sandbox agent --bundle-from` fails on its own read-only
  bundle dirs (culture#497; until fixed, `chmod -R u+w` the bundle before
  each rebuild that delta `b4` requires); katvan `docs/legal-version.md` still
  shows the 1.1 example; merge katvan#73 and agentirc#64 (SonarCloud gate);
  sibling repos not examined (culture-chat, culture-guide, league-of-agents,
  social profiles) — parked v2.
