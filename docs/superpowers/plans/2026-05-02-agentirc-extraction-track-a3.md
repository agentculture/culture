# AgentIRC Extraction — Track A3 (Final Cutover) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This plan supersedes** `docs/superpowers/plans/2026-04-30-agentirc-extraction-track-a.md`. The original drafted A1+A2+A3 as a single PR; phasing forced a split. A1 shipped in culture#309 (8.8.0); A2 ships in `feat/bots-public-extension-api` (8.9.0, see `docs/superpowers/plans/2026-05-02-agentirc-extraction-track-a2.md`); A3 is this plan.

**Goal:** Delete the bundled IRCd in `culture/agentirc/` and shim `culture server <verb>` to the installed `agentirc` binary. After A3, the only Python files remaining under `culture/agentirc/` are `config.py` (the A1 re-export shim) and `__init__.py`. Major version bump 8.x → 9.0.0.

**Architecture:** Single PR off `main`, after A2 has merged. `git rm` the IRCd source files (`ircd.py`, `server_link.py`, `channel.py`, `events.py`, the four stores, `rooms_util.py`, `skill.py`, `skills/`). `git mv` `client.py` and `remote_client.py` to `culture/transport/` (preserves blame; they were never IRCd code). Replace `culture/cli/server.py:_run_server` with a thin passthrough into `agentirc.cli.dispatch` (in-process) or `subprocess.run(["agentirc", *argv])` (subprocess) — Task 4 chooses. Delete `protocol/extensions/` (lives in agentirc's repo). Bump major.

**Tech Stack:** Python 3.x, uv, argparse, pytest + pytest-asyncio + pytest-xdist, pre-commit (black + isort + flake8 + pylint + bandit + markdownlint).

**Companion spec:** `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md` (sections "Boundary", "Architecture", "CLI surface", "Import contract").

---

## Preconditions (do not start until all are true)

- A2 has merged and a culture release has been cut. `culture/bots/*` no longer imports anything from `culture.agentirc.{ircd, server_link, channel, events, room_store, thread_store, history_store, rooms_util, skill}`. Verify:

  ```bash
  grep -rn 'from culture\.agentirc\.\(ircd\|server_link\|channel\|events\|room_store\|thread_store\|history_store\|rooms_util\|skill\)' culture/bots/ tests/
  ```

  Expected: no matches under `culture/bots/`. Test files may still hit IRCd internals — Task 7 cleans those up.

- `agentirc-cli>=9.5,<10` exposes the full CLI surface culture is shimming to:

  ```bash
  cd /tmp && uv run --with "agentirc-cli>=9.5,<10" agentirc --help
  ```

  Expected: subcommands `serve start stop restart status link logs version` (or a superset). If anything's missing, **stop** — the shim parity test (Task 5) will fail.

- `agentirc.cli.dispatch(argv) -> int` is callable in-process (verifies the shim path works without forking a subprocess):

  ```bash
  cd /tmp && uv run --with "agentirc-cli>=9.5,<10" python -c "
  from agentirc.cli import dispatch
  rc = dispatch(['version'])
  print('exit code:', rc)
  "
  ```

  Expected: agentirc prints its version and `exit code: 0`. If `dispatch` is not exposed (only `main()`), Task 4 must use the subprocess path.

- Working tree on `main` is clean. `git status` shows no staged/unstaged changes inside `culture/agentirc/`, `culture/cli/server.py`, `culture/cli/shared/mesh.py`, `culture/transport/`, `protocol/extensions/`, or related tests.

---

## File Structure (what changes in this PR)

| Path | Action | Notes |
|---|---|---|
| `pyproject.toml` | Modify | Drop `culture/agentirc/` from package data / wheel includes if explicitly listed. No dep change (A2 already pinned `>=9.5,<10`). |
| `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py` | Delete | The IRCd core. |
| `culture/agentirc/skills/` | Delete (whole dir) | Server-side skill plugins. |
| `culture/agentirc/__main__.py` | Delete | `python -m culture.agentirc` retired (no known callers). |
| `culture/agentirc/client.py` | `git mv` → `culture/transport/client.py` | Preserves blame. |
| `culture/agentirc/remote_client.py` | `git mv` → `culture/transport/remote_client.py` | Preserves blame. |
| `culture/agentirc/config.py` | Keep | A1 re-export shim over `agentirc.config`. Stays through 9.x; remove in 10.0.0. |
| `culture/agentirc/__init__.py` | Modify | Trim to a single re-export of `agentirc.config` symbols. |
| `culture/transport/__init__.py` | Create | Re-exports the public class names from `client.py` and `remote_client.py`. |
| `culture/cli/server.py` | Rewrite | ~25 lines. Argparse `REMAINDER` passthrough into `agentirc.cli.dispatch` or `subprocess.run`. |
| `culture/bots/`, `culture/clients/*/daemon.py` | Modify (imports only) | `culture.agentirc.{client,remote_client}` → `culture.transport.{client,remote_client}`. |
| `protocol/extensions/` | Delete (whole dir) | Lives in agentirc now. |
| `tests/test_*` for IRCd internals | Delete | They live in agentirc's repo now. |
| `tests/test_server_shim.py` | Create | Asserts every verb in `agentirc --help` is reachable via `culture server <verb> --help`. |
| `tests/test_agentirc_smoke.py` | Create | Boots `agentirc serve` as a subprocess, connects a culture transport client, exchanges one message. |
| `culture/__init__.py`, `pyproject.toml`, `CHANGELOG.md` | Modify | `/version-bump major` (8.9.x → 9.0.0). |

---

## Task 1 — Verify preconditions

**Files:** none (verification only).

- [ ] **Step 1.1:** Confirm A2 already cleaned up bot-side imports (run the grep from "Preconditions"). If any matches remain in `culture/bots/`, **stop** — A2 wasn't fully done.

- [ ] **Step 1.2:** Confirm the agentirc CLI surface (run the `agentirc --help` snippet from "Preconditions"). Save the output — Task 5's shim parity test reads from this.

- [ ] **Step 1.3:** Decide between in-process `dispatch` and subprocess `run`:

  - **In-process** (`from agentirc.cli import dispatch; return dispatch(argv)`) — same Python interpreter, same env, faster, easier to debug, but couples culture's process to agentirc internals.
  - **Subprocess** (`subprocess.run(["agentirc", *argv])`) — fully decoupled, works even if agentirc fails to import, but adds ~50ms fork overhead per invocation and may complicate signal handling for `culture server start` (which is long-running).

  Recommendation: **in-process** for everything except `start` (which is long-running and benefits from process isolation). Verify `dispatch` is exposed (run the snippet from "Preconditions"). **Record the decision in this task before continuing.**

---

## Task 2 — Branch and audit remaining culture-side imports of `culture.agentirc.*`

**Files:** none in this task (audit only).

- [ ] **Step 2.1:** Branch out.

  ```bash
  git checkout main && git pull
  git checkout -b feat/agentirc-extraction-cutover
  ```

- [ ] **Step 2.2:** Audit:

  ```bash
  grep -rn 'culture\.agentirc' culture/ tests/
  ```

  Tabulate. Expected categories:
  1. `from culture.agentirc.config import …` — keep (config.py stays as the A1 shim).
  2. `from culture.agentirc.client import Client` — Task 3 retargets to `culture.transport.client`.
  3. `from culture.agentirc.remote_client import RemoteClient` — Task 3 retargets to `culture.transport.remote_client`.
  4. `from culture.agentirc.{ircd,server_link,channel,events,…} import …` — Task 7 deletes / rewrites.
  5. `python -m culture.agentirc` invocations in scripts/CI — replace with `agentirc` (binary) or `python -m agentirc`.

  Anything outside these five categories is a surprise; investigate before deleting.

---

## Task 3 — Move `client.py` + `remote_client.py` to `culture/transport/`

**Files:** `culture/agentirc/client.py`, `culture/agentirc/remote_client.py`, `culture/transport/__init__.py`.

- [ ] **Step 3.1:** Create the destination directory:

  ```bash
  mkdir -p culture/transport
  ```

- [ ] **Step 3.2:** `git mv` (preserves blame):

  ```bash
  git mv culture/agentirc/client.py culture/transport/client.py
  git mv culture/agentirc/remote_client.py culture/transport/remote_client.py
  ```

- [ ] **Step 3.3:** Create `culture/transport/__init__.py` re-exporting the public class names:

  ```python
  """Culture's IRC transport — TCP client + remote-server-link client."""

  from culture.transport.client import Client
  from culture.transport.remote_client import RemoteClient

  __all__ = ["Client", "RemoteClient"]
  ```

  Names to re-export are whatever the moved files publicly defined; `grep -E '^class |^def ' culture/transport/{client,remote_client}.py` to enumerate. Don't speculate — match what was already there.

- [ ] **Step 3.4:** Inside `culture/transport/client.py`, replace any IRC-verb / numeric / extension-tag string literals with `agentirc.protocol.*` constants. Survey first:

  ```bash
  grep -nE '"(JOIN|PART|PRIVMSG|NOTICE|NAMES|WHO|MODE|CAP|EVENT|EVENTSUB|EVENTPUB|EVENTUNSUB|EVENTERR|SEVENT|SMSG|THREAD|ROOM|HISTORY|DEFAULT|RENAME|ARCHIVE|UNARCHIVE|[0-9]{3})"' culture/transport/client.py
  ```

  Replace each match with the named constant from `agentirc.protocol`. This is the same retargeting A1 did for `culture/cli/shared/mesh.py` against `agentirc.config` — the pattern is mechanical. If `agentirc.protocol` doesn't expose a constant for a verb culture uses, file an issue against agentirc and keep the string literal for now (mark with a `# TODO(A3)` comment).

- [ ] **Step 3.5:** Update every importer in culture:

  ```bash
  grep -rln 'from culture\.agentirc\.client import\|from culture\.agentirc\.remote_client import' culture/ tests/ | xargs sed -i 's|from culture\.agentirc\.client import|from culture.transport.client import|g; s|from culture\.agentirc\.remote_client import|from culture.transport.remote_client import|g'
  ```

  Spot-check the diff before staging.

- [ ] **Step 3.6:** Run a focused test pass:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_*client*.py
  ```

  Expected: green. Transport behavior is unchanged; only the import path moved.

---

## Task 4 — Replace `culture/cli/server.py:_run_server` with a passthrough shim

**Files:** `culture/cli/server.py`.

- [ ] **Step 4.1:** Read the current `culture/cli/server.py` and identify the exact entrypoint culture's CLI dispatcher calls (likely `_run_server(args)` or similar). Note the function signature and return-type contract.

- [ ] **Step 4.2:** Replace with the shim. Preferred shape (in-process, per Task 1.3 decision):

  ```python
  """culture server <verb> — passthrough into the installed agentirc CLI."""
  from agentirc.cli import dispatch as _agentirc_dispatch

  def server(argv: list[str]) -> int:
      """culture server <verb> <args> → agentirc <verb> <args>."""
      return _agentirc_dispatch(argv)
  ```

  If Task 1.3 chose subprocess for `start`, special-case it:

  ```python
  import os
  import subprocess
  from agentirc.cli import dispatch as _agentirc_dispatch

  def server(argv: list[str]) -> int:
      if argv and argv[0] == "start":
          # long-running; isolate in subprocess so a culture upgrade can replace it cleanly
          return subprocess.run(["agentirc", *argv]).returncode
      return _agentirc_dispatch(argv)
  ```

  Properties to preserve:
  - **Pure forwarding.** Culture does not parse, validate, or rename any flag. New verbs added in agentirc are reachable via `culture server <new-verb>` automatically.
  - **Single source of truth.** Help text, error messages, exit codes — all from agentirc.

- [ ] **Step 4.3:** Wire the shim into culture's CLI dispatcher. The dispatcher (in `culture/cli/__init__.py` or `culture/cli/main.py`) probably calls `_run_server(args)` today; switch it to call `server(argv)`.

- [ ] **Step 4.4:** Manual smoke:

  ```bash
  uv run python -m culture server --help
  uv run python -m culture server status
  uv run python -m culture server version
  ```

  Each should produce the same output as `agentirc --help`, `agentirc status`, `agentirc version`. Differences are bugs in the shim, not features.

---

## Task 5 — Add the shim parity test

**Files:** `tests/test_server_shim.py`.

- [ ] **Step 5.1:** Create the test:

  ```python
  """Asserts culture server <verb> is byte-identical to agentirc <verb>."""
  import subprocess
  import sys

  import pytest

  CULTURE = [sys.executable, "-m", "culture", "server"]
  AGENTIRC = ["agentirc"]

  def _run(cmd: list[str]) -> tuple[int, str, str]:
      proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
      return proc.returncode, proc.stdout, proc.stderr

  def test_help_matches() -> None:
      culture_rc, culture_out, culture_err = _run([*CULTURE, "--help"])
      agentirc_rc, agentirc_out, agentirc_err = _run([*AGENTIRC, "--help"])
      assert culture_rc == agentirc_rc
      # The argv[0] in usage lines will differ ("culture server" vs "agentirc"); strip it.
      def _strip_argv0(text: str) -> str:
          return "\n".join(
              line.replace("culture server", "agentirc")
              for line in text.splitlines()
          )
      assert _strip_argv0(culture_out) == agentirc_out

  @pytest.mark.parametrize("verb", ["status", "version", "logs"])
  def test_verb_help_matches(verb: str) -> None:
      culture_rc, culture_out, _ = _run([*CULTURE, verb, "--help"])
      agentirc_rc, agentirc_out, _ = _run([*AGENTIRC, verb, "--help"])
      assert culture_rc == agentirc_rc
      def _strip_argv0(text: str) -> str:
          return "\n".join(line.replace("culture server", "agentirc") for line in text.splitlines())
      assert _strip_argv0(culture_out) == agentirc_out
  ```

  Pattern lifted from PR #309's `tests/test_agentirc_config_shim.py::test_shim_is_identity` — same identity-invariant style.

- [ ] **Step 5.2:** Run it:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_server_shim.py
  ```

---

## Task 6 — Add the cross-repo smoke test

**Files:** `tests/test_agentirc_smoke.py`.

- [ ] **Step 6.1:** Create a test that boots a real `agentirc serve` subprocess on a random port, connects a `culture.transport.Client`, and exchanges a `PRIVMSG`:

  ```python
  """End-to-end smoke: culture transport client talks to a real agentirc subprocess."""
  import asyncio
  import socket
  import subprocess
  import sys
  from pathlib import Path

  import pytest

  from culture.transport import Client


  def _free_port() -> int:
      with socket.socket() as s:
          s.bind(("127.0.0.1", 0))
          return s.getsockname()[1]


  @pytest.mark.asyncio
  async def test_agentirc_subprocess_smoke(tmp_path: Path) -> None:
      port = _free_port()
      cfg = tmp_path / "server.yaml"
      cfg.write_text(f"name: smoke\nirc_port: {port}\nwebhook_port: 0\n")
      proc = subprocess.Popen(
          ["agentirc", "serve", "--config", str(cfg)],
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
      )
      try:
          await asyncio.sleep(1.0)  # give the daemon a beat to bind
          client = Client(host="127.0.0.1", port=port, nick="smoke")
          await client.connect()
          await client.privmsg("smoke", "hello")
          await client.disconnect()
      finally:
          proc.terminate()
          proc.wait(timeout=5)
  ```

  This is the *only* cross-repo integration test in culture — it imports nothing from agentirc internals; it just confirms the wire works.

- [ ] **Step 6.2:** Run it:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q tests/test_agentirc_smoke.py
  ```

---

## Task 7 — Delete the bundled IRCd

**Files:** `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py`, `culture/agentirc/skills/`, `culture/agentirc/__main__.py`, `culture/agentirc/__init__.py`, `protocol/extensions/`, IRCd-internal tests under `tests/`.

- [ ] **Step 7.1:** `git rm` the IRCd source:

  ```bash
  git rm culture/agentirc/ircd.py
  git rm culture/agentirc/server_link.py
  git rm culture/agentirc/channel.py
  git rm culture/agentirc/events.py
  git rm culture/agentirc/room_store.py
  git rm culture/agentirc/thread_store.py
  git rm culture/agentirc/history_store.py
  git rm culture/agentirc/rooms_util.py
  git rm culture/agentirc/skill.py
  git rm -r culture/agentirc/skills/
  git rm culture/agentirc/__main__.py
  ```

- [ ] **Step 7.2:** Trim `culture/agentirc/__init__.py` to just the A1 re-export shim:

  ```python
  """A1-introduced re-export shim — kept through the 9.x line, removed in 10.0.0.

  All new code should import from `agentirc.config` directly.
  """
  from culture.agentirc.config import ServerConfig, LinkConfig, PeerSpec, TelemetryConfig

  __all__ = ["ServerConfig", "LinkConfig", "PeerSpec", "TelemetryConfig"]
  ```

  Match the actual exports of `culture/agentirc/config.py` after A1; don't speculate.

- [ ] **Step 7.3:** `git rm` `protocol/extensions/`:

  ```bash
  git rm -r protocol/extensions/
  ```

  These were already moved to agentirc's repo as part of Track B (see spec). The culture-side directory is dead weight.

- [ ] **Step 7.4:** Delete IRCd-internal tests. Survey:

  ```bash
  grep -rln 'from culture\.agentirc\.\(ircd\|server_link\|channel\|events\|room_store\|thread_store\|history_store\|rooms_util\|skill\)' tests/
  ```

  For each match, `git rm` the test file. They live in agentirc's repo now (per Track B's test-suite migration). Examples likely to appear: `tests/test_ircd_*.py`, `tests/test_server_link_*.py`, `tests/test_channel_*.py`, `tests/test_room_store_*.py`, etc.

  **Caveat:** preserve any test that survives because it covers culture's *use* of these modules, not the modules themselves. Such tests are rare after A2; if you find one, list it for review before deleting.

- [ ] **Step 7.5:** Run the full suite:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q
  ```

  Expected: green. The remaining tests cover transport, bots (now CAP clients), CLI, telemetry, and the new shim/smoke tests.

---

## Task 8 — Doc-test alignment + pre-push reviewer + version bump

**Files:** `culture/__init__.py`, `pyproject.toml`, `CHANGELOG.md`, plus any docs surfaced by the audit.

- [ ] **Step 8.1:** Run `doc-test-alignment`:

  ```bash
  Agent(subagent_type="doc-test-alignment", prompt="Audit the staged diff on feat/agentirc-extraction-cutover for new public surface and report missing docs/ or protocol/extensions/ coverage. Note that protocol/extensions/ was deleted in this PR — references to it in docs/ should be removed or pointed at agentirc's repo.")
  ```

  Address findings: update `docs/` references, point at agentirc's docs where appropriate, remove dead links.

- [ ] **Step 8.2:** Stage, then run `superpowers:code-reviewer`:

  ```bash
  git add -A
  ```

  Then invoke `superpowers:code-reviewer` per culture/CLAUDE.md's "Pre-push review for library/protocol code" rule. A3 is structural — deleting code, moving call sites, swapping a process boundary. The reviewer catches anything that breaks an exception-handling chain or leaves dangling imports.

- [ ] **Step 8.3:** `/version-bump major` (8.9.x → 9.0.0). A3 deletes in-tree code and changes the launch model from in-process to (potentially) subprocess; that's structural, even though user-visible CLI is unchanged.

- [ ] **Step 8.4:** Edit the new `[9.0.0]` section of `CHANGELOG.md`:

  ```markdown
  ### Changed (breaking)
  - The bundled IRCd in `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py` and `culture/agentirc/skills/` is gone; `culture server <verb>` now passes through to the installed `agentirc-cli` binary. User-visible behavior is unchanged.
  - `python -m culture.agentirc` is removed (no known callers). Use `agentirc` (CLI) or `python -m agentirc`.
  - `protocol/extensions/` moved to agentirc's repo.
  - `culture/agentirc/{client,remote_client}.py` moved to `culture/transport/`. Code that imported them under the old path needs to update imports — `culture/bots/*` and `culture/clients/*/daemon.py` are already updated.

  ### Kept (transitional)
  - `culture/agentirc/config.py` remains as a re-export shim over `agentirc.config` through the 9.x line; remove in 10.0.0.

  ### Notes
  - Final phase of the agentirc extraction. A1 (config dataclasses, 8.8.0, #309) and A2 (bot framework rewrite, 8.9.0) preceded this. Spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md`.
  ```

- [ ] **Step 8.5:** Final test pass + format check:

  ```bash
  bash .claude/skills/run-tests/scripts/test.sh -p -q
  uv run black culture/ tests/
  uv run isort culture/ tests/
  ```

- [ ] **Step 8.6:** Commit and push:

  ```bash
  git commit -m "$(cat <<'EOF'
  feat!: delete bundled IRCd; shim culture server to agentirc-cli (Track A3)

  Final phase of the agentirc extraction. Deletes culture/agentirc/{ircd,
  server_link,channel,events,...} and protocol/extensions/. Moves
  client.py + remote_client.py to culture/transport/. Replaces
  culture/cli/server.py:_run_server with a passthrough into
  agentirc.cli.dispatch. culture/agentirc/config.py stays as the A1
  re-export shim through 9.x.

  BREAKING CHANGE: python -m culture.agentirc is gone. User-visible
  culture server <verb> behavior is unchanged.

  - Claude

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  git push -u origin feat/agentirc-extraction-cutover
  ```

- [ ] **Step 8.7:** Open the PR:

  ```bash
  gh pr create --title "Phase A3: delete bundled IRCd; shim culture server to agentirc-cli" --body "$(cat <<'EOF'
  ## Summary

  - Delete `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py` and `culture/agentirc/skills/` — the bundled IRCd is gone. Final phase of the agentirc extraction.
  - `git mv culture/agentirc/{client,remote_client}.py → culture/transport/` (preserves blame). Update importers across `culture/bots/*` and `culture/clients/*/daemon.py`.
  - Replace `culture/cli/server.py:_run_server` with a thin passthrough into `agentirc.cli.dispatch`. New verbs added in agentirc reach `culture server <verb>` automatically.
  - Delete `protocol/extensions/` (lives in agentirc's repo now).
  - Keep `culture/agentirc/config.py` as the A1 re-export shim through 9.x; removed in 10.0.0.
  - `8.9.x → 9.0.0` (major — structural change, even though user-visible CLI is unchanged).

  ## Why

  Final phase of the agentirc extraction (spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md`). A1 (config dataclasses, #309) and A2 (bot framework rewrite, see Track A2 plan) preceded this; A3 completes the cutover.

  ## Test plan

  - [x] `bash .claude/skills/run-tests/scripts/test.sh -p -q` — full suite green
  - [x] New `tests/test_server_shim.py` asserts every verb in `agentirc --help` is reachable via `culture server <verb> --help` with byte-identical output
  - [x] New `tests/test_agentirc_smoke.py` boots `agentirc serve` as a subprocess and TCP-connects via `culture.transport.Client`
  - [x] Pre-commit hooks clean
  - [x] `superpowers:code-reviewer` clean
  - [x] `doc-test-alignment` clean

  ## Migration notes for users

  - `python -m culture.agentirc` is gone. Run `agentirc` (CLI) or `python -m agentirc` instead.
  - Code that imports from `culture.agentirc.{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}` is broken. There is no replacement in culture; depend on `agentirc-cli` and import from `agentirc.*`.
  - `culture.agentirc.{client,remote_client}` imports — update to `culture.transport.{client,remote_client}`.
  - `culture.agentirc.config` imports — still work; remove in 10.0.0.

  - Claude

  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  EOF
  )"
  ```

- [ ] **Step 8.8:** Wait for CI:

  ```bash
  gh pr checks
  ```

  If anything fails, fix inline, push, run `/sonarclaude` before declaring ready (per culture/CLAUDE.md). Use the `pr-review` skill for automated reviewer comments.

---

## Summary

- Deletes `culture/agentirc/{ircd,server_link,channel,events,room_store,thread_store,history_store,rooms_util,skill}.py` + `skills/` (~3,600 LOC). Final structural change of the agentirc extraction.
- Moves `client.py` + `remote_client.py` to `culture/transport/` (`git mv` preserves blame).
- Replaces `culture/cli/server.py:_run_server` with a passthrough into `agentirc.cli.dispatch`.
- Deletes `protocol/extensions/` (lives in agentirc's repo).
- Keeps `culture/agentirc/config.py` as the A1 re-export shim through 9.x.
- Major version bump 8.9.x → 9.0.0.

User-visible behavior unchanged: every `culture server <verb> <args>` invocation continues to work, with flags / output / exit codes coming from `agentirc-cli`. On-disk footprint (config path, sockets, systemd units, logs) unchanged.

Spec: `docs/superpowers/specs/2026-04-30-agentirc-extraction-design.md`.

## Test plan

- [ ] Existing test suite passes (`bash .claude/skills/run-tests/scripts/test.sh -p -q`).
- [ ] New `tests/test_server_shim.py` asserts every verb in `agentirc --help` is reachable via `culture server <verb> --help`.
- [ ] New `tests/test_agentirc_smoke.py` boots `agentirc serve` as a subprocess and TCP-connects to it.
- [ ] Post-merge: verify on the spark host that `culture-agent-spark-culture.service` restarts cleanly with the new release; `culture server status` and `agentirc status` produce identical output (Track C verification).

---

## Post-merge (Track C — out of scope for this plan, but on the to-do list)

After this PR merges and a culture release is published:

- `pip install -U culture` on the spark host.
- Confirm `culture-agent-spark-culture.service` restarts cleanly.
- Confirm `culture server status` and `agentirc status` produce byte-identical output.
- Confirm an existing peer link still establishes (`culture server link` → other peer responds).
- Optionally: schedule a follow-up agent in 6 months to drop `culture/agentirc/config.py` (and the surrounding shim machinery) when culture cuts a 10.0.0 release.
