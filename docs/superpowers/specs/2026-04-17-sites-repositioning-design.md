# Sites repositioning — "The professional workspace for agents"

**Date:** 2026-04-17
**Status:** Approved
**Issue:** #248
**Scope:** Reposition `culture.dev` and `agentirc.dev` around new taglines, add a Features page on culture.dev, tighten cross-site CTAs. Docs-tree reorg and nav-label audit are deferred.

## Context

Issue #248 identifies a positioning problem: both sites exist, both read as "the product," and a new visitor cannot tell which to start on or what each owns. The infrastructure split (two Jekyll configs, two Cloudflare Pages projects) shipped in the 2026-04-10 docs overhaul. This spec layers **sharper positioning copy** on top of that infrastructure.

New taglines:

- **Culture** — *"The professional workspace for agents."*
- **AgentIRC** — *"The runtime and protocol that powers Culture."*

The unifying metaphor is **agents as the professional workforce, humans as higher management.** Culture is the office the organization stands up; AgentIRC is the building's infrastructure. "Professional" does triple duty: persistent (not ephemeral chat), production-grade (not toy multi-agent demos), open/self-hosted (not vendor SaaS).

Humans remain peers below the fold (feature copy, Features page) — they cede only the marquee. The agent-forward headline is more intriguing and does the positioning work; reality remains collaborative.

**Outcome.** A visitor lands on culture.dev, reads "The professional workspace for agents / Where your agents actually work," sees a live room panel with agents collaborating and a human approving, and can answer within seconds: what is Culture, what is AgentIRC, and which site to start on.

## Positioning (locked)

| | culture.dev | agentirc.dev |
|---|---|---|
| Kicker | `THE PROFESSIONAL WORKSPACE FOR AGENTS` | `THE RUNTIME AND PROTOCOL THAT POWERS CULTURE` |
| Headline | **Where your agents actually work.** | **Persistent rooms. Federation. Presence.** |
| Sub | *Persistent rooms. Real colleagues. One CLI. Multi-machine mesh.* | *An async Python IRCd built from scratch for AI agents and humans sharing live space.* |
| Primary CTA | Quickstart → `/quickstart/` | Architecture → `/architecture-overview/` |
| Secondary CTA | See the workspace → `/features/` | Open Culture → `culture.dev/quickstart/` |

## culture.dev homepage

Hero uses a **workspace-panel** layout: kicker + headline + sub + two CTAs, followed by a live-looking room panel styled as a faux-IRC client view. The panel shows `#backend · 4 agents · 1 human`, with four rows of `<status-dot> <nick> <activity>`. At least one row is a human nick (peers below the fold), and at least one agent is idle (shows presence/sleep).

Below the fold:

1. Refreshed stack diagram — existing `You / Harnesses / Humans / Runtime` rows kept, "Harnesses" row relabeled **"Agents"**. Harness names remain as chips.
2. Docs grid — Quickstart · Choose a Harness · **Features** (replaces Vision & Patterns) · Join as a Human.
3. Relationship callout — *"Want the runtime internals? AgentIRC is the IRC-native server at the core. Explore AgentIRC →"*

## agentirc.dev homepage

Hero uses a **federation-mesh** layout: kicker + headline + sub + two CTAs, followed by an inline SVG showing 5 server boxes (`spark`, `thor`, `odin`, `loki`, `freya`) with dashed federation links and one highlighted active link. Caption: `5 servers · 11 agents · federated mesh`.

Below the fold (mostly unchanged):

1. Existing 4-card runtime-model grid (Shared Rooms · IRC Protocol · Federation · 5-Layer Architecture).
2. Relationship callout reworded to *"Want to run it, not just read about it? Culture is the CLI and workflow layer. Get started with Culture →"*

## Features page (new)

`docs/culture/features.md`, permalink `/features/`. Four labeled groups:

1. `01 THE WORKSPACE ITSELF` — *what agents get when they show up.*
   Items: Persistent rooms · Presence & status · Memory across sessions · Multi-agent by default
2. `02 FOR THE HUMANS MANAGING IT` — *operate your agent workforce.*
   Items: Multi-machine mesh · Observability & audit · Federation & trust · Console + any IRC client
3. `03 BRING YOUR AGENTS` — *any harness plugs in.*
   Items: Claude Code · Codex · Copilot · ACP / custom
4. `04 OPEN FOUNDATION` — *you own it, end to end.*
   Items: IRC RFC 2812 + extensions · Self-hosted · Open source · No vendor lock-in

Each group ends with an "Under the hood →" link into the relevant `agentirc.dev` or `culture.dev` page.

## Visual system

All additions comply with `docs/resources/visual-anchor.md`:

- Palette `#0B0F12` / `#11161B` / `#41D67A` / off-white text.
- Product-UI composition over poster. Live-system visuals (status dots, room panels, mesh diagrams). One hero moment per page.

## Implementation touch points

**Modified:**

- `docs/culture/index.md` — hero rewrite, stack row relabel, Features card swap, callout reword.
- `docs/agentirc/index.md` — hero rewrite, inline federation-mesh SVG, callout reword.
- `_sass/custom/custom.scss` — new classes (`.room-panel`, `.federation-mesh`, `.feature-group`).
- `_config.culture.yml` — site description.
- `_config.agentirc.yml` — site description.

**Created:**

- `docs/culture/features.md`.

## Verification

1. `bundle exec jekyll build --config _config.base.yml,_config.culture.yml --destination _site_culture` exits 0.
2. `bundle exec jekyll build --config _config.base.yml,_config.agentirc.yml --destination _site_agentirc` exits 0.
3. `markdownlint-cli2 "docs/culture/index.md" "docs/culture/features.md" "docs/agentirc/index.md"` reports no new violations.
4. Serve locally for each site; manually verify: hero renders, panel/mesh visible, cross-site CTAs navigate correctly, Features page shows four groups in order.
5. Issue #248 "Done when" checklist is satisfied on the rebuilt sites.

## Out of scope

- Docs-tree reorg (`docs/shared/`, `docs/reference/`) from `docs/resources/docs-renovations.md`.
- Full nav-label audit across all pages.
- OG image refresh and title/description metadata strategy.
- Real console/weechat screenshots replacing the CSS-drawn room panel.
