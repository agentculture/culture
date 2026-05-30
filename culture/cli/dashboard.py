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
    p.add_argument(
        "--auth",
        action="store_true",
        help="Require a token (auto-generated at ~/.culture/dashboard-token). Use for remote access.",
    )
    p.add_argument(
        "--auth-token",
        default=None,
        help="Explicit dashboard token (implies --auth). Overrides the token file.",
    )
    p.add_argument(
        "--trusted-host",
        action="append",
        default=None,
        metavar="HOST",
        help="Allow this Host/Origin in addition to loopback (e.g. your Tailscale name). Repeatable.",
    )


def dispatch(args: argparse.Namespace) -> None:
    from culture.dashboard.server import (
        default_token_path,
        load_or_create_token,
        serve_dashboard,
    )

    auth_token = None
    if args.auth_token:
        auth_token = args.auth_token
    elif args.auth:
        auth_token = load_or_create_token(default_token_path())

    trusted = args.trusted_host or []
    if auth_token:
        hosts = trusted or ["<your-trusted-host>"]
        print("Dashboard auth is ON. Open the login page and paste your token:")
        for host in hosts:
            print(f"  https://{host}/auth")
        print(f"  Token: {auth_token}")
        print(
            "  (form-submitted token — does not appear in browser history, server "
            "access logs, or Referer)"
        )
        if not trusted:
            print(
                "  (no --trusted-host given; add your tunnel hostname, "
                "e.g. --trusted-host mymac.tailXXduck.ts.net)"
            )

    try:
        serve_dashboard(
            host=args.host,
            port=args.port,
            config_path=args.config,
            unsafe_bind=args.unsafe_bind,
            auth_token=auth_token,
            trusted_hosts=trusted,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
