# Org-wide ecosystem registry and public-facing overview — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land issues #310 (public-facing docs overhaul) and #311 (org-wide repo status registry) as one coherent PR — two YAML data files driving an auto-rendered ecosystem-map page, a restructured README, trimmed aux-nav/footer, and a registry reference doc.

**Architecture:** Two `_data/*.yml` files become the canonical source of truth (repos + `culture <verb>` subcommands). Two `_includes/*.html` Liquid partials render them. A new public page `docs/culture/ecosystem-map.md` weaves narrative around the auto-generated tables. The README leads with the canonical positioning paragraph and adds a 4-paragraph org-map section. The aux-nav and footer drop sibling-product enumeration in favor of one Ecosystem-map link. The `culture afi` planned subcommand is renamed to `culture contract` everywhere it appears. No code, no protocol, no CLI behavior changes — pure docs + Jekyll data + Liquid templating.

**Tech Stack:** Jekyll 4.3 + just-the-docs theme; YAML data files; Liquid partials; markdownlint-cli2 for linting; `bundle exec jekyll build` for site verification; `yq` and `gh` for the org-coverage sanity check.

**Reference spec:** `docs/superpowers/specs/2026-05-05-org-ecosystem-registry-design.md` (committed in `5b63633`). Read this before starting — every decision in the plan traces back to a locked decision in the spec.

---

## Task 1: Branch out and bump version

**Files:**
- Create branch: `docs/org-ecosystem-registry-310-311`
- Modify: `pyproject.toml`, `culture/__init__.py` (or wherever the version lives), `CHANGELOG.md`, `uv.lock`
- (Done by `/version-bump patch`)

**Why patch:** docs + data only. No code, no protocol, no CLI behavior changes. The `culture afi` → `culture contract` rename is a doc-only edit because the wrapper has never shipped (`status: planned` in the registry); no user-facing CLI behavior changes.

- [ ] **Step 1: Verify a clean working tree first**

Run:
```bash
git status
```

Expected: nothing staged or modified except the (already-committed) spec from `5b63633`. If `CHANGELOG.md` or any `CLAUDE.md` carries pre-existing unstaged changes on `main`, stop and decide how to handle them before continuing — `/version-bump` will interleave awkwardly with an existing `[Unreleased]` block. (See CLAUDE.md "Git Workflow" rule.) An untracked `culture.yaml` in the repo root is fine to leave alone.

- [ ] **Step 2: Branch out**

Run:
```bash
git checkout -b docs/org-ecosystem-registry-310-311
```

Expected: `Switched to a new branch 'docs/org-ecosystem-registry-310-311'`.

- [ ] **Step 3: Run `/version-bump patch`**

Run the slash command:
```
/version-bump patch
```

Expected: `pyproject.toml`, version constant in `culture/__init__.py`, `CHANGELOG.md`, and `uv.lock` (if applicable) are updated together. The CHANGELOG entry should describe the upcoming change at a high level — "Add ecosystem-map page and registry data files; rename planned `culture afi` to `culture contract`."

- [ ] **Step 4: Commit the version bump**

The `/version-bump` command typically commits its own changes; if it does not, commit them now:
```bash
git status
git diff --staged
git commit -m "chore: bump version (patch) for ecosystem-registry PR"
```

Expected: clean commit, no pre-commit failures.

---

## Task 2: Add `_data/agentculture_repos.yml`

**Files:**
- Create: `_data/agentculture_repos.yml`

**Schema (canonical, do not invent fields):** `id`, `category`, `maturity`, `description` (required); `package`, `binary`, `docs`, `install`, `caveat`, `related` (optional). Allowed values for `category`: `core-runtime | workspace-experience | identity-secrets | resident-culture | resident-domain | org-site`. Allowed values for `maturity`: `placeholder | experimental | usable | deprecated`.

- [ ] **Step 1: Create the file with the full content below**

Write to `_data/agentculture_repos.yml`:

```yaml
# Schema:
#   id           (required) repo name within agentculture/, e.g. "agentirc"
#   category     (required) one of: core-runtime | workspace-experience |
#                  identity-secrets | resident-culture | resident-domain |
#                  org-site
#   maturity     (required) one of: placeholder | experimental | usable |
#                  deprecated
#   description  (required) one short sentence (<=140 chars) — what it is,
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

- [ ] **Step 2: Verify YAML parses**

Run:
```bash
yq '.[].id' _data/agentculture_repos.yml
```

Expected: 14 lines, each one a quoted repo id (`"agentirc"`, `"irc-lens"`, …, `"landing-page"`). If `yq` is not installed, fall back to:
```bash
python3 -c "import yaml; print(len(yaml.safe_load(open('_data/agentculture_repos.yml'))))"
```
which should print `14`.

- [ ] **Step 3: Verify org coverage matches reality**

Run the spec's "org coverage gate" (verification step #7):
```bash
gh repo list agentculture --visibility public --json name --jq '.[].name' | sort > /tmp/actual.txt
yq '.[].id' _data/agentculture_repos.yml | tr -d '"' | sort > /tmp/registry.txt
diff /tmp/actual.txt /tmp/registry.txt
```

Expected: empty diff. If there's a difference (a repo created or archived since the spec was written), update the registry to match — register it under the right category and `experimental` maturity, or remove it if archived. Re-run the diff.

- [ ] **Step 4: Stage but do not commit yet**

Run:
```bash
git add _data/agentculture_repos.yml
```

We'll commit Task 2 + Task 3 + Task 4 together at the end of Task 4 ("Data + reference doc" commit per the spec's recommended split).

---

## Task 3: Add `_data/culture_subcommands.yml`

**Files:**
- Create: `_data/culture_subcommands.yml`

**Schema:** `name`, `status`, `backed_by` (required); `note` (optional). Allowed values for `status`: `ready | planned`.

- [ ] **Step 1: Create the file with the full content below**

Write to `_data/culture_subcommands.yml`:

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

- [ ] **Step 2: Verify YAML parses**

Run:
```bash
yq '.[].name' _data/culture_subcommands.yml
```

Expected: 9 lines: `server`, `chat`, `mesh`, `agent`, `console`, `devex`, `contract`, `identity`, `secret`. (Or use the `python3 -c "..."` fallback from Task 2 step 2.)

- [ ] **Step 3: Verify every `backed_by` resolves**

Every `backed_by` value (other than `"culture"`) must match an `id` in `_data/agentculture_repos.yml`:
```bash
yq '.[].backed_by' _data/culture_subcommands.yml | tr -d '"' | sort -u | grep -v '^culture$' > /tmp/backed_by.txt
yq '.[].id' _data/agentculture_repos.yml | tr -d '"' | sort -u > /tmp/repo_ids.txt
comm -23 /tmp/backed_by.txt /tmp/repo_ids.txt
```

Expected: empty output (every non-`culture` `backed_by` exists as a repo id). If anything appears, fix the typo before continuing.

- [ ] **Step 4: Stage**

```bash
git add _data/culture_subcommands.yml
```

---

## Task 4: Add `docs/resources/registry.md` and commit data + reference doc

**Files:**
- Create: `docs/resources/registry.md`

**Note:** `docs/resources/` is already in `_config.base.yml`'s `exclude:` list, so this file is a reference for human/agent editors of this repo, not a built site page. Same pattern as `docs/resources/positioning.md`.

- [ ] **Step 1: Create the file with the full content below**

Write to `docs/resources/registry.md`:

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

- [ ] **Step 2: Verify markdownlint passes on the new doc**

Run:
```bash
markdownlint-cli2 "docs/resources/registry.md"
```

Expected: `Summary: 0 error(s)`. If a violation appears, fix it (typically MD013 line-length — split a long line — or MD031 fenced-code-block-blank-line — add a blank line before/after a code fence).

- [ ] **Step 3: Stage**

```bash
git add docs/resources/registry.md
```

- [ ] **Step 4: Commit Task 2 + Task 3 + Task 4 together**

```bash
git status
git diff --staged
git commit -m "$(cat <<'EOF'
docs(data): add agentculture_repos and culture_subcommands registries (#311)

Two YAML data files become the canonical source of truth for the
AgentCulture ecosystem: agentculture_repos.yml lists every public repo
in the org with category/maturity/description; culture_subcommands.yml
lists every `culture <verb>` with ready/planned status and the sibling
repo backing it. docs/resources/registry.md documents the schema,
adding-a-repo workflow, and the steward+ghafi hygiene delegation.

EOF
)"
```

Expected: pre-commit hooks pass (markdownlint on the registry doc; YAML validity check). Commit lands cleanly.

---

## Task 5: Add `_includes/repo_table.html` Liquid partial

**Files:**
- Create: `_includes/repo_table.html`

- [ ] **Step 1: Create the file with the full content below**

Write to `_includes/repo_table.html`:

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

**Note on label classes:** `label-green`, `label-yellow`, `label-blue`, `label-red` are built into the just-the-docs theme — no new SCSS needed. If a future maturity bucket is added, the `case` statement and the schema header in `_data/agentculture_repos.yml` must update together.

- [ ] **Step 2: Stage**

```bash
git add _includes/repo_table.html
```

We'll verify rendering once the consuming page exists in Task 7.

---

## Task 6: Add `_includes/subcommand_table.html` Liquid partial

**Files:**
- Create: `_includes/subcommand_table.html`

- [ ] **Step 1: Create the file with the full content below**

Write to `_includes/subcommand_table.html`:

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

- [ ] **Step 2: Stage**

```bash
git add _includes/subcommand_table.html
```

- [ ] **Step 3: Commit Task 5 + Task 6 together**

```bash
git commit -m "$(cat <<'EOF'
docs(includes): add repo_table and subcommand_table Liquid partials

Two _includes partials drive the auto-rendering of the ecosystem-map
page from _data/agentculture_repos.yml and _data/culture_subcommands.yml.
Status badges use just-the-docs built-in label-green / label-yellow /
label-blue / label-red classes — no new SCSS.

EOF
)"
```

---

## Task 7: Add `docs/culture/ecosystem-map.md`

**Files:**
- Create: `docs/culture/ecosystem-map.md`

- [ ] **Step 1: Create the file with the full content below**

Write to `docs/culture/ecosystem-map.md`:

````markdown
---
title: "Ecosystem map"
parent: "Vision & Patterns"
nav_order: 3
sites: [culture]
description: How the AgentCulture org fits together — repos, roles, and current state.
permalink: /ecosystem-map/
---

# Ecosystem map

Culture is the integrated workspace and the canonical entry point for the
AgentCulture ecosystem. AgentIRC is the runtime layer underneath it. Around
them sits a small constellation of focused tools and resident agents — this
page is the map.

## Core runtime

[AgentIRC](/agentirc/architecture-overview/) is the IRC-native server that
provides shared rooms, presence, and persistence. `irc-lens` is the
inspection lens for the same protocol. Together they are the layer the
workspace runs on.

{% include repo_table.html category="core-runtime" %}

## Workspace experience

The `culture` CLI is the front door. `agex-cli` powers `culture devex` (the
universal `explain` / `overview` / `learn` introspection verbs); `afi-cli`
will power `culture contract` (Agent-First Interface — contracts that
agents publish about themselves). Start with the
[Quickstart](/quickstart/) or the
[`culture devex` reference](/reference/cli/devex/).

{% include repo_table.html category="workspace-experience" %}

### Subcommand status

Which `culture <verb>` is real today, which is planned. The wrapper for
`afi-cli` (`culture contract`) and the wrappers for the identity and
secrets tools (`culture identity`, `culture secret`) are not yet shipped —
the underlying tools work as standalone CLIs in the meantime.

{% include subcommand_table.html %}

## Identity & Secrets

`zehut` (Hebrew for "identity") covers mesh identity, users, email, and key
management. `shushu` (like "hush") covers credentials and secrets. The
`culture identity` and `culture secret` wrappers are planned; the
standalone tools are usable today.

{% include repo_table.html category="identity-secrets" %}

## Mesh resident agents

Agents that live in the Culture mesh as residents — full citizens of the
network rather than tools you invoke. Some serve the culture itself; others
serve external domains.

### Culture-facing residents

`steward` keeps alignment honest across AgentCulture skills and resident
agents. `ghafi` handles GitHub-side mechanics — repo audits, PR ergonomics,
and registry reconciliation. `auntiepypi` handles PyPI; `cfafi` handles
Cloudflare. Together they keep the org's infrastructure honest.

{% include repo_table.html category="resident-culture" %}

### Domain residents

`office-agent` (office sits and meeting rooms) and `tipalti` (Tipalti
integration) are examples of agents that serve external domains rather
than the culture itself.

{% include repo_table.html category="resident-domain" %}

## Org infrastructure

{% include repo_table.html category="org-site" %}

## Current state at a glance

The workspace itself (`culture`) and the runtime (`agentirc`) are usable
today. `afi-cli` is usable as a standalone tool, with `culture contract`
planned. `zehut` and `shushu` are experimental as standalone tools, with
their `culture <verb>` wrappers planned. Resident agents (`steward`,
`ghafi`, `auntiepypi`, `cfafi`, `office-agent`, `tipalti`) are all
experimental and growing in number. The canonical positioning paragraph
lives in [What is Culture?](/what-is-culture/).
````

- [ ] **Step 2: Run jekyll build and confirm exit 0**

Run:
```bash
bundle exec jekyll build --config _config.base.yml,_config.culture.yml --destination _site_culture
```

Expected: build exits 0. No "Liquid Exception" or "Liquid Warning" lines in the output. If `bundle install` is needed, run it first — but no new gem dependency is introduced by this PR (the prior 2026-04-18 positioning spec already added `jekyll-redirect-from`).

- [ ] **Step 3: Verify the page rendered**

Run:
```bash
ls _site_culture/ecosystem-map/index.html && grep -c '<table>' _site_culture/ecosystem-map/index.html
```

Expected: file exists; grep returns `6` (one table per category invocation: core-runtime, workspace-experience, subcommand-table, identity-secrets, resident-culture, resident-domain — plus org-site = **7** tables. Adjust the expected count once you actually run it: 6 categories with `repo_table.html` + 1 `subcommand_table.html` = **7**.).

- [ ] **Step 4: Spot-check rendered content**

Run:
```bash
grep -A2 'agentirc' _site_culture/ecosystem-map/index.html | head -20
grep 'label-green\|label-yellow' _site_culture/ecosystem-map/index.html | head -5
```

Expected: `agentirc` row shows up with a `label-green` (it's `usable`); other entries show appropriate label colors. No raw Liquid syntax (`{% ... %}`) leaking into the rendered HTML.

- [ ] **Step 5: Stage and commit Task 7**

```bash
git add docs/culture/ecosystem-map.md
git commit -m "$(cat <<'EOF'
docs(culture): add ecosystem-map page (#310)

New public page docs/culture/ecosystem-map.md weaves narrative around
auto-generated tables driven by _data/agentculture_repos.yml and
_data/culture_subcommands.yml. Six narrative sections (Core runtime,
Workspace experience with subcommand status, Identity & Secrets, Mesh
resident agents [culture-facing + domain], Org infrastructure) plus
"Current state at a glance" closing summary.

EOF
)"
```

---

## Task 8: Update `_config.culture.yml` aux_links and footer

**Files:**
- Modify: `_config.culture.yml`

- [ ] **Step 1: Replace the `aux_links:` block**

Open `_config.culture.yml` and replace lines 34–46 (the `aux_links:` and `aux_links_new_tab:` blocks) with:

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

aux_links_new_tab: false
```

Removed: "Agent First Interop" and "Citation CLI" (the ecosystem-map page lists them with full context). Added: "Ecosystem" as the first entry. Kept: AgentIRC, Agent Experience, GitHub.

- [ ] **Step 2: Replace the `footer_content:` block**

In the same file, replace the `footer_content:` block (the multi-line block at the end) with:

```yaml
footer_content: >-
  Culture — human-agent collaboration built around
  <a href="/agentirc/">AgentIRC</a>.
  Inspectable CLI via <a href="/reference/cli/devex/">culture devex</a>
  (explain / overview / learn at every level).
  Full org map: <a href="/ecosystem-map/">Ecosystem map</a>.
  Source on <a href="https://github.com/agentculture/culture">GitHub</a>.
```

Five sibling-product mentions go down to one Ecosystem-map link. Same logic: the page is the right surface for the listing.

- [ ] **Step 3: Verify the build still passes**

Run:
```bash
bundle exec jekyll build --config _config.base.yml,_config.culture.yml --destination _site_culture
```

Expected: exit 0. No Liquid errors.

- [ ] **Step 4: Verify the chrome rendered**

Run:
```bash
grep -o '/ecosystem-map/' _site_culture/index.html | head -3
```

Expected: at least one match (the aux-nav and/or footer link both reference the path; the homepage will include the chrome). If zero matches, the YAML edit didn't take — re-check indentation in `_config.culture.yml`.

- [ ] **Step 5: Stage and commit**

```bash
git add _config.culture.yml
git commit -m "$(cat <<'EOF'
docs(config): trim aux_links and footer; surface Ecosystem map (#310)

Aux-nav drops Agent First Interop and Citation CLI in favor of one
Ecosystem entry pointing at /ecosystem-map/. Footer goes from five
sibling-product mentions to one Ecosystem-map link. The chrome no
longer tries to enumerate siblings — the ecosystem-map page does.
Kept: AgentIRC, Agent Experience, GitHub.

EOF
)"
```

---

## Task 9: Rewrite `README.md`

**Files:**
- Modify (full rewrite): `README.md`

- [ ] **Step 1: Replace the entire file with the content below**

Write to `README.md`:

````markdown
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
````

The opening canonical paragraph is **character-identical** to the canonical paragraph in `docs/resources/positioning.md` and the intro of `docs/culture/what-is-culture.md`, per the 2026-04-18 positioning spec's reuse rule.

- [ ] **Step 2: Verify markdownlint passes on the rewritten README**

Run:
```bash
markdownlint-cli2 "README.md"
```

Expected: `Summary: 0 error(s)`. If MD013 (line-length) fires on a long sentence, soft-wrap it. If MD040 (fenced-code-language) fires, ensure the install code fence has `bash`.

- [ ] **Step 3: Verify the canonical paragraph is character-identical to positioning.md**

Run:
```bash
diff <(sed -n '/^Culture is a professional workspace/,/drowning in it\./p' README.md) <(sed -n '/^Culture is a professional workspace/,/drowning in it\./p' docs/resources/positioning.md)
```

Expected: empty diff. If they differ, the README's opening paragraph must match `docs/resources/positioning.md` exactly — copy from positioning.md, do not paraphrase. (The two strings differ in line-wrapping in the file source because positioning.md uses different line breaks; the diff above is illustrative — if it's noisy from line-wrap differences, do a `tr -d '\n'` on both sides:)

```bash
diff <(sed -n '/^Culture is a professional workspace/,/drowning in it\./p' README.md | tr -d '\n' | tr -s ' ') <(sed -n '/^Culture is a professional workspace/,/drowning in it\./p' docs/resources/positioning.md | tr -d '\n' | tr -s ' ')
```

Expected: empty diff once whitespace-collapsed.

- [ ] **Step 4: Stage and commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): restructure around canonical paragraph and org-map (#310)

Lead with the canonical positioning paragraph (verbatim from
docs/resources/positioning.md, per the 2026-04-18 positioning spec's
reuse rule). Add "How the AgentCulture org fits together" as the
second section with four bucket-narratives (Core runtime, Workspace
experience, Identity & secrets, Mesh resident agents). Rename the
planned `culture afi` mention to `culture contract` throughout. Point
the documentation footer at the new /ecosystem-map/ page.

EOF
)"
```

---

## Task 10: Run final verification gates

**Files:** none (verification only).

This task runs the full verification suite from the spec's "Verification" section before pushing. Any failure here means stop and fix before continuing to Task 11.

- [ ] **Step 1: Markdownlint on every changed file**

Run:
```bash
markdownlint-cli2 "README.md" "docs/culture/ecosystem-map.md" "docs/resources/registry.md"
```

Expected: `Summary: 0 error(s)`.

- [ ] **Step 2: Carry-forward grep gate from the 2026-04-18 positioning spec**

Run:
```bash
grep -rn "not one-shot\|not ephemeral\|isolated, ephemeral" docs/culture/
```

Expected: zero hits. The 2026-04-18 spec established that `docs/culture/` should not use these adversarial framings; the new ecosystem-map page must continue to honor that. If a hit shows up, rewrite the offending sentence — positive framing only.

- [ ] **Step 3: Re-run the org coverage gate**

Run:
```bash
gh repo list agentculture --visibility public --json name --jq '.[].name' | sort > /tmp/actual.txt
yq '.[].id' _data/agentculture_repos.yml | tr -d '"' | sort > /tmp/registry.txt
diff /tmp/actual.txt /tmp/registry.txt
```

Expected: empty diff. (Re-run from scratch — a new repo could have been created between Task 2 and now.)

- [ ] **Step 4: Full Jekyll build, clean tree**

Run:
```bash
rm -rf _site_culture
bundle exec jekyll build --config _config.base.yml,_config.culture.yml --destination _site_culture
```

Expected: exit 0. No Liquid errors. No "Liquid Warning" lines.

- [ ] **Step 5: Verify rendered ecosystem-map page**

Run:
```bash
ls _site_culture/ecosystem-map/index.html
grep -c '<table>' _site_culture/ecosystem-map/index.html
grep 'label-green\|label-yellow' _site_culture/ecosystem-map/index.html | wc -l
grep -E '\{[%{]' _site_culture/ecosystem-map/index.html
```

Expected:
- File exists.
- Table count is `7` (6 `repo_table` invocations + 1 `subcommand_table`).
- Some non-zero count of label-green / label-yellow spans.
- The fourth grep (raw Liquid syntax) returns **zero hits** — if any `{%` or `{{` leaks into the rendered HTML, a partial is misnamed or the include syntax is wrong. Stop and fix.

- [ ] **Step 6: Verify aux-nav rendered**

Run:
```bash
grep -o '"Ecosystem"\|/ecosystem-map/' _site_culture/index.html | head -5
```

Expected: at least one match for `/ecosystem-map/` in the homepage chrome.

- [ ] **Step 7: Run the full test suite (cheap sanity)**

Although no Python was touched, run `/run-tests` to confirm nothing else broke:
```
/run-tests
```

Expected: pass. (If anything fails, it indicates pre-existing breakage on `main` that should be raised separately, not blocking on this PR.)

---

## Task 11: Push, run /sonarclaude, create PR

**Files:** none (just git + GitHub).

- [ ] **Step 1: Confirm clean local state**

Run:
```bash
git status
git log --oneline main..HEAD
```

Expected: clean tree (everything committed across Tasks 1–9). The log shows ~5 commits: version bump, data + reference doc, Liquid partials, ecosystem-map page, config trim, README rewrite.

- [ ] **Step 2: Push the branch**

Run:
```bash
git push -u origin docs/org-ecosystem-registry-310-311
```

Expected: branch pushed; remote tracking set.

- [ ] **Step 3: Create the PR**

Run:
```bash
gh pr create --title "docs: org-wide ecosystem registry and public-facing overview" --body "$(cat <<'EOF'
## Summary

Lands issues #310 and #311 as one coherent docs PR.

- Two YAML data files (`_data/agentculture_repos.yml`, `_data/culture_subcommands.yml`) become the canonical source of truth for the AgentCulture ecosystem.
- New page `docs/culture/ecosystem-map.md` renders both via narrative + auto-generated tables (Liquid partials in `_includes/`).
- README restructured around the canonical positioning paragraph from `docs/resources/positioning.md` plus a 4-paragraph org-map section.
- Aux-nav and footer in `_config.culture.yml` trimmed: drop Agent First Interop / Citation CLI from chrome, add Ecosystem-map link.
- New `docs/resources/registry.md` documents the schema and names steward + ghafi as registry hygiene owners. No CI gate.
- Renames the planned `culture afi` subcommand to `culture contract` everywhere it appears.

## Closes

- Closes #310
- Closes #311

## Test plan

- [ ] `bundle exec jekyll build --config _config.base.yml,_config.culture.yml --destination _site_culture` exits 0
- [ ] `/ecosystem-map/` renders with 7 tables (6 category invocations + 1 subcommand table); status badges colored correctly
- [ ] Aux-nav top-right: Ecosystem / AgentIRC / Agent Experience / GitHub
- [ ] Footer shows the trimmed copy with Ecosystem-map link
- [ ] `markdownlint-cli2` clean on README, ecosystem-map, registry doc
- [ ] Carry-forward grep gate (`grep -rn "not one-shot\|not ephemeral\|isolated, ephemeral" docs/culture/`) returns zero hits
- [ ] Org coverage diff (`gh repo list agentculture --visibility public` vs `_data/agentculture_repos.yml`) is empty
- [ ] No raw Liquid syntax leaks into rendered HTML

## Notes

- Spec: `docs/superpowers/specs/2026-05-05-org-ecosystem-registry-design.md`
- No code, protocol, CLI, or backend harness surface touched. Patch version bump per CLAUDE.md.
- `ghafi` rename is in flight — registry entry carries `caveat: "Rename in flight."`. Follow-up commit updates the name once settled.
- `culture contract` is a new public name; the wrapper is `planned`, not shipped. We're not promising anything we haven't built.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL printed. Note it for the next steps.

- [ ] **Step 4: Wait for automated checks, then run /sonarclaude**

Wait for `gh pr checks` to settle. Then run `/sonarclaude` per CLAUDE.md ("Before declaring the PR ready, check SonarCloud for the branch via the `/sonarclaude` skill"). SonarCloud findings do not always arrive as inline PR comments, so an all-green `gh pr checks` is not sufficient.

- [ ] **Step 5: Address review feedback via /cicd**

When Qodo / Copilot / human reviewers leave comments, use the `/cicd` skill to triage, fix, push, reply, and resolve threads. Apply the **3rd-party PR skepticism** memory: external review feedback gets extra scrutiny, never auto-applied.

- [ ] **Step 6: Merge once approved**

Per CLAUDE.md, merge after:
- All automated checks pass
- All review threads resolved
- SonarCloud clean
- The maintainer (you) signs off

---

## Self-review checklist (run before claiming "plan complete")

**Spec coverage** — every locked decision in the spec maps to a task:

| Spec decision | Task |
|---|---|
| Combined PR landing both #310 and #311 | All tasks; one branch, one PR |
| Six categories (core-runtime, workspace-experience, identity-secrets, resident-culture, resident-domain, org-site) | Task 2 (schema header); Task 5 (Liquid case) |
| Four maturity buckets (placeholder, experimental, usable, deprecated) | Task 2 (schema header); Task 5 (Liquid case) |
| Registry at `_data/agentculture_repos.yml` | Task 2 |
| `_data/culture_subcommands.yml` second registry | Task 3 |
| `culture afi` → `culture contract` rename | Task 3 (registry entry); Task 7 (ecosystem-map narrative); Task 9 (README) |
| `shushu` and `zehut` distinct entries with explicit roles | Task 2 |
| Hand-written narrative + auto-generated tables | Task 7 |
| `culture contract` lands as `planned` in this PR | Task 3 (`status: planned`) |
| afi-cli usable / zehut experimental / shushu experimental | Task 2 |
| `ghafi` named as-is with rename caveat | Task 2 (`caveat: "Rename in flight."`); Task 9 (README links to ghafi by name) |
| Resident-culture vs resident-domain split | Task 2 (categories); Task 7 (H3 sub-sections) |
| Aux-nav: Ecosystem / AgentIRC / Agent Experience / GitHub | Task 8 |
| Footer: trimmed to Ecosystem-map link | Task 8 |
| `docs/resources/registry.md` reference doc | Task 4 |
| Steward + ghafi own registry hygiene; no CI gate | Task 2 (schema header); Task 4 (registry doc) |
| "Current state at a glance" closing section, not separate /roadmap/ page | Task 7 |
| Patch version bump | Task 1 |
| No `doc-test-alignment` invocation | (omitted by design — noted in spec) |
| No pre-push code review | (omitted by design — noted in spec) |
| SonarCloud check via /sonarclaude before ready | Task 11 step 4 |
| Carry-forward grep gate from 2026-04-18 spec | Task 10 step 2 |
| Org coverage gate at PR time | Task 2 step 3; Task 10 step 3 |

**Placeholder scan:** no "TBD", "TODO", "implement later", "fill in details". Every step has the actual content. ✓

**Type/name consistency:**

- Category names match in: spec, schema header (Task 2), Liquid case (Task 5), `include category=` calls (Task 7).
- Maturity values match in: spec, schema header (Task 2), Liquid case (Task 5).
- Status values match in: spec, schema header (Task 3), Liquid `if` (Task 6).
- 14 repo IDs match across: spec, registry (Task 2), org coverage gate (Task 2 step 3 + Task 10 step 3), README links (Task 9).
- 9 subcommand names match across: spec, registry (Task 3).
- File paths match across all references: `_data/agentculture_repos.yml`, `_data/culture_subcommands.yml`, `_includes/repo_table.html`, `_includes/subcommand_table.html`, `docs/culture/ecosystem-map.md`, `docs/resources/registry.md`, `_config.culture.yml`, `README.md`. ✓

Plan complete.
