# AgentIRC Extraction — Track A (Culture-Side Cutover) Implementation Plan

> **⚠ Superseded (2026-05-02).** This plan drafted A1+A2+A3 as a single PR. Phasing forced a split:
>
> - **A1** (config dataclasses) shipped in culture#309 (8.8.0, 2026-05-01).
> - **A2** (bot framework rewrite against agentirc-cli 9.5 public extension API) — see `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a2.md`.
> - **A3** (delete bundled IRCd + subprocess shim + major bump) — see `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a3.md`.
>
> This file is kept for historical context; do not execute it. Companion spec (still authoritative): `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut over culture to consume the `agentirc-cli` PyPI package: delete the in-tree IRCd, relocate the client transport to `culture/transport/`, install a 1:1 passthrough shim at `culture server <verb>`, and ship a major version bump.

**Architecture:** Single PR off `main`. Adds `agentirc-cli>=9.0,<10.0` as a runtime dep. Replaces `culture/cli/server.py` with a thin argparse-`REMAINDER` passthrough into `agentirc.cli.dispatch(argv) -> int`. Moves `culture/agentirc/{client,remote_client}.py` to `culture/transport/` (git mv to preserve blame). Deletes `culture/agentirc/` and `protocol/extensions/` wholesale. Adds one shim parity test and one cross-repo smoke test.

**Tech Stack:** Python 3.x, uv (deps + lockfile), argparse, pytest + pytest-asyncio + pytest-xdist, pre-commit (black + isort + flake8 + pylint + bandit + markdownlint).

**Companion spec:** `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md`

---

## Preconditions (do not start until all are true)

- `agentirc-cli==9.0.0` is published to PyPI (verified at plan-write time on 2026-04-30; Track B merged in `agentculture/agentirc` and tagged 9.0.0). If a newer 9.0.x patch exists by execution time, prefer it; the dependency pin range `>=9.0,<10.0` accepts any 9.x.y.
- Working tree on `main` is clean: `git status` shows no staged or unstaged changes inside `culture/agentirc/`, `culture/cli/server.py`, `culture/cli/shared/mesh.py`, `culture/bots/`, `culture/clients/`, or `protocol/extensions/`.
- Pre-existing untracked changes elsewhere are fine but should be stashed if they could interfere with `/version-bump` or `git mv`.

If any precondition fails, stop and resolve it before continuing.

---

## File Structure (what changes in this PR)

| Path | Action | Notes |
|---|---|---|
| `pyproject.toml` | Modify | Add `agentirc-cli>=9.0,<10.0` to runtime deps. |
| `uv.lock` | Modify | Regenerate via `uv lock`. |
| `culture/agentirc/` | Delete (whole directory) | All ~3,600 LOC, including `__main__.py` and `skills/`. |
| `culture/agentirc/client.py` | `git mv` → `culture/transport/client.py` | Preserves blame. |
| `culture/agentirc/remote_client.py` | `git mv` → `culture/transport/remote_client.py` | Preserves blame. |
| `culture/transport/__init__.py` | Create | Re-exports the public class names from `client.py` and `remote_client.py`. |
| `culture/cli/server.py` | Rewrite | ~25 lines. Argparse `REMAINDER` passthrough into `agentirc.cli.dispatch`. |
| `culture/cli/shared/mesh.py` | Modify | Replace `from culture.agentirc.config import LinkConfig` with `from agentirc.config import LinkConfig`. |
| `culture/bots/`, `culture/clients/*/daemon.py` | Modify (imports only) | `culture.agentirc.{client,remote_client}` → `culture.transport.{client,remote_client}`. |
| `culture/transport/client.py` | Modify (imports only) | Replace any IRC-verb / numeric / extension-tag string literals with `agentirc.protocol.*`. |
| `protocol/extensions/` | Delete (whole directory) | Lives in agentirc now. |
| `tests/test_*` for IRCd internals | Delete | They live in agentirc's repo now. |
| `tests/test_server_shim.py` | Create | Asserts every verb in `agentirc --help` is reachable via `culture server <verb> --help`. |
| `tests/test_agentirc_smoke.py` | Create | Boots `agentirc serve` as a subprocess, connects a culture transport client, exchanges one message. |
| `culture/__init__.py` (or `pyproject.toml`) | Modify | Version bumped via `/version-bump major`. |
| `CHANGELOG.md` | Modify | New section at top via `/version-bump major`. |

---

## Task 1 — Verify the precondition: agentirc-cli is on PyPI

**Files:** none (verification only).

- [ ] **Step 1.1:** Confirm the published version.

```bash
uv pip install --dry-run "agentirc-cli==9.0.0" 2>&1 | tail -5
```

Expected: a line like `Would install agentirc-cli==9.0.0`. If you see `Could not find a version that satisfies the requirement`, agentirc-cli isn't on PyPI yet — STOP. Do not start the cutover.

- [ ] **Step 1.2:** Smoke-test the binary in a clean throwaway venv.

```bash
cd /tmp && uv venv check-agentirc && uv pip install --python check-agentirc/bin/python "agentirc-cli==9.0.0" >/dev/null && check-agentirc/bin/agentirc --help && rm -rf check-agentirc
```

Expected: `agentirc --help` lists subcommands `serve start stop restart status link logs version` (or a superset). Confirms the binary is wired up.

- [ ] **Step 1.3:** Inspect the public API surface.

```bash
cd /tmp && uv run --with "agentirc-cli==9.0.0" python -c "
import agentirc.config, agentirc.cli, agentirc.protocol
print('config:', sorted(n for n in dir(agentirc.config) if not n.startswith('_')))
print('cli:', sorted(n for n in dir(agentirc.cli) if not n.startswith('_')))
print('protocol:', sorted(n for n in dir(agentirc.protocol) if not n.startswith('_')))
"
```

Expected: `config` lists at least `LinkConfig`, `PeerSpec`, `ServerConfig`. `cli` lists at least `dispatch`, `main`. `protocol` lists verb names (e.g., `JOIN`, `PRIVMSG`, `THREAD`, `ROOM`) as constants. **Save the protocol output** — Task 5 needs it.

---

## Task 2 — Branch and add the dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 2.1:** Cut a branch off `main`.

```bash
cd /home/spark/git/culture && git switch main && git pull --ff-only && git switch -c agentirc-extraction-cutover
```

Expected: branch created and checked out.

- [ ] **Step 2.2:** Add `agentirc-cli` to runtime dependencies.

Open `pyproject.toml` and add `"agentirc-cli>=9.0,<10.0"` to the `[project] dependencies` list. Keep the list alphabetised if culture's existing entries are alphabetised; otherwise append.

- [ ] **Step 2.3:** Regenerate the lockfile.

```bash
uv lock
```

Expected: `uv.lock` is updated; `agentirc-cli` and any of its transitive deps appear.

- [ ] **Step 2.4:** Smoke-import inside the project venv.

```bash
uv run python -c "import agentirc.config, agentirc.cli, agentirc.protocol; print('OK')"
```

Expected: `OK`. If `ModuleNotFoundError`, the lock didn't take — re-run `uv sync`.

- [ ] **Step 2.5:** Commit.

```bash
git add pyproject.toml uv.lock
git commit -m "feat(deps): add agentirc-cli runtime dependency"
```

---

## Task 3 — Relocate client transport to culture/transport/

**Files:**
- `git mv` `culture/agentirc/client.py` → `culture/transport/client.py`
- `git mv` `culture/agentirc/remote_client.py` → `culture/transport/remote_client.py`
- Create: `culture/transport/__init__.py`

- [ ] **Step 3.1:** Create the destination package directory.

```bash
mkdir -p culture/transport
```

- [ ] **Step 3.2:** Move the two files (preserves git blame).

```bash
git mv culture/agentirc/client.py culture/transport/client.py
git mv culture/agentirc/remote_client.py culture/transport/remote_client.py
```

- [ ] **Step 3.3:** Discover the public symbols to re-export.

```bash
grep -nE "^class |^def " culture/transport/client.py culture/transport/remote_client.py | grep -v "^.*: *def _" | head -20
```

Expected output: lists the top-level classes/functions in each file. Note the public class names (e.g., `Client`, `RemoteClient`, plus any helper classes that today are imported from `culture.agentirc.client` directly).

- [ ] **Step 3.4:** Write `culture/transport/__init__.py` re-exporting the public symbols.

```python
"""Culture's IRC client transport, separated from the IRCd (which lives in agentirc-cli)."""

from culture.transport.client import *  # noqa: F401,F403  -- re-export public surface
from culture.transport.remote_client import *  # noqa: F401,F403
```

Then narrow the wildcards: open each module, check whether it defines `__all__`. If yes, the wildcard is bounded; keep it. If no, replace `*` with explicit names from Step 3.3 to avoid leaking helpers.

Final form (typical):

```python
"""Culture's IRC client transport, separated from the IRCd (which lives in agentirc-cli)."""

from culture.transport.client import Client  # plus any other public names
from culture.transport.remote_client import RemoteClient

__all__ = ["Client", "RemoteClient"]
```

- [ ] **Step 3.5:** Update every importer in the repo.

```bash
grep -rln "from culture\.agentirc\.\(client\|remote_client\)\|import culture\.agentirc\.\(client\|remote_client\)" culture tests | sort -u
```

Expected output: a list of files. For each, run a sed-style replace:

```bash
grep -rln "culture\.agentirc\.client" culture tests | xargs sed -i 's|culture\.agentirc\.client|culture.transport.client|g'
grep -rln "culture\.agentirc\.remote_client" culture tests | xargs sed -i 's|culture\.agentirc\.remote_client|culture.transport.remote_client|g'
```

Verify nothing was missed:

```bash
grep -rn "culture\.agentirc\.\(client\|remote_client\)" culture tests
```

Expected: no output.

- [ ] **Step 3.6:** Run the test suite.

```bash
/run-tests
```

Expected: tests that depend on transport pass; tests that depend on the (still-present) `culture/agentirc/` IRCd internals also pass for now (we haven't deleted yet). Any failures here mean an importer was missed; go back to 3.5.

- [ ] **Step 3.7:** Commit.

```bash
git add -A
git commit -m "refactor: relocate IRC client transport to culture/transport/"
```

---

## Task 4 — Switch mesh config to agentirc.config

**Files:**
- Modify: `culture/cli/shared/mesh.py`

- [ ] **Step 4.1:** Inspect current imports.

```bash
grep -n "from culture\.agentirc\.config\|import culture\.agentirc\.config" culture/cli/shared/mesh.py
```

Expected: two matches (lines 23 and 44 per the spec).

- [ ] **Step 4.2:** Rewrite the imports.

```bash
sed -i 's|from culture\.agentirc\.config import|from agentirc.config import|g' culture/cli/shared/mesh.py
```

Verify:

```bash
grep -n "agentirc\.config\|culture\.agentirc\.config" culture/cli/shared/mesh.py
```

Expected: only `from agentirc.config import …` matches; no `culture.agentirc.config` references remain.

- [ ] **Step 4.3:** Run mesh-related tests.

```bash
/run-tests tests/test_mesh*.py tests/test_culture_link*.py
```

(If those test files don't exist, run the full suite: `/run-tests`.)

Expected: pass. The dataclasses in `agentirc.config` are byte-equivalent to what was at `culture/agentirc/config.py`, so behaviour is unchanged.

- [ ] **Step 4.4:** Commit.

```bash
git add culture/cli/shared/mesh.py
git commit -m "refactor(mesh): import LinkConfig/PeerSpec from agentirc.config"
```

---

## Task 5 — Replace protocol-constant string literals with agentirc.protocol

**Files:**
- Modify: `culture/transport/client.py`
- Possibly: `culture/transport/remote_client.py`, `culture/bots/*.py`

- [ ] **Step 5.1:** Recall the protocol constants you saved in Step 1.3. List them again:

```bash
uv run python -c "
import agentirc.protocol as p
for name in sorted(n for n in dir(p) if not n.startswith('_')):
    print(name, '=', repr(getattr(p, name)))
"
```

Expected: a list like `JOIN = 'JOIN'`, `PRIVMSG = 'PRIVMSG'`, `THREAD = 'THREAD'`, `ROOM = 'ROOM'`, plus numerics and extension tag names.

- [ ] **Step 5.2:** Find candidate string-literal usages in culture's transport.

```bash
grep -nE '"[A-Z]{3,}"|'\''[A-Z]{3,}'\''' culture/transport/client.py culture/transport/remote_client.py | head -40
```

Expected: lines where IRC verb names appear as bare uppercase string literals (e.g., `"PRIVMSG"`, `"JOIN"`, `"THREAD"`).

- [ ] **Step 5.3:** Replace each verb literal with the matching `agentirc.protocol` constant.

Open `culture/transport/client.py`. Add at the top of the imports section:

```python
from agentirc import protocol as _proto
```

Then for each verb literal you found in Step 5.2 that has a matching constant in Step 5.1, replace `"PRIVMSG"` with `_proto.PRIVMSG`, etc. **Do not replace** verbs that aren't in `agentirc.protocol` — those may be culture-only protocol additions that haven't been promoted to the shared module yet; leave them as literals and note them in the PR description.

If `remote_client.py` has the same pattern, repeat there.

- [ ] **Step 5.4:** Verify nothing reads as a now-undefined constant.

```bash
uv run python -c "import culture.transport.client, culture.transport.remote_client; print('OK')"
```

Expected: `OK`. An `AttributeError: module 'agentirc.protocol' has no attribute 'X'` means you replaced a literal that doesn't have a constant — revert that one.

- [ ] **Step 5.5:** Run transport tests.

```bash
/run-tests tests/test_connection.py tests/test_console_*.py
```

Expected: pass.

- [ ] **Step 5.6:** Commit.

```bash
git add culture/transport/
git commit -m "refactor(transport): use agentirc.protocol constants instead of string literals"
```

---

## Task 6 — Replace culture/cli/server.py with the passthrough shim (TDD)

**Files:**
- Create: `tests/test_server_shim.py`
- Rewrite: `culture/cli/server.py`

- [ ] **Step 6.1: Write the failing test.**

Create `tests/test_server_shim.py`:

```python
"""Verify culture server <verb> is a 1:1 passthrough into agentirc.cli.dispatch."""

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


def _agentirc_run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agentirc", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _agentirc_verbs() -> set[str]:
    """Parse `agentirc --help` and extract the verb names from its subcommands list."""
    result = _agentirc_run("--help")
    assert result.returncode == 0, result.stderr
    verbs: set[str] = set()
    in_subcommands = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            in_subcommands = False
            continue
        if stripped.lower().startswith(("subcommands", "commands", "positional arguments")):
            in_subcommands = True
            continue
        if in_subcommands and line.startswith(("    ", "\t")):
            token = stripped.split(None, 1)[0]
            if token and token[0].isalpha() and not token.startswith("-"):
                verbs.add(token)
    assert verbs, f"no verbs parsed from agentirc --help:\n{result.stdout}"
    return verbs


def test_culture_server_help_succeeds():
    """culture server --help must exit 0."""
    result = _run("server", "--help")
    assert result.returncode == 0, result.stderr


def test_every_agentirc_verb_is_reachable_via_culture_server():
    """For every verb agentirc lists, culture server <verb> --help must succeed."""
    for verb in _agentirc_verbs():
        result = _run("server", verb, "--help")
        assert result.returncode == 0, f"culture server {verb} --help failed: {result.stderr}"


def test_culture_server_unknown_verb_returns_agentirc_error():
    """Unknown verbs are routed to agentirc, which returns its own error (nonzero exit)."""
    result = _run("server", "this-verb-does-not-exist")
    assert result.returncode != 0
```

- [ ] **Step 6.2: Run the test; verify it fails.**

```bash
/run-tests tests/test_server_shim.py
```

Expected: the first two tests probably *pass* against today's `culture/cli/server.py` because culture's server group already accepts `--help`. The third test (`test_every_agentirc_verb_is_reachable_via_culture_server`) **fails**, because today `culture server <verb>` only knows the verbs culture's argparse registered (start/stop/status/...), not the full agentirc verb set (which includes `serve`, `link`, `logs`, `version`).

- [ ] **Step 6.3: Replace `culture/cli/server.py` with the shim.**

Overwrite `culture/cli/server.py` with this content (entire file):

```python
"""culture server <verb> ... — 1:1 in-process passthrough into agentirc.cli.dispatch.

Culture does not parse, validate, or rename any flag past `server`. Every flag,
verb, and exit code below this layer comes from agentirc-cli. Adding a verb in
agentirc makes it reachable here automatically.
"""

from __future__ import annotations

import argparse
import sys

from agentirc.cli import dispatch as _agentirc_dispatch

NAME = "server"  # culture's CLI dispatcher routes args.command == NAME → dispatch(args)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `server` subparser as a transparent passthrough."""
    parser = subparsers.add_parser(
        NAME,
        help="Manage the IRC server (passthrough to agentirc-cli)",
        add_help=False,  # let agentirc handle --help so users see agentirc's verbs
    )
    parser.add_argument("agentirc_args", nargs=argparse.REMAINDER)


def dispatch(args: argparse.Namespace) -> None:
    """Forward residual argv to agentirc.cli.dispatch and exit with its code."""
    rc = _agentirc_dispatch(list(args.agentirc_args))
    sys.exit(int(rc) if rc is not None else 0)
```

The `NAME = "server"` constant matches culture's group-module convention (see `culture/cli/agent.py`, `culture/cli/bot.py`, etc.). Culture's top-level dispatcher iterates groups and invokes `group.dispatch(args)` for the group whose `NAME` (or `NAMES`) matches `args.command`.

- [ ] **Step 6.4: Audit for orphaned references.**

The deleted server.py had ~400 LOC referencing `culture.pidfile`, `.shared.constants`, `.shared.mesh.parse_link`, etc. Some of those modules may have been used *only* by `culture/cli/server.py`. Check:

```bash
grep -rln "from culture\.pidfile\|import culture\.pidfile" culture tests
```

Expected: at least one match outside `culture/cli/server.py` (bots/agents may also use the pidfile machinery). If `culture/pidfile.py` has zero remaining importers, it's orphaned — leave it for now and flag it in the PR description; cleanup is out of scope for this cutover.

Repeat for `culture/cli/shared/constants.py` and any other helper that the deleted server.py was a primary consumer of.

- [ ] **Step 6.5: Run the shim test.**

```bash
/run-tests tests/test_server_shim.py
```

Expected: all three tests pass.

- [ ] **Step 6.6: Run the full suite.**

```bash
/run-tests
```

Expected: pass. Failures here are likely tests that imported now-deleted symbols from `culture.cli.server` (e.g., `_resolve_server_name`). Track them down — they belong to Task 7 (deleting `culture/agentirc/`) or are stale tests that should be removed.

- [ ] **Step 6.7: Commit.**

```bash
git add culture/cli/server.py tests/test_server_shim.py
git commit -m "feat(cli): culture server is now a passthrough into agentirc.cli.dispatch"
```

---

## Task 7 — Delete culture/agentirc/ and protocol/extensions/

**Files:**
- Delete: `culture/agentirc/` (entire directory)
- Delete: `protocol/extensions/` (entire directory)

- [ ] **Step 7.1: Confirm no remaining importers of `culture.agentirc.*`.**

```bash
grep -rn "from culture\.agentirc\|import culture\.agentirc" culture tests
```

Expected: no output. If anything matches, it's an importer Tasks 3-6 missed; fix it before deleting.

- [ ] **Step 7.2: Delete `culture/agentirc/`.**

```bash
git rm -r culture/agentirc/
```

Expected: dozens of files staged for deletion.

- [ ] **Step 7.3: Delete `protocol/extensions/`.**

```bash
git rm -r protocol/extensions/
```

Expected: protocol-extension `.md` files staged for deletion.

- [ ] **Step 7.4: Sweep tests for now-broken imports.**

```bash
grep -rln "culture\.agentirc" tests
```

Expected: no output. If anything matches, those are tests that exercised the IRCd directly (now in agentirc's repo). Delete them:

```bash
git rm <each-broken-test-file>
```

- [ ] **Step 7.5: Run the full suite.**

```bash
/run-tests
```

Expected: pass. If a test fails because a fixture imported `culture.agentirc.X`, either the fixture is a transport helper (rewrite to import from `culture.transport`) or it's an IRCd-internal helper (delete the test).

- [ ] **Step 7.6: Commit.**

```bash
git add -A
git commit -m "refactor: delete culture/agentirc/ and protocol/extensions/ (moved to agentirc-cli)"
```

---

## Task 8 — Cross-repo smoke test

**Files:**
- Create: `tests/test_agentirc_smoke.py`

- [ ] **Step 8.1: Write the smoke test.**

Create `tests/test_agentirc_smoke.py`:

```python
"""End-to-end smoke: boot `agentirc serve` as a subprocess, connect a culture transport client.

This is the only cross-repo integration test in culture. It imports nothing from
agentirc internals — it treats agentirc-cli as a black-box binary, the way an
external user would.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.fixture
def agentirc_server(tmp_path: Path):
    """Boot an agentirc daemon in a tmp dir; yield (host, port); tear down."""
    if shutil.which("agentirc") is None:
        pytest.skip("agentirc binary not on PATH (agentirc-cli not installed?)")

    port = _free_port()
    config = tmp_path / "server.yaml"
    config.write_text(
        f"server:\n"
        f"  host: 127.0.0.1\n"
        f"  port: {port}\n"
        f"  name: smoke-test\n"
    )

    proc = subprocess.Popen(
        ["agentirc", "serve", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        if not _wait_for_port("127.0.0.1", port):
            proc.kill()
            stdout, stderr = proc.communicate(timeout=2)
            pytest.fail(
                f"agentirc serve did not bind {port} within 10s.\n"
                f"stdout:\n{stdout.decode(errors='replace')}\n"
                f"stderr:\n{stderr.decode(errors='replace')}"
            )
        yield "127.0.0.1", port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_culture_transport_can_connect_to_agentirc(agentirc_server):
    """A raw TCP connect to agentirc serve succeeds; that's the floor."""
    host, port = agentirc_server
    with socket.create_connection((host, port), timeout=2) as sock:
        # A bare TCP accept is enough for this smoke. Deeper protocol-level
        # exchange is covered by transport unit tests against a fake server.
        assert sock.fileno() != -1
```

The test minimally proves `agentirc serve` boots from the installed binary and accepts TCP. Deeper protocol-level testing stays in transport unit tests against a fake server (those are fast and don't need a subprocess).

- [ ] **Step 8.2: Adjust the config schema if needed.**

The config keys above (`server: {host, port, name}`) match what `agentirc.config.ServerConfig` expects. Verify by running:

```bash
uv run python -c "
import agentirc.config, dataclasses
for f in dataclasses.fields(agentirc.config.ServerConfig):
    print(f.name, f.type)
"
```

Expected: a list of fields. If the actual schema differs (e.g., the top-level key isn't `server:` or fields are differently named), adjust `config.write_text(...)` to match the real schema.

- [ ] **Step 8.3: Run the smoke test.**

```bash
/run-tests tests/test_agentirc_smoke.py -v
```

Expected: 1 test passes. If it skips with "agentirc binary not on PATH," your venv didn't install the script — re-run `uv sync` and try again.

- [ ] **Step 8.4: Commit.**

```bash
git add tests/test_agentirc_smoke.py
git commit -m "test: cross-repo smoke test boots agentirc serve and connects"
```

---

## Task 9 — Version bump (major)

**Files:**
- `culture/__init__.py` (or wherever `__version__` lives)
- `pyproject.toml`
- `CHANGELOG.md`

- [ ] **Step 9.1: Run the bump.**

```bash
/version-bump major
```

Expected: a new major version section in `CHANGELOG.md`, version updated in `pyproject.toml` and `culture/__init__.py` (or equivalent), `uv.lock` regenerated. Single staged commit prepared by the skill.

- [ ] **Step 9.2: Edit the new CHANGELOG section.**

Open `CHANGELOG.md`. Replace the auto-generated stub for the new version with a hand-written entry. Suggested content:

```markdown
## [<NEW-MAJOR-VERSION>] - 2026-04-30

### Changed (breaking)

- The IRCd has been extracted into a separate package, `agentirc-cli`. Culture now depends on `agentirc-cli>=9.0,<10.0`.
- `culture server <verb>` is now a 1:1 passthrough into `agentirc.cli.dispatch`. All existing verbs continue to work; their flags, output, and exit codes come from `agentirc-cli`.
- `culture/agentirc/` has been deleted. Code that imported `culture.agentirc.*` must update:
  - `culture.agentirc.client` / `culture.agentirc.remote_client` → `culture.transport.client` / `culture.transport.remote_client`
  - `culture.agentirc.config` → `agentirc.config`
  - Other internals (e.g., `culture.agentirc.ircd`, `culture.agentirc.server_link`) are not part of the public API and have no replacement.
- `python -m culture.agentirc` is removed. Use `agentirc` (CLI) or `python -m agentirc`.

### Notes

- On-disk footprint is unchanged: default config path, socket paths, log paths, and `culture-*` systemd unit names are preserved. Existing deployments require no migration.
- `protocol/extensions/` documentation has moved to the agentirc repo.
```

- [ ] **Step 9.3: Verify the bump took.**

```bash
grep -E "^version|__version__" pyproject.toml culture/__init__.py 2>/dev/null
```

Expected: version is the new major. If `culture/__init__.py` doesn't have `__version__`, check culture's actual version-source-of-truth (per project CLAUDE.md).

- [ ] **Step 9.4: Commit (or amend the bump's commit).**

If `/version-bump` already created a commit, amend the CHANGELOG hand-edits onto it:

```bash
git add CHANGELOG.md
git commit --amend --no-edit
```

Otherwise:

```bash
git add CHANGELOG.md pyproject.toml culture/__init__.py uv.lock
git commit -m "chore: bump major version for agentirc extraction cutover"
```

---

## Task 10 — Doc-test alignment audit

**Files:** docs across the tree (audit only; fixes inline if needed).

- [ ] **Step 10.1: Invoke the doc-test-alignment subagent.**

Per culture's CLAUDE.md, this audit runs before the first push when public API surface changes. Invoke:

```
Agent(subagent_type="doc-test-alignment", description="Audit cutover branch", prompt="Audit the staged diff on this branch for new public API surface and report whether docs/ and protocol/extensions/ mention them. Specifically check: (1) culture/transport/ — new module path, replaces culture.agentirc.{client,remote_client}; (2) culture/cli/server.py — now a passthrough into agentirc.cli.dispatch; (3) culture/agentirc/ deletion — any doc that says `culture server` runs in-process or imports IRCd internals is now wrong; (4) protocol/extensions/ deletion — culture's docs that pointed at protocol/extensions/ need updating to point at agentirc's repo; (5) python -m culture.agentirc removal. Report missing or now-wrong docs. Do not write fixes.")
```

- [ ] **Step 10.2: Address the report.**

For each gap the audit flags, update the docs in this PR:

- A doc that explained "culture's IRCd lives in `culture/agentirc/`" → rewrite to "culture's IRC server is provided by the `agentirc-cli` package; `culture server` is a passthrough."
- A doc that linked to `protocol/extensions/<file>.md` → repoint at `https://github.com/agentculture/agentirc/blob/main/protocol/extensions/<file>.md`.
- A doc that documented `python -m culture.agentirc` → remove or replace with `agentirc` / `python -m agentirc`.

Be surgical — do not rewrite docs beyond the gaps the audit names.

- [ ] **Step 10.3: Commit doc fixes (if any).**

```bash
git add docs/ README.md  # or whatever paths the audit touched
git commit -m "docs: update references for agentirc extraction"
```

---

## Task 11 — Final verification, push, and PR

**Files:** none (workflow only).

- [ ] **Step 11.1: Format-before-commit safety net.**

```bash
uv run black culture tests
uv run isort culture tests
```

If either reformats anything, stage and amend the most recent commit:

```bash
git status --short
git add -A
git commit --amend --no-edit
```

- [ ] **Step 11.2: Run the full test suite, parallel.**

```bash
/run-tests
```

Expected: all green. Address any failures before continuing.

- [ ] **Step 11.3: Run pre-commit on all changes.**

```bash
uv run pre-commit run --files $(git diff --name-only main...HEAD)
```

Expected: all hooks pass. Fix any flake8/pylint/bandit/markdownlint complaints inline; amend onto the most recent commit.

- [ ] **Step 11.4: Final coupling sweep.**

```bash
grep -rn "culture\.agentirc\|protocol/extensions" culture tests docs
```

Expected: no output (except possibly in `CHANGELOG.md`, where references like `culture.agentirc.client` legitimately appear in the breaking-changes notes — those are fine).

- [ ] **Step 11.5: Push the branch.**

```bash
git push -u origin agentirc-extraction-cutover
```

- [ ] **Step 11.6: Open the PR.**

```bash
gh pr create --base main --title "feat: extract IRCd to agentirc-cli; culture server becomes passthrough" --body "$(cat <<'EOF'
## Summary

- Cuts culture over to consume `agentirc-cli>=9.0,<10.0` from PyPI.
- Deletes `culture/agentirc/` (~3,600 LOC); moves client transport to `culture/transport/`; replaces `culture/cli/server.py` with a 1:1 argparse-`REMAINDER` passthrough into `agentirc.cli.dispatch`.
- Deletes `protocol/extensions/` (it lives in agentirc's repo now).
- Major version bump.

User-visible behavior is unchanged: every `culture server <verb> <args>` invocation continues to work, with flags, output, and exit codes coming from `agentirc-cli`. On-disk footprint (config path, sockets, systemd units, logs) is unchanged.

Spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md` (Track A).

## Test plan

- [x] Existing test suite passes (`/run-tests`).
- [x] New `tests/test_server_shim.py` asserts every verb in `agentirc --help` is reachable via `culture server <verb> --help`.
- [x] New `tests/test_agentirc_smoke.py` boots `agentirc serve` as a subprocess and TCP-connects to it.
- [ ] Post-merge: verify on the spark host that `culture-agent-spark-culture.service` restarts cleanly with the new dependency, and that `culture server status` and `agentirc status` produce identical output (Track C).

- Claude
EOF
)"
```

Expected: PR URL printed. Confirm the PR is open and CI starts.

- [ ] **Step 11.7: Wait for CI and reviewers.**

After CI runs:

```bash
gh pr checks
```

If anything fails, fix inline, push, and use `/sonarclaude` before declaring ready (per culture's CLAUDE.md). Use `/cicd` (renamed from `/pr-review` in culture 8.8.1) to handle automated reviewer comments.

---

## Post-merge (Track C — out of scope for this plan, but on the to-do list)

After this PR merges and a culture release is published:

- `pip install -U culture` on the host running `culture-agent-spark-culture.service`.
- Confirm the service restarts cleanly with the new `agentirc-cli` dependency.
- Confirm `culture server status` and `agentirc status` produce byte-identical output.
- Confirm an existing peer link still establishes.

If anything misbehaves on a real deployment, file follow-up issues; do not roll back unless behavior is broken (the dependency pin `agentirc-cli>=9.0,<10.0` already protects against accidental 10.0 upgrades).
