"""Render mesh overview as HTML and serve via HTTP."""

from __future__ import annotations

import asyncio
import atexit
import os
import re
import signal
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import mistune

from culture.pidfile import (
    is_process_alive,
    read_pid,
    read_port,
    remove_pid,
    remove_port,
    write_pid,
    write_port,
)

from .collector import collect_mesh_state
from .model import MeshState
from .renderer_text import render_text


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

    def do_GET(self):
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
    httpd = HTTPServer(("127.0.0.1", serve_port), handler_cls)
    actual_port = httpd.server_address[1]

    write_pid(pid_name, os.getpid())
    write_port(pid_name, actual_port)

    def cleanup():
        _cleanup_server(pid_name)

    atexit.register(cleanup)
    _setup_signal_handlers(httpd)

    dashboard_url = f"http://localhost:{actual_port}"  # NOSONAR — localhost-only dev URL
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
