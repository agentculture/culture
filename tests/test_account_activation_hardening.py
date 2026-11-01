"""Review findings H1-H5 / M1-M8 on opt-in account activation (culture#506, d7).

Every test runs against a temp HOME, a temp grant store (GRANT_HOME) and temp
unit files; systemctl is never invoked (``persistence._run_cmd`` is mocked).
Where the server matters a real AgentIRC (``IRCd`` on port 0) is used.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shlex
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from agentirc.config import ServerConfig as IrcServerConfig
from agentirc.config import TelemetryConfig
from agentirc.ircd import IRCd
from grant import store as grant_store

from culture_core import accounts, persistence
from culture_core.cli import agents as agent_mod
from culture_core.cli._errors import CultureError
from culture_core.config import AgentConfig, ServerConfig, ServerConnConfig, save_server_config
from culture_core.doctor import accounts as dacc
from culture_core.doctor.model import DoctorReport

SERVER = "spark"
GRANT = "/opt/tools/grant"
PY = "/usr/bin/python3"


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GRANT_HOME", str(home / "grant"))
    return home


@pytest.fixture
def units(tmp_path, monkeypatch):
    """Temp systemd user dir; systemctl calls are recorded, never run."""
    d = tmp_path / "units"
    d.mkdir()
    calls: list[list[str]] = []
    monkeypatch.setattr(persistence, "get_platform", lambda: "linux")
    monkeypatch.setattr(persistence, "_systemd_user_dir", lambda: d)
    monkeypatch.setattr(persistence, "_run_cmd", lambda args, *a, **k: calls.append(args) or True)
    return SimpleNamespace(dir=d, calls=calls)


def write_unit(units, nick, wrapped_secret=None):
    cmd = [PY, "-m", "culture_core", "agents", "start", nick, "--foreground"]
    if wrapped_secret:
        cmd = persistence.grant_wrap(cmd, wrapped_secret, GRANT)
    text = persistence._build_systemd_unit(f"culture-agent-{nick}", cmd, "d")
    path = units.dir / f"culture-agent-{nick}.service"
    path.write_text(text)
    return path


def exec_start(path):
    line = next(ln for ln in path.read_text().splitlines() if ln.startswith("ExecStart="))
    return shlex.split(line[len("ExecStart=") :])


def agent(suffix="gwen", backend="claude", owner=None, channels=("#general",)):
    return AgentConfig(
        suffix=suffix,
        backend=backend,
        nick=f"{SERVER}-{suffix}",
        owner=owner,
        channels=list(channels),
    )


def grant_names():
    try:
        return grant_store.list_names()
    except Exception:
        return []


def grant_value(name):
    return grant_store.get_record(name).value


class FakeUnit:
    """UnitOps over a real temp unit file; restart/verify are scripted."""

    def __init__(self, units, *, restart_ok=True, verify_error=None, client="0.99.0"):
        self.units = units
        self.restart_ok = restart_ok
        self.verify_error = verify_error
        self.client = client
        self.log: list[tuple[str, str]] = []

    def svc(self, nick):
        return f"culture-agent-{nick}"

    def exists(self, nick):
        return (self.units.dir / f"{self.svc(nick)}.service").is_file()

    def client_version(self, nick):
        self.log.append(("client", nick))
        return self.client

    def wrap(self, nick, name):
        self.log.append(("wrap", nick))
        return persistence.wrap_installed_unit(self.svc(nick), name, grant_bin=GRANT)

    def unwrap(self, nick, name):
        self.log.append(("unwrap", nick))
        return persistence.unwrap_installed_unit(self.svc(nick), name)

    def restart(self, nick):
        self.log.append(("restart", nick))
        return self.restart_ok

    def verify(self, nick):
        self.log.append(("verify", nick))
        return self.verify_error


def steps(fake):
    return [s for s, _ in fake.log]


# ---------------------------------------------------------------------------
# H4: register is opt-out of accounts entirely
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "server.yaml"
    save_server_config(cfg_path, ServerConfig(server=ServerConnConfig(name=SERVER)))
    data = tmp_path / "data"
    monkeypatch.setattr(accounts, "DEFAULT_DATA_DIR", str(data))
    return cfg_path, data


def make_dir(tmp_path, suffix, backend="claude", owner=None):
    d = tmp_path / f"agent-{suffix}"
    d.mkdir(exist_ok=True)
    lines = [f"suffix: {suffix}", f"backend: {backend}", "channels: ['#general']"]
    if owner:
        lines.append(f"owner: {owner}")
    (d / "culture.yaml").write_text("\n".join(lines) + "\n")
    return d


def test_h4_register_creates_no_account_secret_or_unit_change(cfg_env, tmp_path, units, capsys):
    cfg_path, data = cfg_env
    unit = write_unit(units, "spark-gwen")
    before = unit.read_text()
    d = make_dir(tmp_path, "gwen")
    # an old cultureagent must not matter: register never activates
    agent_mod._cmd_register(argparse.Namespace(path=str(d), suffix=None, config=str(cfg_path)))
    assert "Registered: spark-gwen" in capsys.readouterr().out
    assert grant_names() == []
    assert not accounts.accounts_path(data).exists()
    assert unit.read_text() == before
    assert units.calls == []


def test_h4_reregister_leaves_revoked_tombstone(cfg_env, tmp_path, units):
    cfg_path, data = cfg_env
    accounts.save([accounts.AccountRecord("spark-gwen", "a" * 64, state="revoked")], SERVER, data)
    d = make_dir(tmp_path, "gwen")
    agent_mod._cmd_register(argparse.Namespace(path=str(d), suffix=None, config=str(cfg_path)))
    assert accounts.find("spark-gwen", SERVER, data).state == "revoked"
    assert grant_names() == []


# ---------------------------------------------------------------------------
# H5: injective secret names + ownership guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [("spark-my_agent", "spark-my-agent"), ("spark-Alpha", "spark-alpha")],
)
def test_h5_secret_names_do_not_collide(a, b):
    assert accounts.secret_name(a) != accounts.secret_name(b)


def test_h5_secret_names_are_valid_grant_names():
    for nick in ("spark-gwen", "spark-" + "x" * 80, "spark-a[b]{c}|^`\\"):
        name = accounts.secret_name(nick)
        assert grant_store.NAME_RE.match(name), name
        assert len(name) <= 64
        assert name.startswith(accounts.SECRET_PREFIX)


def test_h5_refuses_to_mint_over_a_secret_owned_by_another_nick(tmp_path, units):
    data = tmp_path / "data"
    name = accounts.secret_name("spark-gwen")
    grant_store.set_secret(
        name=name,
        value="theirs",
        hidden=True,
        source=accounts.SECRET_SOURCE,
        purpose="AgentIRC account secret for spark-GWEN",
    )
    write_unit(units, "spark-gwen")
    cfg, fake = agent(), FakeUnit(units)
    with pytest.raises(accounts.ActivationError) as ei:
        accounts.activate(cfg, SERVER, data_dir=data, unit=fake)
    assert ei.value.step == "mint"
    assert grant_value(name) == "theirs"
    assert not accounts.accounts_path(data).exists()


# ---------------------------------------------------------------------------
# H1 / H2: ordered activation with full rollback
# ---------------------------------------------------------------------------


def test_h1_full_order_wrap_publish_restart_verify(tmp_path, units):
    data = tmp_path / "data"
    unit = write_unit(units, "spark-gwen")
    fake = FakeUnit(units)
    seen = {}
    orig_restart = fake.restart

    def restart(nick):
        # publish happened before restart, and the unit is already wrapped
        seen["published"] = accounts.find(nick, SERVER, data)
        seen["wrapped"] = exec_start(unit)[1:3] == ["run", "--inject"]
        return orig_restart(nick)

    fake.restart = restart
    res = accounts.activate(agent(), SERVER, data_dir=data, unit=fake)
    assert res.published
    assert not res.pending_owner
    assert steps(fake) == ["client", "wrap", "restart", "verify"]
    assert seen["published"].state == "active"
    assert seen["wrapped"]
    rec = accounts.find("spark-gwen", SERVER, data)
    assert rec.secret_sha256 == accounts.hash_secret(grant_value(res.secret_name))
    assert "64" in next(
        ln for ln in unit.read_text().splitlines() if ln.startswith("RestartPreventExitStatus=")
    )


def test_h1_no_unit_installed_is_not_ready(tmp_path, units):
    data = tmp_path / "data"
    fake = FakeUnit(units)
    cfg = agent()
    with pytest.raises(accounts.ActivationError) as ei:
        accounts.activate(cfg, SERVER, data_dir=data, unit=fake)
    assert ei.value.step == "preflight"
    assert "culture agents install spark-gwen" in str(ei.value)
    assert grant_names() == []
    assert not accounts.accounts_path(data).exists()


def test_h1_non_linux_wrap_returns_none_refuses(tmp_path, units, monkeypatch):
    data = tmp_path / "data"
    write_unit(units, "spark-gwen")
    fake = FakeUnit(units)
    fake.wrap = lambda nick, name: None  # macOS/Windows: nothing rewritten
    cfg = agent()
    with pytest.raises(accounts.ActivationError) as ei:
        accounts.activate(cfg, SERVER, data_dir=data, unit=fake)
    assert ei.value.step == "unit"
    assert grant_names() == []
    assert not accounts.accounts_path(data).exists()


def test_h1_cli_requires_a_unit_object_for_culture_owned(tmp_path):
    cfg, data = agent(), tmp_path / "d"
    with pytest.raises(accounts.ActivationError) as ei:
        accounts.activate(cfg, SERVER, data_dir=data, unit=None)
    assert ei.value.step == "preflight"


def test_h2_client_check_runs_before_any_mint_or_wrap(tmp_path, units):
    data = tmp_path / "data"
    unit = write_unit(units, "spark-gwen")
    before = unit.read_text()
    fake = FakeUnit(units, client="0.15.2")
    cfg = agent()
    with pytest.raises(accounts.ActivationError) as ei:
        accounts.activate(cfg, SERVER, data_dir=data, unit=fake)
    assert ei.value.step == "preflight"
    assert "0.16.0" in str(ei.value)
    assert "wrap" not in steps(fake)
    assert unit.read_text() == before
    assert grant_names() == []


@pytest.mark.parametrize("fail_at", ["publish", "restart", "verify"])
def test_h2_failure_after_wrap_restores_everything(tmp_path, units, monkeypatch, fail_at):
    data = tmp_path / "data"
    unit = write_unit(units, "spark-gwen")
    before = unit.read_text()
    fake = FakeUnit(
        units,
        restart_ok=fail_at != "restart",
        verify_error="did not reconnect" if fail_at == "verify" else None,
    )
    if fail_at == "publish":

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(accounts, "_write_file", boom)
    cfg = agent()
    with pytest.raises(accounts.ActivationError) as ei:
        accounts.activate(cfg, SERVER, data_dir=data, unit=fake)
    assert ei.value.step == fail_at
    assert unit.read_text() == before  # original ExecStart restored
    assert grant_names() == []  # minted secret removed
    assert accounts.find("spark-gwen", SERVER, data) is None
    if fail_at in ("restart", "verify"):
        # the agent is bounced back onto the legacy path after the unwrap
        assert steps(fake)[-1] == "restart"


def test_h2_rollback_restores_previous_entry_and_secret(tmp_path, units):
    """Re-activating a suspended account: a failed verify restores it as it was."""
    data = tmp_path / "data"
    write_unit(units, "spark-gwen")
    first = accounts.activate(agent(), SERVER, data_dir=data, unit=FakeUnit(units))
    secret = grant_value(first.secret_name)
    accounts.set_state("spark-gwen", SERVER, "suspended", data)
    cfg, fake = agent(), FakeUnit(units, verify_error="x")
    with pytest.raises(accounts.ActivationError):
        accounts.activate(cfg, SERVER, data_dir=data, unit=fake)
    rec = accounts.find("spark-gwen", SERVER, data)
    assert rec.state == "suspended"
    assert grant_value(first.secret_name) == secret


# ---------------------------------------------------------------------------
# H1: `culture agents start` hands the secret to the daemon in-process
# ---------------------------------------------------------------------------


def _activated(tmp_path, units, monkeypatch, state="active"):
    data = tmp_path / "data"
    monkeypatch.setattr(accounts, "DEFAULT_DATA_DIR", str(data))
    write_unit(units, "spark-gwen")
    res = accounts.activate(agent(), SERVER, data_dir=data, unit=FakeUnit(units))
    if state != "active":
        accounts.set_state("spark-gwen", SERVER, state, data)
    return data, grant_value(res.secret_name)


def test_h1_agents_start_foreground_injects_secret(tmp_path, units, monkeypatch):
    _, secret = _activated(tmp_path, units, monkeypatch)
    monkeypatch.delenv("CULTURE_AGENT_SECRET", raising=False)
    cfg = ServerConfig(server=ServerConnConfig(name=SERVER), agents=[agent()])
    monkeypatch.setattr(agent_mod, "load_config", lambda _p: cfg)
    monkeypatch.setattr(agent_mod, "_probe_server_connection", lambda *a: None)
    seen = {}

    async def fake_run(config, ag):
        import os

        seen["secret"] = os.environ.pop("CULTURE_AGENT_SECRET", None)

    monkeypatch.setattr(agent_mod, "_run_single_agent", fake_run)
    agent_mod._cmd_start(
        argparse.Namespace(config="x", nick="spark-gwen", all=False, foreground=True)
    )
    assert seen["secret"] == secret


def test_h1_agents_start_keeps_secret_from_grant_run(tmp_path, units, monkeypatch):
    _activated(tmp_path, units, monkeypatch)
    monkeypatch.setenv("CULTURE_AGENT_SECRET", "from-grant-run")
    assert (
        agent_mod._agent_secret_env(agent(), SERVER, single=True)
        == {}  # already injected by the unit's grant wrapper: leave it alone
    )


def test_h1_agents_start_without_account_is_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "DEFAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CULTURE_AGENT_SECRET", raising=False)
    assert agent_mod._agent_secret_env(agent(), SERVER, single=True) == {}


@pytest.mark.parametrize("state", ["suspended", "revoked"])
def test_h1_agents_start_refuses_inactive_account(tmp_path, units, monkeypatch, state):
    _activated(tmp_path, units, monkeypatch, state=state)
    monkeypatch.delenv("CULTURE_AGENT_SECRET", raising=False)
    cfg = agent()
    with pytest.raises(CultureError) as ei:
        agent_mod._agent_secret_env(cfg, SERVER, single=True)
    assert ei.value.code == 78
    assert state in ei.value.message


def test_h1_background_child_gets_its_own_secret(tmp_path, units, monkeypatch):
    _, secret = _activated(tmp_path, units, monkeypatch)
    cfg = ServerConfig(server=ServerConnConfig(name=SERVER), agents=[agent()])
    seen = {}

    async def fake_run(config, ag):
        import os

        seen["secret"] = os.environ.pop("CULTURE_AGENT_SECRET", None)

    monkeypatch.setattr(agent_mod, "_run_single_agent", fake_run)
    monkeypatch.setattr(agent_mod.os, "setsid", lambda: None)
    monkeypatch.setattr(agent_mod.os, "dup2", lambda *a: None)
    monkeypatch.setattr(agent_mod, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(agent_mod, "write_pid", lambda *a: None)
    monkeypatch.setattr(agent_mod, "remove_pid", lambda *a: None)

    class Exit(BaseException):
        pass

    def fake_exit(code):
        seen["code"] = code
        raise Exit

    monkeypatch.setattr(agent_mod.os, "_exit", fake_exit)
    agent_cfg = agent()
    with pytest.raises(Exit):
        agent_mod._agent_daemon_child(cfg, agent_cfg)
    assert seen == {"secret": secret, "code": 0}


def test_h1_unarchive_hint_is_startable(cfg_env, tmp_path, units, monkeypatch, capsys):
    cfg_path, _ = cfg_env
    d = make_dir(tmp_path, "gwen")
    agent_mod._cmd_register(argparse.Namespace(path=str(d), suffix=None, config=str(cfg_path)))
    write_unit(units, "spark-gwen")
    agent_mod._cmd_archive(argparse.Namespace(nick="spark-gwen", reason="", config=str(cfg_path)))
    capsys.readouterr()
    agent_mod._cmd_unarchive(argparse.Namespace(nick="spark-gwen", config=str(cfg_path)))
    out = capsys.readouterr().out
    assert "systemctl --user start culture-agent-spark-gwen" in out


# ---------------------------------------------------------------------------
# H3: disable / revoke / delete / unregister never strand a wrapped unit
# ---------------------------------------------------------------------------


def test_h3_inject_secret_for_only_active(tmp_path):
    data = tmp_path / "data"
    accounts.save(
        [
            accounts.AccountRecord("spark-a", "a" * 64, state="active"),
            accounts.AccountRecord("spark-s", "a" * 64, state="suspended"),
            accounts.AccountRecord("spark-r", "a" * 64, state="revoked"),
        ],
        SERVER,
        data,
    )
    assert accounts.inject_secret_for("spark-a", SERVER, data) == accounts.secret_name("spark-a")
    assert accounts.inject_secret_for("spark-s", SERVER, data) is None
    assert accounts.inject_secret_for("spark-r", SERVER, data) is None


def test_h3_wrapped_units_park_on_grant_run_failure():
    text = persistence._build_systemd_unit(
        "culture-agent-spark-gwen", persistence.grant_wrap([PY, "-m", "x"], "N", GRANT), "d"
    )
    assert "RestartPreventExitStatus=78 64" in text
    plain = persistence._build_systemd_unit("culture-agent-spark-gwen", [PY, "-m", "x"], "d")
    assert "RestartPreventExitStatus=78\n" in plain


def test_h3_unwrap_restores_original_unit(units):
    unit = write_unit(units, "spark-gwen")
    before = unit.read_text()
    name = accounts.secret_name("spark-gwen")
    assert persistence.wrap_installed_unit("culture-agent-spark-gwen", name, grant_bin=GRANT)
    assert unit.read_text() != before
    assert persistence.unwrap_installed_unit("culture-agent-spark-gwen", name) is True
    assert unit.read_text() == before
    assert persistence.unwrap_installed_unit("culture-agent-spark-gwen", name) is False
    assert persistence.unwrap_installed_unit("culture-agent-nope", name) is None


def _cli_env(tmp_path, units, monkeypatch, backend="claude", owner=None):
    cfg_path = tmp_path / "server.yaml"
    d = make_dir(tmp_path, "gwen", backend=backend, owner=owner)
    save_server_config(
        cfg_path,
        ServerConfig(server=ServerConnConfig(name=SERVER), manifest={"gwen": str(d)}),
    )
    data = tmp_path / "data"
    monkeypatch.setattr(accounts, "DEFAULT_DATA_DIR", str(data))
    fakes = []

    def ops(config, args):
        fake = FakeUnit(units)
        fakes.append(fake)
        return fake

    monkeypatch.setattr(agent_mod, "_account_unit_ops", ops)
    return cfg_path, data, fakes


def acct(cfg_path, verb, nick="gwen", **kw):
    kw.setdefault("data_dir", None)
    agent_mod._cmd_account(
        argparse.Namespace(account_command=verb, nick=nick, config=str(cfg_path), **kw)
    )


def test_h3_disable_unwraps_and_restarts_running_unit(tmp_path, units, monkeypatch):
    cfg_path, data, _ = _cli_env(tmp_path, units, monkeypatch)
    unit = write_unit(units, "spark-gwen")
    before = unit.read_text()
    acct(cfg_path, "activate")
    assert unit.read_text() != before
    monkeypatch.setattr(persistence, "service_active_state", lambda n: "active")
    units.calls.clear()
    acct(cfg_path, "disable")
    assert unit.read_text() == before
    assert accounts.find("spark-gwen", SERVER, data) is None
    assert grant_names() == []
    verbs = [c[2] for c in units.calls if c[:2] == ["systemctl", "--user"]]
    assert verbs.index("stop") < verbs.index("start")


def test_h3_revoke_stops_the_unit_and_activate_recovers(tmp_path, units, monkeypatch):
    cfg_path, data, _ = _cli_env(tmp_path, units, monkeypatch)
    write_unit(units, "spark-gwen")
    acct(cfg_path, "activate")
    units.calls.clear()
    acct(cfg_path, "revoke")
    assert ["systemctl", "--user", "stop", "culture-agent-spark-gwen"] in units.calls
    assert accounts.find("spark-gwen", SERVER, data).state == "revoked"
    acct(cfg_path, "activate")  # tombstone -> explicit reactivation
    rec = accounts.find("spark-gwen", SERVER, data)
    assert rec.state == "active"
    assert rec.secret_sha256 == accounts.hash_secret(grant_value(accounts.secret_name(rec.nick)))


@pytest.mark.parametrize("how", ["delete", "unregister"])
def test_h3_delete_and_unregister_uninstall_the_wrapped_unit(tmp_path, units, monkeypatch, how):
    cfg_path, data, _ = _cli_env(tmp_path, units, monkeypatch)
    unit = write_unit(units, "spark-gwen")
    acct(cfg_path, "activate")
    if how == "delete":
        agent_mod._cmd_delete(argparse.Namespace(nick="spark-gwen", config=str(cfg_path)))
    else:
        agent_mod._cmd_unregister(argparse.Namespace(target="spark-gwen", config=str(cfg_path)))
    assert not unit.exists()
    assert accounts.find("spark-gwen", SERVER, data).state == "revoked"


def test_h3_archive_disables_unit_and_unarchive_reenables(tmp_path, units, monkeypatch):
    cfg_path, data, _ = _cli_env(tmp_path, units, monkeypatch)
    write_unit(units, "spark-gwen")
    acct(cfg_path, "activate")
    units.calls.clear()
    agent_mod._cmd_archive(argparse.Namespace(nick="spark-gwen", reason="", config=str(cfg_path)))
    assert ["systemctl", "--user", "disable", "--now", "culture-agent-spark-gwen"] in units.calls
    assert accounts.find("spark-gwen", SERVER, data).state == "suspended"
    agent_mod._cmd_unarchive(argparse.Namespace(nick="spark-gwen", config=str(cfg_path)))
    assert ["systemctl", "--user", "enable", "culture-agent-spark-gwen"] in units.calls
    assert accounts.find("spark-gwen", SERVER, data).state == "active"


# ---------------------------------------------------------------------------
# M1 / M2 / M3 / M4: the accounts file and grant stay consistent
# ---------------------------------------------------------------------------


def test_m1_concurrent_rotations_leave_file_matching_grant(tmp_path, monkeypatch):
    data = tmp_path / "data"
    accounts.mint(SimpleNamespace(suffix="gwen", channels=[]), SERVER, data_dir=data)
    real_set = accounts.grant_store.set_secret

    def slow_set(**kw):
        rec = real_set(**kw)
        time.sleep(0.05)  # widen the window between grant write and file save
        return rec

    monkeypatch.setattr(accounts.grant_store, "set_secret", slow_set)
    errors = []

    def worker():
        try:
            accounts.rotate("spark-gwen", SERVER, data)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rec = accounts.find("spark-gwen", SERVER, data)
    assert rec.secret_sha256 == accounts.hash_secret(
        grant_value(accounts.secret_name("spark-gwen"))
    )


def test_m1_concurrent_mutations_do_not_lose_updates(tmp_path, monkeypatch):
    data = tmp_path / "data"
    accounts.save([accounts.AccountRecord(f"spark-a{i}", "a" * 64) for i in range(6)], SERVER, data)
    real_load = accounts.load

    def slow_load(*a, **k):
        recs = real_load(*a, **k)
        time.sleep(0.03)
        return recs

    monkeypatch.setattr(accounts, "load", slow_load)
    threads = [
        threading.Thread(target=accounts.set_role, args=(f"spark-a{i}", SERVER, "operator", data))
        for i in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    monkeypatch.setattr(accounts, "load", real_load)
    assert {r.role for r in accounts.load(data)} == {"operator"}


def test_m2_foreign_records_survive_every_write(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    thor = {
        "nick": "thor-x",
        "secret_sha256": "b" * 64,
        "role": "operator",
        "grants": {"#ops": "read"},
        "state": "active",
    }
    spark = dict(thor, nick="spark-y", role="resident", grants={})
    (data / "accounts.json").write_text(json.dumps({"version": 1, "accounts": [thor, spark]}))
    accounts.set_state("spark-y", SERVER, "suspended", data)
    accounts.mint(SimpleNamespace(suffix="z", channels=[]), SERVER, data_dir=data)
    accounts.remove("spark-z", SERVER, data)
    doc = json.loads((data / "accounts.json").read_text())
    assert thor in doc["accounts"]
    assert any(a["nick"] == "spark-y" and a["state"] == "suspended" for a in doc["accounts"])


def test_m3_activate_remints_when_grant_value_does_not_match(tmp_path, units):
    data = tmp_path / "data"
    write_unit(units, "spark-gwen")
    res = accounts.activate(agent(), SERVER, data_dir=data, unit=FakeUnit(units))
    name = res.secret_name
    grant_store.set_secret(
        name=name,
        value="drifted",
        hidden=True,
        source=accounts.SECRET_SOURCE,
        purpose=accounts.secret_purpose("spark-gwen"),
    )
    again = accounts.activate(agent(), SERVER, data_dir=data, unit=FakeUnit(units))
    assert not again.reused
    assert grant_value(name) != "drifted"
    rec = accounts.find("spark-gwen", SERVER, data)
    assert rec.secret_sha256 == accounts.hash_secret(grant_value(name))
    actions = [json.loads(x)["action"] for x in accounts.audit_path(data).read_text().splitlines()]
    assert "rotate" not in actions
    assert actions[-1] == "activate"


def test_m3_activate_reuses_matching_secret(tmp_path, units):
    data = tmp_path / "data"
    write_unit(units, "spark-gwen")
    first = accounts.activate(agent(), SERVER, data_dir=data, unit=FakeUnit(units))
    value = grant_value(first.secret_name)
    again = accounts.activate(agent(), SERVER, data_dir=data, unit=FakeUnit(units))
    assert again.reused
    assert grant_value(first.secret_name) == value


def test_m4_rotate_restores_old_secret_when_publish_fails(tmp_path, monkeypatch):
    data = tmp_path / "data"
    accounts.mint(SimpleNamespace(suffix="gwen", channels=[]), SERVER, data_dir=data)
    name = accounts.secret_name("spark-gwen")
    old = grant_value(name)
    before = (data / "accounts.json").read_text()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(accounts, "_write_file", boom)
    with pytest.raises(OSError):
        accounts.rotate("spark-gwen", SERVER, data)
    assert grant_value(name) == old
    assert (data / "accounts.json").read_text() == before


# ---------------------------------------------------------------------------
# M5: the data dir is the server's, and only local persisting servers activate
# ---------------------------------------------------------------------------


def test_m5_data_dir_from_server_argv(monkeypatch, tmp_path):
    monkeypatch.setattr(
        accounts,
        "_probe_server_argv",
        lambda name: ["python", "-m", "culture_core", "server", "start", "--data-dir", "/srv/d"],
    )
    got = accounts.resolve_server_data_dir("sbx")
    assert got.path == Path("/srv/d")
    assert got.persist
    assert got.source != "default"
    monkeypatch.setattr(
        accounts, "_probe_server_argv", lambda name: ["x", "server", "start", "--no-persist"]
    )
    got = accounts.resolve_server_data_dir("sbx")
    assert got.path is None
    assert not got.persist
    monkeypatch.setattr(accounts, "_probe_server_argv", lambda name: ["x", "server", "start"])
    assert accounts.resolve_server_data_dir("sbx").path == accounts.default_data_dir()
    # an explicit --data-dir always wins
    assert accounts.resolve_server_data_dir("sbx", "/e").path == Path("/e")


def test_m5_server_argv_probe_reads_installed_unit(units):
    (units.dir / "culture-server-sbx.service").write_text(
        "[Service]\nExecStart=/usr/bin/python3 -m culture_core server start --foreground "
        "--name sbx --data-dir /home/u/.culture/data-sbx\n"
    )
    argv = accounts._unit_server_argv("sbx")
    assert accounts._data_dir_from_argv(argv) == (Path("/home/u/.culture/data-sbx"), True)
    assert accounts._unit_server_argv("nope") is None


def test_m5_activation_refuses_no_persist_and_remote(tmp_path, units, monkeypatch):
    cfg_path, _, _ = _cli_env(tmp_path, units, monkeypatch)
    write_unit(units, "spark-gwen")
    monkeypatch.setattr(accounts, "_probe_server_argv", lambda n: ["x", "server", "--no-persist"])
    with pytest.raises(CultureError, match="no-persist"):
        acct(cfg_path, "activate")
    monkeypatch.setattr(accounts, "_probe_server_argv", lambda n: None)
    remote = ServerConfig(
        server=ServerConnConfig(name=SERVER, host="203.0.113.9"),
        manifest={"gwen": str(tmp_path / "agent-gwen")},
    )
    save_server_config(cfg_path, remote)
    with pytest.raises(CultureError, match="not this machine"):
        acct(cfg_path, "activate")
    assert grant_names() == []


def test_m5_local_host_detection():
    assert accounts.is_local_host("localhost")
    assert accounts.is_local_host("127.0.0.1")
    assert accounts.is_local_host("0.0.0.0")
    assert not accounts.is_local_host("203.0.113.9")


def test_m5_install_uses_resolved_server_data_dir(tmp_path, monkeypatch):
    cfg_path = tmp_path / "server.yaml"
    d = make_dir(tmp_path, "gwen")
    save_server_config(
        cfg_path, ServerConfig(server=ServerConnConfig(name=SERVER), manifest={"gwen": str(d)})
    )
    srv = tmp_path / "srv-data"
    accounts.save([accounts.AccountRecord("spark-gwen", "a" * 64)], SERVER, srv)
    monkeypatch.setattr(
        accounts, "_probe_server_argv", lambda n: ["x", "server", "start", "--data-dir", str(srv)]
    )
    seen = {}
    monkeypatch.setattr(
        persistence, "install_service", lambda *a, **k: seen.update(k) or tmp_path / "u"
    )
    agent_mod._cmd_install(
        argparse.Namespace(nick="gwen", config=str(cfg_path), allow_dev_interpreter=True)
    )
    assert seen["inject_secret"] == accounts.secret_name("spark-gwen")


def test_m5_mesh_and_doctor_accept_data_dir():
    from culture_core.cli import doctor as doctor_cli
    from culture_core.cli import mesh as mesh_cli

    p = argparse.ArgumentParser()
    sub = p.add_subparsers()
    mesh_cli.register(sub)
    doctor_cli.register(sub)
    assert p.parse_args(["mesh", "setup", "--data-dir", "/d"]).data_dir == "/d"
    assert p.parse_args(["mesh", "update", "--data-dir", "/d"]).data_dir == "/d"
    assert p.parse_args(["doctor", "--data-dir", "/d"]).data_dir == "/d"
    q = argparse.ArgumentParser()
    agent_mod.register(q.add_subparsers())
    assert q.parse_args(["agents", "install", "x", "--data-dir", "/d"]).data_dir == "/d"


# ---------------------------------------------------------------------------
# M6: doctor stays ok on installs without accounts
# ---------------------------------------------------------------------------


def test_m6_missing_account_is_informational():
    cfg = ServerConfig(server=ServerConnConfig(name=SERVER), agents=[agent()])
    found = dacc.check_missing_accounts(cfg, [])
    assert found
    assert all(f.severity == "info" for f in found)
    assert "culture agents account activate spark-gwen" in found[0].fix_hint
    report = DoctorReport(class5=found)
    assert report.ok
    assert report.exit_code == 0


def test_m6_doctor_cli_ok_on_fresh_install(tmp_path, capsys, monkeypatch):
    from culture_core.cli.doctor import dispatch

    monkeypatch.setattr("culture_core.doctor.checks.list_services", lambda: [])
    cfg_path = tmp_path / "server.yaml"
    d = make_dir(tmp_path, "gwen")
    save_server_config(
        cfg_path, ServerConfig(server=ServerConnConfig(name=SERVER), manifest={"gwen": str(d)})
    )
    args = argparse.Namespace(
        config=str(cfg_path), root=str(tmp_path), json=True, fix=False, data_dir=None
    )
    with pytest.raises(SystemExit) as e:
        dispatch(args)
    payload = json.loads(capsys.readouterr().out)
    assert e.value.code == 0
    assert payload["ok"] is True
    assert payload["class5"][0]["severity"] == "info"


# ---------------------------------------------------------------------------
# M7: external residents publish only once the owner is ready
# ---------------------------------------------------------------------------


def test_m7_external_without_owner_ready_mints_but_does_not_publish(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(accounts, "installed_version", lambda d: "0.1.0")  # irrelevant
    ext = agent("ext", "external", owner="gwen.service")
    res = accounts.activate(ext, SERVER, data_dir=data, external=True)
    assert res.pending_owner
    assert not res.published
    assert accounts.find("spark-ext", SERVER, data) is None
    minted = grant_value(res.secret_name)
    res2 = accounts.activate(ext, SERVER, data_dir=data, external=True, owner_ready=True)
    assert res2.published
    assert res2.reused
    assert grant_value(res.secret_name) == minted
    assert accounts.find("spark-ext", SERVER, data).secret_sha256 == accounts.hash_secret(minted)


def test_m7_external_cli_prints_owner_instructions(tmp_path, units, monkeypatch, capsys):
    cfg_path, data, fakes = _cli_env(
        tmp_path, units, monkeypatch, backend="external", owner="gwen.service"
    )
    acct(cfg_path, "activate", owner_ready=False)
    out = capsys.readouterr().out
    name = accounts.secret_name("spark-gwen")
    assert f"CULTURE_AGENT_SECRET={name}" in out
    assert "--owner-ready" in out
    assert accounts.find("spark-gwen", SERVER, data) is None
    acct(cfg_path, "activate", owner_ready=True)
    out = capsys.readouterr().out
    assert "restart gwen.service" in out
    assert accounts.find("spark-gwen", SERVER, data).state == "active"
    assert grant_value(name) not in out
    assert not any(steps(f) for f in fakes)  # no unit, client or verify step


# ---------------------------------------------------------------------------
# M8: grant comes from culture's own environment, guarded like the interpreter
# ---------------------------------------------------------------------------


def test_m8_grant_bin_is_next_to_the_interpreter(tmp_path, monkeypatch):
    bindir = tmp_path / "uv" / "tools" / "culture" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "grant").write_text("#!/bin/sh\n")
    monkeypatch.setattr(persistence.sys, "executable", str(bindir / "python"))
    monkeypatch.setattr(persistence.shutil, "which", lambda n: "/elsewhere/grant")
    assert persistence.resolve_grant_bin() == str(bindir / "grant")


def test_m8_grant_bin_in_dev_venv_is_guarded(tmp_path, monkeypatch):
    bindir = tmp_path / "repo" / ".venv" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "grant").write_text("#!/bin/sh\n")
    monkeypatch.setattr(persistence.sys, "executable", str(bindir / "python"))
    with pytest.raises(CultureError, match="grant"):
        persistence.resolve_grant_bin(allow_dev_interpreter=False)
    assert persistence.resolve_grant_bin(allow_dev_interpreter=True) == str(bindir / "grant")


def test_m8_missing_grant_hint_names_the_right_distribution(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(persistence.shutil, "which", lambda n: None)
    with pytest.raises(CultureError) as ei:
        persistence.resolve_grant_bin()
    assert "grant-cli" not in ei.value.remediation
    assert "uv tool install grant" in ei.value.remediation


# ---------------------------------------------------------------------------
# verify step: the server sees the nick (real AgentIRC)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ircd(tmp_path):
    data = tmp_path / "irc-data"
    data.mkdir()
    cfg = IrcServerConfig(
        name=SERVER,
        host="127.0.0.1",
        port=0,
        webhook_port=0,
        data_dir=str(data),
        telemetry=TelemetryConfig(audit_dir=str(tmp_path / "audit")),
    )
    server = IRCd(cfg)
    await server.start()
    server.config.port = server._server.sockets[0].getsockname()[1]
    yield server
    await server.stop()


@pytest.mark.asyncio
async def test_verify_whois_sees_connected_nick(ircd):
    from culture_core.observer import IRCObserver

    obs = IRCObserver("127.0.0.1", ircd.config.port, SERVER)
    assert await obs.whois("spark-gwen") is False
    reader, writer = await asyncio.open_connection("127.0.0.1", ircd.config.port)
    writer.write(b"NICK spark-gwen\r\nUSER u 0 * :u\r\n")
    await writer.drain()
    async with asyncio.timeout(3):
        while b" 001 " not in await reader.readline():
            pass
    assert await obs.whois("spark-gwen") is True
    writer.close()


def test_account_unit_ops_verify_fails_fast_on_failed_unit(monkeypatch):
    from culture_core.cli.shared.account_units import SystemdUnitOps

    ops = SystemdUnitOps(host="127.0.0.1", port=1, server_name=SERVER, verify_timeout=5)
    monkeypatch.setattr(persistence, "service_active_state", lambda n: "failed")
    t0 = time.monotonic()
    err = ops.verify("spark-gwen")
    assert err
    assert "failed" in err
    assert time.monotonic() - t0 < 3


def test_secret_never_in_audit_or_output(tmp_path, units, monkeypatch, capsys):
    cfg_path, data, _ = _cli_env(tmp_path, units, monkeypatch)
    write_unit(units, "spark-gwen")
    acct(cfg_path, "activate")
    secret = grant_value(accounts.secret_name("spark-gwen"))
    out = capsys.readouterr().out
    assert secret not in out
    assert secret not in accounts.audit_path(data).read_text()
    assert hashlib.sha256(secret.encode()).hexdigest() not in accounts.audit_path(data).read_text()


# ---------------------------------------------------------------------------
# H3: a refused login parks the unit (exit 78) instead of restart-looping
# ---------------------------------------------------------------------------


class AuthenticationRefused(Exception):
    """Stand-in with cultureagent's class name (matched by name)."""


class _RefusingDaemon:
    def __init__(self, at_start):
        self.at_start = at_start
        self.stopped = False
        self._transport = SimpleNamespace(auth_refused=asyncio.Event(), refusal="bad_secret")

    def set_stop_event(self, ev):
        self.ev = ev

    async def start(self):
        if self.at_start:
            raise AuthenticationRefused("bad_secret")
        self._transport.auth_refused.set()  # refused on a later reconnect

    async def stop(self):
        self.stopped = True


@pytest.mark.parametrize("at_start", [True, False])
def test_h3_refused_login_exits_78(monkeypatch, at_start):
    daemon = _RefusingDaemon(at_start)
    monkeypatch.setattr(agent_mod, "_resolve_daemon_factory", lambda b: lambda c, a: daemon)
    run = agent_mod._run_single_agent(SimpleNamespace(), agent())  # a coroutine: runs below
    with pytest.raises(CultureError) as ei:
        asyncio.run(run)
    assert ei.value.code == 78
    assert daemon.stopped


class _PublicApiRefusingDaemon:
    """cultureagent >= 0.16 shape: public refusal API, no ``_transport``."""

    def __init__(self):
        self.stopped = False
        self._refused = asyncio.Event()
        self.auth_refusal = None

    def set_stop_event(self, ev):
        self.ev = ev

    async def start(self):
        self.auth_refusal = "bad_secret"  # refused on a later reconnect
        self._refused.set()

    async def wait_auth_refused(self):
        await self._refused.wait()
        return self.auth_refusal

    async def stop(self):
        self.stopped = True


def test_h3_refused_login_exits_78_via_public_api(monkeypatch):
    daemon = _PublicApiRefusingDaemon()
    monkeypatch.setattr(agent_mod, "_resolve_daemon_factory", lambda b: lambda c, a: daemon)
    run = agent_mod._run_single_agent(SimpleNamespace(), agent())  # a coroutine: runs below
    with pytest.raises(CultureError) as ei:
        asyncio.run(run)
    assert ei.value.code == 78
    assert daemon.stopped
    assert not hasattr(daemon, "_transport")


def test_h3_doctor_flags_wrapped_unit_without_active_account(units):
    write_unit(units, "spark-gwen", wrapped_secret=accounts.secret_name("spark-gwen"))
    cfg = ServerConfig(server=ServerConnConfig(name=SERVER), agents=[agent()])
    revoked = [accounts.AccountRecord("spark-gwen", "a" * 64, state="revoked")]
    for records in ([], revoked):
        found = dacc.check_agent_units(cfg, records, units.dir)
        assert len(found) == 1
        assert found[0].severity == "error"
        assert "account activate spark-gwen" in found[0].fix_hint


def test_m5_mesh_inject_reads_the_servers_data_dir(tmp_path, monkeypatch):
    from culture_core.cli import mesh as mesh_cli

    srv = tmp_path / "srv"
    accounts.save([accounts.AccountRecord("spark-gwen", "a" * 64)], SERVER, srv)
    assert mesh_cli._account_inject("spark-gwen", SERVER, None) is None  # default dir: none
    assert mesh_cli._account_inject("spark-gwen", SERVER, str(srv)) == accounts.secret_name(
        "spark-gwen"
    )
    monkeypatch.setattr(
        accounts, "_probe_server_argv", lambda n: ["x", "server", "start", "--data-dir", str(srv)]
    )
    assert mesh_cli._account_inject("spark-gwen", SERVER, None) == accounts.secret_name(
        "spark-gwen"
    )


@pytest.mark.parametrize("via_script", [False, True])
def test_client_version_uses_the_units_interpreter(units, tmp_path, via_script):
    import sys

    from culture_core.cli.shared.account_units import SystemdUnitOps

    program = sys.executable
    if via_script:  # legacy unit: a console script whose shebang names python
        script = tmp_path / "culture"
        script.write_text(f"#!{sys.executable}\nprint('hi')\n")
        program = str(script)
    cmd = [program, "-m", "culture_core", "agents", "start", "spark-gwen"]
    (units.dir / "culture-agent-spark-gwen.service").write_text(
        persistence._build_systemd_unit("culture-agent-spark-gwen", cmd, "d")
    )
    ops = SystemdUnitOps(host="127.0.0.1", port=1, server_name=SERVER)
    assert ops.client_version("spark-gwen") == accounts.installed_version("cultureagent")
