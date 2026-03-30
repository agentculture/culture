"""Tests for overview CLI subcommand argument parsing."""
import subprocess
import sys


def test_overview_help():
    """The overview subcommand is registered and has help."""
    result = subprocess.run(
        [sys.executable, "-m", "agentirc", "overview", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--room" in result.stdout
    assert "--agent" in result.stdout
    assert "--messages" in result.stdout
    assert "--serve" in result.stdout
    assert "--refresh" in result.stdout


def test_overview_default_args():
    """Default args parse correctly."""
    from agentirc.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["overview"])
    assert args.command == "overview"
    assert args.room is None
    assert args.agent is None
    assert args.messages == 4
    assert args.serve is False
    assert args.refresh == 5


def test_overview_with_flags():
    from agentirc.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["overview", "--room", "#general", "--messages", "10"])
    assert args.room == "#general"
    assert args.messages == 10
