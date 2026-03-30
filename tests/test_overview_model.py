"""Tests for overview data model."""
from datetime import datetime, timezone

from agentirc.overview.model import Agent, Message, MeshState, Room


def test_message_creation():
    msg = Message(nick="spark-claude", text="hello world", timestamp=1711785600.0, channel="#general")
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
        directory="/home/spark/git/agentirc",
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
    agent = Agent(nick="spark-claude", status="active", activity="", channels=["#general"], server="spark")
    room = Room(name="#general", topic="test", members=[agent], operators=[], federation_servers=[], messages=[])
    mesh = MeshState(server_name="spark", rooms=[room], agents=[agent], federation_links=[])
    assert mesh.server_name == "spark"
    assert len(mesh.rooms) == 1
    assert len(mesh.agents) == 1
