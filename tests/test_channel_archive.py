"""Tests for channel archiving: CHANARCHIVE blocks JOINs, hides from LIST."""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest.mark.asyncio
async def test_chanarchive_blocks_join(server, make_client):
    """After CHANARCHIVE, new clients cannot JOIN the channel.

    Uses ``#archive-target`` (not ``#task-*``) so the new per-class
    JOIN ACL — which scopes ``#task-<suffix>`` to the worker named
    -suffix and its boss — does not silently refuse the test's first
    JOIN. The intent of this test is CHANARCHIVE semantics, not the
    task-channel ACL (covered in test_channel_class_acl).
    """
    client1 = await make_client("testserv-archiver", "archiver")
    await client1.send("JOIN #archive-target")
    await client1.recv_all()  # drain join responses

    # Archive the channel
    await client1.send("CHANARCHIVE #archive-target")
    lines = await client1.recv_all()
    assert any("archived" in line.lower() for line in lines)

    # A second client should be refused
    client2 = await make_client("testserv-latecomer", "latecomer")
    await client2.send("JOIN #archive-target")
    lines = await client2.recv_all()
    # Should get a NOTICE about archived, NOT a JOIN confirmation
    assert any("archived" in line.lower() for line in lines)
    assert not any(
        "JOIN" in line and "archive-target" in line
        for line in lines
        if not line.startswith(":testserv")
    )


@pytest.mark.asyncio
async def test_chanarchive_hides_from_list(server, make_client):
    """Archived channels don't appear in LIST."""
    client = await make_client("testserv-lister", "lister")

    # Create two channels
    await client.send("JOIN #active-task")
    await client.recv_all()
    await client.send("JOIN #done-task")
    await client.recv_all()

    # Archive one
    await client.send("CHANARCHIVE #done-task")
    await client.recv_all()

    # LIST should only show #active-task
    await client.send("LIST")
    lines = await client.recv_all()
    list_lines = [l for l in lines if "322" in l]  # RPL_LIST = 322
    channel_names = [l.split()[3] for l in list_lines if len(l.split()) > 3]
    assert "#active-task" in channel_names
    assert "#done-task" not in channel_names


@pytest.mark.asyncio
async def test_chanarchive_already_archived(server, make_client):
    """Archiving an already-archived channel gives a notice.

    Uses ``#archive-double`` (not ``#task-*``) so the per-class JOIN
    ACL does not silently refuse the first JOIN — see the rationale on
    test_chanarchive_blocks_join above.
    """
    client = await make_client("testserv-double", "double")
    await client.send("JOIN #archive-double")
    await client.recv_all()

    await client.send("CHANARCHIVE #archive-double")
    await client.recv_all()

    # Archive again
    await client.send("CHANARCHIVE #archive-double")
    lines = await client.recv_all()
    assert any("already archived" in line.lower() for line in lines)


@pytest.mark.asyncio
async def test_chanarchive_nonexistent_channel(server, make_client):
    """Archiving a nonexistent channel returns ERR_NOSUCHCHANNEL."""
    client = await make_client("testserv-ghost", "ghost")
    await client.send("CHANARCHIVE #no-such")
    lines = await client.recv_all()
    assert any("403" in line for line in lines)  # ERR_NOSUCHCHANNEL


@pytest.mark.asyncio
async def test_chanarchive_marks_persistent(server, make_client):
    """Qodo PR #27 #5 — archived channel must survive going empty.

    Without persistent=True, the server auto-deletes empty
    non-persistent channels on the last PART, dropping the
    archived flag. The fix sets persistent=True on archive so
    the flag survives indefinitely.
    """
    client = await make_client(nick="testserv-alice", user="alice")
    await client.send("JOIN #durability-test")
    await client.recv_all(timeout=1.0)
    await client.send("CHANARCHIVE #durability-test")
    await client.recv_all(timeout=1.0)
    ch = server.channels.get("#durability-test")
    assert ch is not None
    assert ch.archived is True
    assert ch.persistent is True, "archived channel must be persistent"
    # PART removes member but does NOT delete the channel.
    await client.send("PART #durability-test")
    await client.recv_all(timeout=1.0)
    assert "#durability-test" in server.channels
    assert server.channels["#durability-test"].archived is True
