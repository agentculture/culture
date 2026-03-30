"""Tests for rooms federation (S2S sync)."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_sroommeta_syncs_on_burst(linked_servers, make_client_a):
    """When a managed room exists, its metadata syncs to peers via burst."""
    server_a, server_b = linked_servers

    alice = await make_client_a(nick="alpha-alice", user="alice")
    await alice.send("ROOMCREATE #shared :purpose=Shared room;tags=python;persistent=true")
    await alice.recv_all(timeout=1.0)

    # Set +S to share with beta
    await alice.send("MODE #shared +S beta")
    await alice.recv_all(timeout=1.0)

    await asyncio.sleep(0.3)

    # Server B should have the room metadata
    channel_b = server_b.channels.get("#shared")
    assert channel_b is not None
    assert channel_b.room_id is not None
    assert channel_b.purpose == "Shared room"
    assert channel_b.tags == ["python"]


@pytest.mark.asyncio
async def test_stags_syncs_agent_tags(linked_servers, make_client_a):
    """Agent tags sync via STAGS to federated peers."""
    server_a, server_b = linked_servers

    alice = await make_client_a(nick="alpha-alice", user="alice")
    await alice.send("TAGS alpha-alice python,devops")
    await alice.recv_all(timeout=1.0)

    await asyncio.sleep(0.3)

    rc = server_b.remote_clients.get("alpha-alice")
    assert rc is not None
    assert rc.tags == ["python", "devops"]


@pytest.mark.asyncio
async def test_sroomarchive_propagates(linked_servers, make_client_a):
    """ROOMARCHIVE propagates to federated peers."""
    server_a, server_b = linked_servers

    alice = await make_client_a(nick="alpha-alice", user="alice")
    await alice.send("ROOMCREATE #shared :purpose=Test;persistent=true")
    await alice.recv_all(timeout=1.0)
    await alice.send("MODE #shared +S beta")
    await alice.recv_all(timeout=1.0)
    await asyncio.sleep(0.3)

    await alice.send("ROOMARCHIVE #shared")
    await alice.recv_all(timeout=1.0)
    await asyncio.sleep(0.3)

    assert "#shared" not in server_b.channels
    assert "#shared-archived" in server_b.channels
    assert server_b.channels["#shared-archived"].archived is True
