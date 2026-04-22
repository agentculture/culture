"""`culture agex` — passthrough to the standalone agex CLI.

Also registers universal-verb handlers for the ``agex`` topic so
``culture explain agex`` / ``culture overview agex`` / ``culture learn agex``
route through culture's universal-verb path.
"""

from __future__ import annotations

import argparse
import sys

from culture.cli import introspect

NAME = "agex"


def _run_agex(argv: list[str]) -> int:
    """Invoke agex's typer app in-process, returning its exit code.

    Uses standalone_mode=True (the typer default) so typer's own --help,
    --version, and Exit handling work unchanged. Typer calls sys.exit when
    done; we translate that SystemExit back into a return value.
    """
    try:
        from agent_experience.cli import app
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"agex-cli is not installed: {exc}", file=sys.stderr)
        return 2
    try:
        app(args=argv)
    except SystemExit as e:
        if e.code is None:
            return 0
        if isinstance(e.code, int):
            return e.code
        print(e.code, file=sys.stderr)
        return 1
    return 0


# --- universal-verb topic handlers for ``agex`` ---------------------------


def _agex_explain(_topic: str | None) -> tuple[str, int]:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = _run_agex(["explain", "agex"])
    return buf.getvalue(), code


def _agex_overview(_topic: str | None) -> tuple[str, int]:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = _run_agex(["overview", "--agent", "claude-code"])
    return buf.getvalue(), code


def _agex_learn(_topic: str | None) -> tuple[str, int]:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = _run_agex(["learn", "--agent", "claude-code"])
    return buf.getvalue(), code


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
    sys.exit(_run_agex(rest))
