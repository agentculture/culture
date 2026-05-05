"""End-to-end install test for the vendored `communicate` skill.

`culture skills install <backend>` should drop a `communicate/` skill
folder (SKILL.md + executable scripts) into the per-backend skills root
alongside the existing `irc` and `culture` skills.
"""

from __future__ import annotations

import os
import stat
from argparse import Namespace
from unittest.mock import patch

import pytest

from culture.cli import skills

BACKENDS = (
    ("claude", "~/.claude/skills"),
    ("codex", "~/.agents/skills"),
    ("copilot", "~/.copilot_skills"),
    ("acp", "~/.acp/skills"),
)


@pytest.mark.parametrize(("target", "root"), BACKENDS)
def test_install_drops_communicate_skill_for_backend(
    target: str, root: str, tmp_path, monkeypatch
) -> None:
    """Each backend's install should materialize the full communicate skill."""
    monkeypatch.setenv("HOME", str(tmp_path))

    args = Namespace(skills_command="install", target=target)
    skills.dispatch(args)

    expanded_root = os.path.expanduser(root.replace("~", str(tmp_path)))
    if "~" in root:
        # When HOME is overridden via env, expanduser uses HOME for ~.
        expanded_root = os.path.expanduser(root)
    skill_dir = os.path.join(expanded_root, "communicate")

    skill_md = os.path.join(skill_dir, "SKILL.md")
    post_issue = os.path.join(skill_dir, "scripts", "post-issue.sh")
    mesh_message = os.path.join(skill_dir, "scripts", "mesh-message.sh")

    assert os.path.isfile(skill_md), f"missing {skill_md}"
    assert os.path.isfile(post_issue), f"missing {post_issue}"
    assert os.path.isfile(mesh_message), f"missing {mesh_message}"

    # Scripts must land executable so receiving harnesses can bash them.
    for script in (post_issue, mesh_message):
        mode = os.stat(script).st_mode
        assert mode & stat.S_IXUSR, f"{script} not executable for owner"
        assert mode & stat.S_IXGRP, f"{script} not executable for group"
        assert mode & stat.S_IXOTH, f"{script} not executable for other"


def test_install_skill_md_carries_provenance_header(tmp_path, monkeypatch) -> None:
    """The vendored SKILL.md must declare it was sourced from steward."""
    monkeypatch.setenv("HOME", str(tmp_path))
    skills.dispatch(Namespace(skills_command="install", target="claude"))

    skill_md = os.path.expanduser("~/.claude/skills/communicate/SKILL.md")
    text = open(skill_md, encoding="utf-8").read()
    assert "Vendored from agentculture/steward" in text


def test_install_all_backends_includes_communicate(tmp_path, monkeypatch) -> None:
    """`culture skills install all` lays down communicate for every backend."""
    monkeypatch.setenv("HOME", str(tmp_path))
    skills.dispatch(Namespace(skills_command="install", target="all"))

    for _target, root in BACKENDS:
        skill_dir = os.path.expanduser(os.path.join(root, "communicate"))
        assert os.path.isdir(skill_dir), f"missing {skill_dir} after install all"
        assert os.path.isfile(os.path.join(skill_dir, "SKILL.md"))
