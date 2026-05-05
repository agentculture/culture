"""Browser e2e for `culture console` against a real AgentIRC.

Note: this test exists to prove the integration end-to-end, not to
regress-test irc-lens's UI. If irc-lens's testids change, update them
here from irc-lens's own playwright fixtures.


Self-skips when Playwright isn't installed. To run locally:

    uv pip install playwright
    uv run playwright install chromium
    uv run pytest tests/test_cli_console_playwright.py -v

The test exercises the pure-passthrough form (`culture console serve
--host ... --port ... --nick ... --web-port ...`) so it doesn't need a
culture pidfile. AgentIRC is booted in-process via the public
`agentirc.ircd.IRCd` API.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from contextlib import closing

import pytest

# Skip the entire module when Playwright isn't available.
_pw = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright not installed; run `uv pip install playwright && uv run playwright install chromium`",
)
sync_playwright = _pw.sync_playwright
expect = _pw.expect


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"port {host}:{port} never opened within {timeout}s")


def _wait_for_port_or_proc_exit(host: str, port: int, proc, timeout: float = 15.0) -> None:
    """Like _wait_for_port, but raise immediately with stderr if proc dies."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            pass
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            stdout = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
            raise RuntimeError(
                f"culture console serve exited with rc={proc.returncode} before binding "
                f"{host}:{port}\nstdout:\n{stdout}\nstderr:\n{stderr}"
            )
        time.sleep(0.1)
    raise RuntimeError(f"port {host}:{port} never opened within {timeout}s")


@pytest.fixture
def agentirc_server():
    """Boot an in-process AgentIRC server on a free port."""
    pytest.importorskip("agentirc")
    from agentirc.config import ServerConfig
    from agentirc.ircd import IRCd

    port = _free_port()
    cfg = ServerConfig(name="lens-e2e", host="127.0.0.1", port=port)
    server = IRCd(cfg)

    loop = asyncio.new_event_loop()

    async def _run():
        await server.start()

    import threading

    def _runner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())
        loop.run_forever()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    _wait_for_port("127.0.0.1", port)

    yield port

    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=3)


def test_culture_console_serve_drives_help_view(agentirc_server):
    irc_port = agentirc_server
    web_port = _free_port()

    culture_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "culture",
            "console",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(irc_port),
            "--nick",
            "lens-e2e-bot",
            "--web-port",
            str(web_port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port_or_proc_exit("127.0.0.1", web_port, culture_proc)

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{web_port}/")

                indicator = page.locator('[data-testid="view-indicator"]')
                expect(indicator).to_be_attached(timeout=5000)

                chat_input = page.locator('[data-testid="chat-input"]')
                chat_input.fill("/help")
                chat_input.press("Enter")

                # SSE swap should land within ~1s; allow 5s for CI.
                expect(indicator).to_have_attribute("data-view", "help", timeout=5000)
            finally:
                browser.close()
    finally:
        culture_proc.terminate()
        culture_proc.wait(timeout=5)
