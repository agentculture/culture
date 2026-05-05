# `culture console` — passthrough wrapper for `irc-lens`

**Date:** 2026-05-05
**Status:** Approved (brainstorming complete)

## Context

`culture mesh console <server_name>` today launches a Textual TUI (`culture/console/`) that connects to an AgentIRC server as an IRC client. We recently shipped `irc-lens` ([github.com/agentculture/irc-lens](https://github.com/agentculture/irc-lens), on PyPI as `irc-lens` v0.4.2) — a localhost aiohttp + HTMX + SSE web app implementing the same console as a browser-driveable surface with stable `data-testid` selectors and an AFI-rubric CLI (`learn` / `explain` / `overview` / `serve`).

Goal: replace `culture mesh console` with a top-level `culture console` group that wraps `irc-lens` using the established passthrough pattern (`culture devex` → `agex-cli`, `culture afi` → `afi-cli`, agentirc-forwarded verbs in `culture chat`). This standardizes how culture composes sibling CLIs and unlocks Playwright-driven console testing — something the Textual TUI made awkward.

## Design invariants

1. **In-process passthrough**, not subprocess. Matches every other extension. `irc_lens.cli:main(argv)` is invoked directly via `culture.cli._passthrough.run`. *(Subprocess-vs-in-process tradeoff captured as a deferred GitHub issue, see "Deferred decision" below.)*
2. **Pure passthrough by default** — argv is forwarded verbatim to `irc_lens.cli.main`.
3. **One culture-owned shim:** `_resolve_argv()`. If the first non-flag positional is not an irc-lens subcommand, treat it as a culture server name and rewrite argv to `serve --host <h> --port <p> --nick <n> [rest]`. Preserves the old `culture mesh console <server>` ergonomic.
4. **Universal verbs** (`explain` / `overview` / `learn`) wired through `_passthrough.register_topic("console", …)` exactly like `devex`.
5. **Deprecation discipline:** `culture mesh console` stays for one minor cycle as a stderr-warning alias forwarding to `culture console`. The underlying `culture/console/` Textual package is left dormant (untouched code, deprecation note in `__init__`) until 10.0.

## Architecture

```
culture console [args...]
        │
        ▼
culture/cli/console.py ── _resolve_argv(argv)
        │                     │
        │                     ├─ first token in {learn,explain,overview,serve,cli}
        │                     │   or starts with '-' → return argv unchanged
        │                     │
        │                     ├─ argv empty → resolve default culture server,
        │                     │   build ['serve','--host',h,'--port',p,'--nick',n]
        │                     │
        │                     └─ else → treat first token as culture server name,
        │                       resolve to (host,port,nick), build the same
        │                       serve argv with the rest of the original tail
        ▼
_passthrough.run(_entry, resolved_argv)
        │
        ▼
irc_lens.cli.main(resolved_argv)   # in-process; raises SystemExit
```

irc-lens top-level subcommand set (verified by running `uv run irc-lens --help`): `{learn, explain, overview, serve, cli}`.

`serve` flags: `--host` (default `127.0.0.1`), `--port` (default `6667`), `--nick` (REQUIRED), `--web-port` (default `8765`), `--bind`, `--icon`, `--open`, `--seed`, `--log-json`.

## File changes

| File | Change |
|---|---|
| `culture/cli/console.py` | **NEW.** ~80 lines, mirrors `culture/cli/devex.py`. Contains `_entry`, `_resolve_argv`, `_build_serve_argv`, `register`, `dispatch`, plus the `_passthrough.register_topic("console", …)` call. |
| `culture/cli/shared/server.py` (new helper, or relocate within shared) | Move `_resolve_server` and `_resolve_console_nick` out of `culture/cli/mesh.py` so both `console.py` and the legacy `mesh.py` deprecation alias can reuse them without circular imports. |
| `culture/cli/__init__.py` | Add `console` to the `from culture.cli import …` line and append to `GROUPS`. Update the docstring noun-table. |
| `culture/cli/mesh.py` | `_cmd_console` becomes a stderr-deprecation alias that calls `culture.cli.console.dispatch_resolved_argv(args.server_name)`. Remove the Textual import. The mesh subparser keeps the `console` entry but its help text adds `(deprecated; use 'culture console')`. |
| `culture/console/__init__.py` | Add module-level `warnings.warn(...)` and a docstring note. Code otherwise untouched. |
| `pyproject.toml` | Add `"irc-lens>=0.4.2"` to `[project.dependencies]`. |
| `culture/skills/culture/SKILL.md` (lines 344, 367) | Replace `culture mesh console` with `culture console` in the verb table; add deprecation footnote. |
| `culture/learn_prompt.py` (line 201) | Same replacement. |
| `culture/cli/CLAUDE.md` (line 17) | Update the architecture comment block. |
| `CHANGELOG.md` | Note: `culture console` added (passthrough to irc-lens). `culture mesh console` deprecated. |

## `_resolve_argv` — concrete contract

```python
_IRC_LENS_VERBS = frozenset({"learn", "explain", "overview", "serve", "cli"})

def _resolve_argv(argv: list[str]) -> list[str]:
    """Translate `culture console`-flavoured argv into `irc-lens`-flavoured argv.

    - Empty argv → resolve default culture server, build a `serve` invocation.
    - First token is an irc-lens verb or starts with `-` → pure passthrough.
    - Otherwise → treat first token as a culture server name; rewrite to
      ['serve', '--host', h, '--port', p, '--nick', n, *rest].
    """
    if not argv:
        return _build_serve_argv(server_name=None, rest=[])
    head = argv[0]
    if head in _IRC_LENS_VERBS or head.startswith("-"):
        return list(argv)
    return _build_serve_argv(server_name=head, rest=list(argv[1:]))


def _build_serve_argv(server_name: str | None, rest: list[str]) -> list[str]:
    result = _resolve_server(server_name)  # imported from culture.cli.shared.server
    if result is None:
        raise SystemExit("No culture servers running. Start one with: culture chat start")
    name, port = result
    nick = f"{name}-{_resolve_console_nick()}"
    return ["serve", "--host", "127.0.0.1", "--port", str(port),
            "--nick", nick, *rest]
```

The host is hardcoded to `127.0.0.1` to match the existing `_cmd_console` behavior; users who need a remote AgentIRC bypass the shim with `culture console serve --host <remote> --port <p> --nick <n>` (pure passthrough path).

## Error handling

- **AgentIRC unreachable:** `irc-lens serve` already emits `error: cannot reach AgentIRC at <h>:<p>` + `hint: ...` and exits 1. Do not double-wrap.
- **No culture servers running:** shim raises `SystemExit("No culture servers running. Start one with: culture chat start")` — copies the existing `_cmd_console` message.
- **`irc-lens` not importable** (shouldn't happen since it's a declared dep, but matches `devex`): `_entry` catches `ImportError` and exits 2 with `"irc-lens is not installed: <exc>"`.
- **Conflicting `--nick`** (`culture console <server> --nick override`): the shim-generated `--nick` comes before user `--nick` in argv; argparse's last-wins semantics mean the user override takes precedence.

## Tests

| Test file | Coverage |
|---|---|
| `tests/test_console_argv.py` | `_resolve_argv` table: empty, server-name, irc-lens verb, leading flag, server+rest. Mock `_resolve_server` to return both `None` and `(name, port)`. |
| `tests/test_console_passthrough.py` | `culture explain console` / `overview console` / `learn console` capture irc-lens output via `_passthrough.capture`. Mirrors `tests/test_devex_*.py` patterns. |
| `tests/test_mesh_console_deprecation.py` | `culture mesh console <server>` → stderr contains `"deprecated"`, return code matches `culture console <server>`. |
| `tests/test_console_playwright.py` (marker `@pytest.mark.playwright`) | Boot AgentIRC fixture, run `culture console <server>` in a thread, navigate Chromium to `http://127.0.0.1:8765/`, drive `/help`, assert `data-testid="view-indicator"` flips to `data-view="help"`. Reuses irc-lens's documented testid contract. |

## Verification plan (manual, post-implementation)

1. `culture chat start --name spark`
2. `culture console spark --open` → browser launches, lens loads against the local AgentIRC.
3. Drive `/help` via Chrome MCP `form_input` on `[data-testid="chat-input"]`; confirm `[data-testid="view-indicator"][data-view="help"]`.
4. `culture explain console` / `culture overview console` / `culture learn console` each produce irc-lens's documented output.
5. `culture mesh console spark` prints `culture mesh console is deprecated; use 'culture console'` on stderr, then identical behavior.
6. `culture console serve --host 127.0.0.1 --port 6667 --nick lens` (pure passthrough path) — bypasses the shim, lands in irc-lens unchanged.

## Deferred decision

Tracked as a GitHub issue on `agentculture/culture`: "`culture console`: subprocess vs in-process passthrough for `irc-lens`". Default for v9.x is in-process; revisit only on real-world breakage.

## Out of scope

- Any change to `irc-lens` itself.
- Auth on `--bind 0.0.0.0` (irc-lens's existing warning stands).
- Removing `culture/console/` Textual code (deferred to 10.0).
- Updating culture's docs site beyond the SKILL.md/learn_prompt edits noted above.
