"""Model + thinking inheritance: a spawned worker imitates its parent (boss)'s
RUNTIME model and thinking — read from the boss's daemon-log, not its yaml,
so there are no hardcoded model strings anywhere in the inheritance chain.
The SDK picks the current Claude when no model is set; that choice lands in
the boss's agent_start daemon-log record and is propagated forward.
"""

from __future__ import annotations

import json

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import os  # noqa: E402

import yaml  # noqa: E402

import culture.cli.boss as boss  # noqa: E402


def _write_boss_daemon_log(home, boss_nick="local-boss", model="", thinking=""):
    """Write a synthetic ``agent_start`` record to the boss's daemon-log. This
    is what a live boss daemon records on startup, capturing the model+thinking
    it's actually running with."""
    log_dir = os.path.join(str(home), "daemon-log")
    os.makedirs(log_dir, exist_ok=True)
    rec = {
        "ts": "2026-05-30T00:00:00.000Z",
        "nick": boss_nick,
        "action": "agent_start",
        "detail": {"model": model, "thinking": thinking, "directory": "/x"},
    }
    with open(os.path.join(log_dir, f"{boss_nick}.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


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
    # #team is removed from defaults (AD-4 of the mesh rearchitecture);
    # workers default to #task-<own> only.
    assert entry["channels"] == ["#task-qa"]
    assert "#team" not in entry["channels"]
    assert entry["model"] == "claude-opus-4-7"
    # No stray top-level single-agent fields shadowing the list.
    assert "boss" not in data and "channels" not in data and "suffix" not in data
    # The sibling entry is untouched (pre-existing data is preserved verbatim).
    assert next(a for a in data["agents"] if a["suffix"] == "ori")["channels"] == [
        "#team",
        "#task-ori",
    ]


def test_boss_inherits_empty_when_no_daemon_log(tmp_path, monkeypatch):
    # Boss never ran → daemon-log absent → no inherited model/thinking. The
    # caller writes empty strings and the SDK picks the current Claude at the
    # worker's startup. No hardcoded fallback anywhere.
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    assert boss._boss_inherits() == ("", "")
    assert boss._boss_model() == ""


def test_boss_inherits_from_daemon_log_model(tmp_path, monkeypatch):
    # The boss daemon recorded its RUNTIME model on startup → spawn inherits
    # that exact value, not whatever the yaml might say.
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    _write_boss_daemon_log(tmp_path, model="claude-opus-4-8", thinking="")
    model, thinking = boss._boss_inherits()
    assert model == "claude-opus-4-8"
    assert thinking == ""
    # Back-compat alias.
    assert boss._boss_model() == "claude-opus-4-8"


def test_boss_inherits_both_model_and_thinking(tmp_path, monkeypatch):
    # Thinking is inherited the same way as model — workers imitate parent
    # effort level, not just parent model.
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    _write_boss_daemon_log(tmp_path, model="claude-opus-4-8", thinking="high")
    assert boss._boss_inherits() == ("claude-opus-4-8", "high")


def test_boss_inherits_uses_most_recent_agent_start(tmp_path, monkeypatch):
    # Multiple agent_start records (boss restarted) → most recent wins.
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    log_dir = os.path.join(str(tmp_path), "daemon-log")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "local-boss.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": "2026-05-29T00:00:00.000Z",
                    "nick": "local-boss",
                    "action": "agent_start",
                    "detail": {"model": "claude-opus-4-7", "thinking": "medium"},
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "ts": "2026-05-29T00:05:00.000Z",
                    "nick": "local-boss",
                    "action": "agent_exit",
                    "detail": {"exit_code": 0},
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "ts": "2026-05-30T00:00:00.000Z",
                    "nick": "local-boss",
                    "action": "agent_start",
                    "detail": {"model": "claude-opus-4-8", "thinking": "high"},
                }
            )
            + "\n"
        )
    assert boss._boss_inherits() == ("claude-opus-4-8", "high")


def test_record_worker_writes_thinking_too(tmp_path):
    # Inheritance covers thinking, not just model.
    cwd = str(tmp_path)
    boss._record_worker_boss(cwd, "qa", "local-boss", model="claude-opus-4-8", thinking="high")
    with open(os.path.join(cwd, "culture.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["model"] == "claude-opus-4-8"
    assert data["thinking"] == "high"


def _append_daemon_log(home, action, detail, boss_nick="local-boss", ts="2026-05-30T00:00:01.000Z"):
    log_dir = os.path.join(str(home), "daemon-log")
    os.makedirs(log_dir, exist_ok=True)
    rec = {"ts": ts, "nick": boss_nick, "action": action, "detail": detail}
    with open(os.path.join(log_dir, f"{boss_nick}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def test_boss_inherits_falls_back_to_model_resolved_when_yaml_empty(tmp_path, monkeypatch):
    # YAML omitted model (inheritance-friendly boss config) → agent_start.model=''
    # → fall back to the model_resolved event the daemon latches on the first
    # AssistantMessage. This is the fix that stops a YAML-less boss from leaking
    # the SDK CLI's hardcoded default down to workers.
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    _write_boss_daemon_log(tmp_path, model="", thinking="high")
    _append_daemon_log(tmp_path, "model_resolved", {"model": "claude-opus-4-8"})
    assert boss._boss_inherits() == ("claude-opus-4-8", "high")


def test_boss_inherits_prefers_yaml_model_over_resolved_when_set(tmp_path, monkeypatch):
    # YAML explicitly pinned a model → agent_start.model is non-empty → we use
    # IT, even if a later model_resolved event landed (which can happen when
    # the SDK ignores or remaps the requested model — we still want to honor
    # the operator's explicit choice in inheritance).
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    _write_boss_daemon_log(tmp_path, model="claude-opus-4-7", thinking="high")
    _append_daemon_log(tmp_path, "model_resolved", {"model": "claude-opus-4-8"})
    assert boss._boss_inherits() == ("claude-opus-4-7", "high")


def test_boss_inherits_ignores_model_resolved_from_prior_session(tmp_path, monkeypatch):
    # The daemon resets its model_resolved latch on every restart, but if an
    # OLDER session's resolved event sits in the log BEFORE the latest
    # agent_start, the current YAML-empty session should not pick it up.
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    log_dir = os.path.join(str(tmp_path), "daemon-log")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "local-boss.jsonl")
    # Prior session: started with no YAML model, resolved to 4-6 (stale default).
    records = [
        {
            "ts": "2026-05-29T00:00:00.000Z",
            "nick": "local-boss",
            "action": "agent_start",
            "detail": {"model": "", "thinking": "high"},
        },
        {
            "ts": "2026-05-29T00:00:01.000Z",
            "nick": "local-boss",
            "action": "model_resolved",
            "detail": {"model": "claude-opus-4-6"},
        },
        {
            "ts": "2026-05-29T01:00:00.000Z",
            "nick": "local-boss",
            "action": "agent_exit",
            "detail": {"exit_code": 0},
        },
        # Current session: started fresh, YAML still empty, NO model_resolved yet
        # (the first AssistantMessage hasn't landed).
        {
            "ts": "2026-05-30T00:00:00.000Z",
            "nick": "local-boss",
            "action": "agent_start",
            "detail": {"model": "", "thinking": "high"},
        },
    ]
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    # Stale model_resolved (pre-current-agent_start) is ignored → empty model.
    model, thinking = boss._boss_inherits()
    assert model == ""
    assert thinking == "high"


def test_boss_inherits_picks_most_recent_resolved_within_session(tmp_path, monkeypatch):
    # Within one session, multiple model_resolved events can exist if the
    # daemon code one day decides to refresh the latch (e.g. SDK switch
    # mid-session). The most recent one wins. Today's daemon latches once
    # per restart, but the reader is resilient either way.
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    log_dir = os.path.join(str(tmp_path), "daemon-log")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "local-boss.jsonl")
    records = [
        {
            "ts": "2026-05-30T00:00:00.000Z",
            "nick": "local-boss",
            "action": "agent_start",
            "detail": {"model": "", "thinking": "high"},
        },
        {
            "ts": "2026-05-30T00:00:01.000Z",
            "nick": "local-boss",
            "action": "model_resolved",
            "detail": {"model": "claude-opus-4-7"},
        },
        {
            "ts": "2026-05-30T00:01:00.000Z",
            "nick": "local-boss",
            "action": "model_resolved",
            "detail": {"model": "claude-opus-4-8"},
        },
    ]
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    assert boss._boss_inherits() == ("claude-opus-4-8", "high")


def test_boss_inherits_orphan_model_resolved_returns_empty(tmp_path, monkeypatch):
    # Qodo PR #24 #4: a daemon-log that contains ONLY model_resolved
    # records (no agent_start anchor — corrupt file, truncated log, or
    # pre-v8.18.6 instrumentation) must NOT propagate the model. The
    # docstring contract says no agent_start -> return ("", "").
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    monkeypatch.setenv("CULTURE_NICK", "local-boss")
    log_dir = os.path.join(str(tmp_path), "daemon-log")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "local-boss.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": "2026-05-30T00:00:01.000Z",
                    "nick": "local-boss",
                    "action": "model_resolved",
                    "detail": {"model": "claude-opus-4-8"},
                }
            )
            + "\n"
        )
    # No agent_start anchors the model_resolved → contract says ("", "").
    assert boss._boss_inherits() == ("", "")
