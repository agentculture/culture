"""Dashboard subcommand: ``culture dashboard`` — the Mission Control web app.

Localhost-only web UI to watch every agent live and intervene (approve/deny,
pause/resume, close, emergency stop-all, policy edit).

Design spec: docs/superpowers/specs/2026-05-29-mission-control-dashboard-design.md
"""

from __future__ import annotations

import argparse
import sys

from .shared.constants import DEFAULT_CONFIG

NAME = "dashboard"


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "dashboard", help="Mission Control web app (watch + control the mesh)"
    )
    p.add_argument(
        "--host", default="127.0.0.1", help="Bind host (loopback only unless --unsafe-bind)"
    )
    p.add_argument("--port", type=int, default=8787, help="Bind port (default: 8787)")
    p.add_argument("--config", default=DEFAULT_CONFIG, help="Server config path")
    p.add_argument(
        "--unsafe-bind",
        action="store_true",
        help="DANGEROUS: allow binding a non-loopback host (the dashboard can kill agents and approve tool calls)",
    )


def dispatch(args: argparse.Namespace) -> None:
    from culture.dashboard.server import serve_dashboard

    try:
        serve_dashboard(
            host=args.host,
            port=args.port,
            config_path=args.config,
            unsafe_bind=args.unsafe_bind,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
