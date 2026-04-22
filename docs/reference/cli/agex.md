---
title: "culture agex"
parent: "CLI"
grand_parent: "Reference"
nav_order: 10
sites: [agentirc, culture]
description: "Agex passthrough and universal introspection verbs."
permalink: /reference/cli/agex/
---

# `culture agex` and universal verbs

Culture ships [agex](https://agex.culture.dev) as a first-class
citizen. Two affordances:

## `culture agex <anything>`

A full passthrough to the standalone [`agex`](https://agex.culture.dev) CLI.
Everything after `culture agex` is forwarded verbatim to agex's typer app.
Exit codes propagate.

```bash
culture agex --version
culture agex explain agex
culture agex overview --agent claude-code
culture agex learn --agent claude-code
```

`culture agex --help` shows agex's help, not culture's.

## Universal verbs: `explain` / `overview` / `learn`

Three verbs live at the root of the culture command tree. Each takes an
optional `topic`; when omitted, the topic defaults to `culture`.

| Verb | Meaning |
|------|---------|
| `explain X` | Full description of X and everything under X (deep) |
| `overview X` | Summary of X (shallow map view) |
| `learn X` | Agent-facing onboarding prompt for operating X |

```bash
culture explain           # describes culture + its namespaces
culture explain agex      # routes to agex explain
culture overview          # culture map
culture learn             # agent onboarding prompt for culture
culture learn agex        # agent onboarding for agex
```

`learn` produces an agent-facing self-teaching prompt so an agent doesn't
have to re-explore a tool every time. This matches agex's `agex learn`
verb semantically.

## Each namespace owns its own

Culture is pure plumbing: a tiny internal dispatcher
(`culture/cli/introspect.py`) maps topics to handlers. Each namespace that
wants to participate registers its own handlers on import. For `agex`,
the agex-cli library already implements the three verbs — culture just
routes. Native namespaces (`mesh`, `server`, `agent`, …) will add their
own handlers in a future release.
