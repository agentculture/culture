# `culture afi` and universal verbs

Culture ships the **Agent First Interface** (afi) as a first-class
namespace. `culture afi` is powered by the standalone
[`afi-cli`](https://github.com/agentculture/afi-cli) (Python package
`afi`). afi generates and audits agent-first CLIs, MCP servers, and
HTTP sites — and models, on itself, every pattern it enforces on
others.

Two affordances:

## `culture afi <anything>`

A full passthrough to the standalone `afi` CLI. Everything after
`culture afi` is forwarded verbatim to the argparse app. Exit codes
propagate.

```bash
culture afi --version
culture afi explain
culture afi learn
culture afi cli verify .
```

`culture afi --help` shows the underlying afi argparse help, not
culture's.

afi itself is argparse-based and exposes a library-grade
`afi.cli.main(argv) -> int` entry point. Culture embeds it in-process
via the shared passthrough helper at `culture/cli/_passthrough.py`;
stdout and stderr from afi are captured cleanly and exit codes are
preserved.

## Universal verbs: `explain` / `overview` / `learn`

Three verbs live at the root of the culture command tree. Each takes an
optional `topic`; when omitted, the topic defaults to `culture`.

| Verb | Meaning |
|------|---------|
| `explain afi` | Full markdown description of afi (routes to `afi explain`) |
| `overview afi` | Shallow map of afi (routes to `afi overview`) |
| `learn afi` | Agent-facing onboarding prompt for operating afi |

```bash
culture explain afi    # markdown docs for afi root
culture overview afi   # shallow map
culture learn afi      # agent onboarding prompt for afi
```

All three verbs are live from culture 8.1.0 + `afi-cli` 0.3.0 onward;
the `overview` verb and its rubric bundle were added in
[agentculture/afi-cli#5](https://github.com/agentculture/afi-cli/issues/5).

## Each namespace owns its own

Culture is pure plumbing — the dispatcher at
`culture/cli/introspect.py` maps topics to handlers, and the shared
embedder at `culture/cli/_passthrough.py` provides the in-process
call, output capture, and `SystemExit` translation. `afi.py` is a thin
adapter: it supplies the package-specific entry callable and one call
to `_passthrough.register_topic`. Adding another namespace that wraps
a sibling CLI follows the same recipe.

### Sibling namespaces

| Namespace | Backing CLI | State |
|-----------|-------------|-------|
| `culture devex` | `agex-cli` (`agent_experience`) | Registered |
| `culture afi` | `afi-cli` (`afi`) | Registered |
| `culture identity` | future `zehut-cli` | Coming soon |
| `culture secret` | future `shushu-cli` | Coming soon |

Culture uses English for its first-class nouns; the underlying CLIs
keep their brand names. `culture explain` is the always-current source
of truth for which namespaces are ready vs. coming soon.

## See also

- [`culture devex` and universal verbs](./devex/) — the parallel
  passthrough for the developer-experience CLI, and the full handler
  protocol for namespace authors.
- [afi-cli on GitHub](https://github.com/agentculture/afi-cli) — the
  standalone tool, its rubric, and the spec for what makes a CLI
  agent-first.
