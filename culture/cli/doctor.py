"""Doctor subcommand: culture doctor — diagnose drift between manifest and on-disk repos."""

from __future__ import annotations

import argparse
import json
import sys

from culture.cli.shared.constants import _CONFIG_HELP, DEFAULT_CONFIG
from culture.config import load_config_or_default
from culture.doctor import run_doctor
from culture.doctor.fix import register_unregistered

NAME = "doctor"


def register(subparsers):
    p = subparsers.add_parser(
        "doctor", help="Diagnose drift between the agent manifest and on-disk culture.yaml repos"
    )
    p.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)
    p.add_argument(
        "--root",
        default=None,
        help="Workspace root to scan for on-disk culture.yaml repos (default: the culture repo's parent)",
    )
    p.add_argument("--json", action="store_true", help="Emit findings as JSON")
    p.add_argument(
        "--fix",
        "--register",
        dest="fix",
        action="store_true",
        help="Register class-2 (unregistered on-disk) repos into server.yaml",
    )


def _emit_json(report) -> None:
    from dataclasses import asdict

    payload = {
        "class1": [asdict(f) for f in report.class1],
        "class2": [asdict(f) for f in report.class2],
        "class3": [asdict(f) for f in report.class3],
        "ok": report.ok,
        "exit_code": report.exit_code,
    }
    print(json.dumps(payload, indent=2))


def _render_human(report) -> None:
    if report.ok:
        print("✓ culture doctor: no drift detected")
        return
    sections = [
        ("Broken registrations (class 1)", report.class1, "✗"),
        ("Unregistered repos (class 2, warning)", report.class2, "•"),
        ("Suffix collisions (class 3)", report.class3, "⚠"),
    ]
    for label, findings, icon in sections:
        if not findings:
            continue
        print(f"{label}:")
        for f in findings:
            print(f"  {icon} {f.subject}: {f.message}")
            if f.fix_hint:
                print(f"      fix: {f.fix_hint}")


def dispatch(args):
    config = load_config_or_default(args.config)
    report = run_doctor(config, root_override=args.root)  # diagnose only
    if args.json:
        _emit_json(report)
    else:
        _render_human(report)
    if args.fix and report.class2:
        added = register_unregistered(args.config, report.class2)
        for suffix, directory in added:
            print(f"registered: {suffix} -> {directory}")
    sys.exit(report.exit_code)
