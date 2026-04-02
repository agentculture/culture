"""Tests for ThreadsSkill — CREATE, REPLY, THREADS list, THREADCLOSE, PROMOTE."""
import asyncio
import pytest


@pytest.mark.asyncio
async def test_thread_create_delivers_prefixed_privmsg(server, make_client):
    """THREAD CREATE should deliver a [thread:name] prefixed PRIVMSG to channel members."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)  # drain bob's join

    await alice.send("THREAD CREATE #general auth-refactor :Let's refactor auth")
    response = await bob.recv(timeout=2.0)
    assert "PRIVMSG" in response
    assert "#general" in response
    assert "[thread:auth-refactor]" in response
    assert "Let's refactor auth" in response


@pytest.mark.asyncio
async def test_thread_create_duplicate_name_errors(server, make_client):
    """Creating a thread with a name that already exists should return an error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general my-thread :first message")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general my-thread :duplicate")
    response = await alice.recv(timeout=2.0)
    assert "400" in response or "already exists" in response.lower()


@pytest.mark.asyncio
async def test_thread_create_not_on_channel_errors(server, make_client):
    """THREAD CREATE on a channel you haven't joined should error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #nochannel my-thread :hello")
    response = await alice.recv(timeout=2.0)
    assert "442" in response


@pytest.mark.asyncio
async def test_thread_create_invalid_name_errors(server, make_client):
    """THREAD CREATE with an invalid thread name should error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general --bad-name :hello")
    response = await alice.recv(timeout=2.0)
    assert "400" in response or "invalid" in response.lower()


@pytest.mark.asyncio
async def test_thread_reply_delivers_prefixed_privmsg(server, make_client):
    """THREAD REPLY should deliver a [thread:name] prefixed PRIVMSG."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general my-thread :first message")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    await bob.send("THREAD REPLY #general my-thread :second message")
    response = await alice.recv(timeout=2.0)
    assert "PRIVMSG" in response
    assert "#general" in response
    assert "[thread:my-thread]" in response
    assert "second message" in response


@pytest.mark.asyncio
async def test_thread_reply_nonexistent_thread_errors(server, make_client):
    """THREAD REPLY to a thread that doesn't exist should error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD REPLY #general no-thread :hello")
    response = await alice.recv(timeout=2.0)
    assert "404" in response or "no such thread" in response.lower()


@pytest.mark.asyncio
async def test_threads_list(server, make_client):
    """THREADS should list active threads in a channel."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general thread-a :first")
    await alice.recv_all(timeout=0.5)
    await alice.send("THREAD CREATE #general thread-b :second")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREADS #general")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "thread-a" in joined
    assert "thread-b" in joined
    assert "THREADSEND" in joined


@pytest.mark.asyncio
async def test_threadclose_archives_thread(server, make_client):
    """THREADCLOSE should archive a thread and post summary notice."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general my-thread :hello")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    await alice.send("THREADCLOSE #general my-thread :Done discussing")
    lines = await alice.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "NOTICE" in joined
    assert "my-thread" in joined

    # Reply to closed thread should fail
    await alice.send("THREAD REPLY #general my-thread :too late")
    response = await alice.recv(timeout=2.0)
    assert "405" in response or "closed" in response.lower()


@pytest.mark.asyncio
async def test_threadclose_unauthorized_errors(server, make_client):
    """THREADCLOSE by a non-participant non-operator should error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general my-thread :hello")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    # Bob didn't participate in the thread and isn't channel operator
    await bob.send("THREADCLOSE #general my-thread :closing")
    response = await bob.recv(timeout=2.0)
    assert "482" in response or "not authorized" in response.lower()


@pytest.mark.asyncio
async def test_threadclose_promote_creates_breakout(server, make_client):
    """THREADCLOSE PROMOTE should create a breakout channel and auto-join participants."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general auth-refactor :Let's refactor auth")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    await bob.send("THREAD REPLY #general auth-refactor :Good idea")
    await alice.recv_all(timeout=0.5)
    await bob.recv_all(timeout=0.5)

    await alice.send("THREADCLOSE PROMOTE #general auth-refactor")
    await asyncio.sleep(0.3)
    alice_lines = await alice.recv_all(timeout=1.0)
    bob_lines = await bob.recv_all(timeout=1.0)

    alice_joined = " ".join(alice_lines)
    bob_joined = " ".join(bob_lines)

    # Both should get JOIN for breakout channel
    assert "JOIN" in alice_joined
    assert "#general-auth-refactor" in alice_joined
    assert "JOIN" in bob_joined
    assert "#general-auth-refactor" in bob_joined

    # Breakout channel should exist on server
    assert "#general-auth-refactor" in server.channels

    # History replay as NOTICE
    assert "NOTICE" in alice_joined or "NOTICE" in bob_joined


@pytest.mark.asyncio
async def test_thread_create_missing_params_errors(server, make_client):
    """THREAD CREATE with missing parameters should error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general")
    response = await alice.recv(timeout=2.0)
    assert "461" in response or "not enough" in response.lower()


@pytest.mark.asyncio
async def test_thread_unknown_subcommand_errors(server, make_client):
    """THREAD with an unknown subcommand should error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("THREAD BADCMD #general foo :bar")
    response = await alice.recv(timeout=2.0)
    assert "NOTICE" in response or "unknown" in response.lower()


@pytest.mark.asyncio
async def test_thread_reply_to_archived_thread_errors(server, make_client):
    """Replying to a closed thread should return 405."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general done-thread :starting")
    await alice.recv_all(timeout=0.5)
    await alice.send("THREADCLOSE #general done-thread :all done")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD REPLY #general done-thread :too late")
    response = await alice.recv(timeout=2.0)
    assert "405" in response


@pytest.mark.asyncio
async def test_threadclose_archived_thread_not_listed(server, make_client):
    """Closed threads should not appear in THREADS listing."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general temp-thread :temporary")
    await alice.recv_all(timeout=0.5)
    await alice.send("THREADCLOSE #general temp-thread :done")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREADS #general")
    lines = await alice.recv_all(timeout=1.0)
    thread_lines = [l for l in lines if "THREADS" in l and "THREADSEND" not in l]
    assert len(thread_lines) == 0


@pytest.mark.asyncio
async def test_threadclose_promote_replays_history(server, make_client):
    """Promoted breakout should receive thread history as NOTICEs."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general replay-test :Message one")
    await alice.recv_all(timeout=0.5)
    await alice.send("THREAD REPLY #general replay-test :Message two")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREADCLOSE PROMOTE #general replay-test")
    lines = await alice.recv_all(timeout=2.0)

    # Should see history replay as NOTICEs in the breakout
    notices = [l for l in lines if "NOTICE" in l and "#general-replay-test" in l]
    assert len(notices) >= 2
    assert any("Message one" in n for n in notices)
    assert any("Message two" in n for n in notices)
