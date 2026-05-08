from culture.clients.shared.message_buffer import MessageBuffer


def test_add_parses_thread_prefix():
    buf = MessageBuffer()
    buf.add("#general", "alice", "[thread:auth-refactor] I'll take tokens")
    messages = buf.read("#general")
    assert len(messages) == 1
    assert messages[0].thread == "auth-refactor"
    assert messages[0].text == "[thread:auth-refactor] I'll take tokens"


def test_add_no_thread_prefix():
    buf = MessageBuffer()
    buf.add("#general", "alice", "Hello world")
    messages = buf.read("#general")
    assert len(messages) == 1
    assert messages[0].thread is None


def test_read_thread_returns_only_matching():
    buf = MessageBuffer()
    buf.add("#general", "alice", "[thread:auth] Message one")
    buf.add("#general", "bob", "Unrelated channel message")
    buf.add("#general", "charlie", "[thread:auth] Message two")
    buf.add("#general", "dave", "[thread:deploy] Different thread")

    auth_msgs = buf.read_thread("#general", "auth")
    assert len(auth_msgs) == 2
    assert auth_msgs[0].nick == "alice"
    assert auth_msgs[1].nick == "charlie"


def test_read_thread_respects_limit():
    buf = MessageBuffer()
    for i in range(10):
        buf.add("#general", "alice", f"[thread:big] Message {i}")

    msgs = buf.read_thread("#general", "big", limit=3)
    assert len(msgs) == 3
    assert "Message 7" in msgs[0].text


def test_read_thread_nonexistent_returns_empty():
    buf = MessageBuffer()
    buf.add("#general", "alice", "no threads here")
    assert buf.read_thread("#general", "nope") == []


def test_read_still_returns_all_messages():
    buf = MessageBuffer()
    buf.add("#general", "alice", "[thread:auth] Thread msg")
    buf.add("#general", "bob", "Regular msg")
    messages = buf.read("#general")
    assert len(messages) == 2
