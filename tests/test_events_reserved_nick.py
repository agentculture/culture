"""Clients cannot take nicks starting with `system-`."""

import pytest


@pytest.mark.asyncio
async def test_reserved_nick_rejected(server, make_client):
    c = await make_client("testserv-alice")
    await c.send("NICK system-testserv")
    line = await c.recv()
    assert "432" in line
    assert "system-testserv" in line


@pytest.mark.asyncio
async def test_reserved_nick_rejected_for_any_server(server, make_client):
    c = await make_client("testserv-alice")
    for target in ["system-thor", "system-spark-welcome", "system-foo-bar-baz"]:
        await c.send(f"NICK {target}")
        line = await c.recv()
        assert "432" in line


@pytest.mark.asyncio
async def test_normal_nick_still_accepted(server, make_client):
    c = await make_client("testserv-alice")
    # Valid culture nick must still be accepted post-connect.
    await c.send("NICK testserv-alice2")
    # Successful nick change doesn't generate a response in this implementation.
    # Verify no error by trying to receive with a short timeout.
    lines = await c.recv_all(timeout=0.2)
    # Should be no error responses (no 432, 433, etc.)
    for line in lines:
        assert "432" not in line
        assert "433" not in line


@pytest.mark.asyncio
async def test_reserved_nick_rejected_after_registration(server, make_client):
    c = await make_client("testserv-alice", "alice")
    # Client is now registered (make_client with both nick and user drains welcome messages)
    await c.send("NICK system-testserv")
    line = await c.recv()
    assert "432" in line
    assert "system-testserv" in line
