"""Doctor subcommand: culture doctor — diagnose drift between manifest and on-disk repos."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from culture_core.cli.shared.constants import _CONFIG_HELP, DEFAULT_CONFIG
from culture_core.config import load_config_or_default
from culture_core.doctor import run_doctor
from culture_core.doctor.fix import register_unregistered

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


def _emit_json(report, registered=None) -> None:
    from dataclasses import asdict

    payload = {
        "class1": [asdict(f) for f in report.class1],
        "class2": [asdict(f) for f in report.class2],
        "class3": [asdict(f) for f in report.class3],
        "class4": [asdict(f) for f in report.class4],
        "ok": report.ok,
        "exit_code": report.exit_code,
        # Always present so CI consumers see a stable schema; populated only
        # when --fix actually registered something.
        "registered": [
            {"suffix": suffix, "directory": directory} for suffix, directory in (registered or [])
        ],
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
        ("Service health (class 4)", report.class4, "✗"),
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
    # Fail fast on a bad --root rather than letting Path.iterdir() raise an
    # uncaught traceback deep in discovery (reliability).
    if args.root is not None and not Path(args.root).is_dir():
        print(f"culture doctor: --root is not a directory: {args.root}", file=sys.stderr)
        sys.exit(2)

    config = load_config_or_default(args.config)
    report = run_doctor(config, root_override=args.root)  # diagnose only

    # Run the opt-in fix BEFORE emitting, so --json can fold the registered
    # pairs into the payload instead of printing trailing lines that would
    # corrupt the JSON stream (correctness).
    registered = []
    if args.fix and report.class2:
        registered = register_unregistered(args.config, report.class2)

    if args.json:
        _emit_json(report, registered)
    else:
        _render_human(report)
        for suffix, directory in registered:
            print(f"registered: {suffix} -> {directory}")
    sys.exit(report.exit_code)
