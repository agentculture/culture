"""Tests for culture_core.accounts (culture#506 t13)."""

import hashlib
import json
import os
import stat
from types import SimpleNamespace

import pytest
from grant import store as grant_store

from culture_core import accounts


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GRANT_HOME", str(home / "grant"))
    return home


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


def cfg(suffix="gwen", channels=None):
    return SimpleNamespace(suffix=suffix, channels=channels or ["#general", "#ops"])


def lines(data_dir):
    return [json.loads(x) for x in accounts.audit_path(data_dir).read_text().splitlines()]


def test_grant_store_is_under_tmp_home(tmp_home):
    from grant import fs

    assert str(fs.user_store_paths().file).startswith(str(tmp_home))


def test_secret_name_mapping():
    name = accounts.secret_name("spark-gwen")
    tag = hashlib.sha256(b"spark-gwen").hexdigest()[:10].upper()
    assert name == f"CULTURE_AGENT_SPARK_GWEN_{tag}"
    long = accounts.secret_name("spark-" + "x" * 60)  # truncated, still unique + valid
    assert len(long) <= 64
    assert grant_store.NAME_RE.match(long)
    with pytest.raises(accounts.AccountError):
        accounts.secret_name("")


def test_mint_stores_hidden_secret_and_writes_hash_only(data_dir):
    rec = accounts.mint(cfg(), "spark", data_dir=data_dir)
    assert rec.nick == "spark-gwen"
    name = accounts.secret_name("spark-gwen")
    r = grant_store.get_record(name)
    assert r.hidden is True
    assert r.source == "culture agents account"
    assert r.purpose == "AgentIRC account secret for spark-gwen"
    assert "culture agents account rotate spark-gwen" in r.rotation_howto
    secret = r.value
    raw = (data_dir / "accounts.json").read_text()
    assert secret not in raw
    doc = json.loads(raw)
    assert doc["version"] == 1
    assert doc["accounts"][0]["secret_sha256"] == hashlib.sha256(secret.encode()).hexdigest()
    assert stat.S_IMODE(os.stat(data_dir / "accounts.json").st_mode) == 0o600


def test_defaults_role_and_grants(data_dir):
    rec = accounts.mint(cfg(), "spark", data_dir=data_dir)
    assert rec.role == "resident"
    assert rec.state == "active"
    assert rec.grants == {"#general": "speak", "#ops": "speak"}


def test_rotation_changes_hash_keeps_settings(data_dir):
    a = accounts.mint(cfg(), "spark", data_dir=data_dir)
    old = a.secret_sha256
    accounts.set_grants("spark-gwen", "spark", {"#x": "read"}, data_dir=data_dir)
    b = accounts.mint(cfg(), "spark", data_dir=data_dir)
    assert b.secret_sha256 != old
    assert b.grants == {"#x": "read"}
    assert len(accounts.load(data_dir)) == 1


def test_only_own_server_nicks(data_dir):
    agent_cfg = cfg()
    with pytest.raises(accounts.AccountError):
        accounts.mint(agent_cfg, "", data_dir=data_dir)
    foreign = [accounts.AccountRecord("thor-x", "a" * 64)]
    with pytest.raises(accounts.AccountError):
        accounts.save(foreign, "spark", data_dir=data_dir)
    assert not (data_dir / "accounts.json").exists()


def test_foreign_nicks_preserved(data_dir):
    data_dir.mkdir()
    (data_dir / "accounts.json").write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {
                        "nick": "thor-x",
                        "secret_sha256": "b" * 64,
                        "role": "resident",
                        "grants": {},
                        "state": "active",
                    }
                ],
            }
        )
    )
    accounts.mint(cfg(), "spark", data_dir=data_dir)
    assert [r.nick for r in accounts.load(data_dir)] == ["thor-x", "spark-gwen"]


def test_mutators_and_remove(data_dir):
    accounts.mint(cfg(), "spark", data_dir=data_dir)
    assert accounts.set_state("spark-gwen", "spark", "suspended", data_dir).state == "suspended"
    assert accounts.set_role("spark-gwen", "spark", "operator", data_dir).role == "operator"
    removed = accounts.remove("spark-gwen", "spark", data_dir)
    assert removed.nick == "spark-gwen"
    assert accounts.load(data_dir) == []
    name = accounts.secret_name("spark-gwen")
    with pytest.raises(grant_store.NotFoundError):
        grant_store.get_record(name)


def test_invalid_input_never_writes_file(data_dir):
    accounts.mint(cfg(), "spark", data_dir=data_dir)
    before = (data_dir / "accounts.json").read_text()
    for bad in (
        lambda: accounts.set_state("spark-gwen", "spark", "bogus", data_dir),
        lambda: accounts.set_role("spark-gwen", "spark", "root", data_dir),
        lambda: accounts.set_grants("spark-gwen", "spark", {"#a": "god"}, data_dir),
        lambda: accounts.set_grants("spark-gwen", "spark", {"nochan": "read"}, data_dir),
        lambda: accounts.save([accounts.AccountRecord("spark-a", "zz")], "spark", data_dir),
        lambda: accounts.save([accounts.AccountRecord("spark-a", "a" * 64)] * 2, "spark", data_dir),
    ):
        with pytest.raises(accounts.AccountError):
            bad()
    assert (data_dir / "accounts.json").read_text() == before
    assert not [p for p in data_dir.iterdir() if p.name.endswith(".tmp")]


def test_audit_log_one_line_per_change_no_secrets(data_dir):
    accounts.mint(cfg(), "spark", data_dir=data_dir)
    secret = grant_store.get_record(accounts.secret_name("spark-gwen")).value
    digest = json.loads((data_dir / "accounts.json").read_text())["accounts"][0]["secret_sha256"]
    accounts.set_state("spark-gwen", "spark", "revoked", data_dir)
    accounts.remove("spark-gwen", "spark", data_dir)
    ls = lines(data_dir)
    assert [x["action"] for x in ls] == ["create", "set_state", "remove"]
    assert all(x["nick"] == "spark-gwen" and x["actor"] and x["ts"] for x in ls)
    text = accounts.audit_path(data_dir).read_text()
    assert secret not in text
    assert digest not in text
    assert ls[1]["changes"]["state"] == {"from": "active", "to": "revoked"}


def test_default_data_dir_under_home(tmp_home, monkeypatch):
    # conftest's host guard redirects DEFAULT_DATA_DIR; restore the real
    # default here (HOME is already isolated by tmp_home).
    monkeypatch.setattr(accounts, "DEFAULT_DATA_DIR", "~/.culture/data")
    assert accounts.accounts_path() == tmp_home / ".culture/data/accounts.json"
