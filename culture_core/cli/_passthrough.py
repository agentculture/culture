"""Shared plumbing for `culture <ext>` passthrough subcommands.

A passthrough module (e.g. ``culture devex``, ``culture afi``) embeds a
sibling CLI in-process: arguments after the namespace token are forwarded
verbatim to the external CLI's entry callable, and the three universal
verbs (``explain`` / ``overview`` / ``learn``) capture the external CLI's
output and route it through :mod:`culture_core.cli.introspect`.

Each passthrough module supplies a package-specific ``Entry`` callable
with signature ``(argv: list[str]) -> int | None``. The callable may:

* return ``None`` or ``0`` for success,
* return a non-zero ``int`` for a handled error,
* raise ``SystemExit`` for argparse ``--help`` / ``--version`` / errors
  or typer's standalone-mode hard-exit on completion.

:func:`run` propagates via ``sys.exit`` (for the ``dispatch()`` path).
:func:`capture` collects stdout+stderr and translates ``SystemExit`` into
an ``int`` return code (for the universal-verb handlers, which must
return ``(stdout, rc)``).

The long-term target is every embedded CLI exposing a clean
``main(argv) -> int`` (agent-first CLI contract owned by afi-cli). Until
then, typer-backed CLIs like agex-cli can be wrapped in a small entry
adapter that calls the typer ``app`` — the plumbing here stays unchanged.
"""

from __future__ import annotations

import contextlib
import io
import sys
from typing import Callable

from culture_core.cli import introspect

Entry = Callable[[list[str]], "int | None"]


def _translate_exit(code: "int | str | None") -> "tuple[int, str | None]":
    """Map a ``SystemExit.code`` to ``(rc, message)``.

    Python's ``sys.exit`` accepts ``None`` (rc 0), an ``int`` (rc = int), or
    anything else (rc 1 with the stringified value printed to stderr). We
    mirror all three so an embedded CLI using any ``sys.exit`` form is
    surfaced to culture's caller the way a bare invocation would be.
    """
    if code is None:
        return 0, None
    if isinstance(code, int):
        return code, None
    return 1, str(code)


def run(entry: Entry, argv: list[str]) -> None:
    """Invoke ``entry(argv)`` and ``sys.exit`` with its return code.

    Used by a group module's ``dispatch()`` to forward arguments verbatim to
    the embedded CLI. ``SystemExit`` raised inside the entry (typer's
    hard-exit, argparse's ``--help`` / ``--version`` / error path, or
    ``sys.exit("msg")``) is caught, its message (if any) forwarded to
    stderr, and its code re-emitted via ``sys.exit`` so the behaviour
    matches a bare invocation of the embedded CLI.
    """
    try:
        rc = entry(argv) or 0
    except SystemExit as exc:  # NOSONAR S5754 — SystemExit is re-emitted via sys.exit below
        rc, message = _translate_exit(exc.code)
        if message is not None:
            print(message, file=sys.stderr)
    sys.exit(rc)


def capture(entry: Entry, argv: list[str]) -> tuple[str, int]:
    """Invoke ``entry(argv)`` with stdout+stderr captured; return ``(out, rc)``.

    Used by universal-verb handlers (``explain`` / ``overview`` / ``learn``),
    which must return a value rather than exit. ``SystemExit`` raised inside
    the entry is translated into the ``rc`` half of the return tuple; any
    string-valued ``exc.code`` is appended to the captured buffer so the
    embedded CLI's error text reaches the caller the way a bare invocation
    would emit it on stderr.
    """
    buf = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = entry(argv) or 0
    except SystemExit as exc:  # NOSONAR S5754 — code is surfaced as rc; behaviour documented above
        rc, message = _translate_exit(exc.code)
        if message is not None:
            buf.write(message)
            if not message.endswith("\n"):
                buf.write("\n")
    return buf.getvalue(), rc


def register_topic(
    topic: str,
    entry: Entry,
    *,
    explain_argv: list[str],
    overview_argv: list[str],
    learn_argv: list[str],
) -> None:
    """Register ``explain`` / ``overview`` / ``learn`` handlers for ``topic``.

    Each handler captures the output of ``entry(<verb_argv>)`` and returns
    ``(stdout, rc)`` per the :mod:`culture_core.cli.introspect` handler protocol.
    """
    introspect.register_topic(
        topic,
        explain=lambda _t: capture(entry, explain_argv),
        overview=lambda _t: capture(entry, overview_argv),
        learn=lambda _t: capture(entry, learn_argv),
    )
