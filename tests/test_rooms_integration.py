"""End-to-end integration test for rooms management."""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_full_room_lifecycle(server, make_client):
    """Full lifecycle: create, set tags, invite, join, metadata, archive."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    # 1. Bob sets tags
    await bob.send("TAGS testserv-bob python,devops")
    await bob.recv_all(timeout=0.5)

    # 2. Alice creates a managed room with python tag
    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python;persistent=true"
        ";instructions=Help with Python questions"
    )
    await alice.recv_all(timeout=1.0)

    # 3. Bob should have received a ROOMINVITE (tag match)
    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "#pyhelp" in joined

    # 4. Bob joins
    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    # 5. Query metadata
    await bob.send("ROOMMETA #pyhelp")
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "Python help" in joined
    assert "python" in joined

    # 6. Query room ID
    await bob.send("ROOMMETA #pyhelp room_id")
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "R" in joined

    # 7. Alice explicitly invites charlie
    charlie = await make_client(nick="testserv-charlie", user="charlie")
    await alice.send("ROOMINVITE #pyhelp testserv-charlie")
    await alice.recv_all(timeout=0.5)
    lines = await charlie.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "testserv-alice" in joined  # requestor

    # 8. Alice archives the room
    await alice.send("ROOMARCHIVE #pyhelp")
    await alice.recv_all(timeout=1.0)
    await bob.recv_all(timeout=1.0)

    assert "#pyhelp" not in server.channels
    assert "#pyhelp-archived" in server.channels
    assert server.channels["#pyhelp-archived"].archived is True

    # 9. Name is free — create a new room
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help v2")
    await alice.recv_all(timeout=1.0)
    assert "#pyhelp" in server.channels
    assert server.channels["#pyhelp"].purpose == "Python help v2"


@pytest.mark.asyncio
async def test_persistent_room_survives_empty(server, make_client):
    """Persistent managed room stays when all members leave."""
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("ROOMCREATE #persistent :purpose=Stays;persistent=true")
    await alice.recv_all(timeout=1.0)

    room_id = server.channels["#persistent"].room_id

    await alice.send("PART #persistent")
    await alice.recv_all(timeout=1.0)

    # Room still exists
    assert "#persistent" in server.channels
    assert server.channels["#persistent"].room_id == room_id

    # Can rejoin
    await alice.send("JOIN #persistent")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "#persistent" in joined


@pytest.mark.asyncio
async def test_non_persistent_room_cleaned_up(server, make_client):
    """Non-persistent managed room is cleaned up when empty."""
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("ROOMCREATE #temp :purpose=Temporary;persistent=false")
    await alice.recv_all(timeout=1.0)

    await alice.send("PART #temp")
    await alice.recv_all(timeout=1.0)

    assert "#temp" not in server.channels
