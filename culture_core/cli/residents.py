"""Residents verb: culture residents — live resource view of mesh residents.

Front door for the resident-presence resource view (plan task t5): reads
the server-side presence aggregation through the shared seam in
``culture_core.resource_view`` and renders it as a human table or, with
``--json``, as exactly the canonical ``serialize_residents`` payload the
t7 HTTP endpoint shares byte-for-byte.

Degrade contract (plan risks r3/r4): the PRESENCE query surface shipped
in agentirc-cli 9.12.0 (agentirc#53). Against a server still running an
older agentirc this verb reports "not supported" and exits 0 — that is a
known mesh state, not an error. Only an unreachable server is an error
(nonzero, CultureError style, no traceback).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from culture_core.cli._errors import EXIT_USER_ERROR, CultureError
from culture_core.cli._output import emit_error
from culture_core.cli.shared.constants import _CONFIG_HELP, DEFAULT_CONFIG
from culture_core.config import load_config_or_default
from culture_core.resource_view import (
    UNREACHABLE_MESSAGE,
    UNREACHABLE_REMEDIATION,
    PresenceUnsupportedError,
    Resident,
    fetch_residents_for,
    serialize_residents,
    to_json,
)

NAME = "residents"

_MISSING = "-"

_UNSUPPORTED_NOTICE = (
    "server does not support PRESENCE — upgrade the mesh server to "
    "agentirc-cli >= 9.12.0 and restart it (agentirc#53)"
)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "residents",
        help="Live resource view: per-resident presence state, token spend, budget status",
    )
    p.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)
    p.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Emit the canonical resource-view JSON payload (shared with the t7 endpoint)",
    )


def _tokens_cell(resident: Resident) -> str:
    if resident.tokens_in is None and resident.tokens_out is None:
        return _MISSING
    left = _MISSING if resident.tokens_in is None else str(resident.tokens_in)
    right = _MISSING if resident.tokens_out is None else str(resident.tokens_out)
    return f"{left}/{right}"


def _budget_cell(resident: Resident) -> str:
    if resident.budget_used_pct is None:
        return _MISSING
    return f"{resident.budget_used_pct:g}%"


def _flags_cell(resident: Resident) -> str:
    flags = []
    if resident.presumed_hung:
        flags.append("HUNG?")
    if resident.budget_warning:
        flags.append("BUDGET")
    return ",".join(flags) or _MISSING


_COLUMNS: tuple[str, ...] = (
    "NICK",
    "SERVER",
    "STATE",
    "SINCE",
    "TASK",
    "TOKENS (IN/OUT)",
    "BUDGET %",
    "FLAGS",
)


def _row(resident: Resident) -> tuple[str, ...]:
    return (
        resident.nick or _MISSING,
        resident.server or _MISSING,
        resident.state or _MISSING,
        resident.since or _MISSING,
        resident.task or _MISSING,
        _tokens_cell(resident),
        _budget_cell(resident),
        _flags_cell(resident),
    )


def _render_table(residents: list[Resident]) -> str:
    """Render the human table — readable even for state-only residents.

    Rows are sorted by nick (same order as the JSON payload); every missing
    field renders as a dash.
    """
    rows = [_row(r) for r in sorted(residents, key=lambda r: r.nick)]
    widths = [
        max(len(_COLUMNS[i]), *(len(row[i]) for row in rows)) if rows else len(_COLUMNS[i])
        for i in range(len(_COLUMNS))
    ]
    lines = ["  ".join(_COLUMNS[i].ljust(widths[i]) for i in range(len(_COLUMNS))).rstrip()]
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(_COLUMNS))).rstrip())
    return "\n".join(lines)


def dispatch(args: argparse.Namespace) -> None:
    json_mode = bool(getattr(args, "json", False))

    # Config loading is its own failure domain: an unreadable server.yaml
    # must surface as a config error, never be swallowed by the connection
    # handling below and misreported as "cannot connect to IRC server".
    config_path = Path(os.path.expanduser(str(args.config)))
    try:
        config = load_config_or_default(config_path)
    except CultureError as err:
        # Config validation (e.g. a bad presence section) — emit through
        # the verb's json-aware path so --json consumers always get the
        # {code, message, remediation} contract.
        emit_error(err, json_mode=json_mode)
        sys.exit(err.code)
    except OSError as exc:
        err = CultureError(
            EXIT_USER_ERROR,
            f"cannot read server config at {config_path}: {exc}",
            "check the file exists and is readable, or pass --config",
        )
        err.__cause__ = exc
        emit_error(err, json_mode=json_mode)
        sys.exit(err.code)

    parent_nick = os.environ.get("CULTURE_NICK", "").strip() or None
    try:
        try:
            residents = fetch_residents_for(config, parent_nick=parent_nick)
            supported = True
        except PresenceUnsupportedError:
            residents, supported = [], False
    except OSError as exc:
        # Covers ConnectionRefusedError, the observer's registration
        # ConnectionError, TimeoutError, and the mid-stream stall
        # ConnectionError — all OSError subclasses.
        err = CultureError(EXIT_USER_ERROR, UNREACHABLE_MESSAGE, UNREACHABLE_REMEDIATION)
        err.__cause__ = exc
        emit_error(err, json_mode=json_mode)
        sys.exit(err.code)

    if json_mode:
        # Exactly the shared serializer AND the shared dumps site — the t7
        # endpoint emits the same to_json(serialize_residents(...)) bytes.
        print(to_json(serialize_residents(residents, supported)))
        return

    if not supported:
        print(_UNSUPPORTED_NOTICE)
        return

    if not residents:
        print("No residents connected.")
        return

    print(_render_table(residents))
