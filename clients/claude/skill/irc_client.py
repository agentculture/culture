"""IRC Skill Client — connects Claude Code to the agentirc daemon via Unix socket.

This module provides:
- ``SkillClient``: async client library for use from Python code
- CLI entry point: ``python -m clients.claude.skill.irc_client <subcommand> ...``

The client communicates with the daemon's Unix socket using JSON Lines
(one JSON object per line, newline-delimited).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from clients.claude.ipc import (
    encode_message,
    decode_message,
    make_request,
    MSG_TYPE_RESPONSE,
    MSG_TYPE_WHISPER,
)


class SkillClient:
    """Async client that connects to the agentirc daemon Unix socket."""

    def __init__(self, sock_path: str) -> None:
        self.sock_path = sock_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.pending_whispers: list[dict[str, Any]] = []
        self._read_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open a connection to the Unix socket and start the background reader."""
        self._reader, self._writer = await asyncio.open_unix_connection(self.sock_path)
        self._read_task = asyncio.get_running_loop().create_task(self._read_loop())

    async def close(self) -> None:
        """Close the connection and cancel the background reader."""
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None

        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, BrokenPipeError, OSError):
                pass
            self._writer = None
        self._reader = None

        # Fail any pending requests
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("SkillClient closed"))
        self._pending.clear()

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Read lines from the socket; route responses to waiting futures
        and whispers to pending_whispers."""
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                msg = decode_message(line)
                if msg is None:
                    continue
                msg_type = msg.get("type")
                if msg_type == MSG_TYPE_RESPONSE:
                    req_id = msg.get("id", "")
                    future = self._pending.pop(req_id, None)
                    if future is not None and not future.done():
                        future.set_result(msg)
                elif msg_type == MSG_TYPE_WHISPER:
                    self.pending_whispers.append(msg)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            # Resolve any still-pending futures with an error
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Connection lost"))
            self._pending.clear()

    # ------------------------------------------------------------------
    # Request dispatch
    # ------------------------------------------------------------------

    async def _request(self, msg_type: str, **kwargs: Any) -> dict[str, Any]:
        """Send a request and wait for its response by ID correlation."""
        assert self._writer is not None, "Not connected"
        msg = make_request(msg_type, **kwargs)
        req_id: str = msg["id"]
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future
        self._writer.write(encode_message(msg))
        await self._writer.drain()
        return await future

    # ------------------------------------------------------------------
    # Whisper helpers
    # ------------------------------------------------------------------

    def drain_whispers(self) -> list[dict[str, Any]]:
        """Return and clear the pending whispers list."""
        whispers = list(self.pending_whispers)
        self.pending_whispers.clear()
        return whispers

    # ------------------------------------------------------------------
    # High-level IRC methods
    # ------------------------------------------------------------------

    async def irc_send(self, channel: str, message: str) -> dict[str, Any]:
        """Send a PRIVMSG to a channel."""
        return await self._request("irc_send", channel=channel, message=message)

    async def irc_read(self, channel: str, limit: int = 50) -> dict[str, Any]:
        """Read recent messages from a channel buffer."""
        return await self._request("irc_read", channel=channel, limit=limit)

    async def irc_ask(
        self, channel: str, question: str, timeout: int = 30
    ) -> dict[str, Any]:
        """Send a question to a channel (fires a webhook alert on the daemon side)."""
        return await self._request(
            "irc_ask", channel=channel, message=question, timeout=timeout
        )

    async def irc_join(self, channel: str) -> dict[str, Any]:
        """Join a channel."""
        return await self._request("irc_join", channel=channel)

    async def irc_part(self, channel: str) -> dict[str, Any]:
        """Part a channel."""
        return await self._request("irc_part", channel=channel)

    async def irc_channels(self) -> dict[str, Any]:
        """List joined channels."""
        return await self._request("irc_channels")

    async def irc_who(self, target: str) -> dict[str, Any]:
        """Send a WHO query for a channel or nick."""
        return await self._request("irc_who", target=target)

    async def compact(self) -> dict[str, Any]:
        """Send /compact to the Claude agent runner."""
        return await self._request("compact")

    async def clear(self) -> dict[str, Any]:
        """Send /clear to the Claude agent runner."""
        return await self._request("clear")

    async def set_directory(self, directory: str) -> dict[str, Any]:
        """Change the working directory for the Claude agent runner."""
        return await self._request("set_directory", directory=directory)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _sock_path_from_env() -> str:
    """Resolve socket path from AGENTIRC_NICK env var."""
    nick = os.environ.get("AGENTIRC_NICK", "")
    if not nick:
        print("ERROR: AGENTIRC_NICK environment variable is not set", file=sys.stderr)
        sys.exit(1)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return os.path.join(runtime_dir, f"agentirc-{nick}.sock")


async def _main(args: list[str]) -> None:
    """CLI entry point. First arg is the subcommand."""
    if not args:
        print(
            "Usage: irc_client.py <subcommand> [args...]\n"
            "Subcommands: send, read, ask, join, part, channels, who, compact, clear, set-directory",
            file=sys.stderr,
        )
        sys.exit(1)

    sock_path = _sock_path_from_env()
    subcommand = args[0]

    client = SkillClient(sock_path)
    await client.connect()

    try:
        if subcommand == "send":
            # send <channel> <message...>
            channel = args[1]
            message = " ".join(args[2:])
            result = await client.irc_send(channel, message)

        elif subcommand == "read":
            # read <channel> [limit]
            channel = args[1]
            limit = int(args[2]) if len(args) > 2 else 50
            result = await client.irc_read(channel, limit=limit)

        elif subcommand == "ask":
            # ask <channel> [--timeout N] <question...>
            channel = args[1]
            remaining = args[2:]
            if "--timeout" in remaining:
                idx = remaining.index("--timeout")
                timeout = int(remaining[idx + 1])
                remaining = remaining[:idx] + remaining[idx + 2:]
            else:
                timeout = 30
            question = " ".join(remaining)
            result = await client.irc_ask(channel, question, timeout=timeout)

        elif subcommand == "join":
            channel = args[1]
            result = await client.irc_join(channel)

        elif subcommand == "part":
            channel = args[1]
            result = await client.irc_part(channel)

        elif subcommand == "channels":
            result = await client.irc_channels()

        elif subcommand == "who":
            target = args[1]
            result = await client.irc_who(target)

        elif subcommand == "compact":
            result = await client.compact()

        elif subcommand == "clear":
            result = await client.clear()

        elif subcommand == "set-directory":
            directory = args[1]
            result = await client.set_directory(directory)

        else:
            print(f"ERROR: Unknown subcommand: {subcommand!r}", file=sys.stderr)
            sys.exit(1)

        # Print result as JSON
        print(json.dumps(result, indent=2))

        # Print any pending whispers to stderr so the agent can see them
        for whisper in client.drain_whispers():
            print(
                f"[whisper:{whisper.get('whisper_type', '?')}] {whisper.get('message', '')}",
                file=sys.stderr,
            )

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
