"""Tests for server-side user modes (+H/+A/+B) and the ICON skill."""

import pytest

# ---------------------------------------------------------------------------
# User mode tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_mode_set_h(server, make_client):
    """MODE <nick> +H sets the Human mode flag and is returned in RPL_UMODEIS."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("MODE testserv-ori +H")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "221" in joined
    assert "+H" in joined


@pytest.mark.asyncio
async def test_user_mode_set_a(server, make_client):
    """MODE <nick> +A sets the Admin flag."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("MODE testserv-ori +A")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "221" in joined
    assert "+A" in joined


@pytest.mark.asyncio
async def test_user_mode_set_b(server, make_client):
    """MODE <nick> +B sets the Bot flag."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("MODE testserv-ori +B")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "221" in joined
    assert "+B" in joined


@pytest.mark.asyncio
async def test_user_mode_multiple_flags(server, make_client):
    """MODE <nick> +HA sets both Human and Admin flags."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("MODE testserv-ori +HA")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "221" in joined
    # Both flags should appear in the mode string
    assert "A" in joined
    assert "H" in joined


@pytest.mark.asyncio
async def test_user_mode_unset(server, make_client):
    """MODE <nick> -H removes the Human mode flag."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("MODE testserv-ori +H")
    await client.recv_all(timeout=0.5)

    await client.send("MODE testserv-ori -H")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "221" in joined
    # After removing, mode string should just be "+"
    assert "+H" not in joined


@pytest.mark.asyncio
async def test_user_mode_other_user_fails(server, make_client):
    """Attempting to change another user's modes returns ERR_USERSDONTMATCH (502)."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await make_client(nick="testserv-claude", user="claude")

    await client1.send("MODE testserv-claude +H")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "502" in joined


@pytest.mark.asyncio
async def test_user_mode_query_no_modes(server, make_client):
    """MODE <nick> with no modes set returns '+' in RPL_UMODEIS."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("MODE testserv-ori")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "221" in joined


# ---------------------------------------------------------------------------
# ICON skill tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_icon_set(server, make_client):
    """ICON <value> sets the client's display icon."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("ICON 🧑")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ICON" in joined
    assert "🧑" in joined


@pytest.mark.asyncio
async def test_icon_query(server, make_client):
    """ICON with no params returns current icon."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("ICON 🤖")
    await client.recv_all(timeout=0.5)

    await client.send("ICON")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ICON" in joined
    assert "🤖" in joined


@pytest.mark.asyncio
async def test_icon_query_none(server, make_client):
    """ICON with no params and no icon set returns '(none)'."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("ICON")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "ICON" in joined
    assert "(none)" in joined


@pytest.mark.asyncio
async def test_icon_too_long(server, make_client):
    """ICON value longer than 4 chars returns an error NOTICE."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("ICON toolong")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "NOTICE" in joined
    assert "too long" in joined.lower()


# ---------------------------------------------------------------------------
# WHO response includes mode and icon info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_who_includes_user_modes(server, make_client):
    """WHO #channel reply includes user mode flags in the flags field."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")

    await client2.send("MODE testserv-claude +H")
    await client2.recv_all(timeout=0.5)

    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("WHO #general")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    # The WHO reply for claude should include [H] in the flags field
    assert "[H]" in joined


@pytest.mark.asyncio
async def test_who_includes_icon(server, make_client):
    """WHO #channel reply includes icon in the flags field."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")

    await client2.send("ICON 🤖")
    await client2.recv_all(timeout=0.5)

    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("WHO #general")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "{🤖}" in joined


@pytest.mark.asyncio
async def test_who_includes_mode_and_icon(server, make_client):
    """WHO reply includes both modes and icon when both are set."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")

    await client2.send("MODE testserv-claude +B")
    await client2.recv_all(timeout=0.5)
    await client2.send("ICON 🤖")
    await client2.recv_all(timeout=0.5)

    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("WHO #general")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "[B]" in joined
    assert "{🤖}" in joined
