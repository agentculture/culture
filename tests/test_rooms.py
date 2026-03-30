"""Tests for rooms management."""
import asyncio
import pytest


def test_channel_has_room_metadata_fields():
    """Channel should have room metadata fields, all None/empty by default."""
    from agentirc.server.channel import Channel

    ch = Channel("#test")
    assert ch.room_id is None
    assert ch.creator is None
    assert ch.owner is None
    assert ch.purpose is None
    assert ch.instructions is None
    assert ch.tags == []
    assert ch.persistent is False
    assert ch.agent_limit is None
    assert ch.extra_meta == {}
    assert ch.archived is False
    assert ch.created_at is None


def test_channel_is_managed():
    """Channel with room_id is considered managed."""
    from agentirc.server.channel import Channel

    ch = Channel("#test")
    assert ch.is_managed is False
    ch.room_id = "R7K2M9"
    assert ch.is_managed is True


def test_generate_room_id_format():
    """Room ID starts with R followed by uppercase alphanumeric."""
    from agentirc.server.rooms_util import generate_room_id
    import re

    rid = generate_room_id()
    assert rid.startswith("R")
    assert len(rid) >= 6
    assert re.match(r"^R[0-9A-Z]+$", rid)


def test_generate_room_id_uniqueness():
    """Two consecutive calls produce different IDs."""
    from agentirc.server.rooms_util import generate_room_id

    ids = {generate_room_id() for _ in range(100)}
    assert len(ids) == 100


def test_parse_room_meta_basic():
    """Parse key=value pairs separated by semicolons."""
    from agentirc.server.rooms_util import parse_room_meta

    meta = parse_room_meta("purpose=Help with Python;tags=python,code-help;persistent=true")
    assert meta["purpose"] == "Help with Python"
    assert meta["tags"] == "python,code-help"
    assert meta["persistent"] == "true"


def test_parse_room_meta_instructions_last():
    """Instructions field is always last and may contain semicolons."""
    from agentirc.server.rooms_util import parse_room_meta

    meta = parse_room_meta(
        "purpose=Help;tags=py;instructions=Do this; then that; finally done"
    )
    assert meta["purpose"] == "Help"
    assert meta["tags"] == "py"
    assert meta["instructions"] == "Do this; then that; finally done"


def test_parse_room_meta_empty():
    """Empty string returns empty dict."""
    from agentirc.server.rooms_util import parse_room_meta

    assert parse_room_meta("") == {}


@pytest.mark.asyncio
async def test_roomcreate_basic(server, make_client):
    """ROOMCREATE creates a managed room and returns room ID."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python,code-help;persistent=true"
    )
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)

    # Should get a ROOMCREATED response with room ID
    assert "ROOMCREATED" in joined
    assert "#pyhelp" in joined
    assert " R" in joined  # room ID starts with R

    # Should have auto-joined the channel
    assert "JOIN" in joined
    assert "353" in joined  # RPL_NAMREPLY


@pytest.mark.asyncio
async def test_roomcreate_stores_metadata(server, make_client):
    """ROOMCREATE stores metadata on the channel."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python,code-help;persistent=true;agent_limit=5"
    )
    await alice.recv_all(timeout=1.0)

    channel = server.channels.get("#pyhelp")
    assert channel is not None
    assert channel.is_managed
    assert channel.room_id is not None
    assert channel.room_id.startswith("R")
    assert channel.creator == "testserv-alice"
    assert channel.owner == "testserv-alice"
    assert channel.purpose == "Python help"
    assert channel.tags == ["python", "code-help"]
    assert channel.persistent is True
    assert channel.agent_limit == 5
    assert channel.created_at is not None


@pytest.mark.asyncio
async def test_roomcreate_with_instructions(server, make_client):
    """ROOMCREATE handles instructions field (may contain semicolons)."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send(
        "ROOMCREATE #help :purpose=Help;tags=py;instructions=Do this; then that; done"
    )
    await alice.recv_all(timeout=1.0)

    channel = server.channels["#help"]
    assert channel.instructions == "Do this; then that; done"


@pytest.mark.asyncio
async def test_roomcreate_duplicate_name(server, make_client):
    """ROOMCREATE on existing channel fails."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE #pyhelp :purpose=first")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMCREATE #pyhelp :purpose=second")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "already exists" in joined.lower() or "403" in joined


@pytest.mark.asyncio
async def test_roomcreate_requires_hash(server, make_client):
    """ROOMCREATE requires channel name starting with #."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE badname :purpose=test")
    lines = await alice.recv_all(timeout=1.0)
    assert "badname" not in server.channels


@pytest.mark.asyncio
async def test_roomcreate_no_params(server, make_client):
    """ROOMCREATE with missing params returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE")
    resp = await alice.recv()
    assert "461" in resp  # ERR_NEEDMOREPARAMS


@pytest.mark.asyncio
async def test_client_tags_default_empty(server, make_client):
    """Client tags default to empty list."""
    alice = await make_client(nick="testserv-alice", user="alice")
    client = server.clients["testserv-alice"]
    assert client.tags == []


@pytest.mark.asyncio
async def test_roommeta_query_all(server, make_client):
    """ROOMMETA with just channel name returns all metadata."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send(
        "ROOMCREATE #pyhelp :purpose=Python help;tags=python,code-help;persistent=true"
    )
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #pyhelp")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)

    assert "room_id" in joined
    assert "purpose" in joined
    assert "Python help" in joined
    assert "tags" in joined
    assert "python" in joined
    assert "ROOMETAEND" in joined


@pytest.mark.asyncio
async def test_roommeta_query_single_key(server, make_client):
    """ROOMMETA with key returns just that field."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help;tags=python")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #pyhelp tags")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)

    assert "tags" in joined
    assert "python" in joined


@pytest.mark.asyncio
async def test_roommeta_update_tags(server, make_client):
    """ROOMMETA with key and value updates the field (owner can write)."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help;tags=python")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #pyhelp tags python,devops,code-help")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "updated" in joined.lower() or "ROOMETASET" in joined

    channel = server.channels["#pyhelp"]
    assert channel.tags == ["python", "devops", "code-help"]


@pytest.mark.asyncio
async def test_roommeta_update_owner(server, make_client):
    """Room owner can be transferred via ROOMMETA."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("ROOMCREATE #pyhelp :purpose=Test")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #pyhelp owner testserv-bob")
    await alice.recv_all(timeout=1.0)

    channel = server.channels["#pyhelp"]
    assert channel.owner == "testserv-bob"


@pytest.mark.asyncio
async def test_roommeta_non_owner_cannot_write(server, make_client):
    """Non-owner/non-operator cannot update room metadata."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("ROOMCREATE #pyhelp :purpose=Test;tags=python")
    await alice.recv_all(timeout=1.0)

    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    await bob.send("ROOMMETA #pyhelp tags hacked")
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "permission" in joined.lower() or "482" in joined

    channel = server.channels["#pyhelp"]
    assert channel.tags == ["python"]


@pytest.mark.asyncio
async def test_roommeta_nonexistent_channel(server, make_client):
    """ROOMMETA on nonexistent channel returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ROOMMETA #noroom")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "403" in joined  # ERR_NOSUCHCHANNEL


@pytest.mark.asyncio
async def test_roommeta_on_plain_channel(server, make_client):
    """ROOMMETA on non-managed channel returns not-managed notice."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #plain")
    await alice.recv_all(timeout=1.0)

    await alice.send("ROOMMETA #plain")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "not a managed room" in joined.lower() or "NOTICE" in joined


@pytest.mark.asyncio
async def test_tags_set_own(server, make_client):
    """Agent can set its own tags."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("TAGS testserv-alice python,code-review,agentirc")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "TAGSSET" in joined

    client = server.clients["testserv-alice"]
    assert client.tags == ["python", "code-review", "agentirc"]


@pytest.mark.asyncio
async def test_tags_query_own(server, make_client):
    """Agent can query its own tags."""
    alice = await make_client(nick="testserv-alice", user="alice")
    client = server.clients["testserv-alice"]
    client.tags = ["python", "devops"]

    await alice.send("TAGS testserv-alice")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "python,devops" in joined


@pytest.mark.asyncio
async def test_tags_query_other(server, make_client):
    """Anyone can query another agent's tags."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    server.clients["testserv-bob"].tags = ["rust", "infra"]

    await alice.send("TAGS testserv-bob")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "rust,infra" in joined


@pytest.mark.asyncio
async def test_tags_no_params(server, make_client):
    """TAGS with no params returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("TAGS")
    resp = await alice.recv()
    assert "461" in resp


@pytest.mark.asyncio
async def test_tags_nonexistent_nick(server, make_client):
    """TAGS on nonexistent nick returns error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("TAGS testserv-nobody")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "401" in joined  # ERR_NOSUCHNICK


@pytest.mark.asyncio
async def test_tags_cannot_set_others(server, make_client):
    """Non-operator cannot set another agent's tags."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("TAGS testserv-bob python")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "permission" in joined.lower() or "NOTICE" in joined

    assert server.clients["testserv-bob"].tags == []


@pytest.mark.asyncio
async def test_room_tag_added_invites_matching_agents(server, make_client):
    """When a room gains a tag, agents with that tag get a ROOMINVITE."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    # Bob has "python" tag
    await bob.send("TAGS testserv-bob python")
    await bob.recv_all(timeout=0.5)

    # Alice creates room without python tag
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help;tags=devops")
    await alice.recv_all(timeout=1.0)
    await bob.recv_all(timeout=0.3)  # drain any messages

    # Alice adds python tag to the room
    await alice.send("ROOMMETA #pyhelp tags devops,python")
    await alice.recv_all(timeout=1.0)

    # Bob should get a ROOMINVITE
    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "#pyhelp" in joined


@pytest.mark.asyncio
async def test_room_tag_removed_notifies_matching_agents(server, make_client):
    """When a room loses a tag, in-room agents with that tag get a notice."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    # Bob has "python" tag
    await bob.send("TAGS testserv-bob python")
    await bob.recv_all(timeout=0.5)

    # Alice creates room with python tag
    await alice.send("ROOMCREATE #pyhelp :purpose=Help;tags=python")
    await alice.recv_all(timeout=1.0)

    # Bob joins the room
    await bob.recv_all(timeout=0.5)  # drain invite
    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    # Alice removes python tag
    await alice.send("ROOMMETA #pyhelp tags devops")
    await alice.recv_all(timeout=1.0)

    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMTAGNOTICE" in joined
    assert "removed" in joined.lower()


@pytest.mark.asyncio
async def test_agent_tag_added_notifies_about_rooms(server, make_client):
    """When an agent gains a tag, it gets notices about matching rooms."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    # Alice creates room with python tag
    await alice.send("ROOMCREATE #pyhelp :purpose=Python help;tags=python")
    await alice.recv_all(timeout=1.0)

    # Bob sets python tag — should get a ROOMINVITE about #pyhelp
    await bob.send("TAGS testserv-bob python")
    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMINVITE" in joined
    assert "#pyhelp" in joined


@pytest.mark.asyncio
async def test_agent_tag_removed_notifies_about_rooms(server, make_client):
    """When an agent loses a tag, it gets a notice about rooms with that tag."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await bob.send("TAGS testserv-bob python,devops")
    await bob.recv_all(timeout=0.5)

    await alice.send("ROOMCREATE #pyhelp :purpose=Help;tags=python")
    await alice.recv_all(timeout=1.0)

    # Bob joins
    await bob.recv_all(timeout=0.5)  # drain invite
    await bob.send("JOIN #pyhelp")
    await bob.recv_all(timeout=1.0)
    await alice.recv_all(timeout=0.3)

    # Bob removes python tag (keeps devops)
    await bob.send("TAGS testserv-bob devops")
    await asyncio.sleep(0.1)
    lines = await bob.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ROOMTAGNOTICE" in joined
    assert "#pyhelp" in joined


@pytest.mark.asyncio
async def test_no_invite_if_already_in_room(server, make_client):
    """Tag engine doesn't invite agents already in the room."""
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("TAGS testserv-alice python")
    await alice.recv_all(timeout=0.5)

    await alice.send("ROOMCREATE #pyhelp :purpose=Help;tags=python")
    await alice.recv_all(timeout=1.0)

    # Alice is already in the room — adding matching tag to room shouldn't re-invite
    await alice.send("ROOMMETA #pyhelp tags python,code-help")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    # Should get ROOMETASET but NOT ROOMINVITE
    assert "ROOMETASET" in joined
    invite_lines = [l for l in lines if "ROOMINVITE" in l]
    assert len(invite_lines) == 0
