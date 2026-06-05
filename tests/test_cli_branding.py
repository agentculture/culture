"""Tests for CULTURE.DEV product branding in top-level CLI output (issue #440).

The command/package stay ``culture``; the product/source brand is presented as
``CULTURE.DEV CLI`` in ``--version`` and ``--help`` so the mark functions as the
source brand (US trademark specimen support). These assertions pin that contract
so the branding can't silently regress.
"""

import subprocess
import sys

import pytest

from culture import __version__
from culture.cli import _build_parser


def test_version_shows_culture_dev_brand(capsys):
    """``culture --version`` identifies the product as ``CULTURE.DEV CLI``."""
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "CULTURE.DEV CLI" in out
    assert __version__ in out


def test_help_shows_culture_dev_brand():
    """``culture --help`` shows the CULTURE.DEV brand header and tagline."""
    help_text = _build_parser().format_help()
    assert "CULTURE.DEV CLI" in help_text
    assert "The professional workspace for agents." in help_text


def test_command_name_unchanged():
    """Branding is presentation only: the command/program name stays ``culture``."""
    parser = _build_parser()
    assert parser.prog == "culture"
    # The usage line still invokes the tool as ``culture`` (package unchanged).
    assert parser.format_usage().startswith("usage: culture")


def test_version_brand_end_to_end():
    """The real ``python -m culture --version`` entry point is branded too."""
    result = subprocess.run(
        [sys.executable, "-m", "culture", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "CULTURE.DEV CLI" in result.stdout
