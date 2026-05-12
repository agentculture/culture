"""End-to-end install test for the vendored `communicate` skill.

`culture skills install <backend>` should drop a `communicate/` skill
folder (SKILL.md + four executable scripts + a `scripts/templates/`
directory) into the per-backend skills root alongside the existing
`irc` and `culture` skills. In culture 11.1.0 the skill was rebased on
`agtag` and grew two new scripts (`post-comment.sh`, `fetch-issues.sh`)
plus a `templates/` subdir for the broadcast-brief Markdown template.
"""

from __future__ import annotations

import os
import stat
from argparse import Namespace

import pytest

from culture.cli import skills

BACKENDS = (
    ("claude", "~/.claude/skills"),
    ("codex", "~/.agents/skills"),
    ("copilot", "~/.copilot_skills"),
    ("acp", "~/.acp/skills"),
)

EXPECTED_SCRIPTS = (
    "fetch-issues.sh",
    "mesh-message.sh",
    "post-comment.sh",
    "post-issue.sh",
)
EXPECTED_TEMPLATES = ("skill-update-brief.md",)


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
    assert os.path.isfile(skill_md), f"missing {skill_md}"

    script_paths = [os.path.join(skill_dir, "scripts", name) for name in EXPECTED_SCRIPTS]
    for path in script_paths:
        assert os.path.isfile(path), f"missing {path}"

    # The Markdown brief template ships under scripts/templates/ and is
    # not executable.
    for template_name in EXPECTED_TEMPLATES:
        template_path = os.path.join(skill_dir, "scripts", "templates", template_name)
        assert os.path.isfile(template_path), f"missing {template_path}"
        mode = os.stat(template_path).st_mode
        assert not (mode & stat.S_IXUSR), f"{template_path} must not be executable"

    # Scripts must land executable for the owner so receiving harnesses
    # can `bash` them directly. Group / world execute and write are
    # intentionally cleared — skills land in single-user dirs and
    # granting wider permissions trips Sonar's S2612.
    for path in script_paths:
        mode = os.stat(path).st_mode
        assert mode & stat.S_IXUSR, f"{path} not executable for owner"
        assert not (mode & stat.S_IXGRP), f"{path} should not be group-executable"
        assert not (mode & stat.S_IXOTH), f"{path} should not be world-executable"
        assert not (mode & stat.S_IWGRP), f"{path} should not be group-writable"
        assert not (mode & stat.S_IWOTH), f"{path} should not be world-writable"


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
