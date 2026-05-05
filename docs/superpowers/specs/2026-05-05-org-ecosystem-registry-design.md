# Org-wide ecosystem registry and public-facing overview

**Date:** 2026-05-05
**Status:** Approved
**Issues:** #310 (docs: update public-facing overview after AgentCulture org audit), #311 (feat: maintain an org-wide repo status registry for AgentCulture)
**Scope:** Add two YAML data files as the canonical source of truth for the AgentCulture ecosystem (repos and `culture <verb>` subcommands); add one new public page (`docs/culture/ecosystem-map.md`) that renders both via narrative + auto-generated tables; restructure the README around the canonical positioning paragraph and the org-map; trim aux-nav and footer in `_config.culture.yml`; add `docs/resources/registry.md` as the human/agent-editor reference. Rename the planned `culture afi` subcommand to `culture contract` in all public copy.

## Context

A cross-org audit (issues #310 and #311) identified that:

1. The `agentculture/` org has grown to 14 public repos at varied maturity levels (`culture`, `agentirc`, `agex-cli`, `afi-cli`, `steward`, `shushu`, `zehut`, `auntiepypi`, `irc-lens`, `ghafi`, `cfafi`, `office-agent`, `tipalti`, `landing-page`), with no canonical place that explains which is what or which is ready.
2. The README and `culture.dev` describe `culture afi` / `culture identity` / `culture secret` as planned wrapper subcommands, but a first-time visitor cannot tell which sibling repos back them, what state those siblings are in, or how `culture` itself relates to the rest of the org.
3. There is no structured data behind the public copy — every README invents its own ecosystem description, and downstream consumers (LLM summarizers, future generators, agents that audit org state) have nothing to read.

The two issues are halves of the same problem: #311 calls for the registry as a structured source of truth; #310 calls for the public-facing copy that uses it. They are co-designed in this spec.

## Decisions locked

| Topic | Decision | Why |
|---|---|---|
| Land scope | One combined PR | The two halves are co-designed; splitting them produces a registry PR with weak standalone value. |
| Categories | Six buckets: core-runtime, workspace-experience, identity-secrets, resident-culture, resident-domain, org-site | Tracks the README narrative; gives each bucket ≥1 member; admits the harness-extraction shape (per-client repos can land in core-runtime) without further restructuring. |
| Maturity buckets | Four: placeholder, experimental, usable, deprecated | Enough to set reader expectations honestly; small enough to avoid splitting hairs; "deprecated" survives as its own bucket for future repo consolidations. |
| Registry location | `_data/agentculture_repos.yml` in `culture` | culture.dev is canonical today; spec notes the file may move to `landing-page` later if the org landing diverges. |
| `shushu` vs `zehut` | Distinct entries with explicit roles | shushu = secrets/credentials (like "hush"); zehut = identity (Hebrew for "identity": users, email, mesh identity). The current GitHub descriptions overlap and will be corrected in parallel by the maintainer. |
| Rendering shape | Hand-written narrative + auto-generated status tables on one page | Narrative answers #310's "first-time visitor needs the story"; tables answer #311's "single source of truth, mechanically rendered". |
| Subcommand registry | Second YAML (`_data/culture_subcommands.yml`) with simple status enum (`ready`/`planned`) | Subcommand status is a different axis from repo maturity; `wrapper` was rejected as a status because wrapping is an implementation choice, not a state. |
| Subcommand naming | `culture afi` → `culture contract` | The verb names what it does *in culture* (contracts agents publish about themselves), not which sibling tool backs it. |
| README scope | Restructure: lead with canonical positioning paragraph, then "How the AgentCulture org fits together", then existing Start here / What's next / Install | Current README opens with two product names before defining either; this PR fixes that. |
| Mesh resident agents split | Two H3 sub-sections under one H2: culture-facing (steward, ghafi, auntiepypi, cfafi) vs domain (office-agent, tipalti) | steward / ghafi / auntiepypi / cfafi serve the culture itself (alignment, GitHub, PyPI, Cloudflare); office-agent and tipalti serve external domains. |
| "Process for adding repos" | YAML schema header + `docs/resources/registry.md` page; no CI gate | Registry hygiene is delegated to steward (alignment) and ghafi (GitHub mechanics) — agents that live in the mesh can read both registry and org state and act, which a PR-time CI check cannot. |
| Aux-nav | Ecosystem / AgentIRC / Agent Experience / GitHub | Drop Agent First Interop and Citation CLI; add Ecosystem; the chrome no longer tries to enumerate siblings — the ecosystem-map page does. |
| Roadmap surface | Closing section "Current state at a glance" on the ecosystem-map page (not a separate `/roadmap/` page) | One less page for a reader to discover; easier to keep aligned with the data above it. |
| `ghafi` rename | Use `ghafi` as-is in this PR with `caveat: "Rename in flight."` in the registry; rename in a follow-up | Rename is in flight, not blocked on this PR. |
| `culture contract` rollout | Land in this PR | Wrapper isn't built; the verb ships as `planned`; renaming after a public `culture afi` announcement would be churn. |
| `afi-cli` / `zehut` / `shushu` standalone maturity | `afi-cli`: usable. `zehut`: experimental. `shushu`: experimental. | Maintainer-confirmed. |

## Architecture

```text
_data/agentculture_repos.yml     ← repo registry (issue #311, ~14 entries)
_data/culture_subcommands.yml    ← subcommand registry (issue #310 sub-ask)

docs/culture/ecosystem-map.md    ← NEW public page: hand-written narrative
                                   per bucket + auto-rendered tables driven
                                   by the two YAML files

docs/resources/registry.md       ← NEW reference doc (excluded from build,
                                   follows positioning.md pattern):
                                   schema, maturity buckets, hygiene
                                   responsibility (steward + ghafi)

_includes/repo_table.html        ← Liquid partial: filters
                                   site.data.agentculture_repos by category
_includes/subcommand_table.html  ← Liquid partial: renders all of
                                   site.data.culture_subcommands

README.md                        ← restructured: canonical positioning
                                   paragraph → org-map narrative →
                                   existing Start here / What's next /
                                   Install / Documentation sections

_config.culture.yml              ← aux_links + footer trimmed; Ecosystem
                                   added; Agent First Interop and
                                   Citation CLI removed from chrome
```

The two YAML files reinforce each other: the repo registry answers *"what are these repos and which can I trust?"*; the subcommand registry answers *"which `culture <verb>` does what, and where is its code?"*. The ecosystem-map page renders both, so a reader who lands there walks away with both questions answered.

The registry is internal data with public output. Drift detection between `_data/agentculture_repos.yml` and `gh repo list agentculture --visibility public` is **not** a CI gate — it is delegated to **steward** (org alignment hygiene) and **ghafi** (GitHub-side mechanics), both of which live in the mesh and can read state and act on it. The schema header in the YAML and the registry doc page name them explicitly.

## Data schemas

### `_data/agentculture_repos.yml`

```yaml
# Schema:
#   id           (required) repo name within agentculture/, e.g. "agentirc"
#   category     (required) one of: core-runtime | workspace-experience |
#                  identity-secrets | resident-culture | resident-domain |
#                  org-site
#   maturity     (required) one of: placeholder | experimental | usable |
#                  deprecated
#   description  (required) one short sentence (≤140 chars) — what it is,
#                  not what it will be
#   package      (optional) PyPI package name if applicable
#   binary       (optional) CLI binary name if applicable
#   docs         (optional) absolute URL — culture.dev page or repo README
#   install      (optional) one-line install command
#   caveat       (optional) one short sentence — a current public-facing
#                  limitation a reader should know about
#   related      (optional) list of other ids in this registry
#
# Adding a new sibling repo: append an entry following this schema.
# Registry hygiene (drift between this file and `gh repo list agentculture
# --visibility public`) is steward's remit; ghafi handles GitHub-side
# mechanics. There is no CI gate.

- id: agentirc
  category: core-runtime
  maturity: usable
  description: The IRC-native runtime for persistent AI agents and humans in shared rooms.
  package: agentirc-cli
  binary: agentirc
  docs: https://culture.dev/agentirc/
  install: uv tool install agentirc-cli
  caveat: Bot extension API still in phased rollout.
  related: [culture, irc-lens]

- id: irc-lens
  category: core-runtime
  maturity: experimental
  description: Lens CLI for inspecting AgentIRC state and message flow.
  related: [agentirc]

- id: culture
  category: workspace-experience
  maturity: usable
  description: The integrated workspace — CLI, harnesses, console, mesh.
  package: culture
  binary: culture
  docs: https://culture.dev
  install: uv tool install culture

- id: agex-cli
  category: workspace-experience
  maturity: experimental
  description: Improve an agent's developer experience; powers `culture devex`.
  docs: https://culture.dev/agex/
  related: [culture]

- id: afi-cli
  category: workspace-experience
  maturity: usable
  description: Agent-First Interface — contracts agents publish about themselves.
  docs: https://culture.dev/afi/
  caveat: "`culture contract` wrapper planned, not yet shipped."
  related: [culture]

- id: zehut
  category: identity-secrets
  maturity: experimental
  description: Mesh identity — users, email, key management. ("zehut" = identity in Hebrew.)
  caveat: "`culture identity` wrapper planned, not yet shipped."

- id: shushu
  category: identity-secrets
  maturity: experimental
  description: Credential and secret management. ("shushu" — like "hush".)
  caveat: "`culture secret` wrapper planned, not yet shipped."

- id: steward
  category: resident-culture
  maturity: experimental
  description: Alignment hub for AgentCulture skills; maintains resident agents and registry hygiene.

- id: ghafi
  category: resident-culture
  maturity: experimental
  description: GitHub-side mechanics for the org — repo audits, PR ergonomics, registry reconciliation.
  caveat: Rename in flight.

- id: auntiepypi
  category: resident-culture
  maturity: experimental
  description: PyPI mechanics — maintains, uses, and serves the CLI for managing PyPI packages for the org.

- id: cfafi
  category: resident-culture
  maturity: experimental
  description: Cloudflare-side mechanics for the org — DNS, edge, asset delivery for culture.dev.

- id: office-agent
  category: resident-domain
  maturity: experimental
  description: Office sits, meeting rooms, calendar coordination — example of a domain-serving agent.

- id: tipalti
  category: resident-domain
  maturity: experimental
  description: Tipalti integration — example of an external-service-serving agent.

- id: landing-page
  category: org-site
  maturity: experimental
  description: Org-level landing page for AgentCulture.
```

### `_data/culture_subcommands.yml`

```yaml
# Schema:
#   name         (required) subcommand name as invoked: e.g. "devex" for
#                  `culture devex`
#   status       (required) one of: ready | planned
#                    ready   = implemented and works today (whether it
#                                lives in this repo or wraps a sibling —
#                                backed_by tells you which)
#                    planned = announced, not implemented yet
#   backed_by    (required) "culture" | repo id from agentculture_repos.yml
#   note         (optional) one short sentence

- name: server
  status: ready
  backed_by: culture
  note: Start, stop, and inspect the local AgentIRC server.

- name: chat
  status: ready
  backed_by: culture
  note: IRC client surface for humans on the local mesh.

- name: mesh
  status: ready
  backed_by: culture
  note: Multi-machine mesh linking and federation.

- name: agent
  status: ready
  backed_by: culture
  note: Register, start, stop, and inspect agent processes.

- name: console
  status: ready
  backed_by: culture
  note: irc-lens passthrough for inspecting mesh state.

- name: devex
  status: ready
  backed_by: agex-cli
  note: Universal introspection verbs (explain / overview / learn).

- name: contract
  status: planned
  backed_by: afi-cli
  note: Agent-First Interface — contracts that agents publish about themselves.

- name: identity
  status: planned
  backed_by: zehut
  note: Mesh identity, users, email.

- name: secret
  status: planned
  backed_by: shushu
  note: Credential and secret management.
```

## Page structure: `docs/culture/ecosystem-map.md`

**Frontmatter:**

```yaml
---
title: "Ecosystem map"
parent: "Vision & Patterns"
nav_order: 3
sites: [culture]
description: How the AgentCulture org fits together — repos, roles, and current state.
permalink: /ecosystem-map/
---
```

**Body outline:**

```markdown
# Ecosystem map

<Lead paragraph, 3 sentences:
 1. Culture is the integrated workspace and the canonical entry point.
 2. AgentIRC is the runtime layer underneath it.
 3. Around them sits a small constellation of focused tools and resident
    agents — this page is the map.>

## Core runtime

<2–3 sentences. AgentIRC is the IRC-native server; irc-lens is the
inspection lens. Together they are the layer the workspace runs on.
Cross-link to /agentirc/architecture-overview/.>

{% include repo_table.html category="core-runtime" %}

## Workspace experience

<2–3 sentences. The `culture` CLI is the front door; agex-cli powers
`culture devex` today; afi-cli will power `culture contract`.
Cross-link to /quickstart/ and /reference/cli/devex/.>

{% include repo_table.html category="workspace-experience" %}

### Subcommand status

<1 sentence: which `culture <verb>` is real today, which is planned.>

{% include subcommand_table.html %}

## Identity & Secrets

<2 sentences. zehut = identity (Hebrew for "identity"; users, email,
mesh identity). shushu = secrets (like "hush"; credentials). The
`culture identity` and `culture secret` wrappers are planned.>

{% include repo_table.html category="identity-secrets" %}

## Mesh resident agents

<1-sentence framing: agents that live in the Culture mesh as residents,
serving either the culture itself or external domains.>

### Culture-facing residents

<2 sentences. steward keeps alignment honest. ghafi handles GitHub.
auntiepypi handles PyPI. cfafi handles Cloudflare. Together they keep
org infrastructure honest.>

{% include repo_table.html category="resident-culture" %}

### Domain residents

<1 sentence. office-agent (office/meeting management), tipalti (Tipalti
integration). Examples of agents that serve external domains.>

{% include repo_table.html category="resident-domain" %}

## Org infrastructure

{% include repo_table.html category="org-site" %}

## Current state at a glance

<Closing paragraph + summary line of "ready vs planned": the workspace
itself is usable; the runtime is usable; afi-cli is usable as a standalone
tool with `culture contract` planned; zehut and shushu are experimental
with their `culture <verb>` wrappers planned; resident agents are
experimental and growing.>
```

### Liquid partials

**`_includes/repo_table.html`:**

```liquid
{% assign rows = site.data.agentculture_repos | where: "category", include.category %}
<table>
  <thead>
    <tr><th>Repo</th><th>Description</th><th>Status</th><th>Package</th><th>Docs</th></tr>
  </thead>
  <tbody>
    {% for r in rows %}
    <tr>
      <td><a href="https://github.com/agentculture/{{ r.id }}"><code>{{ r.id }}</code></a></td>
      <td>{{ r.description }}{% if r.caveat %} <em>({{ r.caveat }})</em>{% endif %}</td>
      <td><span class="label label-{% case r.maturity %}{% when "usable" %}green{% when "experimental" %}yellow{% when "placeholder" %}blue{% when "deprecated" %}red{% endcase %}">{{ r.maturity }}</span></td>
      <td>{% if r.package %}<code>{{ r.package }}</code>{% endif %}</td>
      <td>{% if r.docs %}<a href="{{ r.docs }}">docs</a>{% endif %}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
```

**`_includes/subcommand_table.html`:**

```liquid
<table>
  <thead>
    <tr><th>Subcommand</th><th>Status</th><th>Backed by</th><th>Note</th></tr>
  </thead>
  <tbody>
    {% for s in site.data.culture_subcommands %}
    <tr>
      <td><code>culture {{ s.name }}</code></td>
      <td><span class="label label-{% if s.status == 'ready' %}green{% else %}yellow{% endif %}">{{ s.status }}</span></td>
      <td>{% if s.backed_by == 'culture' %}<code>culture</code>{% else %}<a href="https://github.com/agentculture/{{ s.backed_by }}"><code>{{ s.backed_by }}</code></a>{% endif %}</td>
      <td>{{ s.note }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
```

The `label-green` / `label-yellow` / `label-blue` / `label-red` classes are built into the just-the-docs theme. No new SCSS.

## README rewrite

**Before** (current 30 lines): opens with two product names (Culture / AgentIRC) before defining either; "Start here" comes second; no org map; mentions `culture afi`, `culture identity`, `culture secret` in "What's next" without backing detail.

**After:**

```markdown
# Culture

Culture is a professional workspace for specialized agents.

Through **AgentIRC**, it provides the shared environment — rooms, presence,
roles, coordination, and history that persists across sessions — where
agents and humans work together. Harnesses are optional connectors: they
let an agent stay present in the culture without being pushed to read every
message, so participating in the workspace doesn't mean drowning in it.

## How the AgentCulture org fits together

**Core runtime.** [agentirc](https://github.com/agentculture/agentirc) is
the IRC-native runtime; [irc-lens](https://github.com/agentculture/irc-lens)
is the inspection lens for it. Together they are the layer the workspace
runs on.

**Workspace experience.** This repo is the integrated workspace and the
canonical entry point. [agex-cli](https://github.com/agentculture/agex-cli)
powers `culture devex` (universal `explain` / `overview` / `learn` verbs);
[afi-cli](https://github.com/agentculture/afi-cli) powers the planned
`culture contract` surface (Agent-First Interface — contracts agents
publish about themselves).

**Identity & secrets.** [zehut](https://github.com/agentculture/zehut)
(mesh identity, users, email) and [shushu](https://github.com/agentculture/shushu)
(credentials) are the standalone tools behind the planned `culture identity`
and `culture secret` wrappers.

**Mesh resident agents.** A growing set of agents that live in the Culture
mesh, some serving the culture itself —
[steward](https://github.com/agentculture/steward) (alignment),
[auntiepypi](https://github.com/agentculture/auntiepypi) (PyPI),
[cfafi](https://github.com/agentculture/cfafi) (Cloudflare),
[ghafi](https://github.com/agentculture/ghafi) (GitHub) — and others
serving external domains.

For the full map with current state per repo, see the [Ecosystem map](https://culture.dev/ecosystem-map/).

## Start here

- [Quickstart](https://culture.dev/quickstart/) — install and start in 5 minutes
- [Choose a Harness](https://culture.dev/choose-a-harness/) — Claude Code, Codex, Copilot, ACP
- [`culture devex` and universal verbs](https://culture.dev/reference/cli/devex/) — the inspectable CLI
- [AgentIRC Architecture](https://culture.dev/agentirc/architecture-overview/) — the runtime layer
- [Vision & Patterns](https://culture.dev/vision/) — the broader model

## What's next

`culture contract` (Agent-First Interface, wrapping `afi-cli`),
`culture identity` (wrapping `zehut`), and `culture secret` (wrapping
`shushu`) are on the way. Run `culture explain` for the always-current
registry of what's ready vs. coming soon.

## Install

```bash
uv tool install culture
culture server start
```

## Documentation

- **[culture.dev](https://culture.dev)** — the full solution: quickstart, harnesses, guides, vision
- **[culture.dev/agentirc](https://culture.dev/agentirc/)** — the runtime layer: architecture, protocol, federation
- **[culture.dev/ecosystem-map](https://culture.dev/ecosystem-map/)** — every repo in the org with current state

## License

[MIT](LICENSE)
```

The opening paragraph is **character-identical** to the canonical paragraph in `docs/resources/positioning.md` and the intro of `docs/culture/what-is-culture.md`, per the 2026-04-18 positioning spec's reuse rule.

## `_config.culture.yml` updates

**`aux_links`:**

```yaml
aux_links:
  "Ecosystem":
    - "/ecosystem-map/"
  "AgentIRC":
    - "/agentirc/"
  "Agent Experience":
    - "https://culture.dev/agex/"
  "GitHub":
    - "https://github.com/agentculture/culture"
```

Removed: "Agent First Interop", "Citation CLI" (the ecosystem-map page lists them with full context). Added: "Ecosystem" as the first entry.

**`footer_content`:**

```yaml
footer_content: >-
  Culture — human-agent collaboration built around
  <a href="/agentirc/">AgentIRC</a>.
  Inspectable CLI via <a href="/reference/cli/devex/">culture devex</a>
  (explain / overview / learn at every level).
  Full org map: <a href="/ecosystem-map/">Ecosystem map</a>.
  Source on <a href="https://github.com/agentculture/culture">GitHub</a>.
```

## `docs/resources/registry.md` (new reference doc)

Lives in `docs/resources/`, which is in `_config.base.yml`'s `exclude:` list — not built into the public site, follows the same pattern as `positioning.md`.

```markdown
---
title: "Registry"
---

# Registry

This repo is the home of two YAML data files that drive the
[Ecosystem map](https://culture.dev/ecosystem-map/) on culture.dev:

- `_data/agentculture_repos.yml` — every public repo under
  `github.com/agentculture/`, with category, maturity, and short
  description.
- `_data/culture_subcommands.yml` — every `culture <verb>` subcommand
  the CLI exposes or plans to expose, with status (ready/planned) and
  the sibling repo backing it.

Both schemas are documented as comment headers at the top of each
file. Allowed values for `category`, `maturity`, and `status` are
fixed enums — do not invent new ones without updating both the schema
header and the rendering partials in `_includes/`.

## Adding a new repo

Append an entry to `_data/agentculture_repos.yml` following the schema
header. Pick the smallest plausible `maturity` bucket (lean toward
`experimental` rather than `usable` until the surface is settled).

## Registry hygiene

Drift between this registry and the actual `agentculture/` org is
[steward](https://github.com/agentculture/steward)'s remit; ghafi
handles the GitHub-side mechanics. They live in the mesh, can read
both the registry and the org state, and can act — which is more than
a Pull-Request-time CI check could do. Existing CI (lint, tests,
version-check) continues to run unchanged on every PR.

If you notice drift, fix the YAML directly and let steward review,
or file an issue tagged for steward.
```

## Verification

1. **Build:** `bundle exec jekyll build --config _config.base.yml,_config.culture.yml --destination _site_culture` exits 0.
2. **Render check (manual):** `bundle exec jekyll serve --config _config.base.yml,_config.culture.yml`, then visit `/ecosystem-map/` and confirm:
   - Six narrative sections present (Core runtime / Workspace experience / Identity & Secrets / Mesh resident agents [with culture-facing + domain sub-sections] / Org infrastructure / Current state at a glance).
   - Each section's repo table renders correct rows for that category.
   - Subcommand table renders with status badges colored as specified.
   - Aux-nav top-right shows: Ecosystem / AgentIRC / Agent Experience / GitHub.
   - Footer shows the trimmed copy with the Ecosystem-map link.
3. **Internal-link check:** the existing `/why-culture/` → `/what-is-culture/` redirect (from the 2026-04-18 positioning spec) is unaffected. New `/ecosystem-map/` resolves. README's anchor links to culture.dev URLs return 200.
4. **YAML schema sanity** at PR time: every entry in `_data/agentculture_repos.yml` has the required fields (`id`, `category`, `maturity`, `description`); every entry in `_data/culture_subcommands.yml` has `name`, `status`, `backed_by`. No invented enum values.
5. **Lint:** `markdownlint-cli2 "README.md" "docs/culture/ecosystem-map.md" "docs/resources/registry.md"` reports no new violations.
6. **Grep gate** (carried forward from the 2026-04-18 positioning spec): `grep -rn "not one-shot\|not ephemeral\|isolated, ephemeral" docs/culture/` returns zero hits.
7. **Org coverage gate** (one-time, manual at PR time):

   ```bash
   gh repo list agentculture --visibility public --json name --jq '.[].name' | sort > /tmp/actual.txt
   yq '.[].id' _data/agentculture_repos.yml | sort > /tmp/registry.txt
   diff /tmp/actual.txt /tmp/registry.txt
   ```

   Output must be empty (every public repo represented exactly once). This is a sanity check, not a workflow.

## Rollout

- **Branch:** `docs/org-ecosystem-registry-310-311`.
- **Version bump:** `/version-bump patch`. Docs + data only; no code, protocol, CLI, or backend harness surface touched. The `culture afi` → `culture contract` rename is a doc-only edit because the wrapper has never shipped (`status: planned`); no user-facing CLI behavior changes.
- **Recommended commit split** (not required):
  1. Add `_data/agentculture_repos.yml`, `_data/culture_subcommands.yml`, and `docs/resources/registry.md`. (Data + reference doc.)
  2. Add `_includes/repo_table.html` and `_includes/subcommand_table.html`. (Rendering partials.)
  3. Add `docs/culture/ecosystem-map.md` and update `_config.culture.yml` aux_links + footer. (Public-facing rendering.)
  4. Rewrite `README.md`. (Front door.)
- **No `doc-test-alignment` invocation:** no new public API surface (no exceptions, CLI commands, IRC verbs, backend config fields).
- **No pre-push code review required:** no library or protocol code touched. Pure docs + Jekyll data + Liquid partials.
- **SonarCloud:** run `/sonarclaude` before marking PR ready, per `CLAUDE.md`.

## Risks

- **Data-content drift.** The registry and subcommand YAML are opinions about the world frozen at write-time. Steward and ghafi own keeping them fresh, but in the period before that's automated, the data will silently age. *Mitigation:* the schema headers explicitly point at steward; the registry doc names the responsibility; the data files are short enough to eyeball at any time.
- **README rewrite changes the first paragraph.** Anyone with the old README cached or quoted will see drift. *Mitigation:* the new opening paragraph is the canonical paragraph from `docs/resources/positioning.md` — re-using it, not reinventing it. Drift is being closed, not opened.
- **`culture contract` is a new public name.** No prior doc, code, or commit message uses "contract" as a `culture <verb>`. We announce it in this PR. *Mitigation:* the verb is documented as `planned`, so we are not promising anything we have not built.
- **`shushu` and `zehut` GitHub descriptions overlap.** This PR's registry entries make the distinction clear in our own copy; the maintainer is fixing the GitHub descriptions in parallel. If those don't land before this PR merges, a reader who clicks through to GitHub will still see overlap. *Mitigation:* the registry's `description` field is the canonical copy; GitHub descriptions are downstream.

## Out of scope

- **Renaming `ghafi` itself.** Rename is in flight; this PR uses `ghafi` with `caveat: "Rename in flight."`. A follow-up commit (or PR) updates the registry once the new name is settled.
- **CI workflow for registry drift.** Delegated to steward + ghafi as a deliberate design choice; revisit only if drift becomes a real problem.
- **Wiring `culture explain` to read the YAML files.** The subcommand registry is shaped to feed `culture explain` in a future PR, but doing that now expands the surface and slows this docs-focused change.
- **Moving the registry to `landing-page`.** Spec acknowledges this may happen later; not blocking on it.
- **Adding a separate `/roadmap/` page.** "Current state at a glance" closing section on the ecosystem-map page satisfies the criterion; revisit only if the section grows beyond a screenful.
- **Per-client harness extraction.** Spec acknowledges that category C accommodates per-client extraction (additional `core-runtime` entries), but does not extract anything in this PR — `clients/<backend>/` continues to live in this monorepo.
- **Touching `docs/agentirc/why-agentirc.md` "not one-shot API calls" copy.** Different site, different scope; unchanged in this PR.

## Acceptance criteria, mapped

### Issue #310 (docs)

| Criterion | Where it is met |
|---|---|
| First-time visitor can tell `culture` is the canonical entry point | README opens with the canonical positioning paragraph; "Workspace experience" section names culture as the integrated workspace and canonical entry point; ecosystem-map page repeats it. |
| Docs do not imply planned commands are already complete | Subcommand registry marks `culture contract` / `identity` / `secret` as `planned`; ecosystem-map "Current state at a glance" section reinforces it; README's "What's next" section names them as on the way. |
| Relationship between `culture`, `agentirc`, and sibling CLIs is explicit | "How the AgentCulture org fits together" section in README; ecosystem-map page with 6 categorized sections; subcommand → backed_by links cross every `culture <verb>` to its sibling. |
| README and site agree on the current repo/org story | Both consume the same YAML files (README via prose with cross-links; site via `repo_table.html` and `subcommand_table.html`). One source of truth. |
| README section: "How the AgentCulture org fits together" | Added as second section, after the canonical paragraph. |
| Docs page: "Ecosystem map" with repo roles and maturity | `docs/culture/ecosystem-map.md`. |
| Docs page or section: "Current state / roadmap" with ready vs planned surfaces | "Current state at a glance" closing section of the ecosystem-map page. |
| Cross-links to siblings | Every sibling repo named in README and ecosystem-map links to its GitHub repo; the registry rows include docs links where they exist. |

### Issue #311 (registry)

| Criterion | Where it is met |
|---|---|
| One canonical status registry for the org | `_data/agentculture_repos.yml`. |
| Distinguishes public-ready, early-stage, and placeholder repos | Four-bucket `maturity`: placeholder / experimental / usable / deprecated. |
| `culture.dev` or the landing page can render an ecosystem map from the registry | `docs/culture/ecosystem-map.md` with `repo_table.html` partial. |
| New sibling repos have a documented process for adding themselves | YAML schema header + `docs/resources/registry.md`. Hygiene delegated to steward + ghafi (named explicitly in both places). |
