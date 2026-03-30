"""Render mesh overview as HTML and serve via HTTP."""
from __future__ import annotations

import asyncio
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import html as html_mod

import mistune

from .collector import collect_mesh_state
from .model import MeshState
from .renderer_text import render_text


def _create_markdown() -> mistune.Markdown:
    """Create a mistune renderer that escapes raw HTML in input."""
    return mistune.create_markdown(escape=True)


def _load_css() -> str:
    """Load the cream stylesheet."""
    css_path = Path(__file__).parent / "web" / "style.css"
    return css_path.read_text()


def _inject_status_badges(html: str) -> str:
    """Replace status text in table cells with styled badges."""
    for status in ("active", "idle", "paused", "remote"):
        html = html.replace(
            f"<td>{status}</td>",
            f'<td><span class="status-{status}">{status}</span></td>',
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
    <title>agentirc overview</title>
    <style>{css}</style>
</head>
<body>
    <div class="refresh-indicator">live &mdash; refreshing every {refresh_interval}s</div>
    {body_html}
</body>
</html>"""


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
) -> None:
    """Start a local HTTP server serving the live overview."""

    class OverviewHandler(SimpleHTTPRequestHandler):
        def do_GET(self):
            mesh = asyncio.run(collect_mesh_state(
                host=host, port=port, server_name=server_name,
                message_limit=message_limit,
            ))
            html = render_html(
                mesh,
                room_filter=room_filter,
                agent_filter=agent_filter,
                message_limit=message_limit,
                refresh_interval=refresh_interval,
            )
            content = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format, *args):
            pass  # Suppress request logging

    httpd = HTTPServer(("127.0.0.1", serve_port), OverviewHandler)
    actual_port = httpd.server_address[1]
    print(f"Overview dashboard: http://localhost:{actual_port}")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
