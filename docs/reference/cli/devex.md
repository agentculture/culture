# `culture devex` and universal verbs

Culture ships a developer-experience namespace as a first-class citizen.
`culture devex` is powered by the standalone
[`agex-cli`](https://culture.dev/devex) library (`agent_experience`
Python package). The command name differs for familiarity with the
developer-experience vocabulary; the underlying tool is the same. Two
affordances:

## `culture devex <anything>`

A full passthrough to the standalone `agex` CLI. Everything after
`culture devex` is forwarded verbatim to the typer app. Exit codes
propagate.

```bash
culture devex --version
culture devex explain agex
culture devex overview --agent claude-code
culture devex learn --agent claude-code
```

`culture devex --help` shows the underlying CLI's help, not culture's.

The passthrough forwards the argument vector verbatim, so the library's
own topic names (for example `agex` as an explain topic) are what you
pass on the devex subcommand. When you reach for culture's universal
verbs, use the culture-facing names (`devex`, not `agex`).

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
culture explain devex     # routes to devex's explain handler
culture overview          # culture map
culture learn             # agent onboarding prompt for culture
culture learn devex       # agent onboarding for devex
```

`learn` produces an agent-facing self-teaching prompt so an agent doesn't
have to re-explore a tool every time. This matches the underlying
`agex learn` verb semantically.

## Each namespace owns its own

Culture is pure plumbing: a tiny internal dispatcher
(`culture/cli/introspect.py`) maps topics to handlers. Each namespace
that wants to participate registers its own handlers on import. For
`devex`, the `agex-cli` library already implements the three verbs —
culture just routes.

### Sibling and upcoming namespaces

[`culture afi`](./afi/) is registered as of culture 8.1, riding the
same passthrough plumbing as devex. Two more namespaces are listed as
`(coming soon)` in `culture explain` output and will gain full
handlers in future releases:

- **`culture identity`** — Identity management across the mesh. Will
  wrap a standalone `zehut-cli` (Hebrew: "identity").
- **`culture secret`** — Secret management. Will wrap a standalone
  `shushu-cli`.

Culture uses English for its first-class nouns; the underlying CLIs keep
their brand names. `culture explain` is the always-current source of
truth for which namespaces are ready vs. coming soon.

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
