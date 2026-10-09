"""`culture sandbox agent` must accept its documented flags (regression)."""

from __future__ import annotations

import sys

import pytest

from culture_core.cli import main as culture_main


def test_sandbox_agent_bundle_from_via_cli(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("# readme\n")
    (repo / "docs" / "a.md").write_text("# a\n")
    cfg = tmp_path / "sbx.yaml"
    cfg.write_text(f"knowledge_dir: {tmp_path / 'kb'}\n")
    argv = ["culture", "sandbox", "agent", "--config", str(cfg), "--bundle-from", str(repo)]
    monkeypatch.setattr(sys, "argv", argv)
    culture_main()
    assert "bundled 2 files" in capsys.readouterr().out
    assert (tmp_path / "kb" / "README.md").exists()
