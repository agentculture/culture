"""IPC and observer helpers for culture CLI."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from culture_core.config import load_config_or_default

logger = logging.getLogger(__name__)

# Module-level so tests can shrink them; production values are seconds.
CONNECT_TIMEOUT = 3.0
RESPONSE_TIMEOUT = 15.0


def agent_socket_path(nick: str) -> str:
    from culture_core.cli.shared.constants import culture_runtime_dir

    return os.path.join(
        culture_runtime_dir(),
        f"culture-{nick}.sock",
    )


def _nick_from_socket_path(socket_path: str) -> str:
    """Best-effort reverse of :func:`agent_socket_path` for log context."""
    basename = os.path.basename(socket_path)
    if basename.startswith("culture-") and basename.endswith(".sock"):
        return basename[len("culture-") : -len(".sock")]
    return ""


def _log_ipc_failure(operation: str, socket_path: str, failure: str, started: float) -> None:
    """One consistent record shape for every IPC failure mode (#17).

    Every line carries nick, socket path, operation, failure class, and
    elapsed seconds so a failed ``culture agents sleep`` (etc.) is
    attributable from logs alone.
    """
    logger.warning(
        "ipc failure: operation=%s nick=%s socket_path=%s failure=%s elapsed=%.3fs",
        operation,
        _nick_from_socket_path(socket_path),
        socket_path,
        failure,
        time.monotonic() - started,
    )


async def ipc_request(socket_path: str, msg_type: str, **kwargs) -> dict | None:
    """Send an IPC request via Unix socket and return the response.

    Returns None on any failure; each failure mode is logged with
    structured context (nick, socket_path, operation, failure class,
    elapsed) via :func:`_log_ipc_failure` so callers' generic "agent not
    responding" errors stay diagnosable. (#17)
    """
    from culture_core.clients.shared.ipc import decode_message, encode_message, make_request

    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path),
            timeout=CONNECT_TIMEOUT,
        )
    except OSError as exc:
        _log_ipc_failure(msg_type, socket_path, f"connect:{type(exc).__name__}", started)
        return None
    try:
        req = make_request(msg_type, **kwargs)
        writer.write(encode_message(req))
        await writer.drain()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RESPONSE_TIMEOUT
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                _log_ipc_failure(msg_type, socket_path, "response_timeout", started)
                return None
            try:
                data = await asyncio.wait_for(reader.readline(), timeout=remaining)
            except TimeoutError:
                _log_ipc_failure(msg_type, socket_path, "response_timeout", started)
                return None
            # EOF (peer closed without responding) — exit early instead
            # of busy-looping until the 15s deadline.
            if not data:
                _log_ipc_failure(msg_type, socket_path, "eof", started)
                return None
            msg = decode_message(data)
            if msg and msg.get("type") == "response":
                return msg
    except OSError as exc:
        _log_ipc_failure(msg_type, socket_path, type(exc).__name__, started)
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def ipc_shutdown(socket_path: str) -> bool:
    """Send a shutdown command via Unix socket IPC."""
    resp = await ipc_request(socket_path, "shutdown")
    return resp is not None and resp.get("ok", False)


def get_observer(config_path: str):
    """Create an IRCObserver from the config file.

    Reads ``CULTURE_NICK`` from the environment so the resulting peek
    connection can name itself after the calling agent. The peek nick
    only carries attribution when ``CULTURE_NICK`` belongs to the same
    server as the observer; otherwise it falls back to the opaque
    ``<server>-_peek<hex>`` form (see #329).
    """
    from culture_core.observer import IRCObserver

    config = load_config_or_default(config_path)
    parent = os.environ.get("CULTURE_NICK", "").strip() or None
    return IRCObserver(
        host=config.server.host,
        port=config.server.port,
        server_name=config.server.name,
        parent_nick=parent,
    )
