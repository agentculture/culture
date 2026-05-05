"""Tests for learn prompt generation (issues #181, #183)."""

from culture.learn_prompt import generate_learn_prompt


def test_learn_prompt_contains_all_nine_commands():
    """Issue #181: learn prompt should document all 9 IRC commands."""
    output = generate_learn_prompt(nick="spark-claude", server="spark")
    for cmd in ["message", "read", "ask", "join", "part", "who", "list", "compact", "clear"]:
        assert f"`{cmd}`" in output, f"Missing command: {cmd}"


def test_learn_prompt_ask_has_timeout():
    """Issue #181: ask command should show --timeout parameter."""
    output = generate_learn_prompt(nick="spark-claude", server="spark")
    assert "--timeout" in output


def test_learn_prompt_uses_culture_channel_cli():
    """All backends should use 'culture channel' CLI instead of python3 -m."""
    for backend in ["claude", "codex", "copilot", "acp"]:
        output = generate_learn_prompt(nick=f"spark-{backend}", backend=backend)
        assert "culture channel" in output, f"Missing 'culture channel' for {backend}"
        assert "python3 -m" not in output, f"Stale python3 -m reference for {backend}"


def test_learn_prompt_has_bot_management():
    """Issue #183: learn prompt should include bot management commands."""
    output = generate_learn_prompt(nick="spark-claude", server="spark")
    assert "culture bot create" in output
    assert "culture bot list" in output
    assert "culture bot start" in output


def test_learn_prompt_has_extended_agent_commands():
    """Issue #183: learn prompt should include rename, archive, delete."""
    output = generate_learn_prompt(nick="spark-claude", server="spark")
    assert "agent rename" in output
    assert "agent archive" in output
    assert "agent delete" in output


def test_learn_prompt_has_mesh_observability():
    """Issue #183: learn prompt should include mesh overview and the web console.

    Updated 2026-05-05: `culture mesh console` is deprecated; the learn
    prompt now references `culture console` (irc-lens passthrough).
    """
    output = generate_learn_prompt(nick="spark-claude", server="spark")
    assert "culture mesh overview" in output
    assert "culture console" in output


def test_learn_prompt_opencode_backend_normalized():
    """Legacy 'opencode' backend should be normalized to 'acp'."""
    output = generate_learn_prompt(nick="spark-acp", backend="opencode")
    assert "culture channel" in output
    assert "python3 -m" not in output


def test_learn_prompt_teaches_communicate_skill_setup():
    """8.9.0: prompt walks the agent through creating their own communicate skill."""
    output = generate_learn_prompt(nick="spark-claude", server="spark", backend="claude")
    # Walkthrough section is present.
    assert "Set Up Your `communicate` Skill" in output
    # Path is a generic placeholder — the agent maps it to whatever their
    # harness uses for project-local skills. No backend-specific path baked
    # into the prompt.
    assert "<current-project>/<your-skills-location>/communicate/SKILL.md" in output
    assert "<current-project>/<your-skills-location>/communicate/scripts/post-issue.sh" in output
    # Includes both halves: in-mesh and cross-repo.
    assert "culture channel" in output
    assert "post-issue.sh" in output


def test_learn_prompt_signature_per_agent():
    """The agent's communicate skill auto-signs `- <nick> (<harness>)`, not as culture."""
    # Claude harness pretty-prints to "Claude Code".
    output = generate_learn_prompt(nick="spark-claude", server="spark", backend="claude")
    assert "- spark-claude (Claude Code)" in output

    # Codex harness pretty-prints to "Codex".
    output = generate_learn_prompt(nick="thor-codex", server="thor", backend="codex")
    assert "- thor-codex (Codex)" in output

    # Copilot.
    output = generate_learn_prompt(nick="orin-copilot", server="orin", backend="copilot")
    assert "- orin-copilot (Copilot)" in output

    # ACP (legacy "opencode" normalizes to acp).
    output = generate_learn_prompt(nick="dev-acp", backend="opencode")
    assert "- dev-acp (ACP)" in output


def test_learn_prompt_communicate_skill_section_is_harness_agnostic():
    """The communicate-skill walkthrough uses a generic project-relative placeholder.

    The user's directive: paths in this section should be in the current
    project directory and harness-agnostic — the agent reading the prompt
    knows which concrete project-local skills directory their own harness
    uses. So the prompt uses `<current-project>/<your-skills-location>/communicate/` as a
    placeholder rather than baking in `.claude/skills/`, `.agents/skills/`,
    `.copilot_skills/`, etc.
    """
    for backend in ("claude", "codex", "acp", "copilot"):
        output = generate_learn_prompt(nick=f"spark-{backend}", backend=backend)
        set_up_section = output.split("## Set Up Your `communicate` Skill", 1)[1]
        set_up_section = set_up_section.split("\n## ", 1)[0]  # cut at next H2
        # Generic placeholder is present.
        assert "<current-project>/<your-skills-location>/communicate/" in set_up_section, (
            f"Set Up section for backend={backend} missing the generic "
            f"<current-project>/<your-skills-location>/communicate/ placeholder"
        )
        # No backend-specific harness paths in this section.
        for harness_path in (
            ".claude/skills/",
            ".agents/skills/",
            ".acp/skills/",
            ".copilot_skills/",
        ):
            assert harness_path not in set_up_section, (
                f"Set Up section for backend={backend} leaks harness-specific "
                f"path {harness_path!r} — should be the generic placeholder"
            )
        # No home-rooted paths.
        assert "~/" not in set_up_section
        assert "/home/" not in set_up_section
