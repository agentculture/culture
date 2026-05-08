import asyncio
import os
import tempfile

import pytest

from culture.clients.shared.ipc import decode_message, encode_message, make_request
from culture.clients.shared.socket_server import SocketServer


@pytest.mark.asyncio
async def test_socket_server_accepts_connection():
    sock_path = os.path.join(tempfile.mkdtemp(), "test.sock")

    async def handler(msg):
        return {"type": "response", "id": msg["id"], "ok": True}

    srv = SocketServer(sock_path, handler)
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        req = make_request("irc_channels")
        writer.write(encode_message(req))
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        resp = decode_message(data)
        assert resp["ok"] is True
        assert resp["id"] == req["id"]
        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()
        os.unlink(sock_path)


@pytest.mark.asyncio
async def test_socket_server_sends_whisper():
    sock_path = os.path.join(tempfile.mkdtemp(), "test.sock")

    async def handler(msg):
        return {"type": "response", "id": msg["id"], "ok": True}

    srv = SocketServer(sock_path, handler)
    await srv.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        await srv.send_whisper("You're spiraling", "CORRECTION")
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        whisper = decode_message(data)
        assert whisper["type"] == "whisper"
        assert whisper["whisper_type"] == "CORRECTION"
        assert "spiraling" in whisper["message"]
        writer.close()
        await writer.wait_closed()
    finally:
        await srv.stop()
        os.unlink(sock_path)


@pytest.mark.asyncio
async def test_socket_permissions():
    sock_path = os.path.join(tempfile.mkdtemp(), "test.sock")

    async def handler(msg):
        return {"type": "response", "id": msg["id"], "ok": True}

    srv = SocketServer(sock_path, handler)
    await srv.start()
    try:
        mode = os.stat(sock_path).st_mode & 0o777
        assert mode == 0o600
    finally:
        await srv.stop()
        os.unlink(sock_path)
