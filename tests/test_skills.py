# tests/test_skills.py
import asyncio

import pytest

from culture.agentirc.skill import Event, EventType, Skill


class RecorderSkill(Skill):
    """Records all events for test assertions."""

    name = "recorder"

    def __init__(self):
        self.events: list[Event] = []

    async def on_event(self, event: Event) -> None:
        self.events.append(event)


class EchoSkill(Skill):
    """Echoes back a NOTICE for any ECHO command."""

    name = "echo"
    commands = {"ECHO"}

    async def on_command(self, client, msg):
        from culture.protocol.message import Message

        text = msg.params[0] if msg.params else ""
        await client.send(
            Message(prefix=self.server.config.name, command="NOTICE", params=[client.nick, text])
        )


@pytest.mark.asyncio
async def test_skill_receives_message_event(server, make_client):
    skill = RecorderSkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("PRIVMSG #test :hello world")
    await bob.recv()  # get the message
    await asyncio.sleep(0.05)

    # Filter to the specific message alice sent (system bots may also emit
    # MESSAGE events for welcome messages when users join).
    msg_events = [
        e
        for e in skill.events
        if e.type == EventType.MESSAGE and e.nick == "testserv-alice" and e.channel == "#test"
    ]
    assert len(msg_events) == 1
    assert msg_events[0].data["text"] == "hello world"


@pytest.mark.asyncio
async def test_skill_receives_join_event(server, make_client):
    skill = RecorderSkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #test")
    await alice.recv_all()

    join_events = [e for e in skill.events if e.type == EventType.JOIN]
    assert len(join_events) == 1
    assert join_events[0].channel == "#test"
    assert join_events[0].nick == "testserv-alice"


@pytest.mark.asyncio
async def test_skill_receives_part_event(server, make_client):
    skill = RecorderSkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #test")
    await alice.recv_all()

    await alice.send("PART #test :goodbye")
    await alice.recv_all()

    part_events = [e for e in skill.events if e.type == EventType.PART]
    assert len(part_events) == 1
    assert part_events[0].channel == "#test"
    assert part_events[0].nick == "testserv-alice"
    assert part_events[0].data["reason"] == "goodbye"


@pytest.mark.asyncio
async def test_skill_receives_quit_event(server, make_client):
    skill = RecorderSkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")
    await alice.send("JOIN #test")
    await alice.recv_all()
    await bob.send("JOIN #test")
    await bob.recv_all()
    await alice.recv_all()

    await alice.send("QUIT :leaving now")
    await asyncio.sleep(0.1)

    quit_events = [e for e in skill.events if e.type == EventType.QUIT]
    assert len(quit_events) == 1
    assert quit_events[0].nick == "testserv-alice"
    assert quit_events[0].data["reason"] == "leaving now"
    assert "#test" in quit_events[0].data["channels"]


@pytest.mark.asyncio
async def test_skill_receives_topic_event(server, make_client):
    skill = RecorderSkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #test")
    await alice.recv_all()

    await alice.send("TOPIC #test :new topic")
    await alice.recv_all()

    topic_events = [e for e in skill.events if e.type == EventType.TOPIC]
    assert len(topic_events) == 1
    assert topic_events[0].channel == "#test"
    assert topic_events[0].nick == "testserv-alice"
    assert topic_events[0].data["topic"] == "new topic"


@pytest.mark.asyncio
async def test_multiple_skills_receive_events(server, make_client):
    skill1 = RecorderSkill()
    skill2 = RecorderSkill()
    await server.register_skill(skill1)
    await server.register_skill(skill2)

    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #test")
    await alice.recv_all()

    join_events_1 = [e for e in skill1.events if e.type == EventType.JOIN]
    join_events_2 = [e for e in skill2.events if e.type == EventType.JOIN]
    assert len(join_events_1) == 1
    assert len(join_events_2) == 1


@pytest.mark.asyncio
async def test_dm_message_event(server, make_client):
    skill = RecorderSkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("PRIVMSG testserv-bob :hello dm")
    await bob.recv()
    await asyncio.sleep(0.05)

    msg_events = [e for e in skill.events if e.type == EventType.MESSAGE]
    assert len(msg_events) == 1
    assert msg_events[0].channel is None
    assert msg_events[0].nick == "testserv-alice"
    assert msg_events[0].data["text"] == "hello dm"


# --- Command dispatch tests ---


@pytest.mark.asyncio
async def test_skill_handles_unknown_command(server, make_client):
    skill = EchoSkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("ECHO :hello skill")
    resp = await alice.recv()
    assert "NOTICE" in resp
    assert "hello skill" in resp


@pytest.mark.asyncio
async def test_unhandled_command_still_errors(server, make_client):
    skill = EchoSkill()
    await server.register_skill(skill)

    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("FAKECMD test")
    resp = await alice.recv()
    assert "421" in resp  # ERR_UNKNOWNCOMMAND


@pytest.mark.asyncio
async def test_skill_command_requires_registration(server, make_client):
    skill = EchoSkill()
    await server.register_skill(skill)

    # Create unregistered client (no nick/user)
    client = await make_client()
    await client.send("ECHO :should not work")
    # Unregistered client should not get a response from the skill
    lines = await client.recv_all(timeout=0.5)
    assert not any("hello" in line.lower() for line in lines)
