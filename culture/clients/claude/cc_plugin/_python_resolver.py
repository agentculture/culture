"""Pick the right Python interpreter for spawning ``python -m culture …``
from a CC hook.

Background
==========

The CC plugin's hooks live at e.g.
``<repo>/culture/clients/claude/cc_plugin/hooks/session_start.py`` and
are wired into ``~/.claude/settings.json`` as
``python3 /abs/path/to/session_start.py`` (see ``install.py``). When CC
fires a hook, that command runs under whatever ``python3`` PATH
resolves to in CC's environment — typically the system / Homebrew
python, which is **not** the culture repo's ``.venv/bin/python3``.

The hook itself is intentionally dependency-free, so the bare python3
runtime is fine for it. But the hook then spawns
``[sys.executable, "-m", "culture", "bridge", "start", nick]`` — and
``sys.executable`` is that same bare python3, which has no PyYAML
installed. The bridge then dies with::

    ModuleNotFoundError: No module named 'yaml'

…and the SessionStart honesty layer (v9.1.2) faithfully surfaces it as
a "BRIDGE SPAWN FAILED" block. The fix isn't to suppress the failure —
it's to spawn the bridge with the **right** interpreter.

Resolution ladder (top-down)
============================

This module exposes :func:`culture_python` returning the ``argv``
prefix for spawning culture code. Three steps, fail-loud at every
boundary:

1. ``$CULTURE_PYTHON`` env override — operator escape hatch.
   Validated by running ``<path> -c 'import culture'`` with a 2s
   timeout. If the env var is set but the named interpreter cannot
   import culture, **fail hard** rather than silently fall through:
   the operator explicitly picked this interpreter and a silent
   fallback would mask their misconfiguration.

2. **Repo-walk from** ``os.path.realpath(__file__)`` looking for
   ``.venv/bin/python3`` next to a ``pyproject.toml`` whose first
   line-anchored ``name = "culture"`` declaration matches. This is
   the ground-truth path: the hook script's resolved location is
   exactly the repo CC was told to wire up at install time
   (``install.py`` embeds absolute hook paths into settings.json).
   No subprocess probe needed — the file structure is the proof.

3. **Last resort:** ``[sys.executable, "-m", "culture"]`` with a
   stderr warning explaining that this is the fallback and is
   likely to fail under exactly the conditions that triggered it.
   We keep the step so a completely novel topology (Nix flake,
   distroless container, frozen tools) doesn't hard-deadlock, but
   we do NOT pretend it's a success.

Steps (3) and (4) from the earlier blueprint draft (sibling
``culture`` launcher + PATH ``culture`` launcher with subprocess
verification) were dropped during adversarial review: each added
2s latency, races on the first-run cache, and PATH-trust failure
modes outweighing their value when step (2) already gives the
ground truth for development checkouts.

Caching
=======

The resolved argv-prefix is cached at module level — one resolution
per hook process. Each CC hook fires a new Python interpreter so the
cache lifetime is the lifetime of one hook invocation; this avoids
the global-cache race a per-host file cache would create.

Operator override
=================

Setting ``CULTURE_PYTHON=/path/to/python3`` in the environment that
runs CC (e.g. exported in ``~/.zshrc`` / ``~/.profile``) forces the
ladder to use that interpreter. Useful when:

- The operator wants to run culture against a non-default venv.
- Nix / distroless / chroot environments where step (2)'s
  filesystem walk can't see the repo.
- CI environments where the venv layout differs from a normal
  ``uv sync`` checkout.

The override is validated before use — a broken value fails fast
with a clear error rather than silently falling through.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

# Sentinel for the cache. ``None`` means "not yet resolved"; a list
# means "resolved, here it is".
_CACHED_PREFIX: list[str] | None = None

# How long ``CULTURE_PYTHON`` validation is allowed to take. Keep
# small: this runs synchronously in the SessionStart hook which has
# a ~60s budget but should return in well under 1s in practice.
_VALIDATION_TIMEOUT_SECONDS = 2.0

# Line-anchored match for ``name = "culture"`` in pyproject.toml so a
# pyproject under e.g. a vendored / fork / template tree that simply
# *mentions* culture in a comment doesn't trigger a false positive.
# Matches both single- and double-quoted forms; tolerates whitespace.
_CULTURE_PYPROJECT_NAME_RE = re.compile(
    r'^\s*name\s*=\s*["\']culture["\']\s*(?:#.*)?$',
    re.MULTILINE,
)


def _is_executable(path: str) -> bool:
    """``os.access(X_OK)`` plus ``os.path.isfile`` — cheap negative
    check that avoids spawning a subprocess against a directory."""
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _validate_can_import_culture(python_path: str) -> tuple[bool, str]:
    """Probe ``<python_path> -c 'import culture'`` with a short
    timeout. Returns ``(ok, message)``. ``ok=False`` carries a
    diagnostic line in ``message`` describing what went wrong — used
    by the operator-facing error when ``CULTURE_PYTHON`` is set but
    broken.

    Catches every failure mode (timeout, OSError, non-zero exit) so
    the caller can decide fail-hard vs fall-through without needing
    to know the exception taxonomy. Single subprocess call; no shell.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv shape, no shell
            [python_path, "-c", "import culture"],
            capture_output=True,
            timeout=_VALIDATION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {_VALIDATION_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return False, f"OSError: {exc}"
    if proc.returncode == 0:
        return True, ""
    err = proc.stderr.decode(errors="replace").strip()
    return False, err or f"exit {proc.returncode}"


def _check_env_override() -> list[str] | None:
    """Resolve step (1): ``$CULTURE_PYTHON`` env override.

    Returns the argv-prefix when the override is set AND validates.
    Raises ``RuntimeError`` when the env var is set but the named
    interpreter cannot import culture — silently falling through
    would mask an explicit operator configuration. Returns ``None``
    when the env var is unset (the resolver moves to step 2).
    """
    raw = os.environ.get("CULTURE_PYTHON", "").strip()
    if not raw:
        return None
    if not _is_executable(raw):
        raise RuntimeError(
            f"CULTURE_PYTHON={raw!r} is set but the path is not "
            "executable. Set CULTURE_PYTHON to the absolute path of a "
            "Python interpreter that has the culture package importable, "
            "or unset CULTURE_PYTHON to let the resolver auto-detect."
        )
    ok, message = _validate_can_import_culture(raw)
    if not ok:
        raise RuntimeError(
            f"CULTURE_PYTHON={raw!r} cannot import culture: {message}. "
            "Either install culture into that interpreter "
            "(`<that-python> -m pip install -e <culture-repo>`) "
            "or unset CULTURE_PYTHON to let the resolver auto-detect."
        )
    return [raw, "-m", "culture"]


def _walk_to_culture_repo_root(start: str) -> str | None:
    """Walk parent directories of ``start`` looking for a directory
    that contains both ``pyproject.toml`` (with line-anchored
    ``name = "culture"``) AND ``.venv/bin/python3``.

    Anchors on ``os.path.realpath`` to defeat symlink traps — a
    relocatable ``__file__`` could otherwise have its path components
    redirected (security review concern: an attacker who controls one
    component of __file__'s path could otherwise plant a fake
    pyproject.toml + fake .venv on the walk).

    Returns the matching repo root or ``None``. Bounded by
    ``os.path.dirname`` returning the same value at the FS root.
    """
    resolved = os.path.realpath(start)
    current = os.path.dirname(resolved)
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        pyproject = os.path.join(current, "pyproject.toml")
        venv_python = os.path.join(current, ".venv", "bin", "python3")
        if os.path.isfile(pyproject) and _is_executable(venv_python):
            # Cheap line-anchored scan — DO NOT import tomllib (keeps
            # this module dependency-free for Python 3.10 hosts) and
            # DO NOT substring-match (a pyproject for a different
            # project that comments "name = 'culture'" should not
            # match).
            try:
                with open(pyproject, encoding="utf-8") as fh:
                    content = fh.read(8192)  # culture's [project] block is in first ~1KB
            except OSError:
                content = ""
            if _CULTURE_PYPROJECT_NAME_RE.search(content):
                return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def _check_repo_walk() -> list[str] | None:
    """Resolve step (2): walk up from this file looking for a culture
    repo with a sibling ``.venv/bin/python3``.

    Returns the argv-prefix when found, ``None`` otherwise. This is
    the load-bearing step for development checkouts and any install
    that wired CC to a culture repo whose ``.venv`` is intact.
    """
    repo_root = _walk_to_culture_repo_root(__file__)
    if repo_root is None:
        return None
    venv_python = os.path.join(repo_root, ".venv", "bin", "python3")
    return [venv_python, "-m", "culture"]


def _fallback_sys_executable() -> list[str]:
    """Step (3): ``[sys.executable, '-m', 'culture']`` with a stderr
    warning. This is the SAME interpreter that was failing before
    9.1.4 — we keep the step so a never-seen topology doesn't hard-
    fail SessionStart, but we do NOT pretend the result is healthy.

    The honesty layer in ``session_start.py:139-141`` will surface
    the subsequent ``ModuleNotFoundError`` via the bridge-spawn
    failure path — at which point the operator sees a real error
    and can set ``CULTURE_PYTHON`` or run from a culture-aware shell.
    """
    sys.stderr.write(
        "culture._python_resolver: WARNING — no CULTURE_PYTHON override "
        "and no .venv/bin/python3 found by walking up from "
        f"{os.path.realpath(__file__)}. Falling back to sys.executable "
        f"({sys.executable}); if culture's deps (e.g. PyYAML) are not "
        "installed under that interpreter, the spawn will fail. Set "
        "CULTURE_PYTHON=/path/to/your/culture/venv/bin/python to fix.\n"
    )
    return [sys.executable, "-m", "culture"]


def culture_python() -> list[str]:
    """Return the argv-prefix for spawning culture as a subprocess.

    Result shape: ``[python_path, "-m", "culture"]``. Callers append
    the subcommand (``"bridge", "start", nick`` etc.) and pass to
    ``subprocess.Popen``.

    Cached at module level — one resolution per hook process. Raises
    ``RuntimeError`` only when ``CULTURE_PYTHON`` is explicitly set
    but broken; all other failure modes degrade through the ladder.
    """
    global _CACHED_PREFIX
    if _CACHED_PREFIX is not None:
        return list(_CACHED_PREFIX)

    # Step 1 — env override (may raise; that's intentional).
    prefix = _check_env_override()
    if prefix is not None:
        _CACHED_PREFIX = prefix
        return list(prefix)

    # Step 2 — repo-walk.
    prefix = _check_repo_walk()
    if prefix is not None:
        _CACHED_PREFIX = prefix
        return list(prefix)

    # Step 3 — last resort with explicit warning.
    prefix = _fallback_sys_executable()
    _CACHED_PREFIX = prefix
    return list(prefix)


def _reset_cache() -> None:
    """Test helper: drop the cache so subsequent calls re-resolve."""
    global _CACHED_PREFIX
    _CACHED_PREFIX = None
