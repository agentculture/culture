"""IPC and observer helpers for culture CLI."""

from __future__ import annotations

import asyncio
import os

from culture.clients.claude.config import load_config_or_default


def agent_socket_path(nick: str) -> str:
    return os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
        f"culture-{nick}.sock",
    )


async def ipc_request(socket_path: str, msg_type: str, **kwargs) -> dict | None:
    """Send an IPC request via Unix socket and return the response."""
    from culture.clients.claude.ipc import decode_message, encode_message, make_request

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path),
            timeout=3.0,
        )
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return None
    try:
        req = make_request(msg_type, **kwargs)
        writer.write(encode_message(req))
        await writer.drain()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            data = await asyncio.wait_for(reader.readline(), timeout=remaining)
            msg = decode_message(data)
            if msg and msg.get("type") == "response":
                return msg
    except (asyncio.TimeoutError, ConnectionError, BrokenPipeError, OSError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, BrokenPipeError, OSError):
            pass


async def ipc_shutdown(socket_path: str) -> bool:
    """Send a shutdown command via Unix socket IPC."""
    resp = await ipc_request(socket_path, "shutdown")
    return resp is not None and resp.get("ok", False)


def get_observer(config_path: str):
    """Create an IRCObserver from the config file."""
    from culture.observer import IRCObserver

    config = load_config_or_default(config_path)
    return IRCObserver(
        host=config.server.host,
        port=config.server.port,
        server_name=config.server.name,
    )
