"""Grant-wrapped units, class-5 doctor checks, ordered activation (culture#506 t15)."""

from __future__ import annotations

import argparse
import json
import shlex

import pytest

from culture_core import accounts, persistence
from culture_core.cli import agents as agent_mod
from culture_core.cli._errors import EXIT_DAEMON_PERMANENT, CultureError
from culture_core.config import AgentConfig, ServerConfig, ServerConnConfig, save_server_config
from culture_core.doctor import accounts as dacc
from culture_core.doctor import run_doctor

SERVER = "spark"
GRANT = "/opt/bin/grant"


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GRANT_HOME", str(home / "grant"))
    monkeypatch.setattr("culture_core.doctor.checks.list_services", lambda: [])
    return home


def agent(suffix="gwen", backend="claude"):
    return AgentConfig(suffix=suffix, backend=backend, nick=f"{SERVER}-{suffix}")


def _exec_start(text):
    line = next(ln for ln in text.splitlines() if ln.startswith("ExecStart="))
    return shlex.split(line[len("ExecStart=") :])


# -- persistence: wrapper + RestartPreventExitStatus -------------------------


def test_install_service_wraps_execstart_with_grant_inject(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "get_platform", lambda: "linux")
    monkeypatch.setattr(persistence, "_systemd_user_dir", lambda: tmp_path)
    monkeypatch.setattr(persistence, "_run_cmd", lambda *a, **k: True)
    secret = accounts.secret_name("spark-gwen")
    path = persistence.install_service(
        "culture-agent-spark-gwen",
        ["/usr/bin/python", "-m", "culture_core", "agents", "start", "spark-gwen"],
        "d",
        inject_secret=secret,
        grant_bin=GRANT,
    )
    text = path.read_text()
    assert _exec_start(text) == [
        GRANT,
        "run",
        "--inject",
        f"CULTURE_AGENT_SECRET={secret}",
        "--",
        "/usr/bin/python",
        "-m",
        "culture_core",
        "agents",
        "start",
        "spark-gwen",
    ]
    assert f"RestartPreventExitStatus={EXIT_DAEMON_PERMANENT}" in text
    assert EXIT_DAEMON_PERMANENT == 78


def test_install_service_without_inject_is_unwrapped(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "get_platform", lambda: "linux")
    monkeypatch.setattr(persistence, "_systemd_user_dir", lambda: tmp_path)
    monkeypatch.setattr(persistence, "_run_cmd", lambda *a, **k: True)
    path = persistence.install_service("culture-agent-x", ["/usr/bin/python", "-m", "x"], "d")
    assert _exec_start(path.read_text()) == ["/usr/bin/python", "-m", "x"]


def test_grant_bin_resolved_absolute_or_clear_error(tmp_path, monkeypatch):
    # no grant next to the interpreter -> PATH fallback
    monkeypatch.setattr(persistence.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(persistence.shutil, "which", lambda n: "/x/grant")
    assert persistence.resolve_grant_bin() == "/x/grant"
    monkeypatch.setattr(persistence.shutil, "which", lambda n: None)
    with pytest.raises(CultureError, match="grant binary not found"):
        persistence.resolve_grant_bin()


def test_agents_install_wraps_when_account_exists(tmp_path, monkeypatch):
    cfg = tmp_path / "server.yaml"
    d = tmp_path / "agent-gwen"
    d.mkdir()
    (d / "culture.yaml").write_text("suffix: gwen\nbackend: claude\n")
    save_server_config(
        cfg, ServerConfig(server=ServerConnConfig(name=SERVER), manifest={"gwen": str(d)})
    )
    data = tmp_path / "data"
    accounts.save([accounts.AccountRecord(nick="spark-gwen", secret_sha256="a" * 64)], SERVER, data)
    seen = {}
    monkeypatch.setattr(
        persistence,
        "install_service",
        lambda *a, **k: seen.update(k) or tmp_path / "u.service",
    )
    args = argparse.Namespace(
        nick="gwen", config=str(cfg), data_dir=str(data), allow_dev_interpreter=True
    )
    agent_mod._cmd_install(args)
    assert seen["inject_secret"] == accounts.secret_name("spark-gwen")
    # an agent with no account is not wrapped
    args.data_dir = str(tmp_path / "empty")
    agent_mod._cmd_install(args)
    assert seen["inject_secret"] is None


def test_wrap_installed_unit_rewrites_once(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "_run_cmd", lambda *a, **k: True)
    unit = tmp_path / "culture-agent-spark-gwen.service"
    unit.write_text("[Service]\nExecStart=/usr/bin/python -m culture_core\nRestart=on-failure\n")
    name = accounts.secret_name("spark-gwen")
    kw = {"unit_dir": tmp_path, "grant_bin": GRANT}
    assert persistence.wrap_installed_unit("culture-agent-spark-gwen", name, **kw) is True
    assert _exec_start(unit.read_text())[:5] == [
        GRANT,
        "run",
        "--inject",
        f"CULTURE_AGENT_SECRET={name}",
        "--",
    ]
    assert persistence.wrap_installed_unit("culture-agent-spark-gwen", name, **kw) is False
    assert persistence.wrap_installed_unit("culture-agent-nope", name, **kw) is None


# -- doctor class 5 ----------------------------------------------------------


def _cfg(*agents):
    return ServerConfig(server=ServerConnConfig(name=SERVER), agents=list(agents))


def _records(*nicks):
    return [accounts.AccountRecord(nick=n, secret_sha256="b" * 64) for n in nicks]


def test_doctor_flags_unit_without_inject_wrapper(tmp_path):
    (tmp_path / "culture-agent-spark-gwen.service").write_text(
        "[Service]\nExecStart=/usr/bin/python -m x\n"
    )
    (tmp_path / "culture-agent-spark-ok.service").write_text(
        f"[Service]\nExecStart={GRANT} run --inject "
        f"CULTURE_AGENT_SECRET={accounts.secret_name('spark-ok')} -- /usr/bin/python\n"
    )
    cfg = _cfg(agent("gwen"), agent("ok"))
    found = dacc.check_agent_units(cfg, _records("spark-gwen", "spark-ok"), tmp_path)
    assert [f.subject for f in found] == ["spark-gwen"]
    assert found[0].severity == "error"
    assert "inject wrapper" in found[0].message
    assert "culture agents account activate spark-gwen" in found[0].fix_hint


def test_doctor_skips_external_units_and_agents_without_account(tmp_path):
    (tmp_path / "culture-agent-spark-ext.service").write_text("[Service]\nExecStart=/x\n")
    (tmp_path / "culture-agent-spark-legacy.service").write_text("[Service]\nExecStart=/x\n")
    cfg = _cfg(agent("ext", "external"), agent("legacy"))
    assert dacc.check_agent_units(cfg, _records("spark-ext"), tmp_path) == []


def test_doctor_flags_old_cultureagent_only_when_accounts_exist():
    old = lambda d: "0.15.2"  # noqa: E731
    found = dacc.check_client_version(_records("spark-gwen"), old)
    assert len(found) == 1 and "0.15.2" in found[0].message and "0.16.0" in found[0].message
    assert "cultureagent>=0.16.0" in found[0].fix_hint
    assert dacc.check_client_version([], old) == []
    assert dacc.check_client_version(_records("spark-gwen"), lambda d: "0.16.0") == []


def test_doctor_flags_old_agentirc():
    found = dacc.check_server_version(_records("spark-gwen"), lambda d: "9.13.0")
    assert len(found) == 1 and "9.14.0" in found[0].fix_hint
    assert dacc.check_server_version(_records("spark-gwen"), lambda d: "9.14.0") == []


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_doctor_flags_open_bind_without_trusted_cidrs(host):
    found = dacc.check_open_bind(_records("spark-gwen"), host, [])
    assert len(found) == 1 and "trusted_cidrs" in found[0].message
    assert "trusted_cidrs" in found[0].fix_hint
    assert dacc.check_open_bind(_records("spark-gwen"), host, ["100.64.0.0/10"]) == []
    assert dacc.check_open_bind([], host, []) == []
    assert dacc.check_open_bind(_records("spark-gwen"), "127.0.0.1", []) == []


def test_doctor_flags_registered_agents_without_account_including_external():
    cfg = _cfg(agent("gwen"), agent("ext", "external"), agent("has"))
    found = dacc.check_missing_accounts(cfg, _records("spark-has"))
    assert sorted(f.subject for f in found) == ["spark-ext", "spark-gwen"]
    assert all(f.severity == "info" and f.fix_hint for f in found)


def test_run_doctor_wires_class5_and_exit_code(tmp_path):
    data = tmp_path / "data"
    accounts.save(_records("spark-gwen"), SERVER, data)
    cfgfile = tmp_path / "server.yaml"
    cfgfile.write_text("server:\n  name: spark\n  host: 0.0.0.0\n")
    cfg = _cfg(agent("gwen"))
    found = dacc.check_accounts(
        cfg, str(cfgfile), data, unit_dir=tmp_path, version_of=lambda d: "0.0.1"
    )
    assert {f.subject for f in found} >= {"cultureagent", "agentirc"}
    cfg.server.host = "0.0.0.0"
    assert any(
        f.subject == "server" for f in dacc.check_accounts(cfg, str(cfgfile), data, tmp_path)
    )
    cfgfile.write_text("accounts:\n  trusted_cidrs: ['100.64.0.0/10']\n")
    assert not any(
        f.subject == "server" for f in dacc.check_accounts(cfg, str(cfgfile), data, tmp_path)
    )
    report = run_doctor(
        cfg, root_override=str(tmp_path), config_path=str(cfgfile), data_dir=str(data)
    )
    assert report.class5 and report.exit_code == (
        1 if any(f.severity == "error" for f in report.class5) else 0
    )


def test_doctor_json_includes_class5(tmp_path, capsys, monkeypatch):
    from culture_core.cli.doctor import dispatch

    cfg = tmp_path / "server.yaml"
    save_server_config(cfg, _cfg())
    with pytest.raises(SystemExit):
        dispatch(argparse.Namespace(config=str(cfg), root=str(tmp_path), json=True, fix=False))
    assert "class5" in json.loads(capsys.readouterr().out)


# -- ordered activation ------------------------------------------------------
# Superseded by the opt-in, rolled-back activation (deviation d7): see
# tests/test_account_activation_hardening.py.
