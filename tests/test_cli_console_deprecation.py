"""Tests for the `culture mesh console` deprecation alias.

The legacy command should still parse, emit a stderr deprecation
warning, and forward to the new `culture console` flow without
launching the Textual TUI.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from unittest.mock import patch


def test_mesh_console_warns_then_forwards(capsys):
    """Direct dispatch path - no subprocess.

    The forwarded call uses `culture.cli.console.dispatch_resolved_argv`,
    which we patch to a no-op so this test stays hermetic (no running
    AgentIRC required).
    """
    from culture.cli import mesh

    args = argparse.Namespace(mesh_command="console", server_name="spark", config=None)
    with patch("culture.cli.mesh.console_dispatch") as forwarded:
        mesh._cmd_console(args)
    captured = capsys.readouterr()
    assert "deprecated" in captured.err.lower()
    assert "culture console" in captured.err
    forwarded.assert_called_once_with("spark")


def test_mesh_console_help_marks_deprecated():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "mesh", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "deprecated" in result.stdout.lower()
