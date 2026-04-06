"""Skills subcommands: culture skills {install}."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

NAME = "skills"


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
        print("Usage: culture skills install <claude|codex|copilot|acp|all>", file=sys.stderr)
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

    return os.path.join(os.path.dirname(culture.__file__), "skills", "culture", "SKILL.md")


def _get_bundled_skill_path() -> str:
    """Return the path to the bundled SKILL.md in the installed package."""
    import culture

    return os.path.join(os.path.dirname(culture.__file__), "clients", "claude", "skill", "SKILL.md")


def _install_admin_skill(root_dir: str, label: str) -> None:
    """Install the admin/ops skill to the given root skills directory."""
    src = _get_bundled_admin_skill_path()
    dest_dir = os.path.join(os.path.expanduser(root_dir), "culture")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed {label} admin skill: {dest}")


def _install_skill_claude() -> None:
    """Install IRC skill for Claude Code."""
    src = _get_bundled_skill_path()
    dest_dir = os.path.expanduser("~/.claude/skills/irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed Claude Code messaging skill: {dest}")
    _install_admin_skill("~/.claude/skills", "Claude Code")


def _get_bundled_codex_skill_path() -> str:
    import culture

    return os.path.join(os.path.dirname(culture.__file__), "clients", "codex", "skill", "SKILL.md")


def _install_skill_codex() -> None:
    """Install IRC skill for Codex."""
    src = _get_bundled_codex_skill_path()
    dest_dir = os.path.expanduser("~/.agents/skills/culture-irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed Codex messaging skill: {dest}")
    _install_admin_skill("~/.agents/skills", "Codex")


def _get_bundled_copilot_skill_path() -> str:
    import culture

    return os.path.join(
        os.path.dirname(culture.__file__), "clients", "copilot", "skill", "SKILL.md"
    )


def _install_skill_copilot() -> None:
    """Install IRC skill for GitHub Copilot."""
    src = _get_bundled_copilot_skill_path()
    dest_dir = os.path.expanduser("~/.copilot_skills/culture-irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed Copilot messaging skill: {dest}")
    _install_admin_skill("~/.copilot_skills", "Copilot")


def _get_bundled_acp_skill_path() -> str:
    import culture

    return os.path.join(os.path.dirname(culture.__file__), "clients", "acp", "skill", "SKILL.md")


def _install_skill_acp() -> None:
    """Install IRC skill for ACP agents (Cline, OpenCode, etc.)."""
    src = _get_bundled_acp_skill_path()
    dest_dir = os.path.expanduser("~/.acp/skills/culture-irc")
    dest = os.path.join(dest_dir, "SKILL.md")

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"Installed ACP messaging skill: {dest}")
    _install_admin_skill("~/.acp/skills", "ACP")
