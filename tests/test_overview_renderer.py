"""Tests for overview text renderer."""

import time

from culture.overview.model import Agent, MeshState, Message, Room
from culture.overview.renderer_text import render_text


def _make_fixture() -> MeshState:
    """Build a realistic MeshState for testing."""
    now = time.time()
    spark_claude = Agent(
        nick="spark-claude",
        status="active",
        activity="working on: PR #47 review",
        channels=["#general", "#dev"],
        server="spark",
        backend="claude",
        model="claude-opus-4-6",
        directory="/home/spark/git/culture",
        turns=142,
        uptime="3h 22m",
    )
    spark_codex = Agent(
        nick="spark-codex",
        status="idle",
        activity="idle since 15m",
        channels=["#dev"],
        server="spark",
        backend="codex",
        model="codex",
        directory="/home/spark/git/project",
        turns=30,
        uptime="1h 5m",
    )
    thor_claude = Agent(
        nick="thor-claude",
        status="remote",
        activity="",
        channels=["#general"],
        server="thor",
    )

    general_msgs = [
        Message(
            nick="spark-claude", text="I've pushed the fix", timestamp=now - 120, channel="#general"
        ),
        Message(
            nick="thor-claude", text="looks good, approved", timestamp=now - 240, channel="#general"
        ),
    ]
    dev_msgs = [
        Message(nick="spark-claude", text="tests passing", timestamp=now - 1200, channel="#dev"),
    ]

    general = Room(
        name="#general",
        topic="Agent coordination & planning",
        members=[spark_claude, thor_claude],
        operators=["spark-claude"],
        federation_servers=["thor"],
        messages=general_msgs,
    )
    dev = Room(
        name="#dev",
        topic="culture development",
        members=[spark_claude, spark_codex],
        operators=[],
        federation_servers=[],
        messages=dev_msgs,
    )

    return MeshState(
        server_name="spark",
        rooms=[general, dev],
        agents=[spark_claude, spark_codex, thor_claude],
        federation_links=["thor"],
    )


def test_default_view_has_mesh_header():
    mesh = _make_fixture()
    output = render_text(mesh)
    assert output.startswith("# spark mesh\n")
    assert "2 rooms" in output
    assert "3 agents" in output
    assert "1 federation link (thor)" in output


def test_default_view_has_room_headers():
    mesh = _make_fixture()
    output = render_text(mesh)
    assert "## #general" in output
    assert "## #dev" in output


def test_default_view_has_agent_tables():
    mesh = _make_fixture()
    output = render_text(mesh)
    assert "| spark-claude" in output
    assert "| thor-claude" in output
    assert "| active" in output or "active" in output
    assert "| remote" in output or "remote" in output


def test_default_view_has_topic():
    mesh = _make_fixture()
    output = render_text(mesh)
    assert "Topic: Agent coordination & planning" in output
    assert "Topic: culture development" in output


def test_default_view_has_messages():
    mesh = _make_fixture()
    output = render_text(mesh)
    assert "I've pushed the fix" in output
    assert "looks good, approved" in output
    assert "tests passing" in output


def test_default_view_message_limit():
    """Default shows 4 messages max per room."""
    now = time.time()
    agent = Agent(nick="a", status="active", activity="", channels=["#test"], server="s")
    msgs = [
        Message(nick="a", text=f"msg {i}", timestamp=now - i * 60, channel="#test")
        for i in range(10)
    ]
    room = Room(
        name="#test", topic="", members=[agent], operators=[], federation_servers=[], messages=msgs
    )
    mesh = MeshState(server_name="s", rooms=[room], agents=[agent], federation_links=[])
    output = render_text(mesh)
    # Should only contain 4 messages (the most recent)
    assert output.count("- a (") == 4


def test_empty_mesh():
    mesh = MeshState(server_name="spark", rooms=[], agents=[], federation_links=[])
    output = render_text(mesh)
    assert "# spark mesh" in output
    assert "0 rooms" in output


def test_room_drilldown():
    mesh = _make_fixture()
    output = render_text(mesh, room_filter="#general")
    assert output.startswith("# #general\n")
    assert "Members: 2" in output
    assert "Operators: spark-claude" in output
    assert "Federation: thor" in output
    # Should NOT show #dev
    assert "## #dev" not in output


def test_room_drilldown_not_found():
    mesh = _make_fixture()
    output = render_text(mesh, room_filter="#nonexistent")
    assert "not found" in output


def test_agent_drilldown():
    mesh = _make_fixture()
    output = render_text(mesh, agent_filter="spark-claude")
    assert output.startswith("# spark-claude\n")
    assert "| Backend | claude |" in output
    assert "| Model | claude-opus-4-6 |" in output
    assert "| Directory | /home/spark/git/culture |" in output
    assert "| Turns | 142 |" in output
    assert "| Uptime | 3h 22m |" in output
    # Channels table
    assert "## Channels (2)" in output
    assert "| #general | operator |" in output
    assert "| #dev | member |" in output
    # Cross-channel activity
    assert "## Recent activity across channels" in output


def test_agent_drilldown_not_found():
    mesh = _make_fixture()
    output = render_text(mesh, agent_filter="nonexistent")
    assert "not found" in output


def test_archived_bot_shows_marker():
    """Issue #184: archived bots should show [archived] in overview."""
    from culture.overview.model import BotInfo

    mesh = _make_fixture()
    mesh.bots = [
        BotInfo(
            name="spark-test-bot",
            owner="spark",
            trigger_type="webhook",
            channels=["#general"],
            status="configured",
            archived=True,
        )
    ]
    output = render_text(mesh)
    assert "spark-test-bot [archived]" in output


def test_non_archived_bot_no_marker():
    """Issue #184: non-archived bots should NOT show [archived]."""
    from culture.overview.model import BotInfo

    mesh = _make_fixture()
    mesh.bots = [
        BotInfo(
            name="spark-active-bot",
            owner="spark",
            trigger_type="webhook",
            channels=["#general"],
            status="configured",
            archived=False,
        )
    ]
    output = render_text(mesh)
    assert "spark-active-bot" in output
    assert "[archived]" not in output


def test_custom_message_limit():
    mesh = _make_fixture()
    output = render_text(mesh, message_limit=1)
    # #general has 2 messages, should only show 1
    lines = [
        l
        for l in output.split("\n")
        if l.startswith("- spark-claude (") or l.startswith("- thor-claude (")
    ]
    # 1 per room: #general gets 1, #dev gets 1
    assert len(lines) == 2
