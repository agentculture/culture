"""Model inheritance: a spawned worker defaults to its parent (boss)'s model;
any parent may override with --model. (User rule: default is the parent's model.)
"""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import os  # noqa: E402

import yaml  # noqa: E402

import culture.cli.boss as boss  # noqa: E402


def _write_boss_manifest(home, boss_nick="local-boss", model="claude-opus-4-7"):
    suffix = boss_nick.split("-", 1)[1]
    bdir = os.path.join(str(home), "boss")
    os.makedirs(bdir, exist_ok=True)
    cfg = {"suffix": suffix, "backend": "claude"}
    if model:  # omit the key entirely when no explicit model (the real "unset" case)
        cfg["model"] = model
    with open(os.path.join(bdir, "culture.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f)
    with open(os.path.join(str(home), "server.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "server": {"name": "local", "host": "127.0.0.1", "port": 6667},
                "agents": {suffix: bdir},
            },
            f,
        )


def test_record_worker_writes_model_when_given(tmp_path):
    cwd = str(tmp_path)
    boss._record_worker_boss(cwd, "qa", "local-boss", model="claude-opus-4-7")
    with open(os.path.join(cwd, "culture.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["model"] == "claude-opus-4-7"
    assert data["boss"] == "local-boss"


def test_record_worker_omits_model_when_empty(tmp_path):
    cwd = str(tmp_path)
    boss._record_worker_boss(cwd, "qa", "local-boss", model="")
    with open(os.path.join(cwd, "culture.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "model" not in data  # falls back to the agent default, not a forced value


def test_inherited_model_does_not_clobber_existing(tmp_path):
    # Re-spawn with an INHERITED model must not overwrite a model the worker
    # already carries (operator hand-set); only an explicit --model overwrites.
    cwd = str(tmp_path)
    with open(os.path.join(cwd, "culture.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"suffix": "qa", "backend": "claude", "model": "claude-haiku-4-5"}, f)
    boss._record_worker_boss(
        cwd, "qa", "local-boss", model="claude-opus-4-7", overwrite_model=False
    )
    with open(os.path.join(cwd, "culture.yaml"), encoding="utf-8") as f:
        assert yaml.safe_load(f)["model"] == "claude-haiku-4-5"  # preserved


def test_explicit_model_overwrites_existing(tmp_path):
    cwd = str(tmp_path)
    with open(os.path.join(cwd, "culture.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"suffix": "qa", "backend": "claude", "model": "claude-haiku-4-5"}, f)
    boss._record_worker_boss(cwd, "qa", "local-boss", model="claude-opus-4-7", overwrite_model=True)
    with open(os.path.join(cwd, "culture.yaml"), encoding="utf-8") as f:
        assert yaml.safe_load(f)["model"] == "claude-opus-4-7"  # explicit --model wins


def test_record_worker_into_multi_agent_yaml(tmp_path):
    # Spawning into a dir that already holds a multi-agent culture.yaml must write
    # boss/channels into THIS worker's entry in the agents list — not top-level
    # (which the loader shadows, leaving the worker unassigned in #general).
    cwd = str(tmp_path)
    with open(os.path.join(cwd, "culture.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "agents": [
                    {"suffix": "ori", "backend": "claude", "channels": ["#team", "#task-ori"]},
                    {"suffix": "qa", "backend": "claude"},
                ]
            },
            f,
        )
    boss._record_worker_boss(cwd, "qa", "local-boss", model="claude-opus-4-7")
    with open(os.path.join(cwd, "culture.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entry = next(a for a in data["agents"] if a["suffix"] == "qa")
    assert entry["boss"] == "local-boss"
    assert entry["channels"] == ["#team", "#task-qa"]
    assert entry["model"] == "claude-opus-4-7"
    # No stray top-level single-agent fields shadowing the list.
    assert "boss" not in data and "channels" not in data and "suffix" not in data
    # The sibling entry is untouched.
    assert next(a for a in data["agents"] if a["suffix"] == "ori")["channels"] == [
        "#team",
        "#task-ori",
    ]


def test_boss_model_empty_when_boss_has_no_explicit_model(tmp_path, monkeypatch):
    # The key fix: _boss_model returns '' (not the hardcoded default) when the
    # boss's culture.yaml has no model — so inheritance isn't illusory.
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    _write_boss_manifest(tmp_path, model="")  # boss culture.yaml omits model
    assert boss._boss_model() == ""


def test_boss_model_read_from_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    _write_boss_manifest(tmp_path, model="claude-opus-4-7")
    assert boss._boss_model() == "claude-opus-4-7"


def test_boss_model_empty_when_boss_not_in_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-ghost")
    _write_boss_manifest(tmp_path, model="claude-opus-4-7")
    assert boss._boss_model() == ""  # unknown boss → no inherited model (falls back to default)
