"""IRC Skill Client — connects Claude Code to the culture daemon via Unix socket.

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

from culture.cli.shared.constants import culture_runtime_dir
from culture.clients.claude.ipc import (
    MSG_TYPE_RESPONSE,
    MSG_TYPE_WHISPER,
    decode_message,
    encode_message,
    make_request,
)


class SkillClient:
    """Async client that connects to the culture daemon Unix socket."""

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
            await asyncio.gather(self._read_task, return_exceptions=True)
            self._read_task = None

        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
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
                self._dispatch_message(msg)
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            # Resolve any still-pending futures with an error
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Connection lost"))
            self._pending.clear()

    def _dispatch_message(self, msg: dict[str, Any]) -> None:
        """Route a decoded message to the appropriate handler."""
        msg_type = msg.get("type")
        if msg_type == MSG_TYPE_RESPONSE:
            req_id = msg.get("id", "")
            future = self._pending.pop(req_id, None)
            if future is not None and not future.done():
                future.set_result(msg)
        elif msg_type == MSG_TYPE_WHISPER:
            self.pending_whispers.append(msg)

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

    async def irc_ask(self, channel: str, question: str, timeout: int = 30) -> dict[str, Any]:
        """Send a question to a channel (fires a webhook alert on the daemon side)."""
        return await self._request("irc_ask", channel=channel, message=question, timeout=timeout)

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

    async def irc_topic(self, channel: str, topic: str | None = None) -> dict[str, Any]:
        """Get or set a channel topic."""
        params: dict[str, Any] = {"channel": channel}
        if topic is not None:
            params["topic"] = topic
        return await self._request("irc_topic", **params)

    async def compact(self) -> dict[str, Any]:
        """Send /compact to the Claude agent runner."""
        return await self._request("compact")

    async def clear(self) -> dict[str, Any]:
        """Send /clear to the Claude agent runner."""
        return await self._request("clear")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _sock_path_from_env() -> str:
    """Resolve socket path from CULTURE_NICK env var."""
    nick = os.environ.get("CULTURE_NICK", "")
    if not nick:
        print("ERROR: CULTURE_NICK environment variable is not set", file=sys.stderr)
        sys.exit(1)
    return os.path.join(culture_runtime_dir(), f"culture-{nick}.sock")


def _parse_ask_timeout(remaining: list[str]) -> tuple[int, list[str]]:
    """Extract --timeout N from args, returning (timeout, filtered_args)."""
    if "--timeout" in remaining:
        idx = remaining.index("--timeout")
        if idx + 1 >= len(remaining):
            print("ERROR: --timeout requires a value", file=sys.stderr)
            sys.exit(2)
        try:
            timeout = int(remaining[idx + 1])
        except ValueError:
            print(
                f"ERROR: --timeout value must be an integer, got {remaining[idx + 1]!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        if timeout <= 0:
            print("ERROR: --timeout must be positive", file=sys.stderr)
            sys.exit(2)
        remaining = remaining[:idx] + remaining[idx + 2 :]
    else:
        timeout = 30
    return timeout, remaining


async def _cmd_send(client: SkillClient, args: list[str]) -> dict[str, Any]:
    channel = args[1]
    message = " ".join(args[2:])
    return await client.irc_send(channel, message)


async def _cmd_read(client: SkillClient, args: list[str]) -> dict[str, Any]:
    channel = args[1]
    limit = int(args[2]) if len(args) > 2 else 50
    return await client.irc_read(channel, limit=limit)


async def _cmd_ask(client: SkillClient, args: list[str]) -> dict[str, Any]:
    channel = args[1]
    timeout, remaining = _parse_ask_timeout(args[2:])
    question = " ".join(remaining)
    return await client.irc_ask(channel, question, timeout=timeout)


async def _cmd_join(client: SkillClient, args: list[str]) -> dict[str, Any]:
    return await client.irc_join(args[1])


async def _cmd_part(client: SkillClient, args: list[str]) -> dict[str, Any]:
    return await client.irc_part(args[1])


async def _cmd_channels(client: SkillClient, args: list[str]) -> dict[str, Any]:
    return await client.irc_channels()


async def _cmd_who(client: SkillClient, args: list[str]) -> dict[str, Any]:
    return await client.irc_who(args[1])


async def _cmd_topic(client: SkillClient, args: list[str]) -> dict[str, Any]:
    channel = args[1]
    topic = " ".join(args[2:]) if len(args) > 2 else None
    return await client.irc_topic(channel, topic)


async def _cmd_compact(client: SkillClient, args: list[str]) -> dict[str, Any]:
    return await client.compact()


async def _cmd_clear(client: SkillClient, args: list[str]) -> dict[str, Any]:
    return await client.clear()


_SUBCOMMANDS: dict[str, Any] = {
    "send": _cmd_send,
    "read": _cmd_read,
    "ask": _cmd_ask,
    "join": _cmd_join,
    "part": _cmd_part,
    "channels": _cmd_channels,
    "who": _cmd_who,
    "topic": _cmd_topic,
    "compact": _cmd_compact,
    "clear": _cmd_clear,
}


async def _main(args: list[str]) -> None:
    """CLI entry point. First arg is the subcommand."""
    if not args:
        print(
            "Usage: irc_client.py <subcommand> [args...]\n"
            "Subcommands: send, read, ask, join, part, channels, who, topic, compact, clear",
            file=sys.stderr,
        )
        sys.exit(1)

    sock_path = _sock_path_from_env()
    subcommand = args[0]

    handler = _SUBCOMMANDS.get(subcommand)
    if handler is None:
        print(f"ERROR: Unknown subcommand: {subcommand!r}", file=sys.stderr)
        sys.exit(1)

    client = SkillClient(sock_path)
    await client.connect()

    try:
        result = await handler(client, args)

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
