from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from culture.clients.bridge._peercred import peercred
from culture.clients.bridge.ipc import decode_message, encode_message, make_response, make_whisper

logger = logging.getLogger(__name__)

RequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class SocketServer:
    """Bridge IPC server over an AF_UNIX stream socket.

    Same-user-only: every accepted connection is checked via
    ``SO_PEERCRED`` (Linux) or ``getpeereid`` (Darwin) before any
    message is read. Connections from another uid are refused —
    ``chmod 0o600`` alone is insufficient because (a) symlink
    permissions may differ from the socket path's, and (b) on macOS
    the ``/tmp`` parent directory is world-traversable. The bridge
    daemon resolves the socket path to a user-private directory
    (``$XDG_RUNTIME_DIR/culture/`` on Linux,
    ``~/Library/Caches/culture/run/`` on Darwin), but the explicit
    peer-uid check is the defense-in-depth that closes T5 from the
    rearchitecture plan.
    """

    def __init__(self, path: str, handler: RequestHandler):
        self.path = path
        self.handler = handler
        self._server: asyncio.Server | None = None
        self._clients: list[asyncio.StreamWriter] = []
        # Queue of encoded whisper bytes pending delivery to any connected client.
        self._whisper_queue: asyncio.Queue[bytes] = asyncio.Queue()
        # Cached own uid for the peercred refusal path.
        self._own_uid: int = os.getuid()

    async def start(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)
        self._server = await asyncio.start_unix_server(self._handle_client, path=self.path)
        # Tighten the socket file's permissions to 0o600 immediately after
        # bind so no other user can even connect() to it. (The peercred
        # check below is the second line of defense against bind races and
        # symlink-target permission mismatches.)
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
        # Peer-credential check (Phase 2.5 of the rearchitecture plan).
        # Closes T5 of the threat table: a same-host attacker on a
        # different uid cannot drive the bridge's IPC even if the
        # filesystem permission boundary slips (e.g. a misconfigured
        # umask or a symlink whose target permissions differ from the
        # bridge socket path's). Refuse with EPERM-style log + close.
        if not self._authorize_peer(writer):
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            return
        self._clients.append(writer)
        try:
            await self._drain_queued_whispers(writer)
            await self._process_client_messages(reader, writer)
        finally:
            self._cleanup_client(writer)

    def _authorize_peer(self, writer: asyncio.StreamWriter) -> bool:
        """Return True iff the peer's uid matches the bridge's own uid.

        Logs and refuses on any of: missing socket fd, peercred syscall
        failure, or uid mismatch. ``NotImplementedError`` from
        ``peercred`` (e.g. on Windows) is treated as refusal —
        AF_UNIX bridge sockets are POSIX-only by design.
        """
        sock = writer.get_extra_info("socket")
        if sock is None:
            logger.warning("Refusing IPC connection: no underlying socket on writer")
            return False
        try:
            sock_fd = sock.fileno()
        except (AttributeError, OSError):
            logger.warning("Refusing IPC connection: cannot resolve socket fd")
            return False
        try:
            uid, _gid = peercred(sock_fd)
        except (NotImplementedError, OSError) as exc:
            logger.warning(
                "Refusing IPC connection: peercred() failed (%s); cannot " "verify peer uid",
                exc,
            )
            return False
        if uid != self._own_uid:
            logger.warning(
                "Refusing IPC connection: peer uid %d != own uid %d",
                uid,
                self._own_uid,
            )
            return False
        return True

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
