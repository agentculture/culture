"""Tests for IRC and admin skill documentation (issues #182, #183)."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

IRC_SKILL_FILES = {
    "claude": REPO_ROOT / "plugins" / "claude-code" / "skills" / "irc" / "SKILL.md",
    "codex": REPO_ROOT / "culture" / "clients" / "codex" / "skill" / "SKILL.md",
    "copilot": REPO_ROOT / "culture" / "clients" / "copilot" / "skill" / "SKILL.md",
    "acp": REPO_ROOT / "culture" / "clients" / "acp" / "skill" / "SKILL.md",
}

ADMIN_SKILL = REPO_ROOT / "culture" / "skills" / "culture" / "SKILL.md"
ADMIN_SKILL_PLUGIN = REPO_ROOT / "plugins" / "claude-code" / "skills" / "culture" / "SKILL.md"

CODEX_SOURCE = REPO_ROOT / "culture" / "clients" / "codex" / "skill" / "SKILL.md"
CODEX_PLUGIN = REPO_ROOT / "plugins" / "codex" / "skills" / "culture-irc" / "SKILL.md"


# --- IRC skill parity (issue #182) ---


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
def test_all_irc_skills_have_nine_commands(backend):
    """Issue #182: every backend SKILL.md should document all 9 IRC commands."""
    content = IRC_SKILL_FILES[backend].read_text()
    for cmd in ["send", "read", "ask", "join", "part", "channels", "who", "compact", "clear"]:
        assert cmd in content, f"{backend} SKILL.md missing command: {cmd}"


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
def test_all_irc_skills_have_timeout(backend):
    """Issue #182: every backend should document --timeout on ask."""
    content = IRC_SKILL_FILES[backend].read_text()
    assert "--timeout" in content, f"{backend} SKILL.md missing --timeout"


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
def test_all_irc_skills_have_whispers(backend):
    """Issue #182: every backend should have a Whispers section."""
    content = IRC_SKILL_FILES[backend].read_text()
    assert "Whisper" in content, f"{backend} SKILL.md missing Whispers section"


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
def test_all_irc_skills_use_culture_channel_cli(backend):
    """Issue #215: every backend should use culture channel CLI, not internal modules."""
    content = IRC_SKILL_FILES[backend].read_text()
    assert "culture channel" in content, f"{backend} SKILL.md missing culture channel CLI"
    assert (
        "python3 -m culture" not in content
    ), f"{backend} SKILL.md still uses internal module path"


def test_codex_plugin_matches_source():
    """Codex plugin SKILL.md should match the source copy."""
    source = CODEX_SOURCE.read_text()
    plugin = CODEX_PLUGIN.read_text()
    assert source == plugin, "Codex plugin SKILL.md diverged from source"


# --- Admin skill completeness (issue #183) ---


def test_admin_skill_has_bot_section():
    """Issue #183: admin skill should have Bot Management section."""
    content = ADMIN_SKILL.read_text()
    assert "Bot Management" in content


def test_admin_skill_has_mesh_overview():
    """Issue #183: admin skill should document mesh overview."""
    content = ADMIN_SKILL.read_text()
    assert "mesh overview" in content


def test_admin_skill_has_console():
    """Issue #183: admin skill should document the web console.

    Updated 2026-05-05: `culture mesh console` is deprecated; the admin
    skill now documents `culture console` (irc-lens passthrough).
    """
    content = ADMIN_SKILL.read_text()
    assert "culture console" in content


def test_admin_skill_has_extended_agent_commands():
    """Issue #183: admin skill should document rename, archive, delete."""
    content = ADMIN_SKILL.read_text()
    assert "agent rename" in content
    assert "agent archive" in content
    assert "agent delete" in content


def test_admin_skill_has_extended_server_commands():
    """Issue #183: admin skill should document chat default, rename, archive.

    Renamed from ``server default/rename/archive`` to ``chat default/rename/archive``
    in culture 9.0.0 (Phase A3 of the agentirc extraction).
    """
    content = ADMIN_SKILL.read_text()
    assert "chat default" in content
    assert "chat rename" in content
    assert "chat archive" in content


def test_admin_skill_quick_reference_has_enough_entries():
    """Issue #183: Quick Reference should have >= 15 entries."""
    content = ADMIN_SKILL.read_text()
    # Count table rows (lines starting with '|' that aren't header/separator)
    in_qr = False
    row_count = 0
    for line in content.split("\n"):
        if "## Quick Reference" in line:
            in_qr = True
            continue
        if in_qr:
            if line.startswith("| ") and "---" not in line and "Task" not in line:
                row_count += 1
            elif line.startswith("#"):
                break
    assert row_count >= 15, f"Quick Reference has only {row_count} entries"


def test_admin_skill_plugin_has_bot_section():
    """Issue #183: plugin copy should also have Bot Management."""
    content = ADMIN_SKILL_PLUGIN.read_text()
    assert "Bot Management" in content


def test_admin_skill_plugin_has_mesh_observability():
    """Issue #183: plugin copy should have mesh overview and a console reference.

    The plugin copy is AUTO-COPIED from the canonical SKILL.md and may
    lag behind by a release. Accept either the legacy `mesh console`
    string or the new `culture console` string so the test passes
    whichever side of a sync we're on.
    """
    content = ADMIN_SKILL_PLUGIN.read_text()
    assert "mesh overview" in content
    assert "mesh console" in content or "culture console" in content
