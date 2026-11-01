"""Review findings on PRs #514/#515 (culture#506 agent accounts).

1. ``culture server start`` hands the server.yaml ``accounts:`` block to the
   IRCd (and doctor reads the same block);
2. culture never writes an accounts file agentirc would reject as a whole;
3. ``account disable`` / ``revoke`` roll the unit back when the account
   mutation fails;
4. wrapping a unit without ``RestartPreventExitStatus=`` adds one (and
   unwrapping removes it again, byte-for-byte);
5. the account CLI never prints a value derived from a ``secret*`` name;
6. the CI version-gate markers skip on old agentirc/cultureagent.

Every test runs on a temp HOME / grant store / unit dir (see the imported
fixtures); systemctl is never invoked.
"""

from __future__ import annotations

import argparse
import json
import logging
from types import SimpleNamespace

import pytest
from grant import store as grant_store

from culture_core import accounts, accounts_policy, persistence
from culture_core.cli import agents as agent_mod
from culture_core.cli import server as srv_mod
from culture_core.cli._errors import EXIT_DAEMON_PERMANENT, CultureError
from culture_core.config import AgentConfig
from culture_core.doctor import accounts as dacc
from tests import conftest as ct
from tests.test_account_activation_hardening import (  # noqa: F401 - fixtures
    GRANT,
    SERVER,
    _cli_env,
    acct,
    grant_names,
    tmp_home,
    units,
    write_unit,
)


def agent(suffix="gwen", channels=("#general",)):
    return AgentConfig(
        suffix=suffix, backend="claude", nick=f"{SERVER}-{suffix}", channels=list(channels)
    )


def write_server_yaml(path, name=SERVER, accounts_block=None, host="0.0.0.0"):  # nosec B104
    lines = ["server:", f"  name: {name}", f"  host: '{host}'", "  port: 6667"]
    if accounts_block is not None:
        lines.append("accounts: " + json.dumps(accounts_block))
    path.write_text("\n".join(lines) + "\n")
    return path


def start_args(cfg, name=SERVER):
    return argparse.Namespace(name=name, config=str(cfg))


# ---------------------------------------------------------------------------
# 1. server start reads the accounts: block
# ---------------------------------------------------------------------------


@pytest.mark.requires_agentirc_accounts
def test_server_start_builds_accounts_config_from_server_yaml(tmp_path):
    cfg = write_server_yaml(
        tmp_path / "server.yaml",
        accounts_block={
            "trusted_cidrs": ["100.64.0.0/10"],
            "trust_loopback": False,
            "guest_channel_prefixes": ["#sbx-"],
            "auth_failure_limit": 3,
            "auth_failure_window": 30,
        },
    )
    acfg = srv_mod._resolve_accounts_config(start_args(cfg))
    got = (
        acfg.trusted_cidrs,
        acfg.trust_loopback,
        acfg.guest_channel_prefixes,
        acfg.auth_failure_limit,
        acfg.auth_failure_window,
    )
    assert got == (["100.64.0.0/10"], False, ["#sbx-"], 3, 30)


@pytest.mark.requires_agentirc_accounts
@pytest.mark.asyncio
async def test_run_server_passes_accounts_to_ircd(tmp_path, monkeypatch):
    import agentirc.ircd

    captured = {}

    class _StopIRCd:
        def __init__(self, config):
            captured["config"] = config

        async def start(self):
            raise RuntimeError("stop here")

    monkeypatch.setattr(agentirc.ircd, "IRCd", _StopIRCd)
    cfg = write_server_yaml(tmp_path / "s.yaml", accounts_block={"trusted_cidrs": ["10.0.0.0/8"]})
    acfg = srv_mod._resolve_accounts_config(start_args(cfg))
    with pytest.raises(RuntimeError, match="stop here"):
        await srv_mod._run_server(SERVER, "127.0.0.1", 0, [], 0, "", accounts=acfg)
    assert captured["config"].accounts.trusted_cidrs == ["10.0.0.0/8"]


def test_server_start_has_config_flag():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    srv_mod.register(sub)
    args = parser.parse_args(["server", "start", "--config", "/x/server.yaml"])
    assert args.config == "/x/server.yaml"


def test_server_start_ignores_another_servers_block(tmp_path):
    cfg = write_server_yaml(
        tmp_path / "server.yaml", name="spark", accounts_block={"trusted_cidrs": ["10.0.0.0/8"]}
    )
    assert srv_mod._resolve_accounts_config(start_args(cfg, name="sbx")) is None


def test_server_start_without_block_keeps_defaults(tmp_path):
    cfg = write_server_yaml(tmp_path / "server.yaml")
    assert srv_mod._resolve_accounts_config(start_args(cfg)) is None


def test_server_start_invalid_block_is_a_permanent_error(tmp_path):
    cfg = write_server_yaml(
        tmp_path / "server.yaml", accounts_block={"guest_channel_prefixes": ["sbx-"]}
    )
    with pytest.raises(CultureError) as exc:
        srv_mod._resolve_accounts_config(start_args(cfg))
    assert exc.value.code == EXIT_DAEMON_PERMANENT
    assert "guest_channel_prefixes" in exc.value.message


def test_server_start_old_agentirc_warns_and_ignores_block(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(accounts_policy, "agentirc_builder", lambda: None)
    cfg = write_server_yaml(tmp_path / "server.yaml", accounts_block={"trusted_cidrs": []})
    with caplog.at_level(logging.WARNING):
        assert srv_mod._resolve_accounts_config(start_args(cfg)) is None
    assert "accounts:" in caplog.text
    assert "9.14.0" in caplog.text


def test_doctor_reads_the_same_block(tmp_path):
    cfg = write_server_yaml(
        tmp_path / "server.yaml", accounts_block={"guest_channel_prefixes": "#sbx-"}
    )
    config = SimpleNamespace(
        server=SimpleNamespace(host="0.0.0.0", name=SERVER), agents=[]
    )  # nosec
    findings = dacc.check_accounts(config, str(cfg), tmp_path / "data")
    assert [f.severity for f in findings] == ["error"]
    assert "guest_channel_prefixes" in findings[0].message


def test_doctor_open_bind_uses_policy_loader(tmp_path, monkeypatch):
    cfg = write_server_yaml(tmp_path / "server.yaml", accounts_block={"trusted_cidrs": []})
    seen = []
    real = accounts_policy.load_accounts_block

    def spy(path):
        seen.append(path)
        return real(path)

    monkeypatch.setattr(accounts_policy, "load_accounts_block", spy)
    config = SimpleNamespace(
        server=SimpleNamespace(host="0.0.0.0", name=SERVER), agents=[]
    )  # nosec
    dacc.check_accounts(config, str(cfg), tmp_path / "data")
    assert seen == [str(cfg)]


# ---------------------------------------------------------------------------
# 2. never write a file agentirc rejects as a whole
# ---------------------------------------------------------------------------


def test_mint_guest_without_prefixes_is_refused(tmp_path):
    data = tmp_path / "data"
    with pytest.raises(accounts.AccountError, match="guest_channel_prefixes"):
        accounts.mint(agent(), SERVER, role="guest", data_dir=data)
    assert not (data / "accounts.json").exists()
    assert grant_names() == []


def test_set_role_guest_with_grants_outside_sandbox_is_refused(tmp_path):
    data = tmp_path / "data"
    accounts.mint(agent(), SERVER, data_dir=data)
    before = (data / "accounts.json").read_text()
    with pytest.raises(accounts.AccountError, match="outside the guest sandbox"):
        accounts.set_role(
            "spark-gwen", SERVER, "guest", data_dir=data, guest_channel_prefixes=("#sbx-",)
        )
    assert (data / "accounts.json").read_text() == before


def test_guest_inside_sandbox_is_written(tmp_path):
    data = tmp_path / "data"
    rec = accounts.mint(
        agent(channels=("#sbx-1",)),
        SERVER,
        role="guest",
        data_dir=data,
        guest_channel_prefixes=("#sbx-",),
    )
    assert rec.role == "guest"
    assert rec.grants == {"#sbx-1": "speak"}


def test_non_printable_ascii_nick_is_refused(tmp_path):
    data = tmp_path / "data"
    rec = accounts.AccountRecord(nick="spark-gwén", secret_sha256="a" * 64)
    with pytest.raises(accounts.AccountError, match="printable ASCII"):
        accounts.save([rec], SERVER, data)


def test_case_only_duplicate_with_a_foreign_record_is_refused(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    foreign = {
        "nick": "SPARK-gwen",
        "secret_sha256": "b" * 64,
        "role": "resident",
        "grants": {},
        "state": "active",
    }
    (data / "accounts.json").write_text(json.dumps({"version": 1, "accounts": [foreign]}))
    rec = accounts.AccountRecord(nick="spark-gwen", secret_sha256="a" * 64)
    with pytest.raises(accounts.AccountError, match="letter case"):
        accounts.save([rec], SERVER, data)


def test_agentirc_parser_has_the_last_word(tmp_path, monkeypatch):
    def strict(_doc, _prefixes=()):
        raise ValueError("accounts[0]: some future rule")

    monkeypatch.setattr(accounts, "_agentirc_parse_accounts", lambda: strict)
    with pytest.raises(accounts.AccountError, match="some future rule"):
        accounts.mint(agent(), SERVER, data_dir=tmp_path / "data")
    assert grant_names() == []


def test_mirror_rules_hold_without_agentirc_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "_agentirc_parse_accounts", lambda: None)
    with pytest.raises(accounts.AccountError, match="guest_channel_prefixes"):
        accounts.mint(agent(), SERVER, role="guest", data_dir=tmp_path / "data")


@pytest.mark.requires_agentirc_accounts
def test_every_file_culture_writes_parses_in_agentirc(tmp_path):
    from agentirc.accounts import parse_accounts

    data = tmp_path / "data"
    prefixes = ("#sbx-",)
    accounts.mint(agent("a"), SERVER, data_dir=data, guest_channel_prefixes=prefixes)
    accounts.mint(
        agent("g", channels=("#sbx-x",)),
        SERVER,
        role="guest",
        data_dir=data,
        guest_channel_prefixes=prefixes,
    )
    accounts.set_state(
        "spark-a", SERVER, "suspended", data_dir=data, guest_channel_prefixes=prefixes
    )
    accounts.revoke("spark-g", SERVER, data_dir=data, guest_channel_prefixes=prefixes)
    table = parse_accounts(json.loads((data / "accounts.json").read_text()), prefixes)
    assert sorted(table) == ["spark-a", "spark-g"]


def test_cli_passes_server_yaml_guest_prefixes(tmp_path, units, monkeypatch):
    cfg_path, data, _ = _cli_env(tmp_path, units, monkeypatch)
    text = cfg_path.read_text() + "accounts:\n  guest_channel_prefixes: ['#sbx-']\n"
    cfg_path.write_text(text)
    accounts.mint(
        agent(channels=("#sbx-1",)),
        SERVER,
        role="guest",
        data_dir=data,
        guest_channel_prefixes=("#sbx-",),
    )
    acct(cfg_path, "rotate")  # would be refused without the configured prefixes
    assert accounts.find("spark-gwen", SERVER, data).role == "guest"


# ---------------------------------------------------------------------------
# 3. disable / revoke roll the unit back when the account mutation fails
# ---------------------------------------------------------------------------


def _activated(tmp_path, units, monkeypatch):
    cfg_path, data, _ = _cli_env(tmp_path, units, monkeypatch)
    unit = write_unit(units, "spark-gwen")
    acct(cfg_path, "activate")
    monkeypatch.setattr(persistence, "service_active_state", lambda n: "active")
    units.calls.clear()
    return cfg_path, data, unit


def _break_file(data):
    (data / "accounts.json").write_text('{"version": 1, "accounts": [{"nick": "spark-gwen", ')


def _foreign_grant(nick):
    """Someone else's grant entry under culture's name for *nick*."""
    name = accounts.secret_name(nick)
    value = grant_store.get_record(name).value
    grant_store.delete(name)
    grant_store.set_secret(
        name=name, value=value, hidden=True, source="someone else", purpose="not culture"
    )


def _systemctl_verbs(units):
    return [c[2] for c in units.calls if c[:2] == ["systemctl", "--user"] and len(c) > 2]


def _break(data, breakage):
    if breakage == "malformed":
        _break_file(data)
    else:
        _foreign_grant("spark-gwen")
    return (data / "accounts.json").read_text()


@pytest.mark.parametrize("breakage", ["malformed", "foreign"])
@pytest.mark.parametrize("helper", ["_account_disable", "_account_revoke"])
def test_unit_rolled_back_when_account_mutation_fails(
    tmp_path, units, monkeypatch, breakage, helper
):
    """The file breaks (or the grant entry turns foreign) after the CLI's own
    lookup: the account mutation fails after the unit was stopped/unwrapped."""
    cfg_path, data, unit = _activated(tmp_path, units, monkeypatch)
    wrapped = unit.read_text()
    before = _break(data, breakage)
    config = agent_mod.load_config_or_default(str(cfg_path))
    run = getattr(agent_mod, helper)
    with pytest.raises(accounts.AccountError):
        run(config, "spark-gwen", data)
    assert unit.read_text() == wrapped  # grant inject still in ExecStart
    assert (data / "accounts.json").read_text() == before
    verbs = _systemctl_verbs(units)
    assert verbs[0] == "stop"
    assert verbs[-1] == "start"  # was running -> running again


def test_cli_disable_with_foreign_grant_entry_changes_nothing(tmp_path, units, monkeypatch):
    cfg_path, data, unit = _activated(tmp_path, units, monkeypatch)
    wrapped = unit.read_text()
    before = _break(data, "foreign")
    with pytest.raises(CultureError, match="refusing to touch it"):
        acct(cfg_path, "disable")
    assert unit.read_text() == wrapped
    assert (data / "accounts.json").read_text() == before
    assert _systemctl_verbs(units)[-1] == "start"


def test_revoke_and_remove_check_grant_ownership_before_writing(tmp_path):
    data = tmp_path / "data"
    accounts.mint(agent(), SERVER, data_dir=data)
    _foreign_grant("spark-gwen")
    before = (data / "accounts.json").read_text()
    with pytest.raises(accounts.AccountError, match="refusing to touch it"):
        accounts.revoke("spark-gwen", SERVER, data_dir=data)
    with pytest.raises(accounts.AccountError, match="refusing to touch it"):
        accounts.remove("spark-gwen", SERVER, data_dir=data)
    assert (data / "accounts.json").read_text() == before


# ---------------------------------------------------------------------------
# 4. RestartPreventExitStatus on legacy / hand-edited units
# ---------------------------------------------------------------------------


def _strip_prevent(path):
    text = "".join(
        ln
        for ln in path.read_text().splitlines(keepends=True)
        if not ln.startswith("RestartPreventExitStatus=")
    )
    path.write_text(text)
    return text


def _prevent_lines(path):
    return [
        ln for ln in path.read_text().splitlines() if ln.startswith("RestartPreventExitStatus=")
    ]


def test_wrap_adds_missing_restart_prevent_line_and_unwrap_removes_it(units):
    unit = write_unit(units, "spark-gwen")
    legacy = _strip_prevent(unit)
    name = accounts.secret_name("spark-gwen")
    assert persistence.wrap_installed_unit("culture-agent-spark-gwen", name, grant_bin=GRANT)
    assert _prevent_lines(unit) == ["RestartPreventExitStatus=78 64"]
    section = unit.read_text().split("[Service]", 1)[1].split("[Install]", 1)[0]
    assert "RestartPreventExitStatus=78 64" in section
    assert persistence.unwrap_installed_unit("culture-agent-spark-gwen", name) is True
    assert unit.read_text() == legacy


def test_wrap_keeps_hand_edited_restart_prevent_values(units):
    unit = write_unit(units, "spark-gwen")
    text = unit.read_text().replace(
        "RestartPreventExitStatus=78", "RestartPreventExitStatus=78 SIGKILL"
    )
    unit.write_text(text)
    name = accounts.secret_name("spark-gwen")
    persistence.wrap_installed_unit("culture-agent-spark-gwen", name, grant_bin=GRANT)
    assert _prevent_lines(unit) == ["RestartPreventExitStatus=78 SIGKILL 64"]
    persistence.unwrap_installed_unit("culture-agent-spark-gwen", name)
    assert unit.read_text() == text


def test_rewrap_for_a_new_secret_does_not_stack_markers(units):
    unit = write_unit(units, "spark-gwen")
    legacy = _strip_prevent(unit)
    persistence.wrap_installed_unit("culture-agent-spark-gwen", "OLD_NAME", grant_bin=GRANT)
    persistence.wrap_installed_unit("culture-agent-spark-gwen", "NEW_NAME", grant_bin=GRANT)
    assert _prevent_lines(unit) == ["RestartPreventExitStatus=78 64"]
    persistence.unwrap_installed_unit("culture-agent-spark-gwen", "NEW_NAME")
    assert unit.read_text() == legacy


# ---------------------------------------------------------------------------
# 4b. unit paths / unit content derived from file content stay confined
#     (SonarCloud pythonsecurity:S2083 on persistence._rewrite_unit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "culture-agent-../../outside",
        "culture-agent-spark/../../outside",
        "/abs/outside",
        "culture-agent-x\n",
        "",
    ],
)
def test_unit_helpers_refuse_names_that_escape_the_unit_dir(units, tmp_path, name):
    victim = tmp_path / "outside.service"
    victim.write_text("[Service]\nExecStart=/bin/true\n")
    before = victim.read_text()
    helpers = [
        lambda: persistence.wrap_installed_unit(name, "N", grant_bin=GRANT),
        lambda: persistence.unwrap_installed_unit(name, "N"),
        lambda: persistence.read_unit(name),
        lambda: persistence.restore_unit(name, "x"),
        lambda: persistence.unit_exec_start(name),
    ]
    for call in helpers:
        with pytest.raises(CultureError, match="invalid systemd unit name") as exc:
            call()
        assert exc.value.code == 1
    assert victim.read_text() == before
    assert units.calls == []


def test_unwrap_refuses_a_marker_that_would_restore_another_directive(units):
    unit = write_unit(units, "spark-gwen")
    _strip_prevent(unit)
    name = accounts.secret_name("spark-gwen")
    persistence.wrap_installed_unit("culture-agent-spark-gwen", name, grant_bin=GRANT)
    tampered = unit.read_text().replace(
        "original RestartPreventExitStatus: absent",
        "original RestartPreventExitStatus: ExecStartPre=/bin/sh -c evil",
    )
    assert tampered != unit.read_text()
    unit.write_text(tampered)
    calls_before = list(units.calls)
    with pytest.raises(CultureError, match="not a RestartPreventExitStatus= line") as exc:
        persistence.unwrap_installed_unit("culture-agent-spark-gwen", name)
    assert exc.value.code == 2
    assert unit.read_text() == tampered
    assert units.calls == calls_before


@pytest.mark.parametrize("bad", ["a\nExecStartPre=/bin/evil", "a\rb", "a\0b"])
def test_rewrite_refuses_argv_that_would_inject_unit_lines(units, bad):
    unit = write_unit(units, "spark-gwen")
    before = unit.read_text()
    argv = ["/usr/bin/python3", bad]
    with pytest.raises(CultureError, match="line break or NUL"):
        persistence._rewrite_unit(unit, argv)
    assert unit.read_text() == before
    assert units.calls == []


# ---------------------------------------------------------------------------
# 5. nothing derived from a secret* name reaches print
# ---------------------------------------------------------------------------


def test_grant_entry_name_matches_secret_name():
    assert accounts.grant_entry_name("spark-gwen") == accounts.secret_name("spark-gwen")


def test_account_cli_prints_grant_entry_without_secret_named_sources():
    import ast
    import inspect
    import textwrap

    for fn in (agent_mod._account_activate, agent_mod._account_rotate):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        names = {
            n.attr if isinstance(n, ast.Attribute) else n.id
            for n in ast.walk(tree)
            if isinstance(n, (ast.Attribute, ast.Name))
        }
        assert not {n for n in names if "secret" in n.lower()}, fn.__name__


# ---------------------------------------------------------------------------
# 6. the CI version gates
# ---------------------------------------------------------------------------


def _gate_item(marker):
    marks = {marker: object()}
    return SimpleNamespace(get_closest_marker=marks.get)


def test_agentirc_gate_skips_without_accounts_module():
    reason = ct.agentirc_accounts_gap(version_of=lambda _d: "9.13.0", has_module=lambda _m: False)
    assert "agentirc.accounts" in reason


def test_agentirc_gate_skips_old_version_even_with_module():
    reason = ct.agentirc_accounts_gap(version_of=lambda _d: "9.13.2", has_module=lambda _m: True)
    assert "9.13.2" in reason


def test_agentirc_gate_passes_on_new_version():
    assert (
        ct.agentirc_accounts_gap(version_of=lambda _d: "9.14.0", has_module=lambda _m: True) is None
    )


def test_cultureagent_gate():
    assert "0.15.3" in ct.cultureagent_sasl_gap(version_of=lambda _d: "0.15.3")
    assert ct.cultureagent_sasl_gap(version_of=lambda _d: "0.16.0") is None


def test_gate_hook_uses_the_probe_at_call_time(monkeypatch):
    item = _gate_item("requires_agentirc_accounts")
    monkeypatch.setattr(ct, "agentirc_accounts_gap", lambda: "agentirc-cli 9.13.0 is below 9.14.0")
    assert ct.version_gate_skip_reason(item).startswith("requires_agentirc_accounts:")
    monkeypatch.setattr(ct, "agentirc_accounts_gap", lambda: None)
    assert ct.version_gate_skip_reason(item) is None
    assert ct.version_gate_skip_reason(_gate_item("unrelated")) is None


def test_gates_open_in_this_environment():
    """With the new versions installed (the d8 pins), nothing is skipped."""
    if ct.agentirc_accounts_gap() or ct.cultureagent_sasl_gap():
        pytest.skip("old agentirc/cultureagent installed (CI before the d8 pins)")
    assert ct.version_gate_skip_reason(_gate_item("requires_agentirc_accounts")) is None
