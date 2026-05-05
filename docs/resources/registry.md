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
