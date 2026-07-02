"""Unified CLI entry point for culture.

Commands are organized into noun-based groups:
    culture agents   {create,join,start,stop,status,rename,assign,sleep,wake,learn,message,read,archive,unarchive,delete}
    culture server   {start,stop,status,default,rename,archive,unarchive,restart,link,logs,version,serve}
    culture console  {...irc-lens verbs and flags...}    # passthrough; reactive web console
    culture mesh     {overview,setup,update,console}     # `console` here is deprecated; use `culture console`
    culture channel  {list,read,message,who}
    culture bot      {create,start,stop,list,inspect,archive,unarchive}
    culture skills   {install}
    culture devex    {...developer-experience passthrough (powered by agex-cli)...}
    culture afi      {...agent-first interface passthrough (powered by agentfront)...}

Universal verbs (available at the root):
    culture explain [topic]    full description of topic (default: culture)
    culture overview [topic]   shallow summary
    culture learn [topic]      agent-facing onboarding prompt
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import NoReturn

from culture_core import __version__
from culture_core.cli import (
    afi,
    agents,
    bot,
    channel,
    console,
    devex,
    doctor,
    introspect,
    mesh,
    server,
    skills,
)
from culture_core.cli._errors import EXIT_USER_ERROR, CultureError
from culture_core.cli._output import emit_error

GROUPS = [agents, server, mesh, channel, bot, skills, devex, afi, console, introspect, doctor]


def _names_of(group) -> set[str]:
    names = getattr(group, "NAMES", None)
    if names is not None:
        return set(names)
    return {group.NAME}


def _json_mode_active(argv: list[str]) -> bool:
    """``--json`` is meaningful only on the three universal verbs.

    Other groups (`agents`, `server`, …) reject ``--json`` themselves; we
    only honor the AgentCulture JSON-error contract when the user is
    addressing an introspection verb. This matches what katvan's
    reference-sync actually invokes.
    """
    if "--json" not in argv:
        return False
    return any(v in argv for v in introspect.NAMES)


class _JsonAwareParser(argparse.ArgumentParser):
    """``argparse.ArgumentParser`` that honors the ``--json`` error contract.

    When the user runs e.g. ``culture explain --bogus --json``, argparse's
    default ``error()`` writes plain text to stderr and exits 2. That
    violates the AgentCulture sibling contract that *any* failure under
    ``--json`` emit a parseable ``{code, message, remediation}`` object on
    stderr. We override ``error()`` so parse-time failures route through
    :func:`culture_core.cli._output.emit_error` whenever ``--json`` is active.

    Applied at the top parser and (via ``parser_class``) at every
    subparser, so any subcommand that reaches ``parse_args()`` honors the
    contract.
    """

    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        if _json_mode_active(sys.argv[1:]):
            err = CultureError(
                code=EXIT_USER_ERROR,
                message=message,
                remediation="run 'culture --help' or 'culture explain' for usage",
            )
            emit_error(err, json_mode=True)
            self.exit(EXIT_USER_ERROR)
        super().error(message)


def _prog_name() -> str:
    """Derive the program name from ``sys.argv[0]``.

    Falls back to ``culture-core`` when argv is empty, or argv[0] is empty,
    ``-c`` (python -c), or ``__main__.py`` (python -m culture_core).
    """
    argv0 = sys.argv[0] if sys.argv else ""
    base = os.path.basename(argv0 or "")
    if not base or base in ("__main__.py", "-c"):
        return "culture-core"
    return base


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonAwareParser(
        prog=_prog_name(),
        description="CULTURE.DEV CLI\n\nThe professional workspace for agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"CULTURE.DEV CLI v{__version__}")
    sub = parser.add_subparsers(dest="command", parser_class=_JsonAwareParser)
    for group in GROUPS:
        group.register(sub)
    return parser


def _maybe_forward_to_agentirc(argv: list[str]) -> int | None:
    """Bypass argparse for ``culture server <forwarded-verb> ...`` calls.

    Returns the exit code to propagate, or ``None`` if argparse should
    handle the invocation. argparse's ``REMAINDER`` parser cannot capture
    ``--help`` reliably (it leaks to the root parser as an unrecognized
    argument), so the forwarded surface is short-circuited here before
    argparse runs.
    """
    if len(argv) < 2 or argv[0] != "server":
        return None
    if argv[1] not in server._AGENTIRC_FORWARDED_VERBS:
        return None
    from agentirc.cli import dispatch as _agentirc_dispatch

    return _agentirc_dispatch(argv[1:])


def _maybe_forward_to_steward(argv: list[str]) -> int | None:
    """Bypass argparse for steward verbs forwarded under ``agents`` / ``skills``.

    Mirrors ``_maybe_forward_to_agentirc``: argparse REMAINDER can't capture
    ``--help`` reliably, so forwarded steward verbs are short-circuited here and
    replayed through ``steward.cli.main`` verbatim (the ``skills`` verb is remapped
    to steward's canonical name). Returns the exit code, or None to let
    argparse handle a native verb.
    """
    if len(argv) < 2:
        return None
    noun, verb = argv[0], argv[1]
    if noun == "agents" and verb in agents._STEWARD_FORWARDED_VERBS:
        steward_argv = [verb, *argv[2:]]
    elif noun == "skills" and verb in skills._STEWARD_FORWARDED:
        steward_argv = [skills._STEWARD_FORWARDED[verb], *argv[2:]]
    else:
        return None
    try:
        from steward.cli import main as steward_main
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"steward-cli is not installed: {exc}", file=sys.stderr)
        return 2
    return steward_main(steward_argv)


# Command groups that read the server config; the first-run notice below
# only applies to them (introspection/passthrough verbs work configless).
_CONFIG_CONSUMING_GROUPS = {"agents", "server", "channel", "bot", "mesh", "skills"}


def _notice_first_run(args: argparse.Namespace) -> None:
    """Tell a fresh operator the default config is missing (#19).

    Fires once per invocation, only for config-consuming groups, and only
    when no explicit ``--config`` was given — an explicit path that doesn't
    exist is the command's own error surface. Without this, a fresh install
    runs every command against an empty default ServerConfig and "works"
    against nothing, silently.
    """
    if getattr(args, "command", None) not in _CONFIG_CONSUMING_GROUPS:
        return
    # argparse accepts both `--config PATH` and `--config=PATH`.
    if any(tok == "--config" or tok.startswith("--config=") for tok in sys.argv[1:]):
        return
    default_config = os.path.expanduser("~/.culture/server.yaml")
    legacy_config = os.path.expanduser("~/.culture/agents.yaml")
    if os.path.exists(default_config) or os.path.exists(legacy_config):
        return
    print(
        f"note: no server config at {default_config} — using defaults. "
        "First run? Start a server with 'culture server start', "
        "then add an agent with 'culture agents create'.",
        file=sys.stderr,
    )


def main() -> None:
    # Logging must be configured before any dispatch path runs — including
    # the agentirc forwarder bypass below — so any logs emitted by
    # agentirc.cli.dispatch land in culture's standard format rather than
    # whatever default the importing process happens to have.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    try:
        forwarded = _maybe_forward_to_agentirc(sys.argv[1:])
        if forwarded is not None:
            sys.exit(forwarded)

        forwarded = _maybe_forward_to_steward(sys.argv[1:])
        if forwarded is not None:
            sys.exit(forwarded)

        parser = _build_parser()
        args = parser.parse_args()

        if args.command is None:
            parser.print_help()
            sys.exit(1)

        _notice_first_run(args)

        for group in GROUPS:
            if args.command in _names_of(group):
                group.dispatch(args)
                return
        parser.print_help()
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except CultureError as err:
        # The sanctioned failure path (#19): every handler failure raised as
        # CultureError reaches the user with its remediation — as the
        # ``error:`` / ``hint:`` pair in text mode, as the structured
        # {code, message, remediation} object under --json. Runtime backstop
        # for the AST guard: a dynamically-computed remediation that ends up
        # empty still yields a usable hint.
        if not err.remediation.strip():
            err.remediation = "run 'culture --help' or 'culture explain' for usage"
        emit_error(err, json_mode=_json_mode_active(sys.argv[1:]))
        sys.exit(err.code)
    except Exception as exc:
        # Honor the --json contract on unexpected exceptions too: an agent
        # consumer that asked for JSON should never see a plain-text
        # "Error: ..." trailer on stderr instead of the structured shape.
        if _json_mode_active(sys.argv[1:]):
            emit_error(
                CultureError(
                    code=1,
                    message=str(exc),
                    remediation="check the command and try again",
                ),
                json_mode=True,
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
