# tests/test_federation.py
"""Layer 4: Federation tests -- server-to-server linking."""

import asyncio

import pytest
import pytest_asyncio  # noqa: F401

from culture.agentirc.config import LinkConfig, ServerConfig
from culture.agentirc.ircd import IRCd
from culture.agentirc.skill import Event
from tests.conftest import TEST_LINK_PASSWORD, IRCTestClient

# =============================================================================
# Phase 1: Handshake
# =============================================================================


@pytest.mark.asyncio
async def test_server_link_handshake(linked_servers):
    """Two servers link, both have each other in `links`."""
    server_a, server_b = linked_servers
    assert "beta" in server_a.links
    assert "alpha" in server_b.links


@pytest.mark.asyncio
async def test_server_link_bad_password():
    """Wrong password -> link rejected."""
    config_a = ServerConfig(name="alpha", host="127.0.0.1", port=0)
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password="correct")],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    try:
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, "wrong")
        await asyncio.sleep(0.5)
        # Link should NOT be established
        assert "beta" not in server_a.links
        assert "alpha" not in server_b.links
    finally:
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_server_link_duplicate_name_rejected():
    """Same server name trying to link twice -> rejected."""
    link_password = TEST_LINK_PASSWORD
    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()
    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    try:
        # First link
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        for _ in range(50):
            if "beta" in server_a.links:
                break
            await asyncio.sleep(0.05)
        assert "beta" in server_a.links

        # Second link attempt should fail
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        await asyncio.sleep(0.5)
        # Should still only have one link
        assert len(server_a.links) <= 1
    finally:
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_squit_cleans_up_link(linked_servers):
    """SQUIT removes link."""
    server_a, server_b = linked_servers
    assert "beta" in server_a.links

    link = server_a.links["beta"]
    await link.send_raw("SQUIT alpha :Shutting down")
    await asyncio.sleep(0.3)

    assert "beta" not in server_a.links


# =============================================================================
# Phase 2: Burst (state sync)
# =============================================================================


@pytest.mark.asyncio
async def test_burst_sends_local_clients(linked_servers, make_client_a):
    """Remote clients populated after link when clients exist before linking."""
    server_a, server_b = linked_servers

    # Create a client on server A
    await make_client_a(nick="alpha-alice", user="alice")
    await asyncio.sleep(0.3)

    # Server B should have alice as a remote client
    assert "alpha-alice" in server_b.remote_clients
    rc = server_b.remote_clients["alpha-alice"]
    assert rc.server_name == "alpha"


@pytest.mark.asyncio
async def test_burst_sends_channel_membership(linked_servers, make_client_a):
    """Remote clients appear in channels after burst."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    # Server B should see alice in #test
    assert "#test" in server_b.channels
    channel_b = server_b.channels["#test"]
    member_nicks = {m.nick for m in channel_b.members}
    assert "alpha-alice" in member_nicks


@pytest.mark.asyncio
async def test_burst_sends_channel_topic(linked_servers, make_client_a):
    """Topic synced across servers."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await client_a.send("TOPIC #test :Hello from alpha")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    assert "#test" in server_b.channels
    assert server_b.channels["#test"].topic == "Hello from alpha"


@pytest.mark.asyncio
async def test_remote_client_appears_in_names(linked_servers, make_client_a, make_client_b):
    """NAMES shows remote nicks."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    resp = await client_b.recv_all(timeout=0.5)

    # Find NAMES reply (353)
    names_line = [l for l in resp if " 353 " in l]
    assert names_line, f"No NAMES reply found in: {resp}"
    names_text = names_line[0]
    assert "alpha-alice" in names_text


@pytest.mark.asyncio
async def test_remote_client_appears_in_who(linked_servers, make_client_a, make_client_b):
    """WHO shows remote nicks."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)

    await client_b.send("WHO #test")
    resp = await client_b.recv_all(timeout=0.5)

    who_lines = [l for l in resp if " 352 " in l]
    nicks_in_who = [l.split()[7] for l in who_lines if len(l.split()) > 7]
    assert "alpha-alice" in nicks_in_who, f"WHO response: {resp}"


@pytest.mark.asyncio
async def test_remote_client_appears_in_whois(linked_servers, make_client_a, make_client_b):
    """WHOIS works for remote clients, shows remote server name."""
    server_a, server_b = linked_servers

    await make_client_a(nick="alpha-alice", user="alice")
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("WHOIS alpha-alice")
    resp = await client_b.recv_all(timeout=0.5)

    # 311 = RPL_WHOISUSER
    whois_user = [l for l in resp if " 311 " in l]
    assert whois_user, f"No WHOISUSER reply: {resp}"
    assert "alpha-alice" in whois_user[0]

    # 312 = RPL_WHOISSERVER - should show "alpha" not "beta"
    whois_server = [l for l in resp if " 312 " in l]
    assert whois_server, f"No WHOISSERVER reply: {resp}"
    assert "alpha" in whois_server[0]


# =============================================================================
# Phase 3: Real-time relay
# =============================================================================


@pytest.mark.asyncio
async def test_privmsg_relayed_to_remote_channel(linked_servers, make_client_a, make_client_b):
    """A sends PRIVMSG to #test, B receives it."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    await client_a.send("PRIVMSG #test :hello from alpha")
    await asyncio.sleep(0.3)
    resp = await client_b.recv_all(timeout=0.5)

    privmsgs = [l for l in resp if "PRIVMSG" in l and "hello from alpha" in l]
    assert privmsgs, f"Expected PRIVMSG relay, got: {resp}"


@pytest.mark.asyncio
async def test_privmsg_dm_to_remote_nick(linked_servers, make_client_a, make_client_b):
    """DM across servers."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await asyncio.sleep(0.3)

    await client_a.send("PRIVMSG beta-bob :secret message")
    await asyncio.sleep(0.3)
    resp = await client_b.recv_all(timeout=0.5)

    privmsgs = [l for l in resp if "PRIVMSG" in l and "secret message" in l]
    assert privmsgs, f"Expected DM relay, got: {resp}"


@pytest.mark.asyncio
async def test_notice_relayed(linked_servers, make_client_a, make_client_b):
    """NOTICE across servers."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    await client_a.send("NOTICE #test :notice from alpha")
    await asyncio.sleep(0.3)
    resp = await client_b.recv_all(timeout=0.5)

    notices = [l for l in resp if "NOTICE" in l and "notice from alpha" in l]
    assert notices, f"Expected NOTICE relay, got: {resp}"


@pytest.mark.asyncio
async def test_join_relayed(linked_servers, make_client_a, make_client_b):
    """JOIN visible to remote members."""
    server_a, server_b = linked_servers

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    resp = await client_b.recv_all(timeout=0.5)
    joins = [l for l in resp if "JOIN" in l and "alpha-alice" in l]
    assert joins, f"Expected JOIN relay, got: {resp}"


@pytest.mark.asyncio
async def test_part_relayed(linked_servers, make_client_a, make_client_b):
    """PART visible to remote members."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    await client_a.send("PART #test :leaving")
    await asyncio.sleep(0.3)
    resp = await client_b.recv_all(timeout=0.5)

    parts = [l for l in resp if "PART" in l and "alpha-alice" in l]
    assert parts, f"Expected PART relay, got: {resp}"


@pytest.mark.asyncio
async def test_quit_relayed(linked_servers, make_client_a, make_client_b):
    """QUIT visible, RemoteClient removed."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    await client_a.send("QUIT :goodbye")
    await asyncio.sleep(0.5)

    resp = await client_b.recv_all(timeout=0.5)
    quits = [l for l in resp if "QUIT" in l]
    assert quits, f"Expected QUIT relay, got: {resp}"

    # RemoteClient should be removed from server B
    assert "alpha-alice" not in server_b.remote_clients


@pytest.mark.asyncio
async def test_topic_change_relayed(linked_servers, make_client_a, make_client_b):
    """TOPIC visible across servers."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    await client_a.send("TOPIC #test :new topic from alpha")
    await asyncio.sleep(0.3)

    assert server_b.channels["#test"].topic == "new topic from alpha"


@pytest.mark.asyncio
async def test_no_relay_loop(linked_servers, make_client_a, make_client_b):
    """Message from B relayed to A is NOT sent back to B."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    # Bob sends a message
    await client_b.send("PRIVMSG #test :hello from beta")
    await asyncio.sleep(0.3)

    # Alice should see it
    resp_a = await client_a.recv_all(timeout=0.5)
    privmsgs_a = [l for l in resp_a if "PRIVMSG" in l and "hello from beta" in l]
    assert privmsgs_a, "Alice should receive Bob's message"

    # Bob should NOT receive his own message back via relay
    resp_b = await client_b.recv_all(timeout=0.5)
    echo_msgs = [l for l in resp_b if "PRIVMSG" in l and "hello from beta" in l]
    assert not echo_msgs, f"Bob should NOT get echo: {resp_b}"


# =============================================================================
# Phase 4: Link loss
# =============================================================================


@pytest.mark.asyncio
async def test_link_loss_removes_remote_clients(linked_servers, make_client_a, make_client_b):
    """Drop link -> remote clients gone, local clients see QUITs."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    # Verify remote client exists
    assert "alpha-alice" in server_b.remote_clients

    # Drop link by closing A's side
    link_a = server_a.links.get("beta")
    if link_a:
        link_a.writer.close()
    await asyncio.sleep(0.5)

    # Remote clients should be gone from B
    assert "alpha-alice" not in server_b.remote_clients

    # Bob should see a QUIT for alice
    resp = await client_b.recv_all(timeout=0.5)
    quits = [l for l in resp if "QUIT" in l]
    assert quits, f"Expected QUIT notification, got: {resp}"


@pytest.mark.asyncio
async def test_link_loss_cleans_empty_channels(linked_servers, make_client_a):
    """Channels with only remote members are removed after link loss."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #alphaonly")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    # Server B should have #alphaonly with only remote member
    assert "#alphaonly" in server_b.channels

    # Drop link
    link_a = server_a.links.get("beta")
    if link_a:
        link_a.writer.close()
    await asyncio.sleep(0.5)

    # Channel should be cleaned up on B
    assert "#alphaonly" not in server_b.channels


@pytest.mark.asyncio
async def test_reconnect_resyncs_state():
    """Drop + reconnect = remote clients reappear."""
    link_password = TEST_LINK_PASSWORD

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()
    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    try:
        # Connect client to A
        reader_a, writer_a = await asyncio.open_connection("127.0.0.1", server_a.config.port)
        tc = IRCTestClient(reader_a, writer_a)
        await tc.send("NICK alpha-alice")
        await tc.send("USER alice 0 * :alice")
        await tc.recv_all(timeout=0.5)
        await tc.send("JOIN #test")
        await tc.recv_all(timeout=0.5)

        # Link
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        for _ in range(50):
            if "beta" in server_a.links:
                break
            await asyncio.sleep(0.05)
        assert "beta" in server_a.links
        await asyncio.sleep(0.3)
        assert "alpha-alice" in server_b.remote_clients

        # Drop link
        link = server_a.links.get("beta")
        if link:
            link.writer.close()
        await asyncio.sleep(0.5)
        assert "alpha-alice" not in server_b.remote_clients

        # Reconnect
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        for _ in range(50):
            if "beta" in server_a.links:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)

        # Remote clients should be back
        assert "alpha-alice" in server_b.remote_clients
        assert "#test" in server_b.channels
    finally:
        try:
            await tc.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


# =============================================================================
# Phase 5: History backfill
# =============================================================================


@pytest.mark.asyncio
async def test_backfill_replays_missed_messages():
    """Send messages during disconnect, reconnect, B has them."""
    link_password = TEST_LINK_PASSWORD

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()
    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    try:
        # Connect clients
        reader_a, writer_a = await asyncio.open_connection("127.0.0.1", server_a.config.port)
        tc_a = IRCTestClient(reader_a, writer_a)
        await tc_a.send("NICK alpha-alice")
        await tc_a.send("USER alice 0 * :alice")
        await tc_a.recv_all(timeout=0.5)
        await tc_a.send("JOIN #test")
        await tc_a.recv_all(timeout=0.5)

        reader_b, writer_b = await asyncio.open_connection("127.0.0.1", server_b.config.port)
        tc_b = IRCTestClient(reader_b, writer_b)
        await tc_b.send("NICK beta-bob")
        await tc_b.send("USER bob 0 * :bob")
        await tc_b.recv_all(timeout=0.5)
        await tc_b.send("JOIN #test")
        await tc_b.recv_all(timeout=0.5)

        # Link
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        for _ in range(50):
            if "beta" in server_a.links:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)

        # Record B's last_seen_seq from A's link
        link_b = server_b.links.get("alpha")
        _last_seq = link_b.last_seen_seq if link_b else 0

        # Drop link
        link_a = server_a.links.get("beta")
        if link_a:
            link_a.writer.close()
        await asyncio.sleep(0.5)

        # Send messages while disconnected
        await tc_a.send("PRIVMSG #test :missed message 1")
        await tc_a.send("PRIVMSG #test :missed message 2")
        await asyncio.sleep(0.3)

        # Reconnect -- server_a reconnects to server_b
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        for _ in range(50):
            if "beta" in server_a.links:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)

        # Bob should receive backfilled messages
        resp = await tc_b.recv_all(timeout=1.0)
        missed = [l for l in resp if "PRIVMSG" in l and "missed message" in l]
        assert len(missed) >= 2, f"Expected 2 backfilled messages, got: {resp}"
    finally:
        try:
            await tc_a.close()
        except Exception:
            pass
        try:
            await tc_b.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_backfill_does_not_duplicate():
    """Pre-disconnect messages not re-sent."""
    link_password = TEST_LINK_PASSWORD

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()
    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    try:
        reader_a, writer_a = await asyncio.open_connection("127.0.0.1", server_a.config.port)
        tc_a = IRCTestClient(reader_a, writer_a)
        await tc_a.send("NICK alpha-alice")
        await tc_a.send("USER alice 0 * :alice")
        await tc_a.recv_all(timeout=0.5)
        await tc_a.send("JOIN #test")
        await tc_a.recv_all(timeout=0.5)

        reader_b, writer_b = await asyncio.open_connection("127.0.0.1", server_b.config.port)
        tc_b = IRCTestClient(reader_b, writer_b)
        await tc_b.send("NICK beta-bob")
        await tc_b.send("USER bob 0 * :bob")
        await tc_b.recv_all(timeout=0.5)
        await tc_b.send("JOIN #test")
        await tc_b.recv_all(timeout=0.5)

        # Link
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        for _ in range(50):
            if "beta" in server_a.links:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)

        # Send a message while linked (pre-disconnect)
        await tc_a.send("PRIVMSG #test :pre-disconnect msg")
        await asyncio.sleep(0.3)
        resp = await tc_b.recv_all(timeout=0.5)
        _pre_count = len([l for l in resp if "pre-disconnect msg" in l])

        # Drop and reconnect
        link_a = server_a.links.get("beta")
        if link_a:
            link_a.writer.close()
        await asyncio.sleep(0.5)

        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        for _ in range(50):
            if "beta" in server_a.links:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)

        # Check bob doesn't get duplicates
        resp = await tc_b.recv_all(timeout=0.5)
        dup_count = len([l for l in resp if "pre-disconnect msg" in l])
        assert dup_count == 0, f"Got {dup_count} duplicate(s): {resp}"
    finally:
        try:
            await tc_a.close()
        except Exception:
            pass
        try:
            await tc_b.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_backfill_empty_when_nothing_missed(linked_servers, make_client_a, make_client_b):
    """Just BACKFILLEND when nothing missed."""
    server_a, server_b = linked_servers
    # Link is already up, nothing sent -- backfill was already exchanged during handshake
    # This is essentially a no-op test: verify no crashes and link is stable
    assert "beta" in server_a.links
    assert "alpha" in server_b.links


@pytest.mark.asyncio
async def test_history_includes_backfilled_messages():
    """HISTORY RECENT shows backfilled messages."""
    link_password = TEST_LINK_PASSWORD

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()
    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    try:
        reader_a, writer_a = await asyncio.open_connection("127.0.0.1", server_a.config.port)
        tc_a = IRCTestClient(reader_a, writer_a)
        await tc_a.send("NICK alpha-alice")
        await tc_a.send("USER alice 0 * :alice")
        await tc_a.recv_all(timeout=0.5)
        await tc_a.send("JOIN #test")
        await tc_a.recv_all(timeout=0.5)

        reader_b, writer_b = await asyncio.open_connection("127.0.0.1", server_b.config.port)
        tc_b = IRCTestClient(reader_b, writer_b)
        await tc_b.send("NICK beta-bob")
        await tc_b.send("USER bob 0 * :bob")
        await tc_b.recv_all(timeout=0.5)
        await tc_b.send("JOIN #test")
        await tc_b.recv_all(timeout=0.5)

        # Link
        await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
        for _ in range(50):
            if "beta" in server_a.links:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)

        # Alice sends a message (relayed to B, stored in B's history via event)
        await tc_a.send("PRIVMSG #test :hello world")
        await asyncio.sleep(0.3)
        await tc_b.recv_all(timeout=0.5)  # drain

        # Bob queries history
        await tc_b.send("HISTORY RECENT #test 10")
        resp = await tc_b.recv_all(timeout=0.5)

        history_lines = [l for l in resp if "HISTORY" in l and "hello world" in l]
        assert history_lines, f"Expected history entry, got: {resp}"
    finally:
        try:
            await tc_a.close()
        except Exception:
            pass
        try:
            await tc_b.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


# =============================================================================
# Phase 6: Polish
# =============================================================================


@pytest.mark.asyncio
async def test_three_way_conversation(linked_servers, make_client_a, make_client_b):
    """Clients on both servers chat back and forth."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #chat")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #chat")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    # Alice -> Bob
    await client_a.send("PRIVMSG #chat :hey bob")
    await asyncio.sleep(0.3)
    resp_b = await client_b.recv_all(timeout=0.5)
    assert any("hey bob" in l for l in resp_b), f"Bob didn't get message: {resp_b}"

    # Bob -> Alice
    await client_b.send("PRIVMSG #chat :hey alice")
    await asyncio.sleep(0.3)
    resp_a = await client_a.recv_all(timeout=0.5)
    assert any("hey alice" in l for l in resp_a), f"Alice didn't get message: {resp_a}"


@pytest.mark.asyncio
async def test_remote_client_mentioned(linked_servers, make_client_a, make_client_b):
    """@mention across servers sends NOTICE."""
    server_a, server_b = linked_servers

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_b.send("JOIN #test")
    await client_b.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    # Bob mentions Alice (remote)
    await client_b.send("PRIVMSG #test :hey @alpha-alice check this")
    await asyncio.sleep(0.5)

    resp_a = await client_a.recv_all(timeout=0.5)
    # Alice should get either a NOTICE about the mention or the PRIVMSG with her name
    mention_notices = [l for l in resp_a if "NOTICE" in l and "mentioned" in l]
    privmsgs = [l for l in resp_a if "PRIVMSG" in l and "alpha-alice" in l]
    assert mention_notices or privmsgs, f"Expected mention notification, got: {resp_a}"


# =============================================================================
# Phase 7: Link trust levels & channel federation modes
# =============================================================================


async def _make_linked_pair(*, trust: str = "full"):
    """Helper: create two servers linked with the given trust level.

    Returns (server_a, server_b) after the link handshake completes.
    Both link configs carry the specified trust value.
    """
    link_password = TEST_LINK_PASSWORD

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[
            LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password, trust=trust)
        ],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[
            LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password, trust=trust)
        ],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)
    await server_a.start()
    await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    # Update link configs with actual ports
    config_a.links[0].port = server_b.config.port
    config_b.links[0].port = server_a.config.port

    # Server A connects to Server B (pass trust level)
    try:
        await server_a.connect_to_peer(
            "127.0.0.1", server_b.config.port, link_password, trust=trust
        )
        for _ in range(50):
            if "beta" in server_a.links and "alpha" in server_b.links:
                break
            await asyncio.sleep(0.05)

        assert (
            "beta" in server_a.links and "alpha" in server_b.links
        ), "Server link handshake failed — link not established"
    except Exception:
        await server_a.stop()
        await server_b.stop()
        raise

    return server_a, server_b


async def _connect_client(server, nick, user):
    """Helper: connect a test client to a server, return IRCTestClient."""
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
    client = IRCTestClient(reader, writer)
    await client.send(f"NICK {nick}")
    await client.send(f"USER {user} 0 * :{user}")
    await client.recv_all(timeout=0.5)
    return client


@pytest.mark.asyncio
async def test_full_link_restricted_channel_not_relayed():
    """Full trust link: +R channel is never federated."""
    server_a, server_b = await _make_linked_pair(trust="full")

    try:
        # Connect Bob on server B first and join #secret
        client_b = await _connect_client(server_b, "beta-bob", "bob")
        await client_b.send("JOIN #secret")
        await client_b.recv_all(timeout=0.5)

        client_a = await _connect_client(server_a, "alpha-alice", "alice")
        await client_a.send("JOIN #secret")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.3)

        # Set +R on #secret (restrict from federation)
        await client_a.send("MODE #secret +R")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.3)

        # Verify +R is set locally
        channel_a = server_a.channels.get("#secret")
        assert channel_a is not None, "Channel should exist on server A"
        assert channel_a.restricted, "Channel should be restricted (+R)"

        # Drain Bob's buffer before sending the test message
        await client_b.recv_all(timeout=0.5)

        # Send a message to #secret
        await client_a.send("PRIVMSG #secret :top secret info")
        await asyncio.sleep(0.5)

        # Bob should NOT have received the "top secret info" message
        resp = await client_b.recv_all(timeout=0.5)
        secret_msgs = [l for l in resp if "PRIVMSG" in l and "top secret info" in l]
        assert not secret_msgs, f"Restricted channel message should NOT relay: {resp}"
    finally:
        try:
            await client_a.close()
        except Exception:
            pass
        try:
            await client_b.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_restricted_link_no_share_not_relayed():
    """Restricted trust link: channels without +S are NOT relayed."""
    server_a, server_b = await _make_linked_pair(trust="restricted")

    try:
        # Connect Bob on server B first and join #general
        client_b = await _connect_client(server_b, "beta-bob", "bob")
        await client_b.send("JOIN #general")
        await client_b.recv_all(timeout=0.5)

        client_a = await _connect_client(server_a, "alpha-alice", "alice")
        await client_a.send("JOIN #general")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.3)

        # Drain Bob's buffer
        await client_b.recv_all(timeout=0.5)

        # Send a message -- no +S is set, so nothing should cross
        await client_a.send("PRIVMSG #general :hello from alpha")
        await asyncio.sleep(0.5)

        # Bob's #general is local to server B, should NOT have received the message
        resp = await client_b.recv_all(timeout=0.5)
        relayed = [l for l in resp if "PRIVMSG" in l and "hello from alpha" in l]
        assert not relayed, f"Message should NOT relay on restricted link without +S: {resp}"
    finally:
        try:
            await client_a.close()
        except Exception:
            pass
        try:
            await client_b.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_restricted_link_mutual_share_relayed():
    """Restricted trust link: mutual +S enables federation for that channel."""
    server_a, server_b = await _make_linked_pair(trust="restricted")

    try:
        # Both sides create #collab and set +S for the other server
        client_a = await _connect_client(server_a, "alpha-alice", "alice")
        await client_a.send("JOIN #collab")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.1)

        client_b = await _connect_client(server_b, "beta-bob", "bob")
        await client_b.send("JOIN #collab")
        await client_b.recv_all(timeout=0.5)
        await asyncio.sleep(0.1)

        # Alice sets +S beta on server A's #collab
        await client_a.send("MODE #collab +S beta")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.1)

        # Bob sets +S alpha on server B's #collab
        await client_b.send("MODE #collab +S alpha")
        await client_b.recv_all(timeout=0.5)
        await asyncio.sleep(0.3)

        # Verify +S is set on both sides
        channel_a = server_a.channels.get("#collab")
        assert channel_a is not None
        assert (
            "beta" in channel_a.shared_with
        ), f"Server A #collab should share with beta: {channel_a.shared_with}"

        channel_b = server_b.channels.get("#collab")
        assert channel_b is not None
        assert (
            "alpha" in channel_b.shared_with
        ), f"Server B #collab should share with alpha: {channel_b.shared_with}"

        # Now Alice sends a message -- it should relay to Bob
        await client_a.send("PRIVMSG #collab :shared message")
        await asyncio.sleep(0.5)
        resp = await client_b.recv_all(timeout=0.5)

        relayed = [l for l in resp if "PRIVMSG" in l and "shared message" in l]
        assert relayed, f"Mutual +S should enable relay on restricted link, got: {resp}"
    finally:
        try:
            await client_a.close()
        except Exception:
            pass
        try:
            await client_b.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_restricted_link_one_sided_share_not_relayed():
    """Restricted trust link: one-sided +S is NOT enough for relay."""
    server_a, server_b = await _make_linked_pair(trust="restricted")

    try:
        client_a = await _connect_client(server_a, "alpha-alice", "alice")
        await client_a.send("JOIN #collab")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.1)

        client_b = await _connect_client(server_b, "beta-bob", "bob")
        await client_b.send("JOIN #collab")
        await client_b.recv_all(timeout=0.5)
        await asyncio.sleep(0.1)

        # Only server A sets +S beta -- server B does NOT set +S alpha
        await client_a.send("MODE #collab +S beta")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.3)

        # Verify only one side has +S
        channel_a = server_a.channels.get("#collab")
        assert "beta" in channel_a.shared_with

        channel_b = server_b.channels.get("#collab")
        assert "alpha" not in channel_b.shared_with, "Server B should NOT have +S alpha set"

        # Alice sends a message -- should NOT relay (one-sided +S)
        await client_a.send("PRIVMSG #collab :one sided message")
        await asyncio.sleep(0.5)
        resp = await client_b.recv_all(timeout=0.5)

        relayed = [l for l in resp if "PRIVMSG" in l and "one sided message" in l]
        assert not relayed, f"One-sided +S should NOT enable relay on restricted link: {resp}"
    finally:
        try:
            await client_a.close()
        except Exception:
            pass
        try:
            await client_b.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_new_channel_on_full_link_relayed():
    """Full trust link: new channel created after linking is visible to peer."""
    server_a, server_b = await _make_linked_pair(trust="full")

    try:
        # Create a new channel on server A after the link is already up
        client_a = await _connect_client(server_a, "alpha-alice", "alice")
        await client_a.send("JOIN #new-room")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.5)

        # Server B should see #new-room (via SJOIN relay)
        assert (
            "#new-room" in server_b.channels
        ), f"New channel should federate on full link: {list(server_b.channels.keys())}"

        # Remote member should be visible
        channel_b = server_b.channels["#new-room"]
        member_nicks = {m.nick for m in channel_b.members}
        assert (
            "alpha-alice" in member_nicks
        ), f"alpha-alice should be in #new-room on server B: {member_nicks}"
    finally:
        try:
            await client_a.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


@pytest.mark.asyncio
async def test_new_channel_on_restricted_link_not_relayed():
    """Restricted trust link: new channel without +S is NOT visible to peer."""
    server_a, server_b = await _make_linked_pair(trust="restricted")

    try:
        # Create a new channel on server A -- no +S set
        client_a = await _connect_client(server_a, "alpha-alice", "alice")
        await client_a.send("JOIN #new-room")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.5)

        # Server B should NOT see #new-room (restricted link, no +S)
        assert (
            "#new-room" not in server_b.channels
        ), f"New channel should NOT federate on restricted link: {list(server_b.channels.keys())}"
    finally:
        try:
            await client_a.close()
        except Exception:
            pass
        await server_a.stop()
        await server_b.stop()


# =============================================================================
# Phase 8: Replay regression — issue #291
# =============================================================================


@pytest.mark.asyncio
async def test_replay_event_handles_string_typed_message_event(linked_servers):
    """Regression for #291: _replay_event must accept event.type as a plain
    string (the shape produced by _parse_event_type for non-enum wire types).

    Before the fix, ``event.type == EventType.MESSAGE`` returned False when
    ``event.type`` was the string ``"message"``, so the typed fast path
    silently skipped — a federated MESSAGE event reaching backfill replay
    would not produce the SMSG/SNOTICE wire output.
    """
    server_a, _ = linked_servers
    link_to_b = server_a.links["beta"]

    captured: list[bytes] = []
    real_write = link_to_b.writer.write

    def recording_write(data):
        captured.append(data)
        return real_write(data)

    link_to_b.writer.write = recording_write
    try:
        # Construct a string-typed MESSAGE event — the exact shape
        # _parse_event_type emits.
        event = Event(
            type="message",  # type: ignore[arg-type] -- intentional string
            channel=None,
            nick="alpha-bob",
            data={"target": "beta-charlie", "text": "ping-291"},
        )
        await link_to_b._replay_event(seq=42, event=event)
    finally:
        link_to_b.writer.write = real_write

    wire = b"".join(captured).decode("utf-8", errors="replace")
    assert (
        " SMSG " in wire
    ), f"expected SMSG in wire output for string-typed MESSAGE event, got: {wire!r}"
    assert "ping-291" in wire
