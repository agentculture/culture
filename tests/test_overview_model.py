"""Tests for overview data model."""

from culture_core.overview.model import Agent, MeshState, Message, Room


def test_message_creation():
    msg = Message(
        nick="spark-claude", text="hello world", timestamp=1711785600.0, channel="#general"
    )
    assert msg.nick == "spark-claude"
    assert msg.text == "hello world"
    assert msg.channel == "#general"


def test_agent_local():
    agent = Agent(
        nick="spark-claude",
        status="active",
        activity="working on: PR #47",
        channels=["#general", "#dev"],
        server="spark",
        backend="claude",
        model="claude-opus-4-6",
        directory="/home/spark/git/culture",
        turns=142,
        uptime="3h 22m",
    )
    assert agent.is_local is True
    assert agent.status == "active"


def test_agent_remote():
    agent = Agent(
        nick="thor-claude",
        status="remote",
        activity="",
        channels=["#general"],
        server="thor",
    )
    assert agent.is_local is False
    assert agent.backend is None


def test_room_creation():
    room = Room(
        name="#general",
        topic="Agent coordination",
        members=[],
        operators=["spark-claude"],
        federation_servers=["thor"],
        messages=[],
    )
    assert room.name == "#general"
    assert room.federation_servers == ["thor"]


def test_mesh_state():
    agent = Agent(
        nick="spark-claude", status="active", activity="", channels=["#general"], server="spark"
    )
    room = Room(
        name="#general",
        topic="test",
        members=[agent],
        operators=[],
        federation_servers=[],
        messages=[],
    )
    mesh = MeshState(server_name="spark", rooms=[room], agents=[agent], federation_links=[])
    assert mesh.server_name == "spark"
    assert len(mesh.rooms) == 1
    assert len(mesh.agents) == 1


def test_room_has_tags_and_metadata():
    """Room dataclass should have tags, room_id, owner, purpose fields."""
    from culture_core.overview.model import Room

    room = Room(
        name="#pyhelp",
        topic="Python help",
        members=[],
        operators=["spark-ori"],
        federation_servers=[],
        messages=[],
        room_id="R7K2M9",
        owner="spark-ori",
        purpose="Python help and discussion",
        tags=["python", "code-help"],
        persistent=True,
    )
    assert room.room_id == "R7K2M9"
    assert room.tags == ["python", "code-help"]
    assert room.owner == "spark-ori"
    assert room.purpose == "Python help and discussion"
    assert room.persistent is True


def test_agent_has_tags():
    """Agent dataclass should have tags field."""
    from culture_core.overview.model import Agent

    agent = Agent(
        nick="spark-claude",
        status="active",
        activity="working",
        channels=["#general"],
        server="spark",
        tags=["python", "code-review"],
    )
    assert agent.tags == ["python", "code-review"]


def test_botinfo_archived_field():
    """Issue #184: BotInfo should accept archived=True."""
    from culture_core.overview.model import BotInfo

    bot = BotInfo(
        name="test-bot",
        owner="spark",
        trigger_type="webhook",
        channels=["#general"],
        status="configured",
        archived=True,
    )
    assert bot.archived is True


def test_botinfo_archived_defaults_false():
    """Issue #184: BotInfo archived should default to False."""
    from culture_core.overview.model import BotInfo

    bot = BotInfo(
        name="test-bot",
        owner="spark",
        trigger_type="webhook",
        channels=["#general"],
        status="configured",
    )
    assert bot.archived is False


def test_agent_stopped_is_local():
    """Issue #178: Stopped agents are local."""
    agent = Agent(
        nick="spark-offline",
        status="stopped",
        activity="",
        channels=[],
        server="spark",
    )
    assert agent.is_local is True


def test_room_defaults_no_metadata():
    """Room with only required fields defaults metadata to None/empty."""
    from culture_core.overview.model import Room

    room = Room(
        name="#plain",
        topic="",
        members=[],
        operators=[],
        federation_servers=[],
        messages=[],
    )
    assert room.room_id is None
    assert room.tags == []
    assert room.owner is None
    assert room.purpose is None
    assert room.persistent is False
    assert room.query_timings == {}


def test_room_query_timings_field():
    """Issue #16: Room carries per-phase query timings."""
    room = Room(
        name="#timed",
        topic="",
        members=[],
        operators=[],
        federation_servers=[],
        messages=[],
        query_timings={"NAMES": 0.01, "WHO": 0.02, "HISTORY": 0.03, "ROOMMETA": 0.04},
    )
    assert room.query_timings["WHO"] == 0.02


def test_failed_room_creation():
    """Issue #16: FailedRoom records name, error class name, and phase."""
    from culture_core.overview.model import FailedRoom

    failed = FailedRoom(name="#broken", error="TimeoutError", phase="WHO")
    assert failed.name == "#broken"
    assert failed.error == "TimeoutError"
    assert failed.phase == "WHO"


def test_mesh_state_failed_rooms_default_empty():
    """Issue #16: MeshState.failed_rooms defaults to an empty list."""
    mesh = MeshState(server_name="spark", rooms=[], agents=[], federation_links=[])
    assert mesh.failed_rooms == []


def test_mesh_state_with_failed_rooms():
    """Issue #16: MeshState accepts failed-room markers."""
    from culture_core.overview.model import FailedRoom

    mesh = MeshState(
        server_name="spark",
        rooms=[],
        agents=[],
        federation_links=[],
        failed_rooms=[FailedRoom(name="#broken", error="ValueError", phase="HISTORY")],
    )
    assert len(mesh.failed_rooms) == 1
    assert mesh.failed_rooms[0].name == "#broken"
