"""The culture#506 success signal, scripted end to end (plan t16, claim c25).

"In a scripted test against a throwaway agentirc: (1) an external agent
appears in ``culture agents status`` within 5 s of its PRESENCE publish, and
``start --all`` forks 0 children for it; (2) 100% of attempts to take a
registered nick without the right secret are refused, whether the agent is
online or offline; (3) a JOIN outside the agent's grants is refused; (4)
after revoke, the live session closes in 5 s or less."

Everything runs against a REAL ``agentirc.ircd.IRCd`` on a random loopback
port with REAL TCP clients.  Culture is exercised through its own code paths:
the ``culture agents ...`` CLI handlers in ``culture_core/cli/agents.py``,
``culture_core.accounts`` and the status renderer in
``culture_core/cli/shared/display.py``.  The only stubs are the systemd hooks
of ``account activate`` (no unit exists in a test) and ``os.fork`` for
``start --all``.  HOME and GRANT_HOME live in tmp_path; secrets are read from
the temp grant store and never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from agentirc.config import ServerConfig as IrcServerConfig
from agentirc.config import TelemetryConfig
from agentirc.ircd import IRCd
from grant import store as grant_store

from culture_core import accounts
from culture_core.cli import agents as agent_mod
from culture_core.config import ServerConfig, ServerConnConfig, save_server_config
from tests.conftest import IRCTestClient

SERVER = "spark"
ALPHA = "spark-alpha"
GWEN = "spark-gwen"

STATUS_DEADLINE_S = 5.0  # spec (1)
REVOKE_DEADLINE_S = 5.0  # spec (4)
MIN_ATTACKS = 20  # spec (2): N >= 20


# -- environment --------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GRANT_HOME", str(home / "grant"))
    return home


@pytest.fixture(autouse=True)
def sasl_capable_client(monkeypatch):
    """The dev venv may hold a pre-SASL cultureagent; the version floor is
    exercised in tests/test_agent_account_activation.py."""
    real = accounts.installed_version
    monkeypatch.setattr(
        accounts,
        "installed_version",
        lambda d: accounts.MIN_CULTUREAGENT_SASL if d == "cultureagent" else real(d),
    )


class StubUnitOps:
    """A culture-owned agent whose unit wraps, restarts and reconnects fine."""

    def exists(self, nick):
        return True

    def client_version(self, nick):
        return accounts.MIN_CULTUREAGENT_SASL

    def wrap(self, nick, name):
        return True

    def unwrap(self, nick, name):
        return True

    def restart(self, nick):
        return True

    def verify(self, nick):
        return None


@pytest.fixture(autouse=True)
def stub_unit_ops(monkeypatch):
    monkeypatch.setattr(agent_mod, "_account_unit_ops", lambda config, args: StubUnitOps())


@pytest.fixture(autouse=True)
def no_pidfiles(monkeypatch):
    """Status must not look at the host's pidfiles: harness agents read as stopped."""
    monkeypatch.setattr("culture_core.cli.shared.display.read_pid", lambda name: None)


@pytest.fixture
def paths(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return tmp_path / "server.yaml", data_dir


@pytest_asyncio.fixture
async def ircd(paths, tmp_path):
    """Throwaway agentirc named ``spark`` on a random port; ``server.yaml``
    (culture's side) is written with the port the OS picked."""
    cfg_path, data_dir = paths
    cfg = IrcServerConfig(
        name=SERVER,
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        data_dir=str(data_dir),
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit")),
    )
    server = IRCd(cfg)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]
    server.config.port = port
    save_server_config(
        cfg_path, ServerConfig(server=ServerConnConfig(name=SERVER, host="127.0.0.1", port=port))
    )
    clients: list[IRCTestClient] = []
    server._test_clients = clients
    yield server
    for c in clients:  # Python 3.12 wait_closed waits on open connections
        c.writer.close()
    await server.stop()


def ns(paths, **kw):
    cfg_path, data_dir = paths
    return argparse.Namespace(config=str(cfg_path), data_dir=str(data_dir), **kw)


def make_agent_dir(tmp_path, suffix, backend="claude", owner=None, channels=("#general",)):
    d = tmp_path / f"agent-{suffix}"
    d.mkdir(exist_ok=True)
    lines = [f"suffix: {suffix}", f"backend: {backend}", f"channels: {list(channels)}"]
    if owner:
        lines.append(f"owner: {owner}")
    (d / "culture.yaml").write_text("\n".join(lines) + "\n")
    return d


def register(paths, tmp_path, suffix, **kw):
    d = make_agent_dir(tmp_path, suffix, **kw)
    agent_mod._cmd_register(argparse.Namespace(config=str(paths[0]), path=str(d), suffix=None))


def activate(paths, suffix):
    """Culture's real opt-in path: ``culture agents account activate`` (CLI
    handler -> accounts.activate: mint into grant, wrap, publish, restart,
    verify); only the systemd unit hooks are stubbed."""
    agent_mod._cmd_account(ns(paths, account_command="activate", nick=suffix, owner_ready=True))


def secret_of(nick):
    """The secret culture minted, read from the temp grant store (never printed)."""
    return grant_store.get_record(accounts.secret_name(nick)).value


def _report(msg: str) -> None:
    """Measured numbers go to the real stdout (visible with -s even when a test
    captures output with capsys)."""
    sys.__stdout__.write(f"[success-signal] {msg}\n")


# -- raw IRC helpers ----------------------------------------------------------


async def connect(server) -> IRCTestClient:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
    c = IRCTestClient(reader, writer)
    server._test_clients.append(c)
    return c


async def drain(c: IRCTestClient, window: float = 0.8) -> list[str]:
    """Everything the server sends within ``window`` s (stops early on close)."""
    lines: list[str] = []
    loop = asyncio.get_running_loop()
    end = loop.time() + window
    while (left := end - loop.time()) > 0:
        try:
            lines.append(await c.recv(timeout=left))
        except (asyncio.TimeoutError, TimeoutError, ConnectionError):
            break
    return lines


def welcomed(lines) -> bool:
    return any(" 001 " in x for x in lines)


async def sasl_login(server, nick, secret):
    """CAP/SASL PLAIN login the way the SASL-capable transport does it."""
    c = await connect(server)
    await c.send("CAP LS 302")
    await c.send(f"NICK {nick}")
    await c.send(f"USER {nick} 0 * :{nick}")
    await c.send("CAP REQ :sasl")
    await c.recv_until(" ACK ")
    await c.send("AUTHENTICATE PLAIN")
    await c.recv_until("AUTHENTICATE +")
    payload = base64.b64encode(f"\0{nick}\0{secret}".encode()).decode()
    await c.send(f"AUTHENTICATE {payload}")
    lines: list[str] = []
    try:
        async with asyncio.timeout(3):
            while True:
                line = await c.recv()
                lines.append(line)
                if " 903 " in line:
                    await c.send("CAP END")
                if " 001 " in line or " 904 " in line:
                    break
    except (ConnectionError, TimeoutError, asyncio.TimeoutError):
        pass
    return c, lines


async def login_ok(server, nick) -> IRCTestClient:
    c, lines = await sasl_login(server, nick, secret_of(nick))
    assert welcomed(lines), lines
    return c


async def expect_closed(c: IRCTestClient, timeout: float):
    """Seconds until the server closes ``c``'s socket, or None if it stays open."""
    t0 = time.monotonic()
    try:
        async with asyncio.timeout(timeout):
            while True:
                await c.recv(timeout=timeout)
    except ConnectionError:
        return time.monotonic() - t0
    except (TimeoutError, asyncio.TimeoutError):
        return None


# -- (1) status + start --all -------------------------------------------------


def _status_rows(out: str) -> dict[str, list[str]]:
    return {line.split()[0]: line.split() for line in out.splitlines() if line.startswith("spark-")}


@pytest.mark.asyncio
async def test_1_external_agent_in_status_within_5s_and_start_all_forks_none(
    ircd, paths, tmp_path, monkeypatch, capsys
):
    register(paths, tmp_path, "gwen", backend="external", owner="gwen.service")
    register(paths, tmp_path, "ada")  # a harness agent, so --all has something to fork
    # The external agent connects as itself (legacy, no account), sets +A and
    # publishes PRESENCE; timing starts at the publish.
    gwen = await connect(ircd)
    await gwen.send(f"NICK {GWEN}")
    await gwen.send(f"USER {GWEN} 0 * :{GWEN}")
    assert welcomed(await drain(gwen, 0.5))
    await gwen.send(f"MODE {GWEN} +A")
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_publish = time.monotonic()
    await gwen.send("PRESENCE :" + json.dumps({"state": "idle", "since": since}))

    # `culture agents status`, polled until it shows the published state.
    row = None
    while time.monotonic() - t_publish <= STATUS_DEADLINE_S:
        await asyncio.to_thread(
            agent_mod._cmd_status, ns(paths, nick=None, all=True, full=False, quiet=False)
        )
        rows = _status_rows(capsys.readouterr().out)
        if rows.get(GWEN, [None, None])[1] == "idle":
            row = rows[GWEN]
            break
        await asyncio.sleep(0.1)
    latency = time.monotonic() - t_publish
    _report(f"status latency after PRESENCE publish: {latency:.3f}s")
    assert row is not None, "spark-gwen never showed as idle in `culture agents status`"
    assert latency <= STATUS_DEADLINE_S

    # `start --all` forks only the harness agent: 0 children for spark-gwen.
    forks: list[int] = []
    monkeypatch.setattr(agent_mod, "_probe_server_connection", lambda *a: None)
    monkeypatch.setattr(agent_mod.os, "fork", lambda: forks.append(1) or 4242)
    monkeypatch.setattr(agent_mod, "write_pid", lambda *a: None)
    # the account for spark-ada is irrelevant to this part: no secret to inject
    agent_mod._cmd_start(ns(paths, all=True, nick=None, foreground=False))
    out = capsys.readouterr().out
    assert len(forks) == 1  # spark-ada only
    assert f"{GWEN}: skipped — owned by gwen.service" in out
    assert "Started spark-ada (PID 4242)" in out
    assert f"Started {GWEN}" not in out
    gwen.writer.close()

    # and with ONLY the external agent registered, start --all forks 0 children
    forks.clear()
    agent_mod._cmd_unregister(ns(paths, target="spark-ada"))
    agent_mod._cmd_start(ns(paths, all=True, nick=None, foreground=False))
    assert forks == []


# -- (2) 100% of nick-takeover attempts refused ------------------------------


async def _attack(server, kind: str, nick: str, wrong: str):
    """One takeover attempt; returns (refused, detail)."""
    c = await connect(server)
    if kind == "no-sasl":
        await c.send(f"NICK {nick}")
        await c.send(f"USER x 0 * :x")
    elif kind == "wrong-secret":
        c2, lines = await sasl_login(server, nick, wrong)
        return not welcomed(lines), lines
    elif kind == "bare-cap-req":
        await c.send("CAP LS 302")
        await c.send(f"NICK {nick}")
        await c.send("USER x 0 * :x")
        await c.send("CAP REQ :sasl")
        await c.send("CAP END")  # never AUTHENTICATEs
    elif kind == "empty-secret":
        c2, lines = await sasl_login(server, nick, "")
        return not welcomed(lines), lines
    elif kind == "pre-registration-privmsg":
        await c.send("PRIVMSG #general :hello")
        await c.send("JOIN #general")
        lines = await drain(c, 0.6)
        ok = (not welcomed(lines)) and any(" 451 " in x for x in lines)
        return ok, lines
    else:  # pragma: no cover
        raise AssertionError(kind)
    lines = await drain(c, 0.8)
    return not welcomed(lines), lines


async def _attack_round(server, wrong_secrets):
    results = []
    variants = [ALPHA, "spark-Alpha", "SPARK-ALPHA"]
    for nick in variants:
        for kind in ("no-sasl", "bare-cap-req", "empty-secret"):
            results.append((kind, nick, *(await _attack(server, kind, nick, ""))))
        for i, wrong in enumerate(wrong_secrets):
            results.append(
                ("wrong-secret", nick, *(await _attack(server, "wrong-secret", nick, wrong)))
            )
    for _ in range(2):
        results.append(
            (
                "pre-registration-privmsg",
                ALPHA,
                *(await _attack(server, "pre-registration-privmsg", ALPHA, "")),
            )
        )
    return results


@pytest.mark.asyncio
async def test_2_every_nick_takeover_without_the_secret_is_refused_online_or_offline(
    ircd, paths, tmp_path
):
    # Activation path used: the real opt-in CLI handler `culture agents account
    # activate spark-alpha` -> accounts.activate (mint into grant, wrap, publish,
    # restart, verify) with only the systemd unit hooks stubbed (StubUnitOps).
    register(paths, tmp_path, "alpha")
    activate(paths, "alpha")
    rec = accounts.find(ALPHA, SERVER, paths[1])
    assert rec is not None and rec.state == "active"
    real = secret_of(ALPHA)
    # nearby-but-wrong secrets, including a prefix of the real one and its hash
    wrong_secrets = ["wrong", real[:-1], real + "x", rec.secret_sha256]
    await asyncio.sleep(1.5)  # let the server pick the account up (it reloads accounts.json)

    # ---- agent OFFLINE ----
    offline = await _attack_round(ircd, wrong_secrets)
    # ---- agent ONLINE (real SASL login with the real secret) ----
    # agentirc rate-limits failed SASL per (peer address, account): 10 per 60 s,
    # loopback not exempt.  The offline barrage above exhausted that budget for
    # 127.0.0.1, so the legit agent (same address here) would be locked out for
    # the rest of the window.  Model "the window has passed" by clearing the
    # limiter's state; the attacks themselves run against the default limits.
    ircd.auth_limiter._events.clear()
    alpha = await login_ok(ircd, ALPHA)
    online = await _attack_round(ircd, wrong_secrets)
    # the real session is unaffected by the attacks
    await alpha.send("PING :still-here")
    assert any("still-here" in x for x in await drain(alpha, 1.0))
    # and the real secret still works for the legit agent only (a second login
    # while online is the legit agent reconnecting, not part of the attack count)
    all_results = offline + online
    failures = [(k, n, d[:3]) for k, n, ok, d in all_results if not ok]
    _report(
        f"takeover attempts: offline={len(offline)} online={len(online)} "
        f"refused={len(all_results) - len(failures)}/{len(all_results)}"
    )
    assert len(offline) >= MIN_ATTACKS and len(online) >= MIN_ATTACKS
    assert not failures, failures  # 100% refused
    # no refused attempt leaked the secret back
    assert not any(real in x for _, _, _, d in all_results for x in d)
    alpha.writer.close()


# -- (3) JOIN outside the agent's grants is refused -------------------------


@pytest.mark.asyncio
async def test_3_join_outside_grants_refused_and_read_only_cannot_speak(ircd, paths, tmp_path):
    register(paths, tmp_path, "alpha", channels=("#speak",))
    activate(paths, "alpha")
    # grants beyond the default (speak on the agent's own channels)
    accounts.set_grants(ALPHA, SERVER, {"#speak": "speak", "#ro": "read"}, data_dir=paths[1])
    await asyncio.sleep(1.5)
    # an unrelated, ordinary observer sitting in the read-only channel
    obs = await connect(ircd)
    await obs.send("NICK spark-obs")
    await obs.send("USER obs 0 * :obs")
    assert welcomed(await drain(obs, 0.5))
    await obs.send("JOIN #ro")
    await drain(obs, 0.3)

    alpha = await login_ok(ircd, ALPHA)
    # granted speak channel: JOIN succeeds, PRIVMSG is delivered
    await alpha.send("JOIN #speak")
    lines = await drain(alpha, 0.8)
    assert any(" JOIN " in x and "#speak" in x for x in lines), lines
    # ungranted channel: refused with a numeric, and alpha is not in it
    await alpha.send("JOIN #forbidden")
    lines = await drain(alpha, 0.8)
    refused = [
        x
        for x in lines
        if any(f" {n} " in x for n in ("403", "404", "473", "474", "475", "477", "482", "489"))
    ]
    _report(f"ungranted JOIN reply: {refused}")
    assert refused, lines
    assert not any(" JOIN " in x and "#forbidden" in x for x in lines), lines
    # read-only grant: JOIN ok, PRIVMSG refused and never delivered
    await alpha.send("JOIN #ro")
    lines = await drain(alpha, 0.8)
    assert any(" JOIN " in x and "#ro" in x for x in lines), lines
    await drain(obs, 0.3)
    await alpha.send("PRIVMSG #ro :should not be delivered")
    lines = await drain(alpha, 0.8)
    refused = [x for x in lines if any(f" {n} " in x for n in ("404", "482", "489"))]
    _report(f"read-only PRIVMSG reply: {refused}")
    assert refused, lines
    assert not any("should not be delivered" in x for x in await drain(obs, 0.8))
    # and the speak-granted channel really does deliver
    await obs.send("JOIN #speak")
    await drain(obs, 0.3)
    await alpha.send("PRIVMSG #speak :delivered")
    assert any("delivered" in x for x in await drain(obs, 1.0))
    alpha.writer.close()
    obs.writer.close()


# -- (4) revoke closes the live session in <= 5 s ---------------------------


@pytest.mark.asyncio
async def test_4_revoke_closes_the_live_session_within_5s(ircd, paths, tmp_path, capsys):
    register(paths, tmp_path, "alpha")
    activate(paths, "alpha")
    await asyncio.sleep(1.5)
    alpha = await login_ok(ircd, ALPHA)
    secret = secret_of(ALPHA)
    capsys.readouterr()

    t0 = time.monotonic()
    # the real CLI handler: `culture agents account revoke spark-alpha --data-dir <tmp>`
    await asyncio.to_thread(agent_mod._cmd_account, ns(paths, account_command="revoke", nick=ALPHA))
    elapsed = await expect_closed(alpha, REVOKE_DEADLINE_S + 1.0)
    total = time.monotonic() - t0
    _report(f"revoke -> socket closed: {total:.3f}s (CLI call included)")
    assert elapsed is not None, "session still open 6 s after revoke"
    assert total <= REVOKE_DEADLINE_S
    assert accounts.find(ALPHA, SERVER, paths[1]).state == "revoked"
    assert secret not in capsys.readouterr().out
    # the nick stays reserved: the old secret no longer logs in
    _, lines = await sasl_login(ircd, ALPHA, secret)
    assert not welcomed(lines)
