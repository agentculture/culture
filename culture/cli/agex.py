"""`culture agex` — passthrough to the standalone agex CLI.

Also registers universal-verb handlers for the ``agex`` topic so
``culture explain agex`` / ``culture overview agex`` / ``culture learn agex``
route through culture's universal-verb path.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys

from culture.cli import introspect

NAME = "agex"


def _run_agex(argv: list[str]) -> None:
    """Invoke agex's typer app in-process.

    Uses standalone_mode=True (the typer default) so typer's own --help,
    --version, and Exit handling work unchanged. Typer calls ``sys.exit``
    when done, raising ``SystemExit``; this function lets that propagate
    so the caller (the ``culture agex`` passthrough) exits with the code
    the user expects. Callers that need to capture output and translate
    the exit into a return value should use :func:`_capture_agex` instead.
    """
    try:
        from agent_experience.cli import app
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"agex-cli is not installed: {exc}", file=sys.stderr)
        sys.exit(2)
    app(args=argv)


def _capture_agex(argv: list[str]) -> tuple[str, int]:
    """Run agex with stdout + stderr captured, translating SystemExit.

    The universal-verb handlers need ``(output, exit_code)`` rather than a
    process-level exit, so we deliberately catch ``SystemExit`` here and
    translate it into a return value. The :func:`_run_agex` variant is for
    the passthrough path where the exit must propagate.
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            _run_agex(argv)
    except SystemExit as exc:  # NOSONAR S5754 — see docstring
        code = exc.code
        if code is None:
            return buf.getvalue(), 0
        if isinstance(code, int):
            return buf.getvalue(), code
        return buf.getvalue() + str(code), 1
    return buf.getvalue(), 0


# --- universal-verb topic handlers for ``agex`` ---------------------------


def _agex_explain(_topic: str | None) -> tuple[str, int]:
    return _capture_agex(["explain", "agex"])


def _agex_overview(_topic: str | None) -> tuple[str, int]:
    return _capture_agex(["overview", "--agent", "claude-code"])


def _agex_learn(_topic: str | None) -> tuple[str, int]:
    return _capture_agex(["learn", "--agent", "claude-code"])


introspect.register_topic(
    "agex",
    explain=_agex_explain,
    overview=_agex_overview,
    learn=_agex_learn,
)


# --- CLI group protocol ---------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    # prefix_chars=chr(0) means the agex subparser has no recognized flag
    # prefix character, so every token (including --help, --version) is
    # treated as positional and captured in agex_args for typer to handle.
    p = subparsers.add_parser(
        NAME,
        help="Run agex (agent-experience CLI) via passthrough",
        add_help=False,
        prefix_chars=chr(0),
    )
    p.add_argument("agex_args", nargs=argparse.REMAINDER, help="Arguments passed to agex")


def dispatch(args: argparse.Namespace) -> None:
    rest = list(getattr(args, "agex_args", []) or [])
    _run_agex(rest)
