"""Tests for learn prompt generation (issues #181, #183)."""

from culture.learn_prompt import generate_learn_prompt


def test_learn_prompt_contains_all_nine_commands():
    """Issue #181: learn prompt should document all 9 IRC commands."""
    output = generate_learn_prompt(nick="spark-claude", server="spark")
    for cmd in ["send", "read", "ask", "join", "part", "who", "channels", "compact", "clear"]:
        assert f"`{cmd}`" in output, f"Missing command: {cmd}"


def test_learn_prompt_ask_has_timeout():
    """Issue #181: ask command should show --timeout parameter."""
    output = generate_learn_prompt(nick="spark-claude", server="spark")
    assert "--timeout" in output


def test_learn_prompt_backend_cli_paths():
    """Each backend should use its own module path."""
    for backend in ["claude", "codex", "copilot", "acp"]:
        output = generate_learn_prompt(nick=f"spark-{backend}", backend=backend)
        expected = f"culture.clients.{backend}.skill.irc_client"
        assert expected in output, f"Missing module path for {backend}"


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
    """Issue #183: learn prompt should include mesh overview and console."""
    output = generate_learn_prompt(nick="spark-claude", server="spark")
    assert "culture mesh overview" in output
    assert "culture mesh console" in output


def test_learn_prompt_opencode_backend_normalized():
    """Legacy 'opencode' backend should be normalized to 'acp'."""
    output = generate_learn_prompt(nick="spark-acp", backend="opencode")
    assert "culture.clients.acp.skill.irc_client" in output
