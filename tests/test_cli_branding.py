"""Tests for CULTURE.DEV product branding in top-level CLI output (issue #440).

The product/source brand is presented as ``CULTURE.DEV CLI`` in ``--version``
and ``--help`` so the mark functions as the source brand (US trademark specimen
support). The program name (``prog``) is derived dynamically from
``os.path.basename(sys.argv[0])`` so that ``culture-core`` standalone installs
and ``culture`` front-package installs each show the correct command name in
help/usage text. These assertions pin that contract so the branding can't
silently regress.
"""

import subprocess
import sys

import pytest

from culture_core import __version__
from culture_core.cli import _build_parser


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


def test_command_name_dynamic(monkeypatch):
    """prog is derived from argv[0] basename; falls back to ``culture-core``."""
    # When invoked as ``culture-core``, prog should reflect that.
    monkeypatch.setattr(sys, "argv", ["culture-core", "--help"])
    parser = _build_parser()
    assert parser.prog == "culture-core"
    assert parser.format_usage().startswith("usage: culture-core")

    # When invoked as ``culture``, prog should reflect that.
    monkeypatch.setattr(sys, "argv", ["/usr/bin/culture", "--help"])
    parser = _build_parser()
    assert parser.prog == "culture"
    assert parser.format_usage().startswith("usage: culture")

    # Empty argv[0] falls back to ``culture-core``.
    monkeypatch.setattr(sys, "argv", [""])
    parser = _build_parser()
    assert parser.prog == "culture-core"

    # ``-c`` (python -c ...) falls back to ``culture-core``.
    monkeypatch.setattr(sys, "argv", ["-c"])
    parser = _build_parser()
    assert parser.prog == "culture-core"

    # ``__main__.py`` (python -m culture_core) falls back to ``culture-core``.
    monkeypatch.setattr(sys, "argv", ["__main__.py"])
    parser = _build_parser()
    assert parser.prog == "culture-core"

    # An empty argv list (embedded interpreter edge case) must not crash.
    monkeypatch.setattr(sys, "argv", [])
    parser = _build_parser()
    assert parser.prog == "culture-core"


def test_version_brand_end_to_end():
    """The real ``python -m culture_core --version`` entry point is branded too."""
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CULTURE.DEV CLI" in result.stdout
