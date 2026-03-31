from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Awaitable

from agentirc.clients.acp.ipc import encode_message, decode_message, make_whisper, make_response

logger = logging.getLogger(__name__)

RequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class SocketServer:
    def __init__(self, path: str, handler: RequestHandler):
        self.path = path
        self.handler = handler
        self._server: asyncio.Server | None = None
        self._clients: list[asyncio.StreamWriter] = []
        # Queue of encoded whisper bytes pending delivery to any connected client.
        self._whisper_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def start(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)
        self._server = await asyncio.start_unix_server(self._handle_client, path=self.path)
        os.chmod(self.path, 0o600)

    async def stop(self) -> None:
        for writer in self._clients:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError, OSError):
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def send_whisper(self, message: str, whisper_type: str) -> None:
        """Enqueue a whisper for delivery to all currently-connected clients.

        If no clients are connected yet, the whisper sits in the queue and
        will be delivered to the first client that completes its handshake.
        """
        whisper = make_whisper(message, whisper_type)
        data = encode_message(whisper)
        if self._clients:
            for writer in list(self._clients):
                try:
                    writer.write(data)
                    await writer.drain()
                except (ConnectionError, BrokenPipeError, OSError):
                    self._clients.remove(writer)
        else:
            await self._whisper_queue.put(data)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.append(writer)
        # Drain any queued whispers that arrived before this client connected.
        while not self._whisper_queue.empty():
            try:
                data = self._whisper_queue.get_nowait()
                writer.write(data)
                await writer.drain()
            except asyncio.QueueEmpty:
                break
            except (ConnectionError, BrokenPipeError, OSError):
                break
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                msg = decode_message(line)
                if msg is None:
                    continue
                try:
                    response = await self.handler(msg)
                    writer.write(encode_message(response))
                    await writer.drain()
                except Exception as exc:
                    logger.exception("Handler error for message: %s", msg)
                    try:
                        request_id = msg.get("id") if isinstance(msg, dict) else None
                        err_resp = make_response(request_id or "", ok=False, error=str(exc))
                        writer.write(encode_message(err_resp))
                        await writer.drain()
                    except (ConnectionError, BrokenPipeError, OSError):
                        break
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if writer in self._clients:
                self._clients.remove(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError, OSError):
                pass
