"""Wiring tests for the top-level ``culture doctor`` command (t7).

Proves the new verb is registered in the CLI group registry and that it does
NOT collide with the steward-forwarded ``culture agents doctor`` (agent
alignment) — the two ``doctor`` surfaces stay distinct.
"""

from __future__ import annotations

import culture.cli as cli


def test_doctor_registered_in_groups():
    """The doctor module is wired into the GROUPS registry under NAME 'doctor'."""
    from culture.cli import GROUPS, doctor

    assert doctor in GROUPS
    assert doctor.NAME == "doctor"


def test_doctor_subcommand_parses():
    """`culture doctor` resolves to the doctor group via argparse."""
    parser = cli._build_parser()
    args = parser.parse_args(["doctor"])
    assert args.command == "doctor"


def test_top_level_doctor_does_not_forward_to_steward():
    """Top-level `culture doctor ...` must never short-circuit to steward."""
    assert cli._maybe_forward_to_steward(["doctor"]) is None
    assert cli._maybe_forward_to_steward(["doctor", "--json"]) is None
    assert cli._maybe_forward_to_steward(["doctor", "--fix"]) is None
    assert cli._maybe_forward_to_steward(["doctor", "--root", "/x"]) is None


def test_agents_doctor_still_forwards_to_steward(monkeypatch):
    """`culture agents doctor` (alignment) is untouched — still forwarded."""
    import steward.cli

    called = {}

    def fake_main(argv):
        called["argv"] = argv
        return 0

    monkeypatch.setattr(steward.cli, "main", fake_main)

    result = cli._maybe_forward_to_steward(["agents", "doctor", "--json"])

    assert result == 0
    # the 'agents' noun is stripped; the verb + remaining args reach steward
    assert called["argv"] == ["doctor", "--json"]
