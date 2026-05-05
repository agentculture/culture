# `culture console` — irc-lens passthrough — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `culture mesh console` (Textual TUI) with a top-level `culture console` group that wraps `irc-lens` via the in-process passthrough pattern, with a one-cycle deprecation alias for the old command.

**Architecture:** Mirror `culture/cli/afi.py` byte-for-byte (irc-lens already exposes `main(argv) -> int`, same as afi). Add one culture-owned shim `_resolve_argv()` that translates `culture console <server_name>` into `["serve", "--host", h, "--port", p, "--nick", n]` before passthrough; everything else flows through to `irc_lens.cli.main(argv)` unchanged. Universal verbs (`explain`/`overview`/`learn`) wired via `_passthrough.register_topic`.

**Tech Stack:** Python 3.11+, argparse, irc-lens >=0.4.2, pytest, pytest-playwright (opt-in marker).

**Spec:** `docs/superpowers/specs/2026-05-05-culture-console-design.md`
**Tracking issue:** [agentculture/culture#322](https://github.com/agentculture/culture/issues/322) (subprocess-vs-in-process deferred decision)

---

## File Plan

| File | Responsibility |
|---|---|
| `culture/cli/shared/console_helpers.py` | **NEW.** Houses `resolve_server()` and `resolve_console_nick()` extracted from `mesh.py`. Pure functions, no argparse dependency. |
| `culture/cli/console.py` | **NEW.** Top-level `console` group. Mirrors `afi.py`. Adds `_resolve_argv()` shim + `dispatch_resolved_argv()` helper used by both the new group and the legacy mesh alias. |
| `culture/cli/__init__.py` | Register the new group in `GROUPS`; update the docstring noun-table. |
| `culture/cli/mesh.py` | `_cmd_console` becomes a stderr-deprecation alias forwarding to `culture.cli.console.dispatch_resolved_argv`. Remove Textual import. Tag the subparser help. |
| `culture/console/__init__.py` | Module-level `DeprecationWarning` + docstring note. Code untouched. |
| `pyproject.toml` | Add `"irc-lens>=0.4.2"` to dependencies. |
| `culture/skills/culture/SKILL.md` (lines 344, 367) | Replace `culture mesh console` with `culture console`. |
| `culture/learn_prompt.py` (line 201) | Same replacement. |
| `culture/cli/CLAUDE.md` (line 17) | Update architecture comment. |
| `CHANGELOG.md` | New entry under unreleased / next-version section. |
| `tests/test_cli_console_argv.py` | **NEW.** Unit table for `_resolve_argv` and `_build_serve_argv`. |
| `tests/test_cli_console.py` | **NEW.** Subprocess-driven smoke tests mirroring `test_cli_devex.py` / `test_cli_afi.py`. |
| `tests/test_cli_console_deprecation.py` | **NEW.** Verifies `culture mesh console` emits deprecation warning + same exit code. |
| `tests/test_cli_console_playwright.py` | **NEW.** Opt-in browser e2e (gated by `@pytest.mark.playwright`). |

---

## Task 1: Extract helpers to `culture/cli/shared/console_helpers.py`

**Files:**
- Create: `culture/cli/shared/console_helpers.py`
- Modify: `culture/cli/mesh.py:204-251` (remove `_resolve_server` and `_resolve_console_nick` definitions; import from new module instead)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_shared_console_helpers.py`:

```python
"""Tests for the shared console helpers extracted from culture.cli.mesh."""

from __future__ import annotations

from unittest.mock import patch

from culture.cli.shared.console_helpers import resolve_console_nick, resolve_server


def test_resolve_server_returns_none_when_no_servers():
    with patch("culture.cli.shared.console_helpers.list_servers", return_value=[]):
        assert resolve_server(None) is None


def test_resolve_server_named_with_known_port():
    with patch("culture.cli.shared.console_helpers.read_port", return_value=7000):
        assert resolve_server("spark") == ("spark", 7000)


def test_resolve_server_named_falls_back_to_default_port():
    with patch("culture.cli.shared.console_helpers.read_port", return_value=None):
        assert resolve_server("spark") == ("spark", 6667)


def test_resolve_server_single_server_picks_it():
    with patch(
        "culture.cli.shared.console_helpers.list_servers",
        return_value=[{"name": "only", "port": 6700}],
    ):
        assert resolve_server(None) == ("only", 6700)


def test_resolve_server_default_match_wins():
    servers = [
        {"name": "a", "port": 6700},
        {"name": "b", "port": 6701},
    ]
    with (
        patch("culture.cli.shared.console_helpers.list_servers", return_value=servers),
        patch("culture.cli.shared.console_helpers.read_default_server", return_value="b"),
    ):
        assert resolve_server(None) == ("b", 6701)


def test_resolve_console_nick_uses_user_env_when_git_fails():
    fake_run = type("R", (), {"returncode": 1, "stdout": ""})()
    with (
        patch("culture.cli.shared.console_helpers.subprocess.run", return_value=fake_run),
        patch.dict("os.environ", {"USER": "ada"}, clear=False),
    ):
        assert resolve_console_nick() == "ada"


def test_resolve_console_nick_sanitizes_git_name():
    fake_run = type("R", (), {"returncode": 0, "stdout": "Ada Lovelace!\n"})()
    with patch("culture.cli.shared.console_helpers.subprocess.run", return_value=fake_run):
        assert resolve_console_nick() == "ada-lovelace"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_shared_console_helpers.py -x 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'culture.cli.shared.console_helpers'`

- [ ] **Step 3: Create the helper module**

Create `culture/cli/shared/console_helpers.py`:

```python
"""Shared helpers for resolving culture servers and console nicks.

Extracted from ``culture.cli.mesh`` so both the new ``culture console``
group and the legacy ``culture mesh console`` deprecation alias can
reuse them without circular imports.
"""

from __future__ import annotations

import os
import re
import subprocess

from culture.pidfile import list_servers, read_default_server, read_port


def resolve_server(server_name: str | None) -> tuple[str, int] | None:
    """Resolve a culture server name (or default) to ``(name, port)``.

    Returns ``None`` when no culture servers are running.
    """
    if server_name:
        p = read_port(server_name)
        port = p if p else 6667
        return server_name, port

    servers = list_servers()
    if not servers:
        return None

    if len(servers) == 1:
        return servers[0]["name"], servers[0]["port"]

    default = read_default_server()
    if default:
        match = [s for s in servers if s["name"] == default]
        if match:
            return match[0]["name"], match[0]["port"]

    return servers[0]["name"], servers[0]["port"]


def resolve_console_nick() -> str:
    """Resolve the human nick: git user.name -> OS USER -> 'human'."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            name = result.stdout.strip().lower()
            name = re.sub(r"[^a-z0-9-]", "", name.replace(" ", "-"))
            if name:
                return name
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return os.environ.get("USER", "human")
```

- [ ] **Step 4: Run helper tests to verify they pass**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_shared_console_helpers.py -v 2>&1 | tail -15
```

Expected: 7 passed.

- [ ] **Step 5: Replace inline definitions in `mesh.py` with imports**

In `culture/cli/mesh.py`, delete the bodies of `_resolve_server` (lines 204-222) and `_resolve_console_nick` (lines 229-251), and replace them with module-level aliases at the same location:

```python
# -----------------------------------------------------------------------
# Console (helpers extracted to culture.cli.shared.console_helpers)
# -----------------------------------------------------------------------

from culture.cli.shared.console_helpers import (
    resolve_console_nick as _resolve_console_nick,
    resolve_server as _resolve_server,
)
```

Place that import just before the `# Console` section header so existing call sites at `mesh.py:256` (`_cmd_console`) keep working unchanged. Re-run mesh tests:

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_mesh.py tests/test_console_integration.py tests/test_console_connection.py -x 2>&1 | tail -10
```

Expected: existing mesh+console tests still pass (no behavior change, helpers just live elsewhere now).

- [ ] **Step 6: Commit**

```bash
cd /home/spark/git/culture && git add \
  culture/cli/shared/console_helpers.py \
  culture/cli/mesh.py \
  tests/test_cli_shared_console_helpers.py
git commit -m "$(cat <<'EOF'
refactor(cli): extract _resolve_server / _resolve_console_nick to shared

These two helpers are reused by the upcoming `culture console` group and
the legacy `culture mesh console` deprecation alias. Pulling them into
culture.cli.shared.console_helpers avoids circular imports between
`mesh.py` and `console.py`. No behavior change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_resolve_argv` shim — pure unit tests

**Files:**
- Create: `tests/test_cli_console_argv.py`
- Create: `culture/cli/console.py` (skeleton with the shim only — full file in Task 3)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_console_argv.py`:

```python
"""Unit tests for `culture.cli.console._resolve_argv`.

Pure-function tests: argv in, argv out. No subprocess, no IRC.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from culture.cli import console


@pytest.fixture
def mock_resolvers():
    """Pin _resolve_server / _resolve_console_nick to deterministic values."""
    with (
        patch.object(console, "_resolve_server", return_value=("spark", 6667)),
        patch.object(console, "_resolve_console_nick", return_value="ada"),
    ):
        yield


def test_irc_lens_verb_passes_through_unchanged(mock_resolvers):
    for verb in ("learn", "explain", "overview", "serve", "cli"):
        assert console._resolve_argv([verb]) == [verb]


def test_leading_flag_passes_through_unchanged(mock_resolvers):
    assert console._resolve_argv(["--help"]) == ["--help"]
    assert console._resolve_argv(["--version"]) == ["--version"]
    assert console._resolve_argv(["-h"]) == ["-h"]


def test_irc_lens_verb_with_tail_passes_through(mock_resolvers):
    argv = ["serve", "--host", "remote.example", "--nick", "lens"]
    assert console._resolve_argv(argv) == argv


def test_empty_argv_builds_serve_with_default_server(mock_resolvers):
    assert console._resolve_argv([]) == [
        "serve",
        "--host", "127.0.0.1",
        "--port", "6667",
        "--nick", "spark-ada",
    ]


def test_server_name_rewrites_to_serve(mock_resolvers):
    assert console._resolve_argv(["spark"]) == [
        "serve",
        "--host", "127.0.0.1",
        "--port", "6667",
        "--nick", "spark-ada",
    ]


def test_server_name_with_extra_flags_appended(mock_resolvers):
    assert console._resolve_argv(["spark", "--open"]) == [
        "serve",
        "--host", "127.0.0.1",
        "--port", "6667",
        "--nick", "spark-ada",
        "--open",
    ]


def test_no_running_servers_raises_systemexit_with_hint():
    with (
        patch.object(console, "_resolve_server", return_value=None),
        patch.object(console, "_resolve_console_nick", return_value="ada"),
    ):
        with pytest.raises(SystemExit) as excinfo:
            console._resolve_argv([])
        assert "No culture servers running" in str(excinfo.value)
        assert "culture chat start" in str(excinfo.value)


def test_user_nick_override_wins_via_argparse_lastwins(mock_resolvers):
    # Documents that --nick after the shim's --nick is the supported
    # override path: argparse's last-wins semantics make this work.
    argv = console._resolve_argv(["spark", "--nick", "override"])
    assert argv[-2:] == ["--nick", "override"]
    assert "spark-ada" in argv  # shim still injects its value first
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_console_argv.py -x 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'culture.cli.console'` (or import error from missing names).

- [ ] **Step 3: Create the skeleton with just the shim**

Create `culture/cli/console.py`:

```python
"""`culture console` — passthrough to the standalone irc-lens CLI.

irc-lens (https://github.com/agentculture/irc-lens) is the
agent-driven web console for AgentIRC: a localhost aiohttp + HTMX +
SSE app implementing the same console as a browser-driveable surface.
Culture embeds it as a first-class namespace so the culture CLI exposes
the lens with culture-aware ergonomics:

    culture console <server_name>     -> resolves to host/port/nick
    culture console serve --host ...  -> pure passthrough
    culture console explain           -> irc-lens explain (passthrough)

The full design lives in
``docs/superpowers/specs/2026-05-05-culture-console-design.md``.
"""

from __future__ import annotations

import argparse
import sys

from culture.cli import _passthrough
from culture.cli.shared.console_helpers import (
    resolve_console_nick as _resolve_console_nick,
    resolve_server as _resolve_server,
)

NAME = "console"

# Top-level subcommands of irc-lens, verified by `irc-lens --help`.
# Anything in this set means the user typed an irc-lens command directly,
# so the shim must NOT rewrite — pure passthrough.
_IRC_LENS_VERBS = frozenset({"learn", "explain", "overview", "serve", "cli"})


def _entry(argv: list[str]) -> "int | None":
    """In-process call into ``irc_lens.cli.main(argv)``.

    irc-lens's ``main`` returns an ``int`` on normal completion and
    raises ``SystemExit`` only for argparse-level exits — the same
    contract afi-cli implements. Both paths are handled by
    :mod:`culture.cli._passthrough`.
    """
    try:
        from irc_lens.cli import main
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"irc-lens is not installed: {exc}", file=sys.stderr)
        sys.exit(2)
    return main(argv)


def _resolve_argv(argv: list[str]) -> list[str]:
    """Translate ``culture console`` argv into ``irc-lens`` argv.

    - Empty argv → resolve default culture server, build a ``serve`` call.
    - First token is an irc-lens verb or starts with ``-`` → pure
      passthrough (return argv unchanged).
    - Otherwise → treat first token as a culture server name; rewrite to
      ``["serve", "--host", h, "--port", p, "--nick", n, *rest]``.

    Raises ``SystemExit`` with a culture-friendly message when the
    server-name path is taken but no culture servers are running.
    """
    if not argv:
        return _build_serve_argv(server_name=None, rest=[])
    head = argv[0]
    if head in _IRC_LENS_VERBS or head.startswith("-"):
        return list(argv)
    return _build_serve_argv(server_name=head, rest=list(argv[1:]))


def _build_serve_argv(server_name: str | None, rest: list[str]) -> list[str]:
    result = _resolve_server(server_name)
    if result is None:
        raise SystemExit(
            "No culture servers running. Start one with: culture chat start"
        )
    name, port = result
    nick = f"{name}-{_resolve_console_nick()}"
    return [
        "serve",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--nick", nick,
        *rest,
    ]


def dispatch_resolved_argv(server_name: str | None) -> None:
    """Used by the legacy ``culture mesh console`` deprecation alias.

    Mirrors the old TUI's invocation surface: just a server name (or
    ``None`` for the default).
    """
    argv = _resolve_argv([server_name] if server_name else [])
    _passthrough.run(_entry, argv)


_passthrough.register_topic(
    "console",
    _entry,
    explain_argv=["explain"],
    overview_argv=["overview"],
    learn_argv=["learn"],
)


# --- CLI group protocol ---------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    # prefix_chars=chr(0): every token (including --help, --version) is
    # treated as positional and captured in console_args for the shim
    # + irc-lens's argparse parser to handle.
    p = subparsers.add_parser(
        NAME,
        help="Open the irc-lens web console (passthrough)",
        add_help=False,
        prefix_chars=chr(0),
    )
    p.add_argument(
        "console_args", nargs=argparse.REMAINDER, help="Arguments passed to irc-lens"
    )


def dispatch(args: argparse.Namespace) -> None:
    raw = list(getattr(args, "console_args", []) or [])
    _passthrough.run(_entry, _resolve_argv(raw))
```

- [ ] **Step 4: Run shim tests to verify they pass**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_console_argv.py -v 2>&1 | tail -15
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/spark/git/culture && git add culture/cli/console.py tests/test_cli_console_argv.py
git commit -m "$(cat <<'EOF'
feat(cli): culture.cli.console module with _resolve_argv shim

Adds the new culture/cli/console.py group (not yet wired into the CLI
parser; that lands in the next commit) and its argv-rewrite shim with
table-driven unit tests. The shim translates `culture console <server>`
into `irc-lens serve --host --port --nick`, leaves irc-lens verbs alone,
and raises SystemExit with a culture-friendly hint when no servers run.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wire `culture console` into the CLI + irc-lens dependency

**Files:**
- Modify: `culture/cli/__init__.py` (lines 1-30, GROUPS list)
- Modify: `pyproject.toml` (`[project.dependencies]`)
- Create: `tests/test_cli_console.py`

- [ ] **Step 1: Add irc-lens to dependencies**

In `pyproject.toml`, find `[project.dependencies]` (or `dependencies = [...]` under `[project]`) and append:

```toml
"irc-lens>=0.4.2",
```

Then sync:

```bash
cd /home/spark/git/culture && uv pip install -e ".[dev]" 2>&1 | tail -5
```

Expected: clean install, irc-lens listed among installed packages.

- [ ] **Step 2: Write the failing integration test**

Create `tests/test_cli_console.py`:

```python
"""Tests for `culture console` passthrough and `console` universal topic.

Mirrors tests/test_cli_devex.py / test_cli_afi.py. These shell out via
`python -m culture` to exercise the registered argparse group end-to-end.
"""

from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "culture", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_culture_console_help_shows_irc_lens_help():
    result = _run("console", "--help")
    assert result.returncode == 0, result.stderr
    out = (result.stdout + result.stderr).lower()
    assert "irc-lens" in out
    assert "usage:" in out


def test_culture_console_version_runs():
    result = _run("console", "--version")
    assert result.returncode == 0, result.stderr
    # irc-lens --version prints just the version string
    body = result.stdout.strip()
    assert body
    assert all(c.isdigit() or c == "." for c in body)


def test_culture_console_explain_runs():
    result = _run("console", "explain")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_console_learn_runs():
    result = _run("console", "learn")
    assert result.returncode == 0, result.stderr
    assert "irc-lens" in result.stdout


def test_culture_explain_console_via_universal_verb():
    result = _run("explain", "console")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_overview_console_via_universal_verb():
    result = _run("overview", "console")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_learn_console_via_universal_verb():
    result = _run("learn", "console")
    assert result.returncode == 0, result.stderr
    assert "irc-lens" in result.stdout
```

- [ ] **Step 3: Run integration tests to verify they fail**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_console.py -x 2>&1 | tail -15
```

Expected: failures because `console` is not registered in the parser yet (`error: argument command: invalid choice: 'console'`).

- [ ] **Step 4: Wire `console` into `culture/cli/__init__.py`**

Open `culture/cli/__init__.py`. Update the docstring noun-table (around line 6-7) to add `console`:

Find:

```python
    culture mesh     {overview,setup,update,console}
    culture channel  {list,read,message,who}
```

Replace with:

```python
    culture console  {…irc-lens verbs and flags…}      # passthrough; reactive web console
    culture mesh     {overview,setup,update,console}    # `console` here is deprecated; use `culture console`
    culture channel  {list,read,message,who}
```

Then update the imports + `GROUPS` list (around line 24):

Find:

```python
from culture.cli import afi, agent, bot, channel, chat, devex, introspect, mesh, server, skills

GROUPS = [agent, chat, server, mesh, channel, bot, skills, devex, afi, introspect]
```

Replace with:

```python
from culture.cli import (
    afi,
    agent,
    bot,
    channel,
    chat,
    console,
    devex,
    introspect,
    mesh,
    server,
    skills,
)

GROUPS = [agent, chat, server, mesh, channel, bot, skills, devex, afi, console, introspect]
```

- [ ] **Step 5: Run integration tests to verify they pass**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_console.py -v 2>&1 | tail -20
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
cd /home/spark/git/culture && git add culture/cli/__init__.py pyproject.toml uv.lock tests/test_cli_console.py
git commit -m "$(cat <<'EOF'
feat(cli): wire culture console into the unified parser

Registers the new console group with the top-level culture argparse
parser, declares irc-lens >=0.4.2 as a dependency, and adds end-to-end
subprocess tests covering --help, --version, the irc-lens AFI verbs,
and culture's universal verbs (explain/overview/learn console).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Deprecation alias for `culture mesh console`

**Files:**
- Modify: `culture/cli/mesh.py` (`_cmd_console` body, around line 255-280)
- Create: `tests/test_cli_console_deprecation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_console_deprecation.py`:

```python
"""Tests for the `culture mesh console` deprecation alias.

The legacy command should still parse, emit a stderr deprecation
warning, and forward to the new `culture console` flow without
launching the Textual TUI.
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch


def test_mesh_console_warns_then_forwards(capsys):
    """Direct dispatch path — no subprocess.

    The forwarded call uses `culture.cli.console.dispatch_resolved_argv`,
    which we patch to a no-op so this test stays hermetic (no running
    AgentIRC required).
    """
    import argparse

    from culture.cli import mesh

    args = argparse.Namespace(mesh_command="console", server_name="spark", config=None)
    with patch("culture.cli.mesh.console_dispatch") as forwarded:
        mesh._cmd_console(args)
    captured = capsys.readouterr()
    assert "deprecated" in captured.err.lower()
    assert "culture console" in captured.err
    forwarded.assert_called_once_with("spark")


def test_mesh_console_help_marks_deprecated():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "mesh", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    # The console subparser help string should mention deprecation.
    assert "deprecated" in result.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_console_deprecation.py -x 2>&1 | tail -15
```

Expected: failures (no `console_dispatch` symbol; help text doesn't mention deprecation; `_cmd_console` still launches the TUI).

- [ ] **Step 3: Update `culture/cli/mesh.py:_cmd_console` and the subparser help**

Open `culture/cli/mesh.py`.

(a) Add an import alias at the top of the file (with the other `from culture.cli...` imports near the helper-extraction block from Task 1):

```python
from culture.cli.console import dispatch_resolved_argv as console_dispatch
```

(b) Replace the body of `_cmd_console` (the function defined around mesh.py:254-280) with:

```python
def _cmd_console(args: argparse.Namespace) -> None:
    """Deprecated: forward to `culture console`.

    Kept for one minor cycle as an alias. Removal target: 10.0.
    """
    print(
        "warning: 'culture mesh console' is deprecated; use 'culture console' instead",
        file=sys.stderr,
    )
    console_dispatch(args.server_name)
```

(c) Update the subparser help text on the `console_parser = mesh_sub.add_parser("console", ...)` line (around mesh.py:92):

```python
    console_parser = mesh_sub.add_parser(
        "console",
        help="Interactive admin console (DEPRECATED: use 'culture console')",
    )
```

Also update the parent `mesh_parser`'s help text (around mesh.py:27):

```python
    mesh_parser = subparsers.add_parser(
        "mesh", help="Mesh operations (overview, setup, update)"
    )
```

(`console` is dropped from the help summary because it's deprecated, even though the subparser still exists.)

- [ ] **Step 4: Run deprecation tests to verify they pass**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_console_deprecation.py -v 2>&1 | tail -15
```

Expected: 2 passed.

- [ ] **Step 5: Run the broader mesh test suite to confirm no regressions**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_mesh.py tests/test_cli_console_deprecation.py -v 2>&1 | tail -20
```

Expected: all passing. (`test_cli_mesh.py` may not exercise `console` directly — that's fine; it just needs to keep passing.)

- [ ] **Step 6: Commit**

```bash
cd /home/spark/git/culture && git add culture/cli/mesh.py tests/test_cli_console_deprecation.py
git commit -m "$(cat <<'EOF'
feat(cli): deprecate `culture mesh console` in favor of `culture console`

`culture mesh console` now emits a stderr deprecation warning and
forwards to `culture.cli.console.dispatch_resolved_argv`. The Textual
TUI is no longer launched. Removal target: 10.0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Mark `culture/console/` package dormant

**Files:**
- Modify: `culture/console/__init__.py`

- [ ] **Step 1: Add deprecation warning + docstring note**

Open `culture/console/__init__.py`. Replace whatever is currently there (likely empty or a single import line) with:

```python
"""Textual TUI for the Culture agent mesh — DEPRECATED.

Replaced by ``irc-lens`` exposed via ``culture console``. This package
is left in place for one minor cycle so out-of-tree importers can
migrate; it will be removed in 10.0.

See ``docs/superpowers/specs/2026-05-05-culture-console-design.md``.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "culture/console/ Textual TUI is deprecated and will be removed in "
    "10.0; replaced by irc-lens via 'culture console'",
    DeprecationWarning,
    stacklevel=2,
)
```

(If `__init__.py` had existing imports/exports, preserve them below the warning. Check first with `cat culture/console/__init__.py`.)

- [ ] **Step 2: Verify the warning fires when the package is imported**

```bash
cd /home/spark/git/culture && uv run python -W error::DeprecationWarning -c "import culture.console" 2>&1 | tail -5
```

Expected: traceback ending with `DeprecationWarning: culture/console/ Textual TUI is deprecated...`. (Using `-W error` promotes the warning to an exception so we can confirm it was emitted.)

- [ ] **Step 3: Confirm existing console-package tests still run**

The existing `tests/test_console_*.py` suite imports `culture.console.*` modules; those imports should still work, just emit the warning. Run:

```bash
cd /home/spark/git/culture && uv run pytest tests/test_console_commands.py -v 2>&1 | tail -10
```

Expected: passing (DeprecationWarning is non-fatal in pytest's default config).

- [ ] **Step 4: Commit**

```bash
cd /home/spark/git/culture && git add culture/console/__init__.py
git commit -m "$(cat <<'EOF'
chore(console): mark culture/console/ package dormant (deprecation only)

Adds a module-level DeprecationWarning + docstring noting that the
Textual TUI is replaced by irc-lens via `culture console` and will be
removed in 10.0. Code is otherwise untouched.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Documentation + skill text updates

**Files:**
- Modify: `culture/skills/culture/SKILL.md` (lines 344, 367)
- Modify: `culture/learn_prompt.py` (line 201)
- Modify: `culture/cli/CLAUDE.md` (line 17)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Replace the verb-table reference in SKILL.md**

In `culture/skills/culture/SKILL.md`, find line 344 (`culture mesh console                       # interactive admin console`) and replace with:

```
culture console                            # reactive web console (irc-lens)
```

Find line 367 (`| Mesh console | `culture mesh console` |`) and replace with:

```
| Web console | `culture console` |
```

If the surrounding table has a header like "Mesh console" referenced in another row, change consistently. Verify with:

```bash
cd /home/spark/git/culture && grep -n "mesh console\|culture console" culture/skills/culture/SKILL.md
```

Expected: only `culture console` remains (no stale `culture mesh console` references).

- [ ] **Step 2: Update `culture/learn_prompt.py:201`**

Find:

```python
culture mesh console                       # interactive admin console
```

Replace with:

```python
culture console                            # reactive web console (irc-lens)
```

- [ ] **Step 3: Update `culture/cli/CLAUDE.md:17`**

Find:

```
├── mesh.py              # culture mesh {overview,setup,update,console}
```

Replace with:

```
├── mesh.py              # culture mesh {overview,setup,update}  (console deprecated)
├── console.py           # culture console — irc-lens passthrough
```

- [ ] **Step 4: Add CHANGELOG entry**

Open `CHANGELOG.md`. Under the topmost `## [Unreleased]` (or the highest unreleased section — match the file's existing convention), add:

```markdown
### Added
- `culture console` — top-level passthrough wrapper around `irc-lens`, the
  reactive web console for AgentIRC. `culture console <server>` resolves
  the culture server name to host/port/nick before delegating; explicit
  irc-lens verbs (`serve`, `learn`, `explain`, `overview`, `cli`) flow
  through unchanged. Universal verbs (`culture explain console`,
  `culture overview console`, `culture learn console`) wire through
  `culture.cli._passthrough.register_topic`.

### Deprecated
- `culture mesh console` — emits a stderr warning and forwards to
  `culture console`. Removal target: 10.0.
- `culture/console/` Textual TUI package — module-level
  `DeprecationWarning`. Removal target: 10.0.
```

- [ ] **Step 5: Lint markdown**

```bash
cd /home/spark/git/culture && npx markdownlint-cli2 CHANGELOG.md culture/skills/culture/SKILL.md docs/superpowers/specs/2026-05-05-culture-console-design.md docs/superpowers/plans/2026-05-05-culture-console.md 2>&1 | tail -15
```

Expected: no errors. Fix any reported issues.

- [ ] **Step 6: Run skill-docs tests (they verify the SKILL.md is internally consistent)**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_skill_docs.py tests/test_learn_prompt.py -v 2>&1 | tail -10
```

Expected: passing. If any test asserts on the old `culture mesh console` string, it must be updated to match the new text — fix in the same commit.

- [ ] **Step 7: Commit**

```bash
cd /home/spark/git/culture && git add \
  culture/skills/culture/SKILL.md \
  culture/learn_prompt.py \
  culture/cli/CLAUDE.md \
  CHANGELOG.md \
  tests/test_skill_docs.py \
  tests/test_learn_prompt.py
git commit -m "$(cat <<'EOF'
docs: replace `culture mesh console` references with `culture console`

Updates the SKILL.md verb table, learn_prompt's command list, the cli
CLAUDE.md architecture comment, and CHANGELOG to reflect the new
top-level console group + the deprecation of the old mesh subcommand.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Playwright e2e test (opt-in)

**Files:**
- Create: `tests/test_cli_console_playwright.py`
- Modify: `pyproject.toml` (add `playwright` marker if missing)

- [ ] **Step 1: Check whether the `playwright` marker is already declared**

```bash
cd /home/spark/git/culture && grep -A 2 "markers" pyproject.toml | head -20
```

If `playwright:` isn't already in `[tool.pytest.ini_options].markers`, append:

```toml
markers = [
    "playwright: opt-in browser e2e (requires `playwright install chromium`).",
]
```

(If a markers list already exists, just add the line — don't replace the whole list.)

- [ ] **Step 2: Add `pytest-playwright` to dev deps if missing**

```bash
cd /home/spark/git/culture && grep "pytest-playwright\|playwright" pyproject.toml
```

If absent, add `"pytest-playwright>=0.5"` and `"playwright>=1.40"` to `[project.optional-dependencies].dev` and re-sync:

```bash
cd /home/spark/git/culture && uv pip install -e ".[dev]"
```

- [ ] **Step 3: Write the Playwright e2e test**

Create `tests/test_cli_console_playwright.py`:

```python
"""Browser e2e for `culture console` against a real AgentIRC.

Opt-in: gated behind `@pytest.mark.playwright`, skipped by the default
suite via the existing `addopts = "-m 'not playwright'"` (or
equivalent). Run with `pytest -m playwright` after
`playwright install chromium`.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from contextlib import closing

import pytest

pytestmark = pytest.mark.playwright


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"port {host}:{port} never opened within {timeout}s")


@pytest.mark.playwright
def test_culture_console_serve_drives_help_view(tmp_path):
    """Boot irc-lens via `python -m culture console serve`, drive /help.

    Uses pure-passthrough (`culture console serve --host ... --port ...
    --nick ...`) instead of the server-name shim so the test doesn't
    need a culture pidfile.
    """
    from playwright.sync_api import sync_playwright

    # Start an AgentIRC test server. Reuse irc-lens's in-tree fixture
    # if it's importable; otherwise skip — culture's standard agentirc
    # server fixture lives in tests/conftest.py.
    pytest.importorskip("agentirc")
    web_port = _free_port()
    irc_port = _free_port()

    # Boot AgentIRC in a subprocess (use culture's pidfile-based starter).
    irc_proc = subprocess.Popen(
        [sys.executable, "-m", "agentirc", "serve", "--host", "127.0.0.1",
         "--port", str(irc_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port("127.0.0.1", irc_port)

        culture_proc = subprocess.Popen(
            [sys.executable, "-m", "culture", "console", "serve",
             "--host", "127.0.0.1", "--port", str(irc_port),
             "--nick", "lens-e2e",
             "--web-port", str(web_port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_for_port("127.0.0.1", web_port)

            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{web_port}/")

                    indicator = page.locator('[data-testid="view-indicator"]')
                    indicator.wait_for(state="attached", timeout=5000)

                    chat_input = page.locator('[data-testid="chat-input"]')
                    chat_input.fill("/help")
                    chat_input.press("Enter")

                    # SSE swap should land within ~1s; allow 5s for CI.
                    page.wait_for_function(
                        "el => el.getAttribute('data-view') === 'help'",
                        arg=indicator.element_handle(),
                        timeout=5000,
                    )
                finally:
                    browser.close()
        finally:
            culture_proc.terminate()
            culture_proc.wait(timeout=5)
    finally:
        irc_proc.terminate()
        irc_proc.wait(timeout=5)
```

- [ ] **Step 4: Confirm the test is collected but skipped by default**

```bash
cd /home/spark/git/culture && uv run pytest tests/test_cli_console_playwright.py --collect-only 2>&1 | tail -10
```

Expected: `1 test deselected` (or "collected 1 / 1 deselected") if `addopts = "-m 'not playwright'"` is configured. If the test runs by default, that means the marker exclusion isn't set and it'll fail without chromium installed — fix the marker config.

- [ ] **Step 5: Run the test once locally to confirm it works (optional, requires browser)**

```bash
cd /home/spark/git/culture && uv run playwright install chromium 2>&1 | tail -3
cd /home/spark/git/culture && uv run pytest tests/test_cli_console_playwright.py -m playwright -v 2>&1 | tail -15
```

Expected: 1 passed. If AgentIRC fixture differs from `python -m agentirc serve`, adapt the spawn line — irc-lens's `tests/_agentirc_server.py` is the canonical reference; consider importing from `culture.tests.conftest` if a reusable fixture exists.

If local environment can't run Playwright, document this in the PR description as "Playwright test added but unverified locally; CI will gate" — do not skip the test.

- [ ] **Step 6: Commit**

```bash
cd /home/spark/git/culture && git add \
  pyproject.toml \
  uv.lock \
  tests/test_cli_console_playwright.py
git commit -m "$(cat <<'EOF'
test(console): browser e2e for `culture console serve` via Playwright

Boots a local AgentIRC, runs `culture console serve --host --port
--nick --web-port` as a subprocess, navigates Chromium to the web
port, drives /help, and asserts the view-indicator's data-view
attribute flips to "help". Gated behind @pytest.mark.playwright so
the default suite stays fast.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final verification, version bump, push, PR

**Files:**
- Modify: `pyproject.toml` (version)
- Modify: `culture/__init__.py` (`__version__`) — only if culture's `/version-bump` skill expects manual edits; usually it handles both.
- Modify: `CHANGELOG.md` (move "Unreleased" entries under the new version header)

- [ ] **Step 1: Run the full default test suite**

```bash
cd /home/spark/git/culture && uv run pytest 2>&1 | tail -25
```

Expected: green. If any unrelated test broke, investigate before continuing — do NOT mark complete.

- [ ] **Step 2: Run linters**

```bash
cd /home/spark/git/culture && uv run flake8 culture/cli/console.py culture/cli/shared/console_helpers.py 2>&1 | tail -10
cd /home/spark/git/culture && uv run black --check culture/cli/console.py culture/cli/shared/console_helpers.py tests/test_cli_console*.py 2>&1 | tail -5
cd /home/spark/git/culture && uv run isort --check-only culture/cli/console.py culture/cli/shared/console_helpers.py tests/test_cli_console*.py 2>&1 | tail -5
```

Expected: clean. If black/isort report formatting drift, run without `--check`/`--check-only` to fix, re-stage, and amend the previous commit (`git commit --amend --no-edit`). Or, if amending feels risky, make a new "style" commit.

- [ ] **Step 3: Bump version**

Use the project's version-bump skill (see culture's CLAUDE.md):

```bash
# Run the slash-command via your normal mechanism, e.g.:
#   /version-bump minor
# This updates pyproject.toml, culture/__init__.py, and inserts a
# CHANGELOG section. Confirm the diff before continuing.
cd /home/spark/git/culture && git diff --stat 2>&1 | tail -5
```

If you have to do it manually:

```bash
cd /home/spark/git/culture && grep "^version" pyproject.toml
# Bump the second number (minor) by 1, reset patch to 0.
# Edit pyproject.toml + culture/__init__.py with the new version.
# Move CHANGELOG entries from "Unreleased" under a new "## [X.Y.0] - YYYY-MM-DD" header.
```

Commit:

```bash
cd /home/spark/git/culture && git add pyproject.toml culture/__init__.py CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore: bump version for culture console (irc-lens passthrough)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push branch**

```bash
cd /home/spark/git/culture && git push -u origin feat/culture-console-irc-lens 2>&1 | tail -5
```

- [ ] **Step 5: Open PR linking the deferred-decision issue**

```bash
cd /home/spark/git/culture && gh pr create \
  --title "feat(cli): culture console — irc-lens passthrough; deprecate culture mesh console" \
  --body "$(cat <<'EOF'
## Summary

- Adds `culture console` as a top-level group: pure passthrough to
  `irc-lens` with one culture-owned shim (`_resolve_argv`) that
  translates `culture console <server>` into the right
  `--host/--port/--nick` flags. Mirrors the `afi`/`devex` pattern.
- Wires universal verbs (`culture {explain,overview,learn} console`)
  through `_passthrough.register_topic`.
- Deprecates `culture mesh console` (stderr warning + forward) and the
  underlying `culture/console/` Textual package (module-level
  `DeprecationWarning`). Removal target: 10.0.
- Declares `irc-lens >= 0.4.2` as a dependency.

Spec: `docs/superpowers/specs/2026-05-05-culture-console-design.md`

## Deferred decision

Issue #322 tracks subprocess-vs-in-process for the irc-lens
passthrough. Default for v9.x is in-process (matches every other
extension); revisit only on real-world breakage.

## Test plan

- [x] `uv run pytest` (default suite green)
- [x] `uv run pytest -m playwright` locally (Chromium e2e passes)
- [x] `culture console spark --open` boots lens against local AgentIRC
- [x] `culture mesh console spark` prints deprecation warning then forwards
- [x] `culture explain console` / `overview console` / `learn console` produce irc-lens output

— Claude
EOF
)"
```

- [ ] **Step 6: Mark task complete**

After CI is green and (eventually) Qodo / Copilot / SonarCloud finish, address review comments per the project's `pr-review` skill.

---

## Self-Review Notes

- **Spec coverage:** Every section of the spec maps to a task. Architecture → Task 2/3. Deprecation discipline → Task 4/5. File-changes table → Tasks 1–6. Verification plan → Task 7 + manual steps in PR description.
- **Type consistency:** `_resolve_server` returns `tuple[str, int] | None` everywhere; `_resolve_console_nick` returns `str`. The shim uses both consistently. `_resolve_argv` returns `list[str]`. `_entry` matches `_passthrough.Entry` (returns `int | None`).
- **Out of scope confirmation:** No edits to `irc-lens`, no auth on `--bind 0.0.0.0`, no removal of `culture/console/` code, no docs-site changes beyond SKILL.md/learn_prompt.
