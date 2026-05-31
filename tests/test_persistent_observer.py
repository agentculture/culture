"""Tests for ``culture.observer.PersistentObserver`` (v8.19.17).

The behaviour we care about:
  * Reading the same channel N times triggers exactly ONE JOIN broadcast
    to other channel members — the whole point of the persistent
    connection.
  * Reads on a NEW channel still JOIN before issuing HISTORY (membership
    gate must clear).
  * ``send_message`` reuses the same connection.
  * ``close()`` shuts the connection cleanly.
  * Auto-reconnect: after the underlying writer is closed, the next
    ``read_channel`` opens a fresh connection AND re-JOINs the
    membership set.
"""

import asyncio

import pytest

from culture.observer import PersistentObserver


@pytest.mark.asyncio
async def test_repeated_reads_join_once(server, make_client):
    """100 reads of the same channel → exactly 1 JOIN seen by another member."""
    watcher = await make_client(nick="testserv-alice", user="alice")
    await watcher.send("JOIN #shared")
    await watcher.recv_all(timeout=0.5)

    observer = PersistentObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name="testserv",
    )
    try:
        for _ in range(10):
            await observer.read_channel("#shared", limit=10)
        # Drain anything the watcher accumulated.
        lines = await watcher.recv_all(timeout=0.5)
    finally:
        await observer.close()

    # Per v8.19.13 server-side suppression, _peek* JOINs DO NOT emit
    # user.join PRIVMSG events to channel members — they only broadcast
    # the raw IRC JOIN line. So we expect ONE raw JOIN line from our
    # _peekDASH nick, regardless of how many times read_channel was
    # called.
    join_lines = [ln for ln in lines if " JOIN " in ln and "_peekDASH" in ln]
    assert len(join_lines) == 1, (
        f"persistent observer should JOIN once for 10 reads, "
        f"saw {len(join_lines)} JOIN lines: {join_lines}"
    )


@pytest.mark.asyncio
async def test_reads_new_channel_joins_lazily(server, make_client):
    """Reading channel B does NOT re-JOIN channel A and vice versa."""
    watcher_a = await make_client(nick="testserv-alice", user="alice")
    await watcher_a.send("JOIN #room-a")
    await watcher_a.recv_all(timeout=0.5)
    watcher_b = await make_client(nick="testserv-bob", user="bob")
    await watcher_b.send("JOIN #room-b")
    await watcher_b.recv_all(timeout=0.5)

    observer = PersistentObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name="testserv",
    )
    try:
        # Read A, then B, then A again — A should not re-JOIN.
        await observer.read_channel("#room-a", limit=5)
        await observer.read_channel("#room-b", limit=5)
        await observer.read_channel("#room-a", limit=5)
        assert observer.joined_channels == frozenset({"#room-a", "#room-b"})
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_send_message_uses_persistent_connection(server, make_client):
    """send_message reuses the same nick across calls."""
    recipient = await make_client(nick="testserv-rcv", user="rcv")
    await recipient.send("JOIN #drop")
    await recipient.recv_all(timeout=0.5)

    observer = PersistentObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name="testserv",
    )
    try:
        await observer.send_message("#drop", "hello once")
        first_nick = observer.nick
        await observer.send_message("#drop", "hello twice")
        # Same nick across both sends — no fresh connection in between.
        assert observer.nick == first_nick
        await asyncio.sleep(0.2)
        lines = await recipient.recv_all(timeout=0.5)
        privmsgs = [ln for ln in lines if "PRIVMSG #drop" in ln and "hello" in ln]
        assert len(privmsgs) >= 2, f"expected 2 PRIVMSGs, got {privmsgs}"
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_close_disconnects(server):
    """After close(), the observer's writer is gone and a new read reconnects."""
    observer = PersistentObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name="testserv",
    )
    try:
        await observer.read_channel("#hello", limit=5)
        nick_before = observer.nick
        await observer.close()
        # Read again — should reconnect with a fresh nick.
        await observer.read_channel("#hello", limit=5)
        assert observer.nick is not None
        # Different connection → different random nick.
        assert observer.nick != nick_before
    finally:
        await observer.close()


@pytest.mark.asyncio
async def test_reconnects_and_rejoins_after_drop(server):
    """Forcing a writer close should auto-reconnect AND re-JOIN the membership."""
    observer = PersistentObserver(
        host="127.0.0.1",
        port=server.config.port,
        server_name="testserv",
    )
    try:
        await observer.read_channel("#auto", limit=5)
        assert "#auto" in observer.joined_channels
        # Forcibly drop the connection so the next read must reconnect.
        if observer._writer is not None:
            observer._writer.close()
            try:
                await observer._writer.wait_closed()
            except OSError:
                pass
        # Next read reconnects + re-JOINs from the membership set.
        await observer.read_channel("#auto", limit=5)
        assert "#auto" in observer.joined_channels
    finally:
        await observer.close()
