# tests/test_history.py
import asyncio
import tempfile

import pytest

from culture.server.skills.history import HistorySkill

# --- Task 4: Recording tests ---


@pytest.mark.asyncio
async def test_history_records_channel_messages(server, make_client):
    skill = HistorySkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :first message")
    await bob.recv()
    await alice.send("PRIVMSG #test :second message")
    await bob.recv()
    await asyncio.sleep(0.05)

    entries = skill.get_recent("#test", 10)
    assert len(entries) == 2
    assert entries[0].nick == "testserv-alice"
    assert entries[0].text == "first message"
    assert entries[1].text == "second message"


@pytest.mark.asyncio
async def test_history_does_not_record_dms(server, make_client):
    skill = HistorySkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("PRIVMSG testserv-bob :secret dm")
    await bob.recv()
    await asyncio.sleep(0.05)

    # No channel history should exist
    assert skill.get_recent("#test", 10) == []
    assert skill._channels == {}


@pytest.mark.asyncio
async def test_history_per_channel_isolation(server, make_client):
    skill = HistorySkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #chan1")
    await alice.recv_all()
    await alice.send("JOIN #chan2")
    await alice.recv_all()
    await bob.send("JOIN #chan1")
    await bob.recv_all()
    await bob.send("JOIN #chan2")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #chan1 :msg for chan1")
    await bob.recv()
    await alice.send("PRIVMSG #chan2 :msg for chan2")
    await bob.recv()
    await asyncio.sleep(0.05)

    chan1 = skill.get_recent("#chan1", 10)
    chan2 = skill.get_recent("#chan2", 10)
    assert len(chan1) == 1
    assert chan1[0].text == "msg for chan1"
    assert len(chan2) == 1
    assert chan2[0].text == "msg for chan2"


@pytest.mark.asyncio
async def test_history_respects_max_entries(server, make_client):
    skill = HistorySkill(maxlen=5)
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    for i in range(8):
        await alice.send(f"PRIVMSG #test :message {i}")
        await bob.recv()

    await asyncio.sleep(0.05)
    entries = skill.get_recent("#test", 100)
    assert len(entries) == 5
    # Should have the latest 5 (messages 3-7)
    assert entries[0].text == "message 3"
    assert entries[4].text == "message 7"


@pytest.mark.asyncio
async def test_history_entries_have_timestamps(server, make_client):
    skill = HistorySkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :timestamped")
    await bob.recv()
    await asyncio.sleep(0.05)

    entries = skill.get_recent("#test", 1)
    assert len(entries) == 1
    assert isinstance(entries[0].timestamp, float)
    assert entries[0].timestamp > 0


@pytest.mark.asyncio
async def test_history_get_recent_empty_channel(server, make_client):
    skill = HistorySkill()
    await server.register_skill(skill)

    entries = skill.get_recent("#nonexistent", 10)
    assert entries == []


# --- Task 5: HISTORY RECENT command tests ---


@pytest.mark.asyncio
async def test_history_recent_command(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    for i in range(5):
        await alice.send(f"PRIVMSG #test :msg {i}")
        await bob.recv()

    await asyncio.sleep(0.05)

    await bob.send("HISTORY RECENT #test 3")
    lines = await bob.recv_all(timeout=1.0)

    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    end_lines = [l for l in lines if "HISTORYEND" in l]

    assert len(history_lines) == 3
    assert len(end_lines) == 1
    assert "msg 2" in history_lines[0]
    assert "msg 3" in history_lines[1]
    assert "msg 4" in history_lines[2]


@pytest.mark.asyncio
async def test_history_recent_includes_nick_and_timestamp(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :hello world")
    await bob.recv()
    await asyncio.sleep(0.05)

    await bob.send("HISTORY RECENT #test 1")
    lines = await bob.recv_all(timeout=1.0)

    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    assert len(history_lines) == 1
    # Format: :server HISTORY #channel nick timestamp :text
    line = history_lines[0]
    assert "testserv-alice" in line
    assert "#test" in line
    assert "hello world" in line


@pytest.mark.asyncio
async def test_history_recent_empty_channel(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")

    await alice.send("HISTORY RECENT #empty 10")
    lines = await alice.recv_all(timeout=1.0)

    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    end_lines = [l for l in lines if "HISTORYEND" in l]
    assert len(history_lines) == 0
    assert len(end_lines) == 1


@pytest.mark.asyncio
async def test_history_recent_count_exceeds_stored(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :only one")
    await bob.recv()
    await asyncio.sleep(0.05)

    await bob.send("HISTORY RECENT #test 100")
    lines = await bob.recv_all(timeout=1.0)

    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    assert len(history_lines) == 1
    assert "only one" in history_lines[0]


@pytest.mark.asyncio
async def test_history_missing_params(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("HISTORY")
    resp = await alice.recv()
    assert "461" in resp  # ERR_NEEDMOREPARAMS


@pytest.mark.asyncio
async def test_history_recent_missing_count(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("HISTORY RECENT #test")
    resp = await alice.recv()
    assert "461" in resp  # ERR_NEEDMOREPARAMS


@pytest.mark.asyncio
async def test_history_unknown_subcommand(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("HISTORY BADCMD #test")
    resp = await alice.recv()
    assert "NOTICE" in resp
    assert "Unknown HISTORY subcommand" in resp


# --- Task 6: HISTORY SEARCH command tests ---


@pytest.mark.asyncio
async def test_history_search_finds_matching_messages(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :hello world")
    await bob.recv()
    await alice.send("PRIVMSG #test :goodbye world")
    await bob.recv()
    await alice.send("PRIVMSG #test :hello again")
    await bob.recv()
    await asyncio.sleep(0.05)

    await bob.send("HISTORY SEARCH #test :hello")
    lines = await bob.recv_all(timeout=1.0)

    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    end_lines = [l for l in lines if "HISTORYEND" in l]
    assert len(history_lines) == 2
    assert "hello world" in history_lines[0]
    assert "hello again" in history_lines[1]
    assert len(end_lines) == 1


@pytest.mark.asyncio
async def test_history_search_case_insensitive(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :Hello World")
    await bob.recv()
    await asyncio.sleep(0.05)

    await bob.send("HISTORY SEARCH #test :hello")
    lines = await bob.recv_all(timeout=1.0)

    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    assert len(history_lines) == 1
    assert "Hello World" in history_lines[0]


@pytest.mark.asyncio
async def test_history_search_no_results(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :some message")
    await bob.recv()
    await asyncio.sleep(0.05)

    await bob.send("HISTORY SEARCH #test :nonexistent")
    lines = await bob.recv_all(timeout=1.0)

    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    end_lines = [l for l in lines if "HISTORYEND" in l]
    assert len(history_lines) == 0
    assert len(end_lines) == 1


@pytest.mark.asyncio
async def test_history_search_missing_term(server, make_client):
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("HISTORY SEARCH #test")
    resp = await alice.recv()
    assert "461" in resp  # ERR_NEEDMOREPARAMS


# --- Task 7: Auto-registration test ---


@pytest.mark.asyncio
async def test_history_auto_registered(server, make_client):
    """Server should have history skill auto-registered."""
    # Check that a HistorySkill exists in server.skills
    history_skills = [s for s in server.skills if isinstance(s, HistorySkill)]
    assert len(history_skills) == 1

    # Verify HISTORY RECENT works without manual registration
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :auto registered test")
    await bob.recv()
    await asyncio.sleep(0.05)

    await bob.send("HISTORY RECENT #test 5")
    lines = await bob.recv_all(timeout=1.0)

    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    assert len(history_lines) == 1
    assert "auto registered test" in history_lines[0]


# --- History store unit tests ---


def test_history_store_append_and_get_recent():
    from culture.server.history_store import HistoryStore

    with tempfile.TemporaryDirectory() as data_dir:
        store = HistoryStore(data_dir)
        store.append("#test", "alice", "first", 1000.0)
        store.append("#test", "bob", "second", 1001.0)
        store.append("#test", "alice", "third", 1002.0)

        recent = store.get_recent("#test", 2)
        assert len(recent) == 2
        assert recent[0]["text"] == "second"
        assert recent[1]["text"] == "third"

        all_entries = store.get_recent("#test", 100)
        assert len(all_entries) == 3
        assert all_entries[0]["text"] == "first"

        store.close()


def test_history_store_search():
    from culture.server.history_store import HistoryStore

    with tempfile.TemporaryDirectory() as data_dir:
        store = HistoryStore(data_dir)
        store.append("#test", "alice", "hello world", 1000.0)
        store.append("#test", "bob", "goodbye world", 1001.0)
        store.append("#test", "alice", "hello again", 1002.0)

        results = store.search("#test", "hello")
        assert len(results) == 2
        assert results[0]["text"] == "hello world"
        assert results[1]["text"] == "hello again"

        no_results = store.search("#test", "nonexistent")
        assert len(no_results) == 0

        store.close()


def test_history_store_prune():
    import time

    from culture.server.history_store import HistoryStore

    with tempfile.TemporaryDirectory() as data_dir:
        store = HistoryStore(data_dir)
        old_ts = time.time() - 86400 * 60  # 60 days ago
        new_ts = time.time() - 86400 * 5  # 5 days ago
        store.append("#test", "alice", "old message", old_ts)
        store.append("#test", "bob", "new message", new_ts)

        deleted = store.prune(30)
        assert deleted == 1

        remaining = store.get_recent("#test", 100)
        assert len(remaining) == 1
        assert remaining[0]["text"] == "new message"

        store.close()


def test_history_store_load_channels():
    from culture.server.history_store import HistoryStore

    with tempfile.TemporaryDirectory() as data_dir:
        store = HistoryStore(data_dir)
        store.append("#general", "alice", "gen msg", 1000.0)
        store.append("#dev", "bob", "dev msg", 1001.0)
        store.append("#general", "carol", "gen msg 2", 1002.0)

        channels = store.load_channels(100)
        assert "#general" in channels
        assert "#dev" in channels
        assert len(channels["#general"]) == 2
        assert len(channels["#dev"]) == 1

        store.close()


# --- History persistence integration tests ---


@pytest.mark.asyncio
async def test_history_persists_across_restart():
    """History should survive server restart when data_dir is configured."""
    from culture.server.config import ServerConfig
    from culture.server.ircd import IRCd
    from tests.conftest import IRCTestClient

    with tempfile.TemporaryDirectory() as data_dir:
        config = ServerConfig(name="testserv", host="127.0.0.1", port=0, data_dir=data_dir)

        # Start server, send some messages
        ircd = IRCd(config)
        await ircd.start()
        port = ircd._server.sockets[0].getsockname()[1]
        ircd.config.port = port

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        alice = IRCTestClient(reader, writer)
        await alice.send("NICK testserv-alice")
        await alice.send("USER alice 0 * :alice")
        await alice.recv_all(timeout=0.5)
        await alice.send("JOIN #test")
        await alice.recv_all(timeout=0.5)

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        bob = IRCTestClient(reader2, writer2)
        await bob.send("NICK testserv-bob")
        await bob.send("USER bob 0 * :bob")
        await bob.recv_all(timeout=0.5)
        await bob.send("JOIN #test")
        await bob.recv_all(timeout=0.5)
        await alice.recv_all(timeout=0.3)

        await alice.send("PRIVMSG #test :persisted message one")
        await bob.recv()
        await alice.send("PRIVMSG #test :persisted message two")
        await bob.recv()
        await asyncio.sleep(0.05)

        await alice.close()
        await bob.close()
        await ircd.stop()

        # Restart server with same data_dir
        ircd2 = IRCd(config)
        await ircd2.start()
        port2 = ircd2._server.sockets[0].getsockname()[1]
        ircd2.config.port = port2

        reader3, writer3 = await asyncio.open_connection("127.0.0.1", port2)
        carol = IRCTestClient(reader3, writer3)
        await carol.send("NICK testserv-carol")
        await carol.send("USER carol 0 * :carol")
        await carol.recv_all(timeout=0.5)

        # Query history — should still have the messages
        await carol.send("HISTORY RECENT #test 10")
        lines = await carol.recv_all(timeout=1.0)
        history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
        assert len(history_lines) == 2
        assert "persisted message one" in history_lines[0]
        assert "persisted message two" in history_lines[1]

        await carol.close()
        await ircd2.stop()


@pytest.mark.asyncio
async def test_history_no_persistence_without_data_dir(server, make_client):
    """History should work in-memory when data_dir is not configured."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :ephemeral message")
    await bob.recv()
    await asyncio.sleep(0.05)

    # Should still work in-memory
    await bob.send("HISTORY RECENT #test 5")
    lines = await bob.recv_all(timeout=1.0)
    history_lines = [l for l in lines if "HISTORY" in l and "HISTORYEND" not in l]
    assert len(history_lines) == 1
    assert "ephemeral message" in history_lines[0]
