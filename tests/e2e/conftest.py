"""E2E test harness fixtures (v9.1.9 — Plenty dogfood "no session has
gotten past brief" investigation).

This harness brings up a REAL pipeline end-to-end inside a single
pytest process so we can verify the operator flow against the actual
codebase, not against fabricated assumptions:

    in-process AgentIRCd  <--TCP-->  real `python -m culture bridge` subprocess
                                  <--TCP-->  raw IRCTestClient pretending to be a worker
                                  <--TCP-->  real `python -m culture boss brief` subprocess

Sandboxing per the design workflow's findings (16 critique blockers
caught a lot of leaks):

- ``CULTURE_HOME``: manifest, agent dirs, logs, audit, mission.
- ``HOME``: catches ``pidfile.PID_DIR = expanduser("~/.culture/pids")``
  which is computed at module IMPORT time. A subprocess that hasn't
  yet imported ``culture.pidfile`` will resolve ``~`` against our
  override, so PID files land under the sandbox.
- ``XDG_RUNTIME_DIR``: catches Unix-domain socket paths.
- OTEL/telemetry envs are scrubbed so a developer's real OTLP
  collector doesn't see test traffic.

The MVP harness DOES NOT spawn a real Claude worker daemon. The
``culture boss spawn`` path requires the Claude Agent SDK at import
time (``culture/clients/claude/agent_runner.py``), which can't run
in CI without an API key. Instead we mint a raw ``IRCTestClient``,
register it as the worker's IRC nick, and JOIN ``#task-<suffix>``.
That mirrors what a real worker's transport does on the wire and
is sufficient to drive the brief verification path
(`culture/cli/boss.py::_channel_members` does a WHO query and looks
for the worker's nick in the reply).

A follow-up patch should add ``CULTURE_SKIP_CLAUDE_SDK`` (or a
similar gate) on ``agent_runner`` so we can also exercise the real
spawn path — tracked in the design workflow's risk register.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio
import yaml

# ---------------------------------------------------------------------------
# Subprocess + env helpers
# ---------------------------------------------------------------------------


_SDK_STUB_INSTALLED = False


def _ensure_sdk_stub() -> None:
    """Install the Claude SDK stub once per pytest session so the
    e2e CLI subprocesses don't pull in the real SDK. The stub lives
    at ``tests/_sdk_stub.py`` (used by other test suites)."""
    global _SDK_STUB_INSTALLED
    if _SDK_STUB_INSTALLED:
        return
    from tests._sdk_stub import install_claude_sdk_stub

    install_claude_sdk_stub()
    _SDK_STUB_INSTALLED = True


def _scrub_telemetry_env(env: dict[str, str]) -> dict[str, str]:
    """Remove OTEL/Prometheus envs so test traffic doesn't reach the
    operator's real observability stack. Reliability critique #6
    flagged this as missing in the original blueprint."""
    pruned = dict(env)
    for k in list(pruned):
        ku = k.upper()
        if (
            ku.startswith("OTEL_")
            or ku.startswith("CULTURE_TELEMETRY")
            or ku.startswith("PROMETHEUS_")
            or ku.startswith("OTLP_")
        ):
            pruned.pop(k, None)
    return pruned


@pytest.fixture
def e2e_root() -> Path:
    """The sandbox root for one test. Everything the harness writes
    lives under this directory; nothing in the developer's real
    ``~/.culture`` is read or written.

    Uses a short path under ``/tmp/cu-<uuid>`` rather than pytest's
    ``tmp_path`` because macOS enforces a 104-char limit on
    AF_UNIX socket paths (``sun_path``) and pytest's default tmpdir
    lives under ``/private/tmp/claude-503/-Users-test-Documents-GitHub-...``
    which blows the limit before the harness can even bind the
    bridge's IPC socket. The harness's own teardown removes this
    dir; nothing is shared across tests.
    """
    import shutil
    import tempfile
    import uuid

    root = Path(tempfile.gettempdir()) / f"cu-{uuid.uuid4().hex[:8]}"
    (root / ".culture").mkdir(parents=True)
    (root / "run").mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def e2e_env(e2e_root: Path) -> dict[str, str]:
    """Subprocess env that isolates every culture-touching path
    found by the operator-flow discovery pass."""
    env = dict(os.environ)
    env["CULTURE_HOME"] = str(e2e_root / ".culture")
    # HOME override — load-bearing because ``culture/pidfile.py:9``
    # computes ``PID_DIR = expanduser("~/.culture/pids")`` at module
    # import time. A subprocess inherits this env before it imports
    # pidfile, so the constant resolves against our sandbox.
    env["HOME"] = str(e2e_root)
    env["USERPROFILE"] = str(e2e_root)  # Windows portability
    env["XDG_RUNTIME_DIR"] = str(e2e_root / "run")
    env["CULTURE_NICK"] = ""  # unset CULTURE_NICK from outer shell
    return _scrub_telemetry_env(env)


def _seed_server_yaml(e2e_root: Path, server_name: str, host: str, port: int) -> Path:
    """Write a server.yaml under the sandbox that matches the live
    IRCd's bound port + name. Tests that exercise drift call this
    with a NAME that disagrees with the IRCd's actual --name."""
    path = e2e_root / ".culture" / "server.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(
            {
                "server": {"name": server_name, "host": host, "port": port},
                "agents": {},
            },
            f,
        )
    return path


@pytest.fixture
def seed_server_yaml(e2e_root):
    """Returns the seed helper, parameterised so individual tests can
    pick the server name (for drift scenarios)."""

    def _seed(server_name: str, host: str, port: int) -> Path:
        return _seed_server_yaml(e2e_root, server_name, host, port)

    return _seed


# ---------------------------------------------------------------------------
# IRCd fixture — defer to the existing one from the top-level conftest
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def e2e_ircd(e2e_root, tmp_path, monkeypatch):
    """Spin up a fresh AgentIRCd on a random port for this test.

    Mirrors the top-level ``server`` fixture but renamed to avoid
    name collision with other tests' ``server`` symbol, and exposes
    the real bound port + host.

    **Load-bearing isolation** (v9.1.9 root-cause fix for "no session
    has gotten past brief"): the IRCd's task-channel ACL
    (``culture/agentirc/client.py::_load_owner_map`` /
    ``_load_role_map``) resolves the manifest path via
    ``culture_home()``, which reads ``os.environ["CULTURE_HOME"]``.
    Without setting it on pytest's process, the IN-PROCESS IRCd
    falls back to ``~/.culture/server.yaml`` — the OPERATOR'S real
    manifest — and rejects every test JOIN with "Not authorized to
    join task channel". Every PR shipped between v9.1.5 and v9.1.8
    passed unit tests precisely because the unit tests bypassed this
    code path. We must set the env on the pytest process itself, not
    just on subprocess env (which ``e2e_env`` already covers).

    We also flush the module-level owner_map / role_map caches at
    fixture entry AND exit so prior tests in the session don't bleed
    their cached operator-manifest data into ours, and vice versa.
    """
    _ensure_sdk_stub()
    from unittest.mock import patch as _patch

    from culture.agentirc import client as _ircd_client
    from culture.agentirc.config import ServerConfig, TelemetryConfig
    from culture.agentirc.ircd import IRCd

    # Bind CULTURE_HOME (+ symmetric HOME / XDG_RUNTIME_DIR) on the
    # pytest process so the in-process IRCd's ACL reads the sandbox
    # manifest, not the operator's real one.
    sandbox_home = e2e_root / ".culture"
    monkeypatch.setenv("CULTURE_HOME", str(sandbox_home))
    monkeypatch.setenv("HOME", str(e2e_root))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(e2e_root / "run"))
    # Flush stale caches BEFORE the IRCd reads the manifest.
    _ircd_client._invalidate_owner_map_cache()

    empty_bots = tmp_path / "_e2e_bots"
    empty_bots.mkdir()
    config = ServerConfig(
        name="testserv",
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "_e2e_audit")),
    )

    _BOTS_DIR_MANAGER = "culture.bots.bot_manager.BOTS_DIR"
    _BOTS_DIR_CONFIG = "culture.bots.config.BOTS_DIR"
    _BOTS_DIR_BOT = "culture.bots.bot.BOTS_DIR"

    try:
        with (
            _patch(_BOTS_DIR_MANAGER, empty_bots),
            _patch(_BOTS_DIR_CONFIG, empty_bots),
            _patch(_BOTS_DIR_BOT, empty_bots),
        ):
            ircd = IRCd(config)
            await ircd.start()
            # Capture the OS-assigned port back into config.
            ircd.config.port = ircd._server.sockets[0].getsockname()[1]
            yield ircd
            await ircd.stop()
    finally:
        # Flush again so post-test code that re-imports the module
        # doesn't see our sandboxed cache.
        _ircd_client._invalidate_owner_map_cache()


# ---------------------------------------------------------------------------
# Boss CLI helper — runs `python -m culture boss <args>` as a subprocess
# under the e2e_env sandbox.
# ---------------------------------------------------------------------------


@pytest.fixture
def boss_cli(e2e_env):
    """Return an ASYNC callable that runs ``python -m culture boss
    <args>`` with the sandboxed env. The harness uses async
    subprocess primitives because the in-process IRCd runs on the
    same event loop the test owns — a synchronous
    ``subprocess.run`` would block that loop, the IRCd would stop
    servicing accept(), and any in-subprocess code that connects
    back (observer registration, bridge IRC handshake) would time
    out. THAT is the failure the harness initially reproduced as a
    "Timed out waiting for server welcome" before we routed
    subprocess waits through asyncio.

    Returns a ``CompletedProcess``-like object with ``returncode``,
    ``stdout``, ``stderr`` fields for parity with the sync API.
    """

    async def _run(*args, timeout: float = 20.0, nick: str | None = None):
        env = dict(e2e_env)
        if nick is not None:
            env["CULTURE_NICK"] = nick
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "culture",
            "boss",
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            raise
        return subprocess.CompletedProcess(
            args=[sys.executable, "-m", "culture", "boss", *args],
            returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    return _run


@pytest.fixture
def agent_cli(e2e_env):
    """Same as boss_cli but for ``python -m culture agent``."""

    def _run(*args, timeout: float = 20.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "culture", "agent", *args],
            env=e2e_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    return _run


# ---------------------------------------------------------------------------
# Raw IRC test client — "fake worker"
#
# Instead of spawning a real Claude worker daemon (which requires the
# Claude Agent SDK and an API key — see the module docstring), the
# harness mints a raw TCP IRC client that registers under the
# worker's expected nick and JOINs its #task channel. From the
# brief's verification perspective (`_channel_members` does a WHO),
# this is indistinguishable from a real worker on the wire.
# ---------------------------------------------------------------------------


class _FakeWorker:
    """Raw IRC TCP client that pretends to be a worker."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        nick: str,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self.nick = nick
        self._received: list[str] = []
        self._read_task: asyncio.Task | None = None

    async def register(self) -> None:
        """Send NICK + USER, drain through 001 welcome."""
        self._writer.write(f"NICK {self.nick}\r\nUSER w 0 * :w\r\n".encode())
        await self._writer.drain()
        # Drain registration replies until 001 is seen.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            line = await asyncio.wait_for(self._reader.readline(), timeout=1.0)
            if not line:
                raise ConnectionError(f"IRCd closed mid-registration for {self.nick!r}")
            decoded = line.decode(errors="replace").strip()
            if " 001 " in decoded:
                return
        raise TimeoutError(f"Worker {self.nick!r} never saw RPL_WELCOME")

    async def join(self, channel: str) -> None:
        """JOIN <channel>; await the JOIN echo confirmation."""
        self._writer.write(f"JOIN {channel}\r\n".encode())
        await self._writer.drain()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            line = await asyncio.wait_for(self._reader.readline(), timeout=1.0)
            if not line:
                raise ConnectionError(f"IRCd closed mid-join for {self.nick!r}")
            decoded = line.decode(errors="replace").strip()
            if f"JOIN {channel}" in decoded or f"JOIN :{channel}" in decoded:
                self._start_background_read()
                return
        raise TimeoutError(f"Worker {self.nick!r} never confirmed JOIN {channel}")

    def _start_background_read(self) -> None:
        """Start pulling messages into ``self._received`` until close."""

        async def _pump():
            try:
                while True:
                    line = await self._reader.readline()
                    if not line:
                        return
                    self._received.append(line.decode(errors="replace").strip())
            except (asyncio.CancelledError, ConnectionError):
                return

        self._read_task = asyncio.create_task(_pump())

    def received_privmsgs(self) -> list[str]:
        """All PRIVMSG lines this fake worker has received so far."""
        return [m for m in self._received if " PRIVMSG " in m]

    async def close(self) -> None:
        if self._read_task is not None and not self._read_task.done():
            self._read_task.cancel()
        try:
            self._writer.write(b"QUIT :test done\r\n")
            await self._writer.drain()
        except (ConnectionError, OSError):
            pass
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except OSError:
            pass


@pytest_asyncio.fixture
async def bridge_join(e2e_root, e2e_ircd):
    """Tell the bridge to join a channel AND wait for the JOIN echo
    to be processed by the bridge before returning.

    Why the wait: the bridge's ``_ipc_irc_join`` handler returns
    ``ok=True`` as soon as ``JOIN <channel>\\r\\n`` is sent to the
    IRCd, but ``self.channels`` (the gate ``_ipc_irc_send`` checks
    before relaying a brief) is updated only when the IRCd ECHOES
    the JOIN back (``irc_transport.py::_on_join`` lines 640-658
    — v8.19.42 stopped doing optimistic membership tracking after a
    bug where rejected joins left the bridge thinking it was in
    channels it wasn't).

    Without this wait, a brief fired immediately after
    ``bridge_join`` hits the race: bridge sees ``ok=True`` from
    join, brief calls ``irc_send``, bridge replies ``ok=False,
    error='Not joined to <channel>'``, CLI prints the misleading
    "boss daemon not reachable over IPC" error.

    This is the same race the Plenty agent's "no session has
    gotten past brief" report is observing in production, and the
    misleading CLI error string is part of why the bug was hard
    to diagnose.
    """
    from culture.cli.shared.ipc import ipc_request

    async def _wait_membership(channel: str, target_nick: str, timeout: float) -> bool:
        """Poll WHO <channel> from a fresh observer until the target
        nick appears in the reply or the deadline fires."""
        import secrets as _secrets

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", e2e_ircd.config.port)
            except OSError:
                await asyncio.sleep(0.1)
                continue
            found = False
            try:
                # Pick a fresh probe nick per poll to avoid 433 collisions.
                probe = f"testserv-whop{_secrets.token_hex(2)}"
                writer.write(f"NICK {probe}\r\nUSER w 0 * :w\r\n".encode())
                await writer.drain()

                saw_welcome = False
                try:
                    for _ in range(20):
                        line = await asyncio.wait_for(reader.readline(), timeout=0.4)
                        if not line:
                            break
                        if " 001 " in line.decode(errors="replace"):
                            saw_welcome = True
                            break
                except asyncio.TimeoutError:
                    pass

                if not saw_welcome:
                    continue

                writer.write(f"WHO {channel}\r\n".encode())
                await writer.drain()
                try:
                    for _ in range(40):
                        line = await asyncio.wait_for(reader.readline(), timeout=0.4)
                        if not line:
                            break
                        decoded = line.decode(errors="replace")
                        if f" 352 {probe} " in decoded and target_nick in decoded:
                            found = True
                            break
                        if f" 315 {probe} " in decoded:
                            break
                except asyncio.TimeoutError:
                    pass
            finally:
                await _safe_close(writer)
            if found:
                return True
            await asyncio.sleep(0.2)
        return False

    async def _join(boss_nick: str, channel: str) -> dict | None:
        sock = str(e2e_root / "run" / f"culture-{boss_nick}.sock")
        resp = await ipc_request(sock, "irc_join", channel=channel)
        if not (resp and resp.get("ok")):
            return resp
        # Now wait for the JOIN echo to be processed by the bridge.
        joined = await _wait_membership(channel, boss_nick, timeout=5.0)
        if not joined:
            return {
                "ok": False,
                "error": (
                    f"bridge's irc_join returned ok=True but {boss_nick!r} "
                    f"never appeared in WHO {channel!r} within 5s — the "
                    f"JOIN echo hasn't been processed. This is the timing "
                    f"race a brief fired immediately after spawn hits in "
                    f"production."
                ),
            }
        return resp

    return _join


@pytest.fixture
def register_fake_worker(e2e_root):
    """Mimic the on-disk effect of ``culture boss spawn <suffix>``
    WITHOUT spawning the Claude SDK-dependent worker daemon.

    The real spawn flow:
        1. Writes ``<helpers>/<suffix>/culture.yaml`` with ``boss:`` and ``suffix:``.
        2. Adds the suffix to ``server.yaml`` manifest ``agents:``.
        3. Starts the worker daemon.

    For the e2e harness's purposes (verifying brief delivery on the
    wire), step 3 is the part that requires the SDK. Steps 1+2 give
    the boss-ownership check the data it needs to allow the brief
    through — the fake worker connection that ``fake_worker``
    mints handles the wire side.
    """

    def _register(suffix: str, boss_nick: str) -> None:
        culture_home = e2e_root / ".culture"
        helpers = culture_home / "helpers" / suffix
        helpers.mkdir(parents=True, exist_ok=True)
        with open(helpers / "culture.yaml", "w") as f:
            yaml.safe_dump(
                {
                    "suffix": suffix,
                    "backend": "claude",
                    "boss": boss_nick,
                    "channels": [f"#task-{suffix}"],
                },
                f,
            )
        # Append to the manifest server.yaml.
        server_yaml = culture_home / "server.yaml"
        with open(server_yaml) as f:
            data = yaml.safe_load(f) or {}
        agents = data.setdefault("agents", {})
        agents[suffix] = str(helpers)
        with open(server_yaml, "w") as f:
            yaml.safe_dump(data, f)
        # The IRCd's task-channel ACL caches owner_map/role_map for
        # OWNER_MAP_TTL_S (5s) keyed on the resolved manifest path.
        # Mutating the manifest must invalidate the cache or the
        # next JOIN sees stale "no such worker" data.
        from culture.agentirc.client import _invalidate_owner_map_cache

        _invalidate_owner_map_cache()

    return _register


@pytest_asyncio.fixture
async def fake_worker(e2e_ircd):
    """Factory: connect a raw IRC client and register it under a nick.
    The factory tracks every minted worker and tears them down at
    test exit so no fake-worker TCP connections leak across tests."""
    workers: list[_FakeWorker] = []

    async def _mint(nick: str, channel: str | None = None) -> _FakeWorker:
        reader, writer = await asyncio.open_connection("127.0.0.1", e2e_ircd.config.port)
        w = _FakeWorker(reader, writer, nick)
        await w.register()
        if channel:
            await w.join(channel)
        workers.append(w)
        return w

    yield _mint

    for w in workers:
        try:
            await w.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Real bridge subprocess
#
# Spawned via ``python -m culture bridge start <nick> --config <path>
# --foreground`` so the test owns the process lifecycle. Wait for the
# bridge to actually appear on the wire (WHOIS reply) before yielding
# — the bridge's own PID file appears BEFORE the IRC connection
# completes, per reliability-critique concern #5.
# ---------------------------------------------------------------------------


async def _wait_for_nick_on_mesh(
    host: str, port: int, target_nick: str, probe_nick: str, timeout: float = 8.0
) -> bool:
    """WHOIS <target_nick> from a fresh client; return True if 311
    (RPL_WHOISUSER) arrives before timeout. Returns False on timeout
    so the caller can attach a useful assertion message."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except OSError:
            await asyncio.sleep(0.1)
            continue
        try:
            writer.write(f"NICK {probe_nick}\r\nUSER p 0 * :p\r\n".encode())
            await writer.drain()
            # Wait for our own 001 before issuing WHOIS.
            saw_welcome = False
            for _ in range(20):
                line = await asyncio.wait_for(reader.readline(), timeout=0.5)
                if not line:
                    break
                if " 001 " in line.decode(errors="replace"):
                    saw_welcome = True
                    break
            if not saw_welcome:
                await _safe_close(writer)
                await asyncio.sleep(0.2)
                continue
            writer.write(f"WHOIS {target_nick}\r\n".encode())
            await writer.drain()
            for _ in range(20):
                line = await asyncio.wait_for(reader.readline(), timeout=0.5)
                if not line:
                    break
                decoded = line.decode(errors="replace")
                if f" 311 {probe_nick} {target_nick} " in decoded:
                    await _safe_close(writer)
                    return True
                if f" 318 {probe_nick} {target_nick} " in decoded:
                    # End of WHOIS without 311 — target not present.
                    break
                if f" 401 {probe_nick} {target_nick} " in decoded:
                    break
        finally:
            await _safe_close(writer)
        await asyncio.sleep(0.2)
    return False


async def _safe_close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


@pytest_asyncio.fixture
async def bridge(e2e_root, e2e_env, e2e_ircd, seed_server_yaml):
    """Real ``culture bridge`` subprocess, waited until on the mesh."""
    started: list[subprocess.Popen] = []

    async def _start(nick: str) -> subprocess.Popen:
        # Seed server.yaml so the bridge's config load picks up our
        # IRCd host+port (and our chosen server.name — must match
        # the IRCd's --name for happy-path tests).
        config_path = seed_server_yaml(e2e_ircd.config.name, "127.0.0.1", e2e_ircd.config.port)
        # NOTE: ``python -m culture.clients.bridge`` is the DAEMON
        # module — it runs in the foreground by definition. The
        # CLI wrapper ``culture bridge start`` is the one that
        # detaches with start_new_session=True. Don't pass
        # --foreground here; it isn't a flag the daemon parses.
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "culture.clients.bridge",
                "start",
                nick,
                "--config",
                str(config_path),
            ],
            env=e2e_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        started.append(proc)

        # Wait for the bridge to be on the wire — that's the
        # operationally meaningful "ready" signal, not just the
        # PID file or IPC socket.
        on_mesh = await _wait_for_nick_on_mesh(
            "127.0.0.1",
            e2e_ircd.config.port,
            target_nick=nick,
            probe_nick="testserv-probe",
            timeout=10.0,
        )
        if not on_mesh:
            # Capture stderr for a useful failure message.
            try:
                proc.terminate()
                try:
                    err_bytes = proc.stderr.read() if proc.stderr else b""
                except (OSError, ValueError):
                    err_bytes = b""
                err = err_bytes.decode(errors="replace")[:2000]
            except Exception:  # noqa: BLE001
                err = "<could not read bridge stderr>"
            raise RuntimeError(
                f"Bridge {nick!r} never appeared on the mesh within 10s. "
                f"Bridge stderr (last 2KB): {err!r}"
            )
        return proc

    yield _start

    for proc in started:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
