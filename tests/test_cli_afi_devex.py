"""Tests for culture.cli.afi + culture.cli.devex — passthrough wrappers.

Both modules wrap an external CLI (afi-cli / agex-cli) in the
`culture.cli._passthrough` plumbing. The shared plumbing itself is
covered by `tests/test_cli_passthrough.py`; this file exercises the
two thin adapters' `dispatch`, `_entry`, and `register` surfaces.

Also pins `culture.cli.shared.formatting` as a re-export of
`culture.formatting.relative_time` since that's a sub-10-line module
that's easier to test alongside.
"""

from __future__ import annotations

import argparse

import pytest

from culture.cli import afi, devex


def _ns(**kw):
    return argparse.Namespace(**kw)


# ---------------------------------------------------------------------------
# afi
# ---------------------------------------------------------------------------


def test_afi_dispatch_forwards_argv(monkeypatch):
    captured: list = []
    monkeypatch.setattr(
        "culture.cli._passthrough.run",
        lambda entry, argv: captured.append(argv),
    )
    afi.dispatch(_ns(afi_args=["--version"]))
    assert captured == [["--version"]]


def test_afi_dispatch_handles_missing_afi_args(monkeypatch):
    """If `args.afi_args` is None, dispatch normalises to an empty list."""
    captured: list = []
    monkeypatch.setattr(
        "culture.cli._passthrough.run",
        lambda entry, argv: captured.append(argv),
    )
    afi.dispatch(_ns(afi_args=None))
    assert captured == [[]]


def test_afi_entry_exits_when_afi_not_installed(monkeypatch):
    """`_entry` exits with rc 2 when `afi.cli` import fails."""
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    )

    def _blocked(name, *a, **kw):
        if name == "afi.cli":
            raise ImportError("afi-cli not installed in this env")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _blocked)
    with pytest.raises(SystemExit) as exc:
        afi._entry(["explain"])
    assert exc.value.code == 2


def test_afi_register_adds_subparser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    afi.register(sub)
    args = parser.parse_args(["afi", "audit", "--strict"])
    assert args.cmd == "afi"
    assert args.afi_args == ["audit", "--strict"]


def test_afi_module_protocol():
    assert afi.NAME == "afi"
    assert callable(afi.register)
    assert callable(afi.dispatch)


# ---------------------------------------------------------------------------
# devex
# ---------------------------------------------------------------------------


def test_devex_dispatch_forwards_argv(monkeypatch):
    captured: list = []
    monkeypatch.setattr(
        "culture.cli._passthrough.run",
        lambda entry, argv: captured.append(argv),
    )
    devex.dispatch(_ns(devex_args=["pr", "open"]))
    assert captured == [["pr", "open"]]


def test_devex_dispatch_handles_missing_devex_args(monkeypatch):
    captured: list = []
    monkeypatch.setattr(
        "culture.cli._passthrough.run",
        lambda entry, argv: captured.append(argv),
    )
    devex.dispatch(_ns(devex_args=None))
    assert captured == [[]]


def test_devex_entry_exits_when_agex_not_installed(monkeypatch):
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    )

    def _blocked(name, *a, **kw):
        if name == "agex.cli":
            raise ImportError("agex-cli not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _blocked)
    with pytest.raises(SystemExit) as exc:
        devex._entry(["pr"])
    assert exc.value.code == 2


def test_devex_register_adds_subparser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    devex.register(sub)
    args = parser.parse_args(["devex", "pr", "open"])
    assert args.cmd == "devex"
    assert args.devex_args == ["pr", "open"]


def test_devex_module_protocol():
    assert devex.NAME == "devex"
    assert callable(devex.register)
    assert callable(devex.dispatch)


# ---------------------------------------------------------------------------
# culture.cli.shared.formatting — trivial re-export
# ---------------------------------------------------------------------------


def test_cli_shared_formatting_reexports_relative_time():
    from culture.cli.shared import formatting
    from culture.formatting import relative_time as canonical

    assert callable(formatting.relative_time)
    # The re-export is the exact same function, not a wrapper.
    assert formatting.relative_time is canonical
