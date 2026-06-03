"""Bridge IPC client used by the CC plugin's hooks + MCP tools.

A thin synchronous wrapper around a single-shot AF_UNIX request/response
exchange against the ``culture-bridge`` daemon. The bridge's IPC
protocol is JSON-Lines (one JSON object per line, newline-delimited).

We deliberately keep this client **stateless** — hook scripts are
short-lived subprocesses spawned per-event by Claude Code, so a
long-lived connection pool would be wasted overhead and a source of
file-descriptor leaks. Each ``BridgeClient.request(verb, **payload)``
opens a connection, writes one request, reads responses until the
matching ``id`` arrives (draining any pending whispers along the way),
then closes.

Socket path resolution mirrors the bridge's own ``_resolve_socket_path``
helper (Phase 2.5). The three search locations are, in order:

    1. ``$XDG_RUNTIME_DIR/culture/<nick>.sock``
    2. macOS: ``~/Library/Caches/culture/run/<nick>.sock``
    3. Linux/POSIX fallback: ``/tmp/culture-<nick>.sock``

We also honour a ``~/.culture/run/<nick>.sock`` symlink that
``ensure_socket_symlink`` creates so a stale daemon path becomes
self-healing across upgrades.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


# Same shape as bridge ipc.py — kept in-sync deliberately rather than
# imported, because hooks must run without the full culture package
# being on sys.path (Claude Code hook subprocesses inherit the user's
# env, not necessarily a uv-managed venv).
MSG_TYPE_RESPONSE = "response"
MSG_TYPE_WHISPER = "whisper"


class BridgeClientError(RuntimeError):
    """Wraps any I/O or protocol failure talking to the bridge."""


def resolve_socket_path(nick: str) -> str:
    """Return the most-likely bridge socket path for ``nick``.

    We check the three known locations in priority order and return the
    first one that exists. If none exist, return the preferred location
    so callers can present a useful "does the bridge exist?" error.
    """
    candidates: list[str] = []
    xdg = os.environ.get("XDG_RUNTIME_DIR", "")
    if xdg:
        candidates.append(os.path.join(xdg, "culture", f"{nick}.sock"))
    if sys.platform == "darwin":
        home = os.path.expanduser("~")
        candidates.append(os.path.join(home, "Library", "Caches", "culture", "run", f"{nick}.sock"))
    candidates.append(os.path.join("/tmp", f"culture-{nick}.sock"))
    # ~/.culture/run/<nick>.sock — the symlink ensure_socket_symlink writes.
    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, ".culture", "run", f"{nick}.sock"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0] if candidates else ""


def bridge_running(nick: str) -> bool:
    """Cheap probe: does a socket file exist + can we connect to it?"""
    path = resolve_socket_path(nick)
    if not path or not os.path.exists(path):
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect(path)
        sock.close()
        return True
    except OSError:
        return False


class BridgeClient:
    """Single-shot synchronous IPC client.

    Not thread-safe — instantiate per call. The hook scripts and MCP
    tool entry points are short-lived subprocesses anyway, so the
    serial pattern is the right model.
    """

    def __init__(self, nick: str, timeout: float = 5.0) -> None:
        self.nick = nick
        self.timeout = timeout
        self.socket_path = resolve_socket_path(nick)

    def request(self, verb: str, **payload: Any) -> dict[str, Any]:
        """Open, send one request, collect any whispers + the matching
        response, then close. Returns the parsed response dict.

        Raises ``BridgeClientError`` on socket/connection problems or on
        a non-OK bridge reply.
        """
        if not self.socket_path:
            raise BridgeClientError(f"No socket path resolved for nick={self.nick!r}")
        if not os.path.exists(self.socket_path):
            raise BridgeClientError(
                f"Bridge socket not found at {self.socket_path!r} — is the bridge running?"
            )
        req_id = str(uuid.uuid4())
        msg = {"type": verb, "id": req_id, **payload}
        line = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        whispers: list[dict[str, Any]] = []
        try:
            sock.connect(self.socket_path)
            sock.sendall(line)
            response = self._read_until_response(sock, req_id, whispers)
        except OSError as exc:
            raise BridgeClientError(f"Bridge IPC I/O error: {exc}") from exc
        finally:
            try:
                sock.close()
            except OSError:
                pass
        # Attach any whispers that arrived in-band; some verbs (drain)
        # carry their payload as response.data while others (push)
        # arrive only as whispers — callers can read both.
        if whispers:
            response.setdefault("_whispers", whispers)
        return response

    def _read_until_response(
        self,
        sock: socket.socket,
        req_id: str,
        whispers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Read newline-delimited JSON until we see the matching response.

        Buffers partial lines across ``recv`` calls. Whispers encountered
        along the way are appended to ``whispers``.
        """
        buf = b""
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                raise BridgeClientError(f"Bridge IPC timed out waiting for response id={req_id}")
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(4096)
            except socket.timeout as exc:
                raise BridgeClientError(
                    f"Bridge IPC timed out waiting for response id={req_id}"
                ) from exc
            if not chunk:
                raise BridgeClientError("Bridge closed connection before response arrived")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                msg_type = msg.get("type")
                if msg_type == MSG_TYPE_RESPONSE and msg.get("id") == req_id:
                    return msg
                if msg_type == MSG_TYPE_WHISPER:
                    whispers.append(msg)


def request(nick: str, verb: str, timeout: float = 5.0, **payload: Any) -> dict[str, Any]:
    """Module-level convenience: one-shot IPC request without the
    intermediate ``BridgeClient`` instance. Hook scripts use this."""
    return BridgeClient(nick=nick, timeout=timeout).request(verb, **payload)
