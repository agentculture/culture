"""Account CLI verbs and lifecycle (culture#506 t14, opt-in per d7).

Accounts are opt-in: ``register`` only adds the manifest entry and the
``activate`` verb creates the account (its unit hooks are stubbed here; the
ordering/rollback is covered in tests/test_account_activation_hardening.py).
Unit tests drive the handlers with HOME/GRANT_HOME in tmp_path; the
integration tests run a real AgentIRC (IRCd on a random port) and real TCP
clients authenticating with SASL PLAIN.  Secrets are read from the temp grant
store inside the test and never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time

import pytest
import pytest_asyncio
from agentirc.config import ServerConfig as IrcServerConfig
from agentirc.config import TelemetryConfig
from agentirc.ircd import IRCd
from grant import store as grant_store

from culture_core import accounts
from culture_core.cli import agents as agent_mod
from culture_core.cli._errors import CultureError
from culture_core.config import ServerConfig, ServerConnConfig, save_server_config
from tests.conftest import IRCTestClient

SERVER = "spark"


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GRANT_HOME", str(home / "grant"))
    return home


@pytest.fixture(autouse=True)
def sasl_capable_client(monkeypatch):
    """The dev venv may hold a pre-SASL cultureagent; the floor is exercised
    explicitly in tests/test_agent_account_activation.py."""
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


@pytest.fixture
def env(tmp_path):
    """server.yaml + data dir + helper to create agent dirs."""
    cfg_path = tmp_path / "server.yaml"
    save_server_config(cfg_path, ServerConfig(server=ServerConnConfig(name=SERVER)))
    data_dir = tmp_path / "data"
    return cfg_path, data_dir


def make_agent(tmp_path, suffix, backend="claude", owner=None):
    d = tmp_path / f"agent-{suffix}"
    d.mkdir(exist_ok=True)
    lines = [f"suffix: {suffix}", f"backend: {backend}", "channels: ['#general']"]
    if owner:
        lines.append(f"owner: {owner}")
    (d / "culture.yaml").write_text("\n".join(lines) + "\n")
    return d


def ns(env, **kw):
    cfg_path, data_dir = env
    return argparse.Namespace(config=str(cfg_path), data_dir=str(data_dir), **kw)


def register(env, tmp_path, suffix, activate=True, **kw):
    """Register the agent and (by default) opt it in to an account."""
    d = make_agent(tmp_path, suffix, **kw)
    cfg_path, _ = env
    agent_mod._cmd_register(argparse.Namespace(config=str(cfg_path), path=str(d), suffix=None))
    if activate:
        agent_mod._cmd_account(ns(env, account_command="activate", nick=suffix, owner_ready=True))


def secret_of(nick):
    return grant_store.get_record(accounts.secret_name(nick)).value


def account(env, nick):
    return accounts.find(nick, SERVER, env[1])


def account_cmd(env, verb, nick=None):
    agent_mod._cmd_account(ns(env, account_command=verb, nick=nick))


# -- unit ------------------------------------------------------------------


def test_register_alone_creates_no_account(env, tmp_path):
    register(env, tmp_path, "gwen", activate=False)
    assert account(env, "spark-gwen") is None


def test_activate_creates_account_and_never_prints_secret(env, tmp_path, capsys):
    register(env, tmp_path, "gwen")
    rec = account(env, "spark-gwen")
    assert rec.state == "active" and rec.role == "resident"
    assert rec.grants == {"#general": "speak"}
    out = capsys.readouterr().out
    assert "Account active: spark-gwen" in out
    assert secret_of("spark-gwen") not in out


def test_archive_suspends_and_unarchive_restores(env, tmp_path):
    register(env, tmp_path, "gwen")
    agent_mod._cmd_archive(ns(env, nick="spark-gwen", reason=""))
    assert account(env, "spark-gwen").state == "suspended"
    agent_mod._cmd_unarchive(ns(env, nick="spark-gwen"))
    assert account(env, "spark-gwen").state == "active"


def test_delete_leaves_tombstone_and_drops_secret(env, tmp_path):
    register(env, tmp_path, "gwen")
    agent_mod._cmd_delete(ns(env, nick="spark-gwen"))
    assert account(env, "spark-gwen").state == "revoked"
    with pytest.raises(grant_store.NotFoundError):
        grant_store.get_record(accounts.secret_name("spark-gwen"))


def test_unregister_leaves_tombstone_and_activate_restores(env, tmp_path):
    register(env, tmp_path, "gwen")
    old = account(env, "spark-gwen").secret_sha256
    agent_mod._cmd_unregister(ns(env, target="spark-gwen"))
    assert account(env, "spark-gwen").state == "revoked"
    register(env, tmp_path, "gwen", activate=False)  # re-register: tombstone stays
    assert account(env, "spark-gwen").state == "revoked"
    agent_mod._cmd_account(  # explicit activation reactivates
        ns(env, account_command="activate", nick="gwen", owner_ready=False)
    )
    rec = account(env, "spark-gwen")
    assert rec.state == "active" and rec.secret_sha256 != old
    assert secret_of("spark-gwen")


def test_revoke_keeps_tombstone(env, tmp_path):
    register(env, tmp_path, "gwen")
    account_cmd(env, "revoke", "gwen")
    assert account(env, "spark-gwen").state == "revoked"


def test_disable_removes_only_that_entry(env, tmp_path):
    register(env, tmp_path, "gwen")
    register(env, tmp_path, "bob")
    account_cmd(env, "disable", "spark-gwen")
    assert account(env, "spark-gwen") is None
    assert account(env, "spark-bob").state == "active"
    assert secret_of("spark-bob")


def test_rotate_external_prints_owner_instruction(env, tmp_path, capsys):
    register(env, tmp_path, "gwen", backend="external", owner="gwen.service")
    old = secret_of("spark-gwen")
    capsys.readouterr()
    account_cmd(env, "rotate", "spark-gwen")
    new = secret_of("spark-gwen")
    out = capsys.readouterr().out
    assert new != old
    assert "gwen.service" in out and "restart" in out
    assert new not in out and old not in out


def test_rotate_restarts_installed_unit(env, tmp_path, monkeypatch, capsys):
    register(env, tmp_path, "bob")
    calls = []
    monkeypatch.setattr(
        "culture_core.persistence.restart_service", lambda n: calls.append(n) or True
    )
    account_cmd(env, "rotate", "bob")
    assert calls == ["culture-agent-spark-bob"]
    assert "Restarted culture-agent-spark-bob" in capsys.readouterr().out


def test_rotate_without_unit_says_restart_daemon(env, tmp_path, monkeypatch, capsys):
    register(env, tmp_path, "bob")
    monkeypatch.setattr("culture_core.persistence.restart_service", lambda n: False)
    account_cmd(env, "rotate", "bob")
    assert "restart the running daemon" in capsys.readouterr().out


def test_unknown_account_errors(env):
    with pytest.raises(CultureError):
        account_cmd(env, "rotate", "nobody")


def test_show_and_list_never_print_secret(env, tmp_path, capsys):
    register(env, tmp_path, "gwen")
    capsys.readouterr()
    account_cmd(env, "show", "gwen")
    account_cmd(env, "list")
    out = capsys.readouterr().out
    assert "spark-gwen" in out and "state=active" in out
    assert secret_of("spark-gwen") not in out
    assert account(env, "spark-gwen").secret_sha256 not in out


def test_revoke_helper_keeps_entry_and_audits(env, tmp_path):
    register(env, tmp_path, "gwen")
    accounts.revoke("spark-gwen", SERVER, env[1])
    lines = [json.loads(x) for x in accounts.audit_path(env[1]).read_text().splitlines()]
    assert lines[-1]["action"] == "revoke"
    assert "secret_sha256" not in json.dumps(lines)


def test_foreign_nick_rejected(env):
    with pytest.raises(accounts.AccountError):
        accounts.revoke("thor-x", SERVER, env[1])


# -- integration with a real AgentIRC --------------------------------------


@pytest_asyncio.fixture
async def ircd(env, tmp_path):
    cfg = IrcServerConfig(
        name=SERVER,
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        data_dir=str(env[1]),
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit")),
    )
    env[1].mkdir(parents=True, exist_ok=True)
    server = IRCd(cfg)
    await server.start()
    server.config.port = server._server.sockets[0].getsockname()[1]
    clients: list[IRCTestClient] = []
    server._test_clients = clients
    yield server
    for c in clients:
        c.writer.close()
    await server.stop()


async def connect(server) -> IRCTestClient:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
    c = IRCTestClient(reader, writer)
    server._test_clients.append(c)
    return c


async def sasl_login(server, nick, secret):
    """Return (client, lines) after attempting SASL PLAIN + registration."""
    c = await connect(server)
    await c.send("CAP LS 302")
    await c.send(f"NICK {nick}")
    await c.send("USER u 0 * :u")
    await c.send("CAP REQ :sasl")
    await c.recv_until(" ACK ")
    await c.send("AUTHENTICATE PLAIN")
    await c.recv_until("AUTHENTICATE +")
    payload = base64.b64encode(f"\0{nick}\0{secret}".encode()).decode()
    await c.send(f"AUTHENTICATE {payload}")
    lines = []
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


async def wait_closed(c, timeout):
    t0 = time.monotonic()
    try:
        async with asyncio.timeout(timeout):
            while True:
                await c.recv(timeout=timeout)
    except ConnectionError:
        return time.monotonic() - t0
    except (TimeoutError, asyncio.TimeoutError):
        return None


async def plain_register(server, nick):
    c = await connect(server)
    await c.send(f"NICK {nick}")
    await c.send("USER u 0 * :u")
    try:
        async with asyncio.timeout(3):
            lines = []
            while True:
                line = await c.recv()
                lines.append(line)
                if " 001 " in line or " 433 " in line or " 462 " in line:
                    break
    except (ConnectionError, TimeoutError, asyncio.TimeoutError):
        pass
    return lines


def welcomed(lines):
    return any(" 001 " in x for x in lines)


@pytest.mark.asyncio
async def test_revoke_closes_live_session_within_5s(env, tmp_path, ircd):
    register(env, tmp_path, "gwen")
    c, lines = await sasl_login(ircd, "spark-gwen", secret_of("spark-gwen"))
    assert welcomed(lines), lines
    account_cmd(env, "revoke", "gwen")
    elapsed = await wait_closed(c, 5.0)
    assert elapsed is not None and elapsed < 5.0


@pytest.mark.asyncio
async def test_rotate_old_secret_fails_new_works(env, tmp_path, ircd):
    register(env, tmp_path, "gwen")
    old = secret_of("spark-gwen")
    account_cmd(env, "rotate", "gwen")
    new = secret_of("spark-gwen")
    assert new != old
    _, lines = await sasl_login(ircd, "spark-gwen", old)
    assert any(" 904 " in x for x in lines) and not welcomed(lines), lines
    _, lines = await sasl_login(ircd, "spark-gwen", new)
    assert welcomed(lines), lines


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["archive", "delete", "unregister"])
async def test_nick_stays_reserved(env, tmp_path, ircd, how):
    register(env, tmp_path, "gwen")
    secret = secret_of("spark-gwen")
    if how == "archive":
        agent_mod._cmd_archive(ns(env, nick="spark-gwen", reason=""))
    elif how == "delete":
        agent_mod._cmd_delete(ns(env, nick="spark-gwen"))
    else:
        agent_mod._cmd_unregister(ns(env, target="spark-gwen"))
    # the SASL secret fails (for delete/unregister the grant secret is gone,
    # but the old value must be rejected by the server too)
    _, lines = await sasl_login(ircd, "spark-gwen", secret)
    assert any(" 904 " in x for x in lines) and not welcomed(lines), lines
    # someone else without SASL cannot take the nick
    lines = await plain_register(ircd, "spark-gwen")
    assert not welcomed(lines), lines
    # unarchive / explicit re-activation restores
    if how == "archive":
        agent_mod._cmd_unarchive(ns(env, nick="spark-gwen"))
    else:
        register(env, tmp_path, "gwen")
    _, lines = await sasl_login(ircd, "spark-gwen", secret_of("spark-gwen"))
    assert welcomed(lines), lines


@pytest.mark.asyncio
async def test_disable_returns_legacy_without_touching_others(env, tmp_path, ircd):
    register(env, tmp_path, "gwen")
    register(env, tmp_path, "bob")
    bob, lines = await sasl_login(ircd, "spark-bob", secret_of("spark-bob"))
    assert welcomed(lines), lines
    assert not welcomed(await plain_register(ircd, "spark-gwen"))  # account: needs SASL
    account_cmd(env, "disable", "gwen")
    await asyncio.sleep(1.5)  # one reload
    assert welcomed(await plain_register(ircd, "spark-gwen"))  # legacy
    assert await wait_closed(bob, 2.0) is None  # bob's session untouched
    assert account(env, "spark-bob").state == "active"
