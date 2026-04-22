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

## For namespace authors

A new namespace plugs into the universal verbs by calling
`introspect.register_topic(...)` at module import time:

```python
# culture/cli/mycmd.py
from culture.cli import introspect


def _explain(_topic):
    return "markdown describing mycmd ...\n", 0


def _overview(_topic):
    return "one-line summary of mycmd\n", 0


def _learn(_topic):
    return "agent onboarding prompt for mycmd\n", 0


introspect.register_topic(
    "mycmd",
    explain=_explain,
    overview=_overview,
    learn=_learn,
)
```

Each handler has signature `Handler = Callable[[str | None], tuple[str, int]]` —
it receives the topic (may be `None`) and returns `(stdout, exit_code)`.

### CLI group protocol: `NAME` vs `NAMES`

A normal group module (e.g. `server`, `agent`) exports a singular
`NAME: str` — the subcommand noun. A group that owns multiple top-level
verbs (e.g. `introspect`, which owns all three of `explain`/`overview`/
`learn`) exports a plural `NAMES: frozenset[str]` instead. The dispatch
loop in `culture/cli/__init__.py` honors both — `NAMES` takes priority
with `{NAME}` as the fallback — so most groups only need `NAME`.
