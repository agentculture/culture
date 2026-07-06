"""Render mesh overview as HTML and serve via HTTP.

Besides the HTML dashboard, the server exposes the resource view as
``GET /residents.json`` (docs/resident-presence.md, plan task t7) —
byte-compatible with ``culture residents --json`` via the one canonical
serializer in :mod:`culture_core.resource_view`.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import signal
import threading
import time
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import mistune

from culture_core.config import ServerConfig, ServerConnConfig
from culture_core.pidfile import (
    is_process_alive,
    read_pid,
    read_port,
    remove_pid,
    remove_port,
    write_pid,
    write_port,
)
from culture_core.resource_view import (
    PresenceUnsupportedError,
    Resident,
    fetch_residents_async,
    serialize_residents,
)

from .collector import collect_mesh_state
from .model import MeshState
from .renderer_text import render_text

# The overview dashboard is the only surface here that may be served over plain
# (clear-text) HTTP, and only because it binds to loopback: the socket is never
# reachable off-box. `_LOOPBACK_HOSTS` is the single source of truth for that
# coupling — `_dashboard_url` derives the scheme from it, so `http://` can never
# pair with a non-loopback host (anything else gets `https://`).
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def _dashboard_url(bind_host: str, port: int) -> str:
    """Build the dashboard URL — clear-text http is allowed only for a loopback bind."""
    scheme = "http" if bind_host in _LOOPBACK_HOSTS else "https"
    return f"{scheme}://localhost:{port}"


def _create_markdown() -> mistune.Markdown:
    """Create a mistune renderer that escapes raw HTML in input."""
    return mistune.create_markdown(escape=True, plugins=["table"])


def _load_css() -> str:
    """Load the cream stylesheet."""
    css_path = Path(__file__).parent / "web" / "style.css"
    return css_path.read_text()


def _inject_status_badges(html: str) -> str:
    """Replace status text in table cells with styled badges."""
    for status in ("active", "idle", "paused", "remote", "stopped", "circuit-open"):
        html = re.sub(
            rf"(<td>)\s*{status}\s*(</td>)",
            rf'\1<span class="status-{status}">{status}</span>\2',
            html,
        )
    return html


def _inject_partial_banner(html: str) -> str:
    """Style the partial-snapshot paragraph as a warning banner."""
    return html.replace(
        "<p><strong>Partial snapshot:</strong>",
        '<p class="partial-warning"><strong>Partial snapshot:</strong>',
        1,
    )


def render_html(
    mesh: MeshState,
    *,
    room_filter: str | None = None,
    agent_filter: str | None = None,
    message_limit: int = 4,
    refresh_interval: int = 5,
) -> str:
    """Render MeshState as a full HTML page."""
    md_text = render_text(
        mesh,
        room_filter=room_filter,
        agent_filter=agent_filter,
        message_limit=message_limit,
    )
    md = _create_markdown()
    body_html = md(md_text)
    body_html = _inject_status_badges(body_html)
    if mesh.failed_rooms:
        body_html = _inject_partial_banner(body_html)
    css = _load_css()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="{refresh_interval}">
    <title>culture overview</title>
    <style>{css}</style>
</head>
<body>
    <div class="refresh-indicator">live &mdash; refreshing every {refresh_interval}s</div>
    {body_html}
</body>
</html>"""


def _terminate_process(pid, timeout=2.0):
    """Send SIGTERM, poll until dead, fall back to SIGKILL. Returns True if killed."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (PermissionError, ProcessLookupError):
        return False
    steps = int(timeout / 0.1)
    for _ in range(steps):
        if not is_process_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        pass
    return not is_process_alive(pid)


def _stop_existing_overview(pid_name: str) -> None:
    """Kill a previous overview instance if running, clean up its files."""
    existing_pid = read_pid(pid_name)
    if existing_pid and is_process_alive(existing_pid):
        existing_port = read_port(pid_name)
        if not _terminate_process(existing_pid):
            remove_pid(pid_name)
            remove_port(pid_name)
            port_msg = f", port {existing_port}" if existing_port else ""
            print(
                f"Warning: could not stop previous overview (PID {existing_pid}{port_msg})",
                flush=True,
            )
            return
        remove_pid(pid_name)
        remove_port(pid_name)
        port_msg = f", port {existing_port}" if existing_port else ""
        print(
            f"Stopped previous overview for '{pid_name.removeprefix('overview-')}'"
            f" (PID {existing_pid}{port_msg})",
            flush=True,
        )
    elif existing_pid:
        remove_pid(pid_name)
        remove_port(pid_name)


class _OverviewHandler(SimpleHTTPRequestHandler):
    """HTTP handler that renders a live mesh overview on each request.

    Configuration is injected via class attributes set by the factory
    :func:`_make_overview_handler`.
    """

    irc_host: str = ""
    irc_port: int = 0
    server_name: str = ""
    room_filter: str | None = None
    agent_filter: str | None = None
    message_limit: int = 4
    refresh_interval: int = 5
    manifest_agents: list | None = None
    # Deterministic-time seam for the /residents.json payload, mirroring
    # serialize_residents(now=...): production leaves it None (current UTC
    # time); tests pin it to a fixed instant for byte-exact assertions.
    residents_now: datetime | None = None

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/residents.json":
            self._serve_residents()
            return
        mesh = asyncio.run(
            collect_mesh_state(
                host=self.irc_host,
                port=self.irc_port,
                server_name=self.server_name,
                message_limit=self.message_limit,
                manifest_agents=self.manifest_agents,
            )
        )
        html = render_html(
            mesh,
            room_filter=self.room_filter,
            agent_filter=self.agent_filter,
            message_limit=self.message_limit,
            refresh_interval=self.refresh_interval,
        )
        content = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _fetch_residents(self) -> list[Resident]:
        """Seam: query the bound culture server for the presence aggregation.

        Tests override this to inject fixtures; production queries the same
        server the overview renders, joining budgets from the same manifest.
        """
        config = ServerConfig(
            server=ServerConnConfig(name=self.server_name, host=self.irc_host, port=self.irc_port),
            agents=list(self.manifest_agents or []),
        )
        return asyncio.run(fetch_residents_async(config))

    def _serve_residents(self) -> None:
        """GET /residents.json — the resource view (plan task t7).

        Read-only, no side effects, and byte-compatible with
        ``culture residents --json``: both emit exactly ``json.dumps`` of
        :func:`culture_core.resource_view.serialize_residents`. Three
        response cases, never an unhandled traceback (the irc-lens console
        consumes this and must never see a bare 500):

        * presence supported          -> 200, canonical payload;
        * no PRESENCE surface         -> 200, ``supported: false`` payload
          (a presence-less mesh is a known state, not an error — pending
          agentirc#53);
        * culture server unreachable  -> 503, ``{code, message, remediation}``.
        """
        try:
            try:
                residents = self._fetch_residents()
                supported = True
            except PresenceUnsupportedError:
                residents, supported = [], False
        except OSError:
            # Covers ConnectionRefusedError, ConnectionError, TimeoutError —
            # all OSError subclasses. Same voice as the residents CLI error.
            self._send_json(
                503,
                {
                    "code": 503,
                    "message": "cannot connect to IRC server. Is the server running?",
                    "remediation": "start it with: culture server start",
                },
            )
            return
        self._send_json(200, serialize_residents(residents, supported, now=self.residents_now))

    def _send_json(self, status: int, payload: dict) -> None:
        content = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass  # Suppress request logging


def _make_overview_handler(
    host: str,
    port: int,
    server_name: str,
    room_filter: str | None,
    agent_filter: str | None,
    message_limit: int,
    refresh_interval: int,
    manifest_agents: list | None = None,
) -> type[_OverviewHandler]:
    """Return an _OverviewHandler subclass with config bound as class attrs."""
    return type(
        "_BoundOverviewHandler",
        (_OverviewHandler,),
        {
            "irc_host": host,
            "irc_port": port,
            "server_name": server_name,
            "room_filter": room_filter,
            "agent_filter": agent_filter,
            "message_limit": message_limit,
            "refresh_interval": refresh_interval,
            "manifest_agents": manifest_agents,
        },
    )


def _setup_signal_handlers(httpd: HTTPServer) -> None:
    """Register SIGTERM handler to gracefully shut down the server."""
    if threading.current_thread() is not threading.main_thread():
        return

    def _handle_term(_sig, _frame):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _handle_term)


def _cleanup_server(pid_name: str) -> None:
    """Remove PID and port files for the overview server."""
    remove_pid(pid_name)
    remove_port(pid_name)


def serve_web(
    host: str,
    port: int,
    server_name: str,
    *,
    room_filter: str | None = None,
    agent_filter: str | None = None,
    message_limit: int = 4,
    refresh_interval: int = 5,
    serve_port: int = 0,
    manifest_agents: list | None = None,
) -> None:
    """Start a local HTTP server serving the live overview."""
    pid_name = f"overview-{server_name}"
    _stop_existing_overview(pid_name)

    handler_cls = _make_overview_handler(
        host,
        port,
        server_name,
        room_filter,
        agent_filter,
        message_limit,
        refresh_interval,
        manifest_agents=manifest_agents,
    )
    # Bound to loopback only — see `_LOOPBACK_HOSTS` / `_dashboard_url`: this is
    # the one and only reason the dashboard may be served over plain HTTP.
    bind_host = "127.0.0.1"
    httpd = HTTPServer((bind_host, serve_port), handler_cls)
    actual_port = httpd.server_address[1]

    write_pid(pid_name, os.getpid())
    write_port(pid_name, actual_port)

    def cleanup():
        _cleanup_server(pid_name)

    atexit.register(cleanup)
    _setup_signal_handlers(httpd)

    dashboard_url = _dashboard_url(bind_host, actual_port)
    print(f"Overview dashboard: {dashboard_url}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        httpd.server_close()
        cleanup()
        atexit.unregister(cleanup)
