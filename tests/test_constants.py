"""Unit tests for culture/cli/shared/constants.py.

Issue #302 turned `culture_runtime_dir()` into the single source of truth
for the daemon socket directory. Pin its contract so any future refactor
that breaks the env-var precedence, fallback path, or 0700 mode is caught
in CI rather than at runtime on macOS.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from culture.cli.shared.constants import culture_runtime_dir


def test_uses_xdg_runtime_dir_when_set(monkeypatch, tmp_path):
    """When XDG_RUNTIME_DIR is set, the value is returned verbatim."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert culture_runtime_dir() == str(tmp_path)


def test_falls_back_to_culture_run_when_xdg_unset(monkeypatch, tmp_path):
    """When XDG_RUNTIME_DIR is missing, fall back to ~/.culture/run/.

    macOS regression: this used to fall back to /tmp in the daemons but to
    ~/.culture/run/ in the CLI. The fix converged everyone on the latter.
    """
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = str(tmp_path / ".culture" / "run")
    assert culture_runtime_dir() == expected


def test_creates_fallback_dir_with_user_only_permissions(monkeypatch, tmp_path):
    """The fallback directory is created mode 0700 (user-private).

    /tmp would have been world-writable; ~/.culture/run/ is mode 0700 so
    sockets there cannot be observed or hijacked by other local users.
    """
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    path = Path(culture_runtime_dir())
    assert path.exists()
    assert path.is_dir()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o700, f"expected mode 0700, got {oct(mode)}"


def test_enforces_permissions_on_existing_dir(monkeypatch, tmp_path):
    """Even if the fallback dir already exists with wrong perms, it's tightened.

    Defensive: a previous version of culture (or a hand-created dir) may
    have left ~/.culture/run/ at 0755. Re-tighten on every call so the
    invariant always holds at runtime.
    """
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    pre_existing = tmp_path / ".culture" / "run"
    pre_existing.mkdir(parents=True, mode=0o755)
    os.chmod(pre_existing, 0o755)  # noqa: S103 — intentional: test that loose perms get tightened

    culture_runtime_dir()

    mode = stat.S_IMODE(os.stat(pre_existing).st_mode)
    assert mode == 0o700
