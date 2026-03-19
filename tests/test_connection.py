# tests/test_connection.py
import asyncio
import pytest


@pytest.mark.asyncio
async def test_server_accepts_connection(server):
    """Server accepts a TCP connection."""
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_server_responds_to_ping(server, make_client):
    """Server responds to PING with PONG."""
    client = await make_client()
    await client.send("PING :hello")
    response = await client.recv()
    assert "PONG" in response
    assert "hello" in response


@pytest.mark.asyncio
async def test_registration_welcome(server, make_client):
    """Client receives 001-004 after NICK + USER."""
    client = await make_client()
    await client.send("NICK testserv-ori")
    await client.send("USER ori 0 * :Ori Nachum")
    lines = await client.recv_all(timeout=1.0)
    codes = [line.split()[1] for line in lines]
    assert "001" in codes
    assert "002" in codes
    assert "003" in codes
    assert "004" in codes


@pytest.mark.asyncio
async def test_nick_must_have_server_prefix(server, make_client):
    """Nick without server name prefix is rejected."""
    client = await make_client()
    await client.send("NICK claude")
    response = await client.recv()
    assert "432" in response  # ERR_ERRONEUSNICKNAME


@pytest.mark.asyncio
async def test_nick_with_correct_prefix(server, make_client):
    """Nick with correct server prefix is accepted."""
    client = await make_client()
    await client.send("NICK testserv-claude")
    await client.send("USER claude 0 * :Claude")
    lines = await client.recv_all(timeout=1.0)
    codes = [line.split()[1] for line in lines]
    assert "001" in codes


@pytest.mark.asyncio
async def test_duplicate_nick_rejected(server, make_client):
    """Second client with same nick is rejected."""
    await make_client(nick="testserv-claude", user="claude")
    client2 = await make_client()
    await client2.send("NICK testserv-claude")
    response = await client2.recv()
    assert "433" in response  # ERR_NICKNAMEINUSE


@pytest.mark.asyncio
async def test_nick_no_param(server, make_client):
    """NICK without parameter returns ERR_NONICKNAMEGIVEN."""
    client = await make_client()
    await client.send("NICK")
    response = await client.recv()
    assert "431" in response


@pytest.mark.asyncio
async def test_user_without_nick(server, make_client):
    """USER without prior NICK does not trigger welcome."""
    client = await make_client()
    await client.send("USER ori 0 * :Ori")
    lines = await client.recv_all(timeout=0.5)
    codes = [line.split()[1] for line in lines if len(line.split()) > 1]
    assert "001" not in codes


@pytest.mark.asyncio
async def test_double_registration_rejected(server, make_client):
    """USER sent twice returns ERR_ALREADYREGISTRED."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("USER ori 0 * :Ori again")
    response = await client.recv()
    assert "462" in response
