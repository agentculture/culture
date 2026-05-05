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
from culture.cli.shared.console_helpers import resolve_console_nick as _resolve_console_nick
from culture.cli.shared.console_helpers import resolve_server as _resolve_server

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

    - Empty argv -> resolve default culture server, build a ``serve`` call.
    - First token is an irc-lens verb or starts with ``-`` -> pure
      passthrough (return argv unchanged).
    - Otherwise -> treat first token as a culture server name; rewrite to
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
        raise SystemExit("No culture servers running. Start one with: culture chat start")
    name, port = result
    nick = f"{name}-{_resolve_console_nick()}"
    return [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--nick",
        nick,
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
    p.add_argument("console_args", nargs=argparse.REMAINDER, help="Arguments passed to irc-lens")


def dispatch(args: argparse.Namespace) -> None:
    raw = list(getattr(args, "console_args", []) or [])
    _passthrough.run(_entry, _resolve_argv(raw))
