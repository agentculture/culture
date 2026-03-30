"""Tests for overview collector against a real IRC server."""
import asyncio
import pytest
import pytest_asyncio

from agentirc.overview.collector import collect_mesh_state
from agentirc.overview.model import MeshState


@pytest.mark.asyncio
async def test_collect_empty_server(server):
    """Collecting from an empty server returns an empty mesh."""
    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    assert isinstance(mesh, MeshState)
    assert mesh.server_name == server.config.name
    assert mesh.rooms == []
    assert mesh.agents == []


@pytest.mark.asyncio
async def test_collect_with_agent_in_channel(server, make_client):
    """Collecting sees agents and channels."""
    client = await make_client(nick="testserv-agent1", user="agent1")
    await client.send("JOIN #testing")
    await client.recv_all(timeout=0.5)

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    assert len(mesh.rooms) == 1
    assert mesh.rooms[0].name == "#testing"
    assert len(mesh.rooms[0].members) >= 1
    found = any(a.nick == "testserv-agent1" for a in mesh.rooms[0].members)
    assert found


@pytest.mark.asyncio
async def test_collect_sees_topic(server, make_client):
    """Collecting includes channel topics."""
    client = await make_client(nick="testserv-agent1", user="agent1")
    await client.send("JOIN #testing")
    await client.recv_all(timeout=0.5)
    await client.send("TOPIC #testing :Hello world topic")
    await client.recv_all(timeout=0.5)

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    assert mesh.rooms[0].topic == "Hello world topic"


@pytest.mark.asyncio
async def test_collect_sees_messages(server, make_client):
    """Collecting includes recent messages via HISTORY."""
    client = await make_client(nick="testserv-agent1", user="agent1")
    await client.send("JOIN #testing")
    await client.recv_all(timeout=0.5)
    await client.send("PRIVMSG #testing :test message one")
    await client.send("PRIVMSG #testing :test message two")
    await asyncio.sleep(0.3)

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    assert len(mesh.rooms[0].messages) == 2
    assert mesh.rooms[0].messages[0].text == "test message one"
    assert mesh.rooms[0].messages[1].text == "test message two"


@pytest.mark.asyncio
async def test_collect_multiple_rooms(server, make_client):
    """Collecting sees all rooms."""
    c1 = await make_client(nick="testserv-a", user="a")
    c2 = await make_client(nick="testserv-b", user="b")
    await c1.send("JOIN #room1")
    await c2.send("JOIN #room2")
    await c1.recv_all(timeout=0.5)
    await c2.recv_all(timeout=0.5)

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    room_names = sorted(r.name for r in mesh.rooms)
    assert "#room1" in room_names
    assert "#room2" in room_names
