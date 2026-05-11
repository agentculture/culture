"""Skills subcommands: culture skills {install}."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys

NAME = "skills"

_SKILL_FILENAME = "SKILL.md"
_COMMUNICATE_SCRIPTS = ("post-issue.sh", "mesh-message.sh")


def register(subparsers: argparse._SubParsersAction) -> None:
    skills_parser = subparsers.add_parser("skills", help="Install IRC skills")
    skills_sub = skills_parser.add_subparsers(dest="skills_command")
    skills_install = skills_sub.add_parser("install", help="Install IRC skill for an agent")
    skills_install.add_argument(
        "target",
        choices=["claude", "codex", "copilot", "acp", "opencode", "all"],
        help="Target agent: claude, codex, copilot, acp, opencode (alias of acp), or all",
    )


def dispatch(args: argparse.Namespace) -> None:
    if not hasattr(args, "skills_command") or args.skills_command != "install":
        print(
            "Usage: culture skills install <claude|codex|copilot|acp|opencode|all>",
            file=sys.stderr,
        )
        sys.exit(1)

    target = args.target

    if target in ("claude", "all"):
        _install_skill_claude()
    if target in ("codex", "all"):
        _install_skill_codex()
    if target in ("copilot", "all"):
        _install_skill_copilot()
    if target in ("acp", "opencode", "all"):
        _install_skill_acp()

    if target == "all":
        print("\nSkills installed for Claude Code, Codex, Copilot, and ACP.")
    print("\nSet CULTURE_NICK in your shell profile to enable the skill.")


# -----------------------------------------------------------------------
# Skill installers
# -----------------------------------------------------------------------


def _get_bundled_admin_skill_path() -> str:
    """Return the path to the bundled admin SKILL.md in the installed package."""
    import culture

    return os.path.join(os.path.dirname(culture.__file__), "skills", "culture", _SKILL_FILENAME)


def _get_bundled_skill_path() -> str:
    """Return the path to the bundled Claude SKILL.md (from cultureagent)."""
    import cultureagent

    return os.path.join(
        os.path.dirname(cultureagent.__file__), "clients", "claude", "skill", _SKILL_FILENAME
    )


def _install_admin_skill(root_dir: str, label: str) -> None:
    """Install the admin/ops skill to the given root skills directory."""
    src = _get_bundled_admin_skill_path()
    dest_dir = os.path.join(os.path.expanduser(root_dir), "culture")
    dest = os.path.join(dest_dir, _SKILL_FILENAME)

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed {label} admin skill: {dest}")


def _get_bundled_communicate_dir() -> str:
    """Return the path to the bundled `communicate/` skill directory."""
    import culture

    return os.path.join(os.path.dirname(culture.__file__), "skills", "communicate")


def _install_communicate_skill(root_dir: str, label: str) -> None:
    """Install the cross-repo + mesh `communicate` skill (SKILL.md + scripts/).

    The two scripts (`post-issue.sh`, `mesh-message.sh`) are written
    executable for the owner so the receiving harness can ``bash`` them
    directly without a separate chmod step. Sourced from steward via
    ``culture/skills/communicate/`` — see the SKILL.md provenance note.
    """
    src_dir = _get_bundled_communicate_dir()
    dest_dir = os.path.join(os.path.expanduser(root_dir), "communicate")
    dest_scripts = os.path.join(dest_dir, "scripts")

    os.makedirs(dest_scripts, exist_ok=True)

    src_skill = os.path.join(src_dir, _SKILL_FILENAME)
    dest_skill = os.path.join(dest_dir, _SKILL_FILENAME)
    shutil.copy2(src_skill, dest_skill)

    for script in _COMMUNICATE_SCRIPTS:
        src_script = os.path.join(src_dir, "scripts", script)
        dest_script = os.path.join(dest_scripts, script)
        shutil.copy2(src_script, dest_script)
        # Wheels strip the +x bit; restore it for owner only. Skills
        # land in single-user dirs (~/.claude/skills/, ~/.agents/skills/,
        # ...) so neither group nor world need execute — and granting
        # them is what trips Sonar's S2612.
        st = os.stat(dest_script)
        os.chmod(
            dest_script,
            (st.st_mode | stat.S_IXUSR)
            & ~(stat.S_IXGRP | stat.S_IXOTH | stat.S_IWGRP | stat.S_IWOTH),
        )

    print(f"Installed {label} communicate skill: {dest_dir}")


def _install_skill_claude() -> None:
    """Install IRC skill for Claude Code."""
    src = _get_bundled_skill_path()
    dest_dir = os.path.expanduser("~/.claude/skills/irc")
    dest = os.path.join(dest_dir, _SKILL_FILENAME)

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed Claude Code messaging skill: {dest}")
    _install_admin_skill("~/.claude/skills", "Claude Code")
    _install_communicate_skill("~/.claude/skills", "Claude Code")


def _get_bundled_codex_skill_path() -> str:
    import cultureagent

    return os.path.join(
        os.path.dirname(cultureagent.__file__), "clients", "codex", "skill", _SKILL_FILENAME
    )


def _install_skill_codex() -> None:
    """Install IRC skill for Codex."""
    src = _get_bundled_codex_skill_path()
    dest_dir = os.path.expanduser("~/.agents/skills/culture-irc")
    dest = os.path.join(dest_dir, _SKILL_FILENAME)

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed Codex messaging skill: {dest}")
    _install_admin_skill("~/.agents/skills", "Codex")
    _install_communicate_skill("~/.agents/skills", "Codex")


def _get_bundled_copilot_skill_path() -> str:
    import cultureagent

    return os.path.join(
        os.path.dirname(cultureagent.__file__), "clients", "copilot", "skill", _SKILL_FILENAME
    )


def _install_skill_copilot() -> None:
    """Install IRC skill for GitHub Copilot."""
    src = _get_bundled_copilot_skill_path()
    dest_dir = os.path.expanduser("~/.copilot_skills/culture-irc")
    dest = os.path.join(dest_dir, _SKILL_FILENAME)

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed Copilot messaging skill: {dest}")
    _install_admin_skill("~/.copilot_skills", "Copilot")
    _install_communicate_skill("~/.copilot_skills", "Copilot")


def _get_bundled_acp_skill_path() -> str:
    import cultureagent

    return os.path.join(
        os.path.dirname(cultureagent.__file__), "clients", "acp", "skill", _SKILL_FILENAME
    )


def _install_skill_acp() -> None:
    """Install IRC skill for ACP agents (Cline, OpenCode, etc.)."""
    src = _get_bundled_acp_skill_path()
    dest_dir = os.path.expanduser("~/.acp/skills/culture-irc")
    dest = os.path.join(dest_dir, _SKILL_FILENAME)

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed ACP messaging skill: {dest}")
    _install_admin_skill("~/.acp/skills", "ACP")
    _install_communicate_skill("~/.acp/skills", "ACP")
