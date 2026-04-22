# Agex integration design

**Date:** 2026-04-22
**Status:** Approved (brainstorming complete, awaiting implementation plan)
**Author:** Ori Nachum

## Context

Culture aims to become "the framework of agreements that makes agent behavior
portable, inspectable, and effective." Two sibling projects extend that
thesis:

- **agex** (`../agex`, published as `agex-cli`) — the tooling that helps
  agents stay effective: introspection, self-teaching, overviews.
- **afi** (`../afi-cli`) — the Agent First Interface: standards for how
  agents design what they work on (CLIs, MCPs, sites, harnesses).

The user wants culture to host both as first-class citizens while they
continue shipping standalone on PyPI. **This spec covers only agex.** Afi
gets the same recipe in a follow-up spec once it stabilizes.

## Approved decisions

1. **Import mechanism — PyPI library dependency.** Culture depends on
   `agex-cli` like any normal Python package. No git subtree, submodule,
   vendoring, workspace path-deps, or editable dev loop. Pure consumer
   relationship. Keeps agex's shipping cadence and CI independent.

2. **CLI surface — passthrough plus universal verbs.** Two overlapping
   affordances:
   - `culture agex <anything>` is a **full passthrough** to agex.
   - Three verbs are **universal** — available at every level of the culture
     command tree, each scoped to that node and its descendants. This spec
     wires them at the root (`culture explain|overview|learn`) and at the
     `agex` node (via passthrough). Native namespaces come in a follow-up.

3. **Tripartite semantics.** The three universal verbs are distinct in
   purpose, not three flavors of the same thing:
   - `explain X` — full description of X and everything under X (deep).
   - `overview X` — summary of X (shallow map view).
   - `learn X` — agent-facing onboarding / self-teaching prompt so an agent
     can operate X effectively without re-exploring it every time. This is
     exactly what `culture agent learn` does today. Aligned with agex's
     existing `agex learn` verb — no semantic divergence.

4. **Each namespace owns its own introspection.** Culture is pure plumbing.
   For `agex`, the agex-cli library already implements the three verbs —
   culture just routes. For future native namespaces (`mesh`, `server`, …)
   the owning module will implement its own handlers. Nothing in agex or afi
   gets modified by this work.

5. **Day-one scope is narrow.** Wire agex + root introspection only. Native
   namespaces are a **separate follow-up spec**. Collisions inside culture
   are tracked as GitHub issues, not solved here.

## Architecture

### Dependency

`pyproject.toml` gains `"agex-cli>=0.13,<1.0"` in `[project].dependencies`.
`uv.lock` regenerates via `uv sync`. Not an optional extra — agex is core
enough to always be present. Transitive additions: `typer`, `jinja2`,
`tomlkit`, `portalocker` (`pyyaml` is already shared).

Pin rationale: `>=0.13` accepts forward-compatible releases; `<1.0` guards
against the inevitable 1.0 breaking-change event. `uv.lock` is the exact
reproducibility mechanism. Revisit the ceiling when agex ships 1.0.

### Dispatcher contract

A tiny internal module (`culture/cli/introspect.py`) owns the universal
verbs. Each participating namespace registers handlers at import time.

```python
# culture/cli/introspect.py
from typing import Callable

Handler = Callable[[str | None], tuple[str, int]]  # (stdout, exit_code)

_explain: dict[str, Handler] = {}
_overview: dict[str, Handler] = {}
_learn:    dict[str, Handler] = {}

def register(topic: str, *,
             explain: Handler | None = None,
             overview: Handler | None = None,
             learn: Handler | None = None) -> None: ...

def explain(topic: str | None)  -> tuple[str, int]: ...
def overview(topic: str | None) -> tuple[str, int]: ...
def learn(topic: str | None)    -> tuple[str, int]: ...
```

Day-one registrants: `"culture"` (root, default when no topic) and `"agex"`.
Unknown topic → exit 1 with a helpful available-topics list.

### Agex adapter

`culture/cli/agex.py` does two things:

1. Registers the `culture agex` subparser with `nargs=argparse.REMAINDER`
   and `prefix_chars=chr(0)` so everything after `culture agex` flows
   verbatim — including `--flags` that argparse would otherwise intercept
   (e.g. `--version`, `--help`). On dispatch:
   ```python
   from agent_experience.cli import app
   try:
       app(args=rest)
   except SystemExit as e:
       return 0 if e.code is None else (e.code if isinstance(e.code, int) else 1)
   ```
   `app()` runs with typer's default `standalone_mode=True` so typer's own
   `--help`/`--version`/`typer.Exit` handling works unchanged; typer calls
   `sys.exit` on completion, and culture translates the `SystemExit` back
   into an exit code. The module is imported lazily inside the handler to
   keep culture's startup path free of agex imports until needed.

2. Registers three handlers with `introspect.register_topic("agex", ...)`.
   Each handler calls `_run_agex([verb, ...])` with stdout (and stderr)
   redirected to an `io.StringIO` buffer so the universal-verb contract's
   `(stdout, exit_code)` tuple captures everything agex emits. Exit codes
   propagate verbatim; no wrapping.

### Root verbs

`culture/cli/__init__.py` gains three new top-level subparsers (`explain`,
`overview`, `learn`), each taking an optional positional `topic`. They
delegate to `introspect.<verb>(topic)`.

- `culture explain` (or `culture explain culture`) → culture's own
  self-description + list of namespaces, marking those without registered
  handlers as `(coming soon)`.
- `culture overview` → one-screen map of culture.
- `culture learn` → agent-facing onboarding prompt for culture-as-a-whole,
  produced by reusing the existing engine in `culture/learn_prompt.py`
  (which drives `culture agent learn` today) with its scope widened from
  "the agent entry" to "culture root."

## CLI surface (post-change)

```
culture agex <anything...>     # full passthrough
culture agex explain <topic>   # = agex explain <topic>
culture agex overview          # = agex overview
culture agex learn             # = agex learn

culture explain [topic]        # topic defaults to "culture"
culture overview [topic]
culture learn [topic]
```

## Data flow

**`culture agex <rest...>`:**
1. argparse captures `rest` via `nargs=argparse.REMAINDER` on a subparser
   configured with `prefix_chars=chr(0)` (so `--flags` don't get
   intercepted by argparse before they reach typer).
2. Handler lazy-imports `agent_experience.cli`.
3. Calls `app(args=rest)` (typer's default `standalone_mode=True`).
4. Typer's `sys.exit(code)` is caught as `SystemExit`; culture translates
   `.code` back to the process exit code.

**`culture explain <topic>`:**
1. argparse passes `topic` to `introspect.explain(topic)`.
2. Registry lookup finds the handler; missing → exit 1 with available list.
3. Handler returns `(stdout, exit_code)`; culture prints and exits.

**`culture explain` (no topic):**
1. `introspect.explain(None)` defaults to the `"culture"` handler.
2. Emits short markdown: culture's positioning statement + namespaces, with
   `(coming soon)` on any that haven't registered.

## Error handling

Three distinct failure modes, handled explicitly:

- **`agex-cli` not importable** → print an error to stderr naming the
  missing dep and return exit code 2. Shouldn't occur in normal installs
  (declared dep); matters for broken editable/dev environments.
- **Unknown topic** → exit 1; stderr: `unknown topic '{topic}'; available:
  {list}`.
- **Agex internal failure** → agex's exit code returned verbatim. No
  wrapping, no retry, no translation.

## Versioning

- Culture: `/version-bump minor`. New CLI surface is a minor bump by the
  project's convention.
- `agex-cli` pin: `>=0.13,<1.0`. `uv.lock` pins exact versions for
  reproducibility. The `<1.0` ceiling will be revisited when agex hits 1.0.

## Testing

Six smoke tests in `tests/test_cli_agex.py`, run via `/run-tests`:

1. `culture agex --version` → prints agex's `__version__`, exit 0.
2. `culture agex explain agex` → non-empty stdout, exit 0.
3. `culture explain` (no args) → stdout contains "culture" and lists the
   registered namespaces, exit 0.
4. `culture overview` (no args) → non-empty stdout, exit 0.
5. `culture learn` (no args) → non-empty stdout from the `learn_prompt.py`
   engine scoped to culture root, exit 0.
6. `culture explain unknown-topic-xyz` → exit 1, stderr mentions the topic.

Pure CLI tests — no server fixtures. pytest-xdist-safe.

## Documentation

- **New** `docs/cli/agex.md` — describes the passthrough, the universal
  verb contract, the tripartite semantics, and the "each namespace owns its
  own" principle so future native implementations have a reference.
- **Updated** `docs/cli/index.md` (or the relevant landing page) — lists
  the three new root verbs.
- No `protocol/extensions/` entry; nothing here is an IRC verb.
- Run `doc-test-alignment` subagent before the first push, per culture's
  CLAUDE.md.

## Explicitly out of scope

- Native-namespace universal verbs (`culture mesh explain`, `culture server
  overview`, etc.) — separate follow-up spec.
- Afi integration — same recipe, filed when afi-cli stabilizes.
- Any modification to `../agex` or `../afi-cli` — upstream-only; filed as
  issues on those repos if needed.

## Follow-up issues to file (not blocking this work)

1. `OriNachum/culture`: native-namespace universal verbs — each of
   `mesh`/`server`/`agent`/`bot`/`channel`/`skills` owns its own
   `explain`/`overview`/`learn`. `culture agent learn` is already correct
   under the universal contract and becomes the reference.
2. `OriNachum/culture`: afi integration spec — apply the same recipe once
   `afi-cli` stabilizes (PyPI dep + passthrough + dispatcher registration).

## Verification (end-to-end)

After implementation lands:

1. `uv sync` resolves cleanly.
2. `/run-tests` — all existing tests still pass, plus the six new ones.
3. Manual: `uv run culture agex --version` prints agex's version.
4. Manual: `uv run culture agex explain agex` produces agex's explanation.
5. Manual: `uv run culture explain` prints culture's self-description and
   namespace list.
6. Manual: `uv run culture agex <real-verb>` behaves identically to
   standalone `agex <same-verb>`.
7. `doc-test-alignment` subagent reports no missing docs for new CLI
   surface.

## Critical files reference

- `culture/cli/__init__.py` — argparse wiring; where new subparsers attach.
- `culture/cli/mesh.py:16` — canonical example of a subcommand module using
  `subparsers.add_parser`.
- `culture/learn_prompt.py` — existing learn-prompt engine; reused at root.
- `pyproject.toml` — `[project].dependencies` block gains `agex-cli`.
- `../agex/src/agent_experience/cli.py:15` — agex's typer `app`; embedded
  via `app(args=[...], standalone_mode=False)`.
