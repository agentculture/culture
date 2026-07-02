"""Unit tests for `culture_core.cli._passthrough` — the shared embedding helper."""

from __future__ import annotations

import pytest

from culture_core.cli import _passthrough


def _ok_entry(argv: list[str]) -> int:
    print(f"stdout:{' '.join(argv)}")
    return 0


def _nonzero_entry(argv: list[str]) -> int:
    print("bad input", flush=True)
    return 3


def _none_entry(argv: list[str]) -> None:
    # Exercises the "returns None → treated as 0" branch.
    print("implicit success")
    return None


def _help_entry(argv: list[str]) -> None:
    # Simulates argparse `--help`: writes to stdout, raises SystemExit(0).
    print("usage: fake [-h]")
    raise SystemExit(0)


def _error_entry(argv: list[str]) -> None:
    # Simulates argparse error: writes to stderr, raises SystemExit(2).
    import sys as _sys

    print("error: bad flag", file=_sys.stderr)
    raise SystemExit(2)


def _str_code_entry(argv: list[str]) -> None:
    # Unusual SystemExit where code is a string: treated as rc=1 and the
    # message is forwarded to the caller (stderr for run, buf for capture).
    raise SystemExit("something went wrong")


def _str_code_no_newline_entry(argv: list[str]) -> None:
    # No trailing newline — capture() should append one to keep the buffer
    # newline-terminated like a bare invocation's stderr stream.
    raise SystemExit("boom")


class TestCapture:
    def test_captures_stdout_and_returns_rc(self):
        out, rc = _passthrough.capture(_ok_entry, ["a", "b"])
        assert rc == 0
        assert "stdout:a b" in out

    def test_non_zero_return_passes_through(self):
        out, rc = _passthrough.capture(_nonzero_entry, [])
        assert rc == 3
        assert "bad input" in out

    def test_none_return_is_zero(self):
        out, rc = _passthrough.capture(_none_entry, [])
        assert rc == 0
        assert "implicit success" in out

    def test_systemexit_zero_is_captured_as_rc_zero(self):
        out, rc = _passthrough.capture(_help_entry, ["--help"])
        assert rc == 0
        assert "usage:" in out

    def test_systemexit_nonzero_is_captured_as_rc(self):
        out, rc = _passthrough.capture(_error_entry, [])
        assert rc == 2
        assert "bad flag" in out

    def test_systemexit_string_code_becomes_one(self):
        out, rc = _passthrough.capture(_str_code_entry, [])
        # Stringly-typed exit codes are treated as generic failure (1).
        assert rc == 1
        # The message must reach the caller — otherwise `culture overview <x>`
        # would print nothing when the embedded CLI does `sys.exit("msg")`.
        assert "something went wrong" in out

    def test_capture_string_code_appends_newline(self):
        out, rc = _passthrough.capture(_str_code_no_newline_entry, [])
        assert rc == 1
        # Even when the embedded CLI's message lacks a trailing newline, the
        # buffer should end with one so introspect.dispatch prints cleanly.
        assert out.endswith("boom\n")


class TestRun:
    def test_run_zero_exits_zero(self):
        with pytest.raises(SystemExit) as ei:
            _passthrough.run(_ok_entry, [])
        assert ei.value.code == 0

    def test_run_nonzero_exits_with_code(self):
        with pytest.raises(SystemExit) as ei:
            _passthrough.run(_nonzero_entry, [])
        assert ei.value.code == 3

    def test_run_propagates_systemexit_code(self):
        with pytest.raises(SystemExit) as ei:
            _passthrough.run(_error_entry, [])
        assert ei.value.code == 2

    def test_run_none_becomes_zero(self):
        with pytest.raises(SystemExit) as ei:
            _passthrough.run(_none_entry, [])
        assert ei.value.code == 0

    def test_run_string_code_prints_and_exits_one(self, capsys):
        with pytest.raises(SystemExit) as ei:
            _passthrough.run(_str_code_entry, [])
        assert ei.value.code == 1
        # Python's default sys.exit("msg") prints the message to stderr
        # before exiting 1; the passthrough must match that behaviour.
        captured = capsys.readouterr()
        assert "something went wrong" in captured.err


class TestRegisterTopic:
    def test_register_wires_all_three_verbs(self):
        # Use the real registry via a fresh topic name. Re-registering is
        # last-write-wins with a warning in culture_core.cli.introspect.
        from culture_core.cli import introspect

        _passthrough.register_topic(
            "__test_topic__",
            _ok_entry,
            explain_argv=["explain"],
            overview_argv=["overview"],
            learn_argv=["learn"],
        )
        out, rc = introspect.explain("__test_topic__")
        assert rc == 0
        assert "stdout:explain" in out
        out, rc = introspect.overview("__test_topic__")
        assert rc == 0
        assert "stdout:overview" in out
        out, rc = introspect.learn("__test_topic__")
        assert rc == 0
        assert "stdout:learn" in out
