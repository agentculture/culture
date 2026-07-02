import asyncio

import pytest

from tests.conftest import IRCTestClient


@pytest.mark.asyncio
async def test_channel_privmsg_produces_span_hierarchy(server, tracing_exporter):
    async def _connect(nick):
        reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
        c = IRCTestClient(reader, writer)
        await c.send(f"NICK {nick}")
        await c.send(f"USER {nick} 0 * :{nick}")
        await c.recv_until("001")
        return c

    alice = await _connect("testserv-alice")
    bob = await _connect("testserv-bob")
    await alice.send("JOIN #c")
    await bob.send("JOIN #c")
    await alice.recv_until("JOIN")
    await bob.recv_until("JOIN")

    tracing_exporter.clear()
    await alice.send("PRIVMSG #c :hello world")
    await bob.recv_until("PRIVMSG #c :hello world")

    spans = tracing_exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "irc.command.PRIVMSG" in names
    assert "irc.privmsg.dispatch" in names
    assert "irc.privmsg.deliver.channel" in names

    # All three share one trace id
    trace_ids = {
        s.context.trace_id
        for s in spans
        if s.name in {"irc.command.PRIVMSG", "irc.privmsg.dispatch", "irc.privmsg.deliver.channel"}
    }
    assert len(trace_ids) == 1

    deliver = next(s for s in spans if s.name == "irc.privmsg.deliver.channel")
    assert deliver.attributes["irc.channel"] == "#c"
    assert deliver.attributes["irc.message.body"] == "hello world"
