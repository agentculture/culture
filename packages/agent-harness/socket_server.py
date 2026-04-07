# ASSIMILAI: Replace BACKEND with your backend name (e.g., codex, opencode)
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from culture.clients.BACKEND.ipc import decode_message, encode_message, make_response, make_whisper

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
            except OSError:
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def send_whisper(self, message: str, whisper_type: str) -> None:
        """Enqueue a whisper for delivery to all currently-connected clients.

        If no clients are connected yet, the whisper sits in the queue and
        will be delivered to the first client that completes its handshake.
        This avoids race conditions in tests where send_whisper is called
        shortly after open_unix_connection.
        """
        whisper = make_whisper(message, whisper_type)
        data = encode_message(whisper)
        # If there are already connected clients, send immediately.
        if self._clients:
            for writer in list(self._clients):
                try:
                    writer.write(data)
                    await writer.drain()
                except OSError:
                    self._clients.remove(writer)
        else:
            # No clients yet — queue for delivery when one connects.
            await self._whisper_queue.put(data)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients.append(writer)
        try:
            await self._drain_queued_whispers(writer)
            await self._process_client_messages(reader, writer)
        finally:
            self._cleanup_client(writer)

    async def _drain_queued_whispers(self, writer: asyncio.StreamWriter) -> None:
        """Deliver any whispers queued before this client connected."""
        while not self._whisper_queue.empty():
            try:
                data = self._whisper_queue.get_nowait()
                writer.write(data)
                await writer.drain()
            except asyncio.QueueEmpty:
                break
            except OSError:
                break

    async def _process_client_messages(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Read and dispatch IPC messages until the client disconnects."""
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
                    if not await self._send_error_response(msg, exc, writer):
                        break
        except (ConnectionError, asyncio.IncompleteReadError):
            pass

    async def _send_error_response(
        self, msg: dict, exc: Exception, writer: asyncio.StreamWriter
    ) -> bool:
        """Send an error response to the client. Return False if the connection broke."""
        try:
            request_id = msg.get("id") if isinstance(msg, dict) else None
            err_resp = make_response(request_id or "", ok=False, error=str(exc))
            writer.write(encode_message(err_resp))
            await writer.drain()
            return True
        except OSError:
            return False

    def _cleanup_client(self, writer: asyncio.StreamWriter) -> None:
        """Remove client from the active list and close the connection."""
        if writer in self._clients:
            self._clients.remove(writer)
        writer.close()
