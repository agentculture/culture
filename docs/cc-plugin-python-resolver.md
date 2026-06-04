---
title: "CC plugin python interpreter resolver"
parent: "Operator guide"
nav_order: 15
---

# CC plugin python interpreter resolver (v9.1.4+)

The CC plugin's hooks (SessionStart, SessionEnd, etc.) run under bare
`python3` from `~/.claude/settings.json`. The hooks themselves are
intentionally dependency-free, so that's fine — but they then **spawn**
`python -m culture …` subprocesses (`culture bridge start`,
`culture boss spawn`, `culture boss close`) that need PyYAML and the
rest of culture's deps. If the bare `python3` lacks those, the spawn
dies with `ModuleNotFoundError`.

The v9.1.4 python resolver picks the right interpreter for these
spawned subprocesses.

## The bug v9.1.4 fixed

```text
culture-bridge: BRIDGE SPAWN FAILED for nick `plenty-ai-guide-mobile`.
Reason: bridge spawn exited 1:
  File "/.../culture/__main__.py", line 3, in <module>
    from culture.cli import main
  File "/.../culture/cli/agent.py", line 14, in <module>
    import yaml
ModuleNotFoundError: No module named 'yaml'
This session is NOT on the mesh.
```

Pre-9.1.4, the hook spawned the bridge with
`[sys.executable, "-m", "culture", ...]`. When CC fires a hook,
`sys.executable` is whatever `python3` PATH resolved to —
typically the system python (no yaml), not the culture repo's
`.venv/bin/python3` (which has yaml).

The honesty layer from v9.1.2 worked correctly — it surfaced the
full traceback. The interpreter selection was just wrong.

## How the resolver picks an interpreter

`culture/clients/claude/cc_plugin/_python_resolver.py::culture_python()`
returns `[python_path, "-m", "culture"]` via a three-step ladder.
Top-down, fail-loud at every boundary:

### Step 1 — `$CULTURE_PYTHON` env override

If `CULTURE_PYTHON` is set, validate it can `import culture` (one
subprocess probe, 2s timeout). If yes, use it. If the env var is
set but the named interpreter cannot import culture, **fail hard**
with a clear error — silently falling through would mask the
operator's explicit configuration.

```bash
# Force the resolver to use a specific interpreter
export CULTURE_PYTHON=/Users/test/Documents/GitHub/culture/.venv/bin/python3
```

Use cases for the override:
- Nix / distroless / chroot environments where step (2) can't see
  the repo.
- CI environments where the venv layout differs from a normal
  `uv sync` checkout.
- Pinning to a non-default culture installation for testing.

### Step 2 — Repo walk

Walk parent directories of `os.path.realpath(__file__)` looking for
a directory containing both `pyproject.toml` (with line-anchored
`name = "culture"`) **AND** `.venv/bin/python3`. The hook's
resolved location is ground truth: the repo CC was told to wire up
at install time (`install.py` embeds absolute hook paths into
settings.json) is exactly the repo whose `.venv` is the right
interpreter.

Defenses:
- **`os.path.realpath`**, not `abspath` — defeats symlink traps.
- **Line-anchored regex** for `name = "culture"` — a pyproject for
  a different project that *mentions* culture in a comment or
  description does not match.
- **No subprocess probe** — file structure is the proof; subprocess
  was the slow + race-prone alternative the original blueprint had
  before adversarial critique trimmed it.

### Step 3 — `sys.executable` fallback

Last resort: `[sys.executable, "-m", "culture"]` **plus** a clear
stderr warning naming the bug class. This is the SAME interpreter
that was failing before v9.1.4 — we keep the step so a completely
novel topology doesn't hard-fail SessionStart, but we do NOT
pretend the result is healthy. The SessionStart honesty layer will
surface any subsequent `ModuleNotFoundError` via the bridge-spawn
failure path so the operator sees a real error and can set
`CULTURE_PYTHON`.

## What about culture boss spawn limits

The v9.1.4 nick fix lifted the resolver's internal `_MAX_LEN = 14`
limit to a single boundary clip at `_BRIDGE_MAX_LEN = 64`. That
matches the cap `culture/cli/bridge.py::_validate_nick` enforces.

To prevent boss-spawn overflow on long-named parents,
`culture/cli/boss.py::_cmd_spawn` now rejects any
`<boss>-<suffix>` combination that exceeds 64 characters with a
clear cause:

```text
Error: worker nick 's...-w...' is 71 chars, exceeding the
64-char limit (culture/cli/bridge.py::_validate_nick). Shorten
either the boss nick (CULTURE_BOSS_NICK) or the worker suffix.
```

## Operator quick reference

```bash
# What interpreter would the resolver pick right now?
python3 -c "
import sys
sys.path.insert(0, '/Users/test/Documents/GitHub/culture')
from culture.clients.claude.cc_plugin import _python_resolver
print(_python_resolver.culture_python())
"

# Force a specific interpreter
CULTURE_PYTHON=/path/to/python3 claude

# Verify your venv has culture importable
/path/to/.venv/bin/python3 -c "import culture; import yaml; print('ok')"
```

## What this does NOT do

- **No multi-process cache.** Each CC hook fires a new Python
  interpreter, so the cache lifetime is one hook invocation. A
  per-host cache would race on first-run install across concurrent
  CC sessions.
- **No PATH search for a `culture` script.** The blueprint
  considered `shutil.which("culture")` as a fallback step, but
  adversarial critique flagged PATH-trust + shebang-trust failure
  modes outweighing the value when step (2) already gives the
  ground truth for development checkouts.
