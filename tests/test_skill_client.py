import asyncio
import json
import os
import tempfile

import pytest

from culture.clients.claude.skill.irc_client import SkillClient
from culture.clients.shared.ipc import encode_message, make_response, make_whisper


@pytest.mark.asyncio
async def test_skill_client_send():
    sock_dir = tempfile.mkdtemp()
    sock_path = os.path.join(sock_dir, "test-agent.sock")

    async def mock_handler(reader, writer):
        data = await reader.readline()
        msg = json.loads(data)
        resp = make_response(msg["id"], ok=True)
        writer.write(encode_message(resp))
        await writer.drain()
        writer.close()

    srv = await asyncio.start_unix_server(mock_handler, path=sock_path)
    try:
        client = SkillClient(sock_path)
        await client.connect()
        result = await client.irc_send("#general", "hello")
        assert result["ok"] is True
        await client.close()
    finally:
        srv.close()
        await srv.wait_closed()
        os.unlink(sock_path)


@pytest.mark.asyncio
async def test_skill_client_read():
    sock_dir = tempfile.mkdtemp()
    sock_path = os.path.join(sock_dir, "test-agent.sock")

    async def mock_handler(reader, writer):
        data = await reader.readline()
        msg = json.loads(data)
        resp = make_response(
            msg["id"],
            ok=True,
            data={"messages": [{"nick": "ori", "text": "hello", "timestamp": 123.0}]},
        )
        writer.write(encode_message(resp))
        await writer.drain()
        writer.close()

    srv = await asyncio.start_unix_server(mock_handler, path=sock_path)
    try:
        client = SkillClient(sock_path)
        await client.connect()
        result = await client.irc_read("#general", limit=50)
        assert result["ok"] is True
        assert len(result["data"]["messages"]) == 1
        await client.close()
    finally:
        srv.close()
        await srv.wait_closed()
        os.unlink(sock_path)


@pytest.mark.asyncio
async def test_skill_client_queues_whispers():
    sock_dir = tempfile.mkdtemp()
    sock_path = os.path.join(sock_dir, "test-agent.sock")

    async def mock_handler(reader, writer):
        whisper = make_whisper("Stop retrying", "CORRECTION")
        writer.write(encode_message(whisper))
        await writer.drain()
        data = await reader.readline()
        msg = json.loads(data)
        resp = make_response(msg["id"], ok=True, data={"channels": ["#general"]})
        writer.write(encode_message(resp))
        await writer.drain()
        writer.close()

    srv = await asyncio.start_unix_server(mock_handler, path=sock_path)
    try:
        client = SkillClient(sock_path)
        await client.connect()
        await asyncio.sleep(0.1)
        assert len(client.pending_whispers) == 1
        assert client.pending_whispers[0]["whisper_type"] == "CORRECTION"
        await client.close()
    finally:
        srv.close()
        await srv.wait_closed()
        os.unlink(sock_path)
