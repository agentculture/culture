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
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys

import yaml
from aiohttp import web

from culture.cli.shared.ipc import agent_socket_path, get_observer, ipc_request
from culture.cli.shared.process import is_process_alive
from culture.clients._audit import audit_path_for
from culture.clients._daemon_log import daemon_log_path_for
from culture.clients._perm_broker import (
    DecisionExistsError,
    InvalidRequestIdError,
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
_CHANNEL_READ_MAX = 200
_CHANNEL_READ_DEFAULT = 50

# Typed app keys.
_CONFIG_PATH: web.AppKey[str | None] = web.AppKey("config_path", object)  # type: ignore[arg-type]
_AUTH_TOKEN: web.AppKey[str | None] = web.AppKey("auth_token", object)  # type: ignore[arg-type]
_TRUSTED_HOSTS: web.AppKey[frozenset] = web.AppKey("trusted_hosts", frozenset)

_AUTH_COOKIE = "culture_dash"
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days — keep a phone logged in across sessions

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


# --- Auth (for remote access via a private tunnel; see docs/agentirc/dashboard.md) ---


def default_token_path() -> str:
    return os.path.join(culture_home(), "dashboard-token")


def load_or_create_token(path: str) -> str:
    """Return the dashboard token at *path*, creating a random one (0600) if absent."""
    try:
        with open(path, encoding="utf-8") as handle:
            existing = handle.read().strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Lost a create race; return the winner's token (regenerate only if the
        # existing file is empty/corrupt).
        with open(path, encoding="utf-8") as handle:
            winner = handle.read().strip()
        if winner:
            return winner
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


def _bare_host(value: str) -> str:
    """Host portion of an Origin or Host header (drop scheme + port)."""
    val = value.split("://", 1)[-1]
    if val.startswith("["):  # [::1]:port
        return val[1 : val.index("]")] if "]" in val else val
    return val.split(":", 1)[0]


def _origin_allowed(origin: str, trusted: frozenset) -> bool:
    # A same-origin GET often omits Origin; absence isn't a CSRF vector (state
    # changes are POSTs, which carry Origin), so an empty Origin is allowed.
    return not origin or bool(_LOOPBACK_RE.match(origin)) or _bare_host(origin) in trusted


def _host_allowed(host: str, trusted: frozenset) -> bool:
    # Real browsers always send Host; a missing Host only comes from a raw client.
    # Tolerate it only in pure-loopback mode — once a trusted host is configured
    # (remote access), a headerless request must not slip past the host gate.
    if not host:
        return not trusted
    return bool(_LOOPBACK_HOST_RE.match(host)) or _bare_host(host) in trusted


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
    """Most recent daemon-action for an agent (empty if none).

    Reads only the file's tail (last ~4 KiB), not the whole log — this runs for
    every agent on every ``/api/agents`` poll, so a full ``readlines()`` would
    block the event loop and scale with log size.
    """
    path = daemon_log_path_for(nick)
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in reversed(tail.splitlines()):
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


def _daemon_logged_idle(nick: str) -> bool:
    """True if the daemon recorded an ``idle_warning`` not superseded by a later
    ``engaged``/``agent_start`` — the daemon's authoritative idle decision.

    Reads the whole daemon-log (a small, lifecycle-only file: start/stop/engaged/
    idle/compact/crash — not per-turn), so the idle/clear ordering can never be
    truncated by a fixed tail window.
    """
    path = daemon_log_path_for(nick)
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return False
    seen_idle = False
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            action = json.loads(line).get("action", "")
        except json.JSONDecodeError:
            continue
        if action == "idle_warning":
            seen_idle = True
        elif action in ("agent_start", "engaged"):
            # A (re)start begins a fresh evaluation; `engaged` means the worker
            # produced a turn → either clears a prior idle_warning.
            seen_idle = False
    return seen_idle


def _is_idle(nick: str, state: str, boss: str) -> bool:
    """Reflect the daemon's authoritative idle decision: a running, boss-owned
    worker whose daemon recorded an ``idle_warning`` not superseded by a later
    ``engaged`` or ``agent_start``.

    Gating on ``boss`` (so bosses/standalone agents aren't flagged) and reading
    the daemon-log alone (never the audit's byte size) avoids false positives at
    startup, on a re-driven worker, and on a rotated/truncated audit.
    """
    if state != "running" or not boss:
        return False
    return _daemon_logged_idle(nick)


def _config_path_or_default(config_path: str | None) -> str:
    return config_path or os.path.join(culture_home(), "server.yaml")


def _agent_channel(nick: str, config_path: str | None) -> str:
    """The channel to talk to an agent on.

    Prefer the agent's private ``#task-*`` channel (the 1:1 the boss briefs in),
    else its first configured channel, else the ``#task-<suffix>`` convention.
    The result is an IRC target only — never a filesystem path — so the
    ``_require_nick`` guard on the caller is sufficient.
    """
    config = load_config_or_default(_config_path_or_default(config_path))
    for agent in config.agents:
        if agent.nick == nick:
            channels = [c for c in (getattr(agent, "channels", []) or []) if isinstance(c, str)]
            for channel in channels:
                if channel.startswith("#task-"):
                    return channel
            if channels:
                return channels[0]
            break
    suffix = nick.split("-", 1)[1] if "-" in nick else nick
    return f"#task-{suffix}"


def list_agents(config_path: str | None = None) -> list[dict]:
    """Programmatic agent grid (no CLI-text parsing)."""
    config = load_config_or_default(_config_path_or_default(config_path))
    pending = _pending_counts()
    rows = []
    for agent in config.agents:
        if getattr(agent, "archived", False):
            continue
        nick = agent.nick
        state = _agent_state(nick)
        rows.append(
            {
                "nick": nick,
                "state": state,
                "pending": pending.get(nick, 0),
                "last_action": _last_action(nick),
                "is_boss": "boss" in (getattr(agent, "tags", []) or []),
                "boss": getattr(agent, "boss", "") or "",
                "idle": _is_idle(nick, state, getattr(agent, "boss", "") or ""),
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


async def _handle_channel_read(request: web.Request) -> web.Response:
    """Recent messages in an agent's channel (both sides of the conversation)."""
    nick = _require_nick(request.match_info["nick"])
    try:
        limit = int(request.query.get("limit", str(_CHANNEL_READ_DEFAULT)))
    except (TypeError, ValueError):
        limit = _CHANNEL_READ_DEFAULT
    limit = max(1, min(limit, _CHANNEL_READ_MAX))
    config_path = request.app.get(_CONFIG_PATH)
    channel = _agent_channel(nick, config_path)
    try:
        observer = get_observer(_config_path_or_default(config_path))
        messages = await observer.read_channel(channel, limit)
    except Exception as exc:  # noqa: BLE001 — mesh unreachable → empty, not a 500
        logger.warning("channel read failed for %s (%s): %s", nick, channel, exc)
        messages = []
    return web.json_response({"nick": nick, "channel": channel, "messages": messages})


async def _handle_message(request: web.Request) -> web.Response:
    """Send a message to an agent's channel, prefixed so its mention fires.

    Mirrors ``culture boss brief``: the agent nick is prepended so the agent's
    mention detector triggers. Goes out over a transient observer connection, so
    no boss daemon is required — this session (or any operator) can talk to an
    agent straight from the dashboard.
    """
    body = await _json_body(request)
    nick = body.get("nick")
    if not nick:
        return web.json_response({"error": "missing nick"}, status=400)
    if not _valid_nick(nick):
        return web.json_response({"error": "invalid nick"}, status=400)
    text = str(body.get("text", "")).strip()
    if not text:
        return web.json_response({"error": "missing text"}, status=400)
    config_path = request.app.get(_CONFIG_PATH)
    channel = _agent_channel(nick, config_path)
    payload = f"@{nick} {text}"
    try:
        observer = get_observer(_config_path_or_default(config_path))
        await observer.send_message(channel, payload)
    except Exception as exc:  # noqa: BLE001 — surface a clean 502, not a 500
        logger.warning("message send failed for %s (%s): %s", nick, channel, exc)
        return web.json_response({"error": "could not reach the mesh"}, status=502)
    return web.json_response({"ok": True, "channel": channel})


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
    except InvalidRequestIdError:
        return web.json_response({"error": "invalid id"}, status=400)
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
    except InvalidRequestIdError:
        return web.json_response({"error": "invalid id"}, status=400)
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
    # The dashboard is the human/root authority — it may close ANY agent as a
    # safeguard. Strip CULTURE_NICK so the agent-stop guard treats this as the
    # human (root), never as an agent bound by the only-a-parent-closes rule.
    env = dict(os.environ)
    env.pop("CULTURE_NICK", None)
    return subprocess.run(
        [sys.executable, "-m", "culture", "agent", "stop", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
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


def _token_cookie_response(request: web.Request, token: str) -> web.Response:
    """Set the auth cookie from a ``?token=`` bootstrap, then redirect to a clean URL."""
    resp = web.HTTPFound(request.path)
    secure = request.secure or request.headers.get("X-Forwarded-Proto", "") == "https"
    resp.set_cookie(
        _AUTH_COOKIE,
        token,
        httponly=True,
        samesite="Strict",
        secure=secure,
        max_age=_COOKIE_MAX_AGE,
    )
    return resp


@web.middleware
async def _loopback_guard(request: web.Request, handler):
    """Origin/Host gate + optional token auth.

    DNS-rebinding defense: a browser page sends its Origin; we allow only loopback
    origins (and a loopback Host) so a malicious page that rebinds DNS to
    127.0.0.1 cannot drive the control plane. For remote access over a private
    tunnel, a configured *trusted host* (e.g. the Tailscale MagicDNS name) is also
    allowed — but only paired with a token: every request must then carry a valid
    ``culture_dash`` cookie (seeded once via ``?token=``), so exposure is safe even
    if the URL leaks. The cookie is ``SameSite=Strict``, which blocks CSRF.
    """
    trusted = request.app[_TRUSTED_HOSTS]
    if not _origin_allowed(request.headers.get("Origin", ""), trusted):
        raise web.HTTPForbidden(text="cross-origin requests are not allowed")
    if not _host_allowed(request.headers.get("Host", ""), trusted):
        raise web.HTTPForbidden(text="host not allowed")

    token = request.app[_AUTH_TOKEN]
    if token is not None:
        bootstrap = request.query.get("token")
        if bootstrap is not None:
            if not hmac.compare_digest(bootstrap, token):
                raise web.HTTPUnauthorized(text="invalid dashboard token")
            raise _token_cookie_response(request, token)
        cookie = request.cookies.get(_AUTH_COOKIE, "")
        if not (cookie and hmac.compare_digest(cookie, token)):
            if request.path.startswith("/api"):
                raise web.HTTPUnauthorized(text="missing or invalid dashboard token")
            raise web.HTTPUnauthorized(
                text="Unauthorized. Open this dashboard via the ?token=… URL "
                "printed by `culture dashboard --auth`."
            )
    return await handler(request)


def build_app(
    config_path: str | None = None,
    auth_token: str | None = None,
    trusted_hosts: object = None,
) -> web.Application:
    app = web.Application(middlewares=[_loopback_guard])
    app[_CONFIG_PATH] = config_path
    app[_AUTH_TOKEN] = auth_token
    app[_TRUSTED_HOSTS] = frozenset(trusted_hosts or ())
    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/agents", _handle_agents)
    app.router.add_get("/api/pending", _handle_pending)
    app.router.add_get("/api/channel/{nick}", _handle_channel_read)
    app.router.add_post("/api/message", _handle_message)
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
    *,
    auth_token: str | None = None,
    trusted_hosts: object = None,
) -> None:
    """Run the dashboard. Refuses a non-loopback host unless unsafe_bind is set.

    For remote access, keep the bind on loopback and put a private tunnel (e.g.
    ``tailscale serve``) in front; pass ``auth_token`` + the tunnel's hostname as
    a trusted host. Do NOT use ``unsafe_bind`` to expose it directly.
    """
    if host not in ("127.0.0.1", "localhost", "::1") and not unsafe_bind:
        raise ValueError(
            f"Refusing to bind dashboard to non-loopback host {host!r}. "
            "The dashboard can approve tool calls and kill agents — keep it on "
            "localhost. Pass unsafe_bind=True only if you understand the risk."
        )
    app = build_app(config_path, auth_token=auth_token, trusted_hosts=trusted_hosts)
    logger.info("Mission Control dashboard on http://%s:%d", host, port)
    web.run_app(app, host=host, port=port, print=None)
