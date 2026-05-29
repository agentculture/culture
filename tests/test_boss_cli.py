"""Tests for the `culture boss` CLI (orchestration surface).

Drives the CLI via subprocess against an isolated CULTURE_HOME so the
queue/decision/ceiling/identity behavior is exercised end-to-end. Commands that
need a running daemon (brief/read/spawn/status/close) are not covered here — they
require a live mesh and are covered by manual smoke per the spec.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml


def _run(args, culture_home, nick="local-boss", **kw):
    env = dict(os.environ)
    env["CULTURE_HOME"] = str(culture_home)
    if nick is not None:
        env["CULTURE_NICK"] = nick
    elif "CULTURE_NICK" in env:
        del env["CULTURE_NICK"]
    return subprocess.run(
        [sys.executable, "-m", "culture", "boss", *args],
        env=env,
        capture_output=True,
        text=True,
        **kw,
    )


def _run_agent(args, culture_home, nick="local-boss", **kw):
    env = dict(os.environ)
    env["CULTURE_HOME"] = str(culture_home)
    if nick is not None:
        env["CULTURE_NICK"] = nick
    elif "CULTURE_NICK" in env:
        del env["CULTURE_NICK"]
    return subprocess.run(
        [sys.executable, "-m", "culture", "agent", *args],
        env=env,
        capture_output=True,
        text=True,
        **kw,
    )


@pytest.fixture
def home(tmp_path):
    return tmp_path


def _write_request(culture_home, rid, tool, input_dict, nick="local-w"):
    qdir = os.path.join(str(culture_home), "perm-queue")
    os.makedirs(qdir, exist_ok=True)
    with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
        json.dump({"id": rid, "helper_nick": nick, "tool_name": tool, "input": input_dict}, f)


def _decision(culture_home, rid):
    path = os.path.join(str(culture_home), "perm-decisions", f"{rid}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _seed_ceiling(culture_home, nick="local-boss"):
    from culture.clients._perm_broker import DEFAULT_BOSS_CEILING

    d = os.path.join(str(culture_home), "boss-policy")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{nick}.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"grant_ceiling": DEFAULT_BOSS_CEILING}, f)


def _register_worker(culture_home, suffix, boss, server="local"):
    """Register a worker in the manifest owned by `boss` (its culture.yaml boss field)."""
    wdir = os.path.join(str(culture_home), "helpers", suffix)
    os.makedirs(wdir, exist_ok=True)
    with open(os.path.join(wdir, "culture.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"suffix": suffix, "backend": "claude", "boss": boss}, f)
    server_yaml = os.path.join(str(culture_home), "server.yaml")
    try:
        with open(server_yaml, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        data = {}
    data.setdefault("server", {"name": server, "host": "127.0.0.1", "port": 6667})
    data.setdefault("agents", {})
    data["agents"][suffix] = wdir
    with open(server_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


class TestApproveDeny:
    def test_approve_in_ceiling_writes_decision(self, home):
        _seed_ceiling(home)
        _write_request(home, "req-ok", "Edit", {"file_path": "/a.py"})
        res = _run(["approve", "req-ok"], home)
        assert res.returncode == 0, res.stderr
        d = _decision(home, "req-ok")
        assert d is not None and d["verdict"] == "allow" and d["scope"] == "once"

    def test_approve_always_sets_scope(self, home):
        _write_request(home, "req-always", "Write", {"file_path": "/a.py"})
        res = _run(["approve", "req-always", "--always"], home)
        assert res.returncode == 0, res.stderr
        assert _decision(home, "req-always")["scope"] == "always"

    def test_approve_above_ceiling_refused(self, home):
        _seed_ceiling(home)
        _write_request(home, "req-mcp", "mcp__gmail__send", {"to": "x@y.z"})
        res = _run(["approve", "req-mcp"], home)
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "above your grant ceiling" in res.stderr
        # No decision written — escalation, not grant.
        assert _decision(home, "req-mcp") is None

    def test_deny_writes_decision_with_reason(self, home):
        _write_request(home, "req-deny", "Bash", {"command": "rm -rf /"})
        res = _run(["deny", "req-deny", "too", "dangerous"], home)
        assert res.returncode == 0, res.stderr
        d = _decision(home, "req-deny")
        assert d["verdict"] == "deny" and d["reason"] == "too dangerous"

    def test_approve_missing_request(self, home):
        res = _run(["approve", "nope"], home)
        assert res.returncode == 1
        assert "no pending request" in res.stderr

    def test_double_decision_refused(self, home):
        _write_request(home, "req-dup", "Edit", {"file_path": "/a"})
        first = _run(["approve", "req-dup"], home)
        assert first.returncode == 0
        second = _run(["deny", "req-dup", "changed mind"], home)
        assert second.returncode == 1
        assert "already exists" in second.stderr

    def test_no_culture_nick_errors(self, home):
        _write_request(home, "req-x", "Edit", {"file_path": "/a"})
        res = _run(["approve", "req-x"], home, nick=None)
        assert res.returncode == 1
        assert "CULTURE_NICK" in res.stderr


class TestSpawnValidation:
    def test_spawn_rejects_path_traversal_name(self, home):
        res = _run(["spawn", "../../evil"], home)
        assert res.returncode == 1
        assert "invalid worker name" in res.stderr
        # Nothing was created outside the helpers dir.
        assert not os.path.exists(os.path.join(str(home), "..", "evil"))

    def test_spawn_rejects_slash_name(self, home):
        res = _run(["spawn", "a/b"], home)
        assert res.returncode == 1
        assert "invalid worker name" in res.stderr


class TestNameValidationAllSubcommands:
    # Qodo: every subcommand taking <name> must validate it (path safety), not
    # just spawn — audit/log build paths via audit_path_for/daemon_log_path_for.
    @pytest.mark.parametrize("cmd", ["audit", "log", "brief", "read", "close"])
    def test_traversal_name_rejected(self, home, cmd):
        argv = [cmd, "../../etc/passwd"] + (["x"] if cmd == "brief" else [])
        res = _run(argv, home)
        assert res.returncode == 1
        assert "invalid worker name" in res.stderr

    def test_approve_rejects_traversal_id(self, home):
        res = _run(["approve", "../../evil"], home)
        # read_request returns None for an invalid id → "no pending request".
        assert res.returncode == 1
        assert "no pending request" in res.stderr


class TestWriteDecisionCleanup:
    def test_failed_write_leaves_no_placeholder(self, home, monkeypatch):
        # If the atomic write fails after the O_EXCL placeholder is created, the
        # placeholder must be removed so the worker doesn't poll a 0-byte file
        # forever and a retry isn't blocked.
        import culture.clients._perm_broker as pb

        monkeypatch.setenv("CULTURE_HOME", str(home))

        def _boom(dest, payload):
            raise OSError("disk full")

        monkeypatch.setattr(pb, "_atomic_write_json", _boom)
        with pytest.raises(OSError):
            pb.write_decision("req-fail", verdict="allow")
        dest = os.path.join(str(home), "perm-decisions", "req-fail.json")
        assert not os.path.exists(dest)
        # A retry is now possible (not blocked by a stale placeholder).
        monkeypatch.undo()
        monkeypatch.setenv("CULTURE_HOME", str(home))
        pb.write_decision("req-fail", verdict="allow")
        assert os.path.exists(dest)


class TestPending:
    def test_pending_lists_requests(self, home):
        _write_request(home, "req-1", "Edit", {"file_path": "/a.py"})
        _write_request(home, "req-2", "Bash", {"command": "ls"})
        res = _run(["pending"], home)
        assert res.returncode == 0
        assert "req-1" in res.stdout and "req-2" in res.stdout
        assert "Edit" in res.stdout and "Bash" in res.stdout


class TestAuditHardening:
    # Fixes confirmed by the verification workflow's adversarial pass.
    def test_deny_nonexistent_request_refused_no_orphan(self, home):
        # deny of a valid-format-but-absent id must NOT write an orphan decision.
        res = _run(["deny", "req-nope-abc", "reason"], home)
        assert res.returncode == 1, (res.returncode, res.stderr)
        assert "no pending request" in res.stderr
        assert _decision(home, "req-nope-abc") is None

    def test_brief_foreign_worker_refused(self, home):
        _register_worker(home, "w2", "local-boss2")
        res = _run(["brief", "w2", "do it"], home, nick="local-boss1")
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "not your worker" in res.stderr

    def test_read_foreign_worker_refused(self, home):
        _register_worker(home, "w2", "local-boss2")
        res = _run(["read", "w2"], home, nick="local-boss1")
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "not your worker" in res.stderr

    def test_init_rejects_traversal_nick(self, home):
        res = _run(["init", "--nick", "../../evil", "--server", "local"], home)
        assert res.returncode == 1
        assert "invalid worker name" in res.stderr

    def test_init_rejects_bad_server(self, home):
        res = _run(["init", "--nick", "boss", "--server", "../x"], home)
        assert res.returncode == 1
        assert "invalid server name" in res.stderr

    def test_isolation_holds_via_recorded_owner_without_manifest(self, home):
        # The fail-open fix: a request carrying boss=local-boss2 is foreign to
        # local-boss1 even with NO manifest entry for the worker (worker's
        # culture.yaml missing/corrupt). Ownership comes from the request itself.
        qdir = os.path.join(str(home), "perm-queue")
        os.makedirs(qdir, exist_ok=True)
        with open(os.path.join(qdir, "req-w2.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": "req-w2",
                    "helper_nick": "local-w2",
                    "boss": "local-boss2",
                    "tool_name": "Bash",
                    "input": {"command": "rm -rf /important"},
                },
                f,
            )
        res = _run(["approve", "req-w2"], home, nick="local-boss1")
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "not your worker" in res.stderr
        assert _decision(home, "req-w2") is None

    def test_pending_hides_foreign_via_recorded_owner(self, home):
        qdir = os.path.join(str(home), "perm-queue")
        os.makedirs(qdir, exist_ok=True)
        for rid, owner, nick in (
            ("req-mine", "local-boss1", "local-w1"),
            ("req-theirs", "local-boss2", "local-w2"),
        ):
            with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "id": rid,
                        "helper_nick": nick,
                        "boss": owner,
                        "tool_name": "Edit",
                        "input": {"file_path": "/a"},
                    },
                    f,
                )
        res = _run(["pending"], home, nick="local-boss1")
        assert res.returncode == 0, res.stderr
        assert "req-mine" in res.stdout and "req-theirs" not in res.stdout


class TestMultiBossIsolation:
    # Each boss manages only its own team: a request from a worker owned by
    # another boss must be invisible + un-actionable to this boss. The dashboard
    # (the human) remains the all-teams view.
    def test_foreign_worker_hidden_from_pending(self, home):
        _register_worker(home, "w2", "local-boss2")
        _write_request(home, "req-w2", "Edit", {"file_path": "/a"}, nick="local-w2")
        _write_request(home, "req-mine", "Edit", {"file_path": "/b"}, nick="local-w1")
        res = _run(["pending"], home, nick="local-boss1")
        assert res.returncode == 0, res.stderr
        assert "req-mine" in res.stdout
        assert "req-w2" not in res.stdout

    def test_foreign_worker_approve_refused(self, home):
        _register_worker(home, "w2", "local-boss2")
        _write_request(home, "req-w2", "Edit", {"file_path": "/a"}, nick="local-w2")
        res = _run(["approve", "req-w2"], home, nick="local-boss1")
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "not your worker" in res.stderr
        assert _decision(home, "req-w2") is None

    def test_foreign_worker_deny_refused(self, home):
        _register_worker(home, "w2", "local-boss2")
        _write_request(home, "req-w2", "Bash", {"command": "ls"}, nick="local-w2")
        res = _run(["deny", "req-w2", "no"], home, nick="local-boss1")
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "not your worker" in res.stderr
        assert _decision(home, "req-w2") is None

    def test_own_worker_still_approvable(self, home):
        _register_worker(home, "w1", "local-boss1")
        _write_request(home, "req-w1", "Edit", {"file_path": "/a"}, nick="local-w1")
        res = _run(["approve", "req-w1"], home, nick="local-boss1")
        assert res.returncode == 0, res.stderr
        assert _decision(home, "req-w1")["verdict"] == "allow"


class TestCloseAuthority:
    # "Only a parent can close its children": no self-close, a boss closes only
    # its own workers, a worker closes nothing, the human (no CULTURE_NICK) is root.
    def _cfg(self, home):
        return os.path.join(str(home), "server.yaml")

    def test_agent_cannot_stop_itself(self, home):
        _register_worker(home, "w1", "local-boss1")
        res = _run_agent(["stop", "local-w1", "--config", self._cfg(home)], home, nick="local-w1")
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "cannot close itself" in res.stderr

    def test_parent_boss_can_stop_its_worker(self, home):
        _register_worker(home, "w1", "local-boss1")
        res = _run_agent(
            ["stop", "local-w1", "--config", self._cfg(home)], home, nick="local-boss1"
        )
        assert res.returncode == 0, res.stderr  # not running → no-op, but allowed

    def test_foreign_boss_cannot_stop_worker(self, home):
        _register_worker(home, "w1", "local-boss1")
        res = _run_agent(
            ["stop", "local-w1", "--config", self._cfg(home)], home, nick="local-boss2"
        )
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "not your child" in res.stderr

    def test_human_no_nick_can_stop_anything(self, home):
        _register_worker(home, "w1", "local-boss1")
        res = _run_agent(["stop", "local-w1", "--config", self._cfg(home)], home, nick=None)
        assert res.returncode == 0, res.stderr  # root authority

    def test_boss_close_foreign_worker_refused(self, home):
        _register_worker(home, "w1", "local-boss1")
        res = _run(["close", "w1"], home, nick="local-boss2")
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "not your worker" in res.stderr

    def test_agent_stop_all_skips_non_children_without_error(self, home):
        # `--all` by a boss stops only its own children; self/foreign are skipped
        # (not refused), so the command still succeeds.
        _register_worker(home, "w1", "local-boss1")
        _register_worker(home, "w2", "local-boss2")
        res = _run_agent(["stop", "--all", "--config", self._cfg(home)], home, nick="local-boss1")
        assert res.returncode == 0, (res.returncode, res.stderr)
        assert "REFUSED" not in res.stderr


class TestCleanup:
    def test_cleanup_removes_dead_helper_request_and_orphan_decision(self, home):
        # No server manifest → no running agents → every queued request is stale.
        _write_request(home, "req-dead", "Edit", {"file_path": "/a.py"}, nick="local-ghost")
        ddir = os.path.join(str(home), "perm-decisions")
        os.makedirs(ddir, exist_ok=True)
        with open(os.path.join(ddir, "req-orphan.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "req-orphan", "verdict": "allow"}, f)

        res = _run(["cleanup", "--config", os.path.join(str(home), "no-server.yaml")], home)
        assert res.returncode == 0, res.stderr
        assert "1 stale request" in res.stdout and "1 orphan decision" in res.stdout
        assert not os.path.exists(os.path.join(str(home), "perm-queue", "req-dead.json"))
        assert not os.path.exists(os.path.join(ddir, "req-orphan.json"))


class TestInit:
    def test_init_creates_boss_identity(self, home):
        res = _run(["init", "--nick", "boss", "--server", "local", "--channel", "#boss"], home)
        assert res.returncode == 0, res.stderr
        # Ceiling seeded.
        ceiling = os.path.join(str(home), "boss-policy", "local-boss.yaml")
        assert os.path.exists(ceiling)
        # Boss cwd culture.yaml has a manager system_prompt + boss tag, no perm-policy.
        boss_cwd = os.path.join(str(home), "boss")
        with open(os.path.join(boss_cwd, "culture.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert "boss" in cfg.get("tags", [])
        assert "manager agent" in cfg.get("system_prompt", "")
        assert not os.path.exists(os.path.join(str(home), "perm-policy", "local-boss.yaml"))
        # Skill copied into the boss cwd.
        assert os.path.exists(os.path.join(boss_cwd, ".claude", "skills", "boss", "SKILL.md"))

    def test_init_removes_stray_perm_policy(self, home):
        # A boss must never be permission-supervised.
        ppdir = os.path.join(str(home), "perm-policy")
        os.makedirs(ppdir, exist_ok=True)
        with open(os.path.join(ppdir, "local-boss.yaml"), "w", encoding="utf-8") as f:
            f.write("auto_allow: []\n")
        res = _run(["init", "--nick", "boss", "--server", "local"], home)
        assert res.returncode == 0, res.stderr
        assert not os.path.exists(os.path.join(ppdir, "local-boss.yaml"))
