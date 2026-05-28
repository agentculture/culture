"""Mission Control dashboard server (aiohttp).

Localhost-only web app that streams every agent's activity + pending approvals
into one browser view and exposes the full intervention surface (approve/deny,
pause/resume, close, emergency stop-all, policy edit). Reuses the existing data
files (``~/.culture/{audit,daemon-log,perm-queue,perm-policy}``) and control
levers (the permission broker, daemon IPC, the ``culture agent`` CLI).

Design spec: docs/superpowers/specs/2026-05-29-mission-control-dashboard-design.md
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import re
import subprocess
import sys

import yaml
from aiohttp import web

from culture.cli.shared.ipc import agent_socket_path, ipc_request
from culture.cli.shared.process import is_process_alive
from culture.clients._audit import audit_path_for
from culture.clients._daemon_log import daemon_log_path_for
from culture.clients._perm_broker import (
    DecisionExistsError,
    culture_home,
    list_pending,
    policy_path_for,
    write_decision,
)
from culture.config import load_config_or_default
from culture.pidfile import read_pid

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_SSE_POLL_SECONDS = 0.25
_SSE_BACKLOG_LINES = 200
_KEEPALIVE_SECONDS = 15

# Typed app key for the optional server-config path.
_CONFIG_PATH: web.AppKey[str | None] = web.AppKey("config_path", object)  # type: ignore[arg-type]

# Agent nicks are <server>-<agent>: alphanumerics + hyphens only. Validating
# every nick that reaches a path builder (audit/daemon-log/policy/socket) closes
# the path-traversal hole — a nick like "../../etc/passwd" must be rejected.
_NICK_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9_-]*[A-Za-z0-9])?$")

# Loopback-only Origin/Host. The dashboard can approve tool calls and kill
# agents; the localhost bind alone does NOT stop a malicious web page from
# POSTing to 127.0.0.1 via DNS rebinding, so mutating requests are origin-gated.
_LOOPBACK_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")
_LOOPBACK_HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost|\[?::1\]?)(:\d+)?$")


def _valid_nick(nick: str) -> bool:
    return bool(nick) and _NICK_RE.fullmatch(nick) is not None


def _require_nick(nick: str) -> str:
    if not _valid_nick(nick):
        raise web.HTTPBadRequest(text=f"invalid nick {nick!r}")
    return nick


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _pending_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for req in list_pending():
        nick = req.get("helper_nick", "")
        if nick:
            counts[nick] = counts.get(nick, 0) + 1
    return counts


def _last_action(nick: str) -> str:
    """Most recent daemon-action for an agent (empty if none)."""
    path = daemon_log_path_for(nick)
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line).get("action", "")
        except json.JSONDecodeError:
            continue
    return ""


def _agent_state(nick: str) -> str:
    pid = read_pid(f"agent-{nick}")
    if pid and is_process_alive(pid):
        return "running"
    return "stopped"


def list_agents(config_path: str | None = None) -> list[dict]:
    """Programmatic agent grid (no CLI-text parsing)."""
    config = (
        load_config_or_default(config_path)
        if config_path
        else load_config_or_default(os.path.join(culture_home(), "server.yaml"))
    )
    pending = _pending_counts()
    rows = []
    for agent in config.agents:
        if getattr(agent, "archived", False):
            continue
        nick = agent.nick
        rows.append(
            {
                "nick": nick,
                "state": _agent_state(nick),
                "pending": pending.get(nick, 0),
                "last_action": _last_action(nick),
                "is_boss": "boss" in (getattr(agent, "tags", []) or []),
                "boss": getattr(agent, "boss", "") or "",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# HTTP / SSE handlers
# ---------------------------------------------------------------------------


async def _handle_index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(os.path.join(_STATIC_DIR, "index.html"))


async def _handle_agents(request: web.Request) -> web.Response:
    return web.json_response({"agents": list_agents(request.app.get(_CONFIG_PATH))})


async def _handle_pending(request: web.Request) -> web.Response:
    return web.json_response({"pending": list_pending()})


def _jsonl_path(kind: str, nick: str) -> str | None:
    if kind == "audit":
        return audit_path_for(nick)
    if kind == "daemon-log":
        return daemon_log_path_for(nick)
    return None


async def _sse_prologue(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
    await resp.prepare(request)
    return resp


async def _handle_stream_jsonl(request: web.Request) -> web.StreamResponse:
    """Tail a per-agent JSONL file as SSE: backlog, then live appends.

    All file I/O is byte-oriented: ``offset`` is a byte offset compared against
    ``os.path.getsize`` (also bytes). Text-mode ``tell()`` returns an opaque
    cursor that does not equal the byte size for non-ASCII content, which would
    break the truncation/no-new-data checks — so we read in binary and decode.
    """
    kind = request.match_info["kind"]
    nick = _require_nick(request.match_info["nick"])
    path = _jsonl_path(kind, nick)
    if path is None:
        raise web.HTTPNotFound(text=f"unknown stream kind {kind!r}")

    resp = await _sse_prologue(request)
    try:
        # Backlog: last N lines via a bounded deque (no full-file load).
        offset = 0
        backlog: collections.deque[str] = collections.deque(maxlen=_SSE_BACKLOG_LINES)
        try:
            with open(path, "rb") as handle:
                for raw in handle:
                    backlog.append(raw.decode("utf-8", "replace").rstrip("\n"))
                offset = handle.tell()
        except OSError:
            offset = 0
        for line in backlog:
            if _client_gone(request):
                return resp
            if line.strip():
                await _sse_send(resp, line)
        # Live tail.
        idle = 0.0
        while not _client_gone(request):
            new_offset, chunk = _read_appended(path, offset)
            if chunk:
                offset = new_offset
                for line in chunk.splitlines():
                    if line.strip():
                        await _sse_send(resp, line)
                idle = 0.0
            else:
                await asyncio.sleep(_SSE_POLL_SECONDS)
                idle += _SSE_POLL_SECONDS
                if idle >= _KEEPALIVE_SECONDS:
                    await resp.write(b": keepalive\n\n")
                    idle = 0.0
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # client disconnected — stop tailing, no leak
    return resp


def _read_appended(path: str, offset: int) -> tuple[int, str]:
    """Read bytes appended to ``path`` since byte ``offset``. Returns (new_offset, text)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return offset, ""
    if size < offset:  # file truncated/rotated — restart from 0
        offset = 0
    if size == offset:
        return offset, ""
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
            return handle.tell(), data.decode("utf-8", "replace")
    except OSError:
        return offset, ""


async def _sse_send(resp: web.StreamResponse, data: str) -> None:
    await resp.write(f"data: {data}\n\n".encode("utf-8"))


def _client_gone(request: web.Request) -> bool:
    """True once the client has disconnected (transport closed)."""
    transport = request.transport
    return transport is None or transport.is_closing()


# ---------------------------------------------------------------------------
# Control handlers (the human operator is the top authority — no grant ceiling)
# ---------------------------------------------------------------------------


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


async def _handle_approve(request: web.Request) -> web.Response:
    body = await _json_body(request)
    req_id = body.get("id")
    if not req_id:
        return web.json_response({"error": "missing id"}, status=400)
    scope = "always" if body.get("always") else "once"
    try:
        write_decision(
            req_id,
            verdict="allow",
            scope=scope,
            pattern=body.get("pattern", ""),
            decided_by="dashboard",
        )
    except DecisionExistsError:
        return web.json_response({"error": "decision already exists"}, status=409)
    return web.json_response({"ok": True})


async def _handle_deny(request: web.Request) -> web.Response:
    body = await _json_body(request)
    req_id = body.get("id")
    if not req_id:
        return web.json_response({"error": "missing id"}, status=400)
    try:
        write_decision(
            req_id,
            verdict="deny",
            reason=str(body.get("reason", "")),
            decided_by="dashboard",
        )
    except DecisionExistsError:
        return web.json_response({"error": "decision already exists"}, status=409)
    return web.json_response({"ok": True})


async def _ipc_to(nick: str, msg_type: str) -> bool:
    resp = await ipc_request(agent_socket_path(nick), msg_type)
    return bool(resp and resp.get("ok"))


async def _handle_pause(request: web.Request) -> web.Response:
    body = await _json_body(request)
    nick = body.get("nick")
    if not nick:
        return web.json_response({"error": "missing nick"}, status=400)
    if not _valid_nick(nick):
        return web.json_response({"error": "invalid nick"}, status=400)
    ok = await _ipc_to(nick, "pause")
    return web.json_response({"ok": ok})


async def _handle_resume(request: web.Request) -> web.Response:
    body = await _json_body(request)
    nick = body.get("nick")
    if not nick:
        return web.json_response({"error": "missing nick"}, status=400)
    if not _valid_nick(nick):
        return web.json_response({"error": "invalid nick"}, status=400)
    ok = await _ipc_to(nick, "resume")
    return web.json_response({"ok": ok})


def _agent_stop(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "culture", "agent", "stop", *args],
        check=False,
        capture_output=True,
        text=True,
    )


async def _handle_close(request: web.Request) -> web.Response:
    body = await _json_body(request)
    nick = body.get("nick")
    if not nick:
        return web.json_response({"error": "missing nick"}, status=400)
    if not _valid_nick(nick):
        return web.json_response({"error": "invalid nick"}, status=400)
    res = await asyncio.to_thread(_agent_stop, nick)
    return web.json_response({"ok": res.returncode == 0, "detail": res.stdout or res.stderr})


async def _handle_stop_all(request: web.Request) -> web.Response:
    body = await _json_body(request)
    mode = body.get("mode", "pause")
    if mode == "kill":
        res = await asyncio.to_thread(_agent_stop, "--all")
        return web.json_response({"ok": res.returncode == 0, "mode": "kill"})
    # pause every running agent
    paused = []
    for agent in list_agents(request.app.get(_CONFIG_PATH)):
        if agent["state"] == "running":
            if await _ipc_to(agent["nick"], "pause"):
                paused.append(agent["nick"])
    return web.json_response({"ok": True, "mode": "pause", "paused": paused})


async def _handle_policy_get(request: web.Request) -> web.Response:
    nick = _require_nick(request.match_info["nick"])
    try:
        with open(policy_path_for(nick), encoding="utf-8") as handle:
            policy = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        policy = {}
    return web.json_response({"nick": nick, "policy": policy})


async def _handle_policy_put(request: web.Request) -> web.Response:
    nick = _require_nick(request.match_info["nick"])
    body = await _json_body(request)
    policy = body.get("policy")
    if not isinstance(policy, dict):
        return web.json_response({"error": "policy must be an object"}, status=400)
    # Atomic write via the broker's discipline.
    from culture.clients._perm_broker import _atomic_write_yaml  # noqa: PLC0415

    _atomic_write_yaml(policy_path_for(nick), policy)
    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------


@web.middleware
async def _loopback_guard(request: web.Request, handler):
    """Reject cross-origin / non-loopback requests (anti-DNS-rebinding).

    A direct fetch/curl sends no Origin; a browser page sends its Origin. We
    allow only loopback origins and a loopback Host header, so a malicious page
    that rebinds DNS to 127.0.0.1 (sending ``Origin: http://evil.com``) cannot
    drive the control plane.
    """
    origin = request.headers.get("Origin", "")
    if origin and not _LOOPBACK_RE.match(origin):
        raise web.HTTPForbidden(text="cross-origin requests are not allowed")
    host = request.headers.get("Host", "")
    if host and not _LOOPBACK_HOST_RE.match(host):
        raise web.HTTPForbidden(text="non-loopback Host is not allowed")
    return await handler(request)


def build_app(config_path: str | None = None) -> web.Application:
    app = web.Application(middlewares=[_loopback_guard])
    app[_CONFIG_PATH] = config_path
    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/agents", _handle_agents)
    app.router.add_get("/api/pending", _handle_pending)
    app.router.add_get("/api/stream/{kind}/{nick}", _handle_stream_jsonl)
    app.router.add_post("/api/approve", _handle_approve)
    app.router.add_post("/api/deny", _handle_deny)
    app.router.add_post("/api/pause", _handle_pause)
    app.router.add_post("/api/resume", _handle_resume)
    app.router.add_post("/api/close", _handle_close)
    app.router.add_post("/api/stop-all", _handle_stop_all)
    app.router.add_get("/api/policy/{nick}", _handle_policy_get)
    app.router.add_put("/api/policy/{nick}", _handle_policy_put)
    if os.path.isdir(_STATIC_DIR):
        app.router.add_static("/static/", _STATIC_DIR)
    return app


def serve_dashboard(
    host: str = "127.0.0.1",
    port: int = 8787,
    config_path: str | None = None,
    unsafe_bind: bool = False,
) -> None:
    """Run the dashboard. Refuses a non-loopback host unless unsafe_bind is set."""
    if host not in ("127.0.0.1", "localhost", "::1") and not unsafe_bind:
        raise ValueError(
            f"Refusing to bind dashboard to non-loopback host {host!r}. "
            "The dashboard can approve tool calls and kill agents — keep it on "
            "localhost. Pass unsafe_bind=True only if you understand the risk."
        )
    app = build_app(config_path)
    logger.info("Mission Control dashboard on http://%s:%d", host, port)
    web.run_app(app, host=host, port=port, print=None)
