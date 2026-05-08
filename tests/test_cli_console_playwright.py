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
    """Boot an in-process AgentIRC server on an OS-assigned port.

    Uses ``port=0`` and reads back the actual bound port from the running
    socket, mirroring ``tests/conftest.py``'s ``server`` fixture. Avoids
    the TOCTOU race where ``_free_port()`` picks a port that another
    process grabs before ``ircd.start()`` binds.
    """
    pytest.importorskip("agentirc")
    from agentirc.config import ServerConfig
    from agentirc.ircd import IRCd

    cfg = ServerConfig(name="lens-e2e", host="127.0.0.1", port=0, webhook_port=0)
    server = IRCd(cfg)

    import threading

    loop = asyncio.new_event_loop()
    started = threading.Event()
    start_error: list[Exception] = []

    def _runner():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.start())
        except Exception as exc:  # noqa: BLE001 — surface to main thread
            start_error.append(exc)
            started.set()
            return
        # Read back the OS-assigned port so callers can connect.
        cfg.port = server._server.sockets[0].getsockname()[1]
        started.set()
        loop.run_forever()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    if not started.wait(timeout=10):
        raise RuntimeError("agentirc_server fixture: server.start() did not signal ready in 10s")
    if start_error:
        raise start_error[0]

    yield cfg.port

    # Clean teardown: stop the IRCd on its loop, then stop+close the loop
    # and join the thread. Without this the server keeps sockets open and
    # pytest-asyncio emits a ResourceWarning.
    stop_done = threading.Event()

    async def _stop():
        try:
            await server.stop()
        finally:
            stop_done.set()

    asyncio.run_coroutine_threadsafe(_stop(), loop)
    stop_done.wait(timeout=5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def test_culture_console_serve_drives_help_view(agentirc_server, tmp_path):
    irc_port = agentirc_server
    web_port = _free_port()

    # irc-lens 0.5.x requires an explicit config file. Materialize a
    # starter dev-mode config in tmp so the serve subprocess doesn't try
    # to read ~/.config/irc-lens/config.yaml.
    config_path = tmp_path / "irc-lens-config.yaml"
    init_rc = subprocess.run(
        [
            sys.executable,
            "-m",
            "irc_lens",
            "config",
            "init",
            "--path",
            str(config_path),
        ],
        capture_output=True,
    ).returncode
    assert init_rc == 0, "irc-lens config init failed"

    culture_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "culture",
            "console",
            "serve",
            "--config",
            str(config_path),
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
        try:
            culture_proc.wait(timeout=5)
        finally:
            # Explicit pipe close — terminate()/wait() don't close the
            # subprocess.PIPE handles, which surfaces as a
            # ResourceWarning under pytest's strict warnings.
            for stream in (culture_proc.stdout, culture_proc.stderr):
                if stream is not None:
                    stream.close()
