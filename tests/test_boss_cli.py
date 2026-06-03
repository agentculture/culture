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


def _write_request(culture_home, rid, tool, input_dict, nick="local-w", owner="local-boss"):
    """Write a perm-queue request AND register the worker in the manifest.

    Ownership is now manifest-only (worker-written payload is not trusted),
    so a request whose helper_nick lacks a manifest entry is unowned and
    refused. Tests that want to assert "unowned" behavior should write the
    queue file directly and skip ``_register_worker`` themselves.
    """
    suffix = nick.split("-", 1)[1] if "-" in nick else nick
    if owner:
        _register_worker(culture_home, suffix, owner)
    qdir = os.path.join(str(culture_home), "perm-queue")
    os.makedirs(qdir, exist_ok=True)
    payload = {"id": rid, "helper_nick": nick, "tool_name": tool, "input": input_dict}
    if owner:
        payload["boss"] = owner  # informational (audit trail), no longer authoritative
    with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)


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
    """Register a worker in the manifest owned by `boss` (its culture.yaml boss field).

    Idempotent in one direction: if a manifest entry already exists for this
    suffix, leaves it untouched (does NOT overwrite a deliberately-set boss).
    """
    wdir = os.path.join(str(culture_home), "helpers", suffix)
    os.makedirs(wdir, exist_ok=True)
    yaml_path = os.path.join(wdir, "culture.yaml")
    if not os.path.exists(yaml_path):
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.safe_dump({"suffix": suffix, "backend": "claude", "boss": boss}, f)
    server_yaml = os.path.join(str(culture_home), "server.yaml")
    try:
        with open(server_yaml, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        data = {}
    data.setdefault("server", {"name": server, "host": "127.0.0.1", "port": 6667})
    data.setdefault("agents", {})
    data["agents"].setdefault(suffix, wdir)
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

    def test_spawn_rejects_traversal_server(self, home):
        # --server flows into worker_nick → policy_path_for; must be validated too.
        res = _run(["spawn", "evil", "--server", "../../escaped"], home)
        assert res.returncode == 1
        assert "invalid server name" in res.stderr


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

    def test_audit_foreign_worker_refused(self, home):
        # SECURITY: reading another team's audit log leaks its activity. The
        # gate must match brief/read/approve/close/deny.
        _register_worker(home, "w2", "local-boss2")
        res = _run(["audit", "w2"], home, nick="local-boss1")
        assert res.returncode == 2, (res.returncode, res.stderr)
        assert "not your worker" in res.stderr

    def test_log_foreign_worker_refused(self, home):
        # SECURITY: reading another team's daemon-log exposes engagement /
        # idle / circuit-open state across teams. Gate must match the others.
        _register_worker(home, "w2", "local-boss2")
        res = _run(["log", "w2"], home, nick="local-boss1")
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

    def test_unowned_request_is_foreign_to_all_bosses(self, home):
        # SECURITY: a request whose worker is NOT in the manifest cannot be
        # approved by ANY boss — even one that the (worker-controlled) payload
        # nominates as owner. The previous "fail-open via payload" let a buggy
        # or malicious worker forge boss=X to route requests to that boss.
        qdir = os.path.join(str(home), "perm-queue")
        os.makedirs(qdir, exist_ok=True)
        with open(os.path.join(qdir, "req-w2.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": "req-w2",
                    "helper_nick": "local-w2",  # NOT in manifest
                    "boss": "local-boss2",  # worker-written, no longer trusted
                    "tool_name": "Bash",
                    "input": {"command": "rm -rf /important"},
                },
                f,
            )
        for boss in ("local-boss1", "local-boss2"):
            res = _run(["approve", "req-w2"], home, nick=boss)
            assert res.returncode == 2, (boss, res.returncode, res.stderr)
            assert "not your worker" in res.stderr
            assert _decision(home, "req-w2") is None

    def test_payload_boss_cannot_forge_ownership(self, home):
        # SECURITY: a worker registered to boss1 cannot forge a request that
        # routes to boss2 by writing boss=boss2 in the payload — ownership is
        # derived from the MANIFEST (which says boss1), not from the worker.
        _register_worker(home, "w1", "local-boss1")
        qdir = os.path.join(str(home), "perm-queue")
        os.makedirs(qdir, exist_ok=True)
        with open(os.path.join(qdir, "req-forge.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": "req-forge",
                    "helper_nick": "local-w1",
                    "boss": "local-boss2",  # worker lies about its owner
                    "tool_name": "Bash",
                    "input": {"command": "rm -rf /etc"},
                },
                f,
            )
        # boss2 sees the forged owner but the manifest says boss1 → REFUSED.
        res = _run(["approve", "req-forge"], home, nick="local-boss2")
        assert res.returncode == 2, res.stderr
        assert "not your worker" in res.stderr
        assert _decision(home, "req-forge") is None
        # boss1 (the real owner per manifest) can act normally.
        res = _run(["pending"], home, nick="local-boss1")
        assert "req-forge" in res.stdout

    def test_pending_hides_foreign_via_manifest_owner(self, home):
        # SECURITY: pending lists only the requests whose worker is owned by
        # ME per the MANIFEST. Payload's `boss` field is ignored — see
        # test_payload_boss_cannot_forge_ownership.
        _register_worker(home, "w1", "local-boss1")
        _register_worker(home, "w2", "local-boss2")
        qdir = os.path.join(str(home), "perm-queue")
        os.makedirs(qdir, exist_ok=True)
        for rid, payload_boss, nick in (
            ("req-mine", "local-boss1", "local-w1"),
            ("req-theirs", "local-boss2", "local-w2"),
        ):
            with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "id": rid,
                        "helper_nick": nick,
                        "boss": payload_boss,  # informational; ignored by gate
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
        _register_worker(home, "w1", "local-boss1")
        _register_worker(home, "w2", "local-boss2")
        _write_request(home, "req-w2", "Edit", {"file_path": "/a"}, nick="local-w2", owner=None)
        _write_request(home, "req-mine", "Edit", {"file_path": "/b"}, nick="local-w1", owner=None)
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
        # The system_prompt grounds the CC session as the boss itself —
        # not a separate "manager agent" behind it.
        assert "a boss on the culture mesh" in cfg.get("system_prompt", "")
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


class TestRecordWorkerBossChannels:
    """Test that _record_worker_boss correctly writes extra_channels into culture.yaml."""

    def test_extra_channels_written_to_yaml(self, home):
        from culture.cli.boss import _record_worker_boss

        cwd = os.path.join(str(home), "helpers", "w1")
        os.makedirs(cwd, exist_ok=True)
        _record_worker_boss(
            cwd,
            "w1",
            "local-boss",
            extra_channels=["#joint-fixes", "#design"],
        )
        with open(os.path.join(cwd, "culture.yaml")) as f:
            data = yaml.safe_load(f)
        # #team is removed from defaults (AD-4) — workers default to
        # their own #task-<suffix> only.
        assert "#team" not in data["channels"]
        assert "#task-w1" in data["channels"]
        assert "#joint-fixes" in data["channels"]
        assert "#design" in data["channels"]

    def test_no_extra_channels_default(self, home):
        from culture.cli.boss import _record_worker_boss

        cwd = os.path.join(str(home), "helpers", "w2")
        os.makedirs(cwd, exist_ok=True)
        _record_worker_boss(cwd, "w2", "local-boss")
        with open(os.path.join(cwd, "culture.yaml")) as f:
            data = yaml.safe_load(f)
        # Default channel set is the worker's own #task-<suffix> only.
        assert data["channels"] == ["#task-w2"]

    def test_extra_channels_no_duplicates(self, home):
        from culture.cli.boss import _record_worker_boss

        cwd = os.path.join(str(home), "helpers", "w3")
        os.makedirs(cwd, exist_ok=True)
        # #task-bot is already in the base list as #task-w3 is the default;
        # passing #task-w3 again must not duplicate the entry.
        _record_worker_boss(
            cwd,
            "w3",
            "local-boss",
            extra_channels=["#task-w3", "#joint-fixes"],
        )
        with open(os.path.join(cwd, "culture.yaml")) as f:
            data = yaml.safe_load(f)
        assert data["channels"].count("#task-w3") == 1
        assert "#joint-fixes" in data["channels"]

    def test_channels_flag_parsing(self):
        """Verify the channel parsing logic used in _cmd_spawn."""
        raw = "#joint-fixes,#design, review"
        extra_channels = [
            ch.strip() if ch.strip().startswith("#") else f"#{ch.strip()}"
            for ch in raw.split(",")
            if ch.strip()
        ]
        assert extra_channels == ["#joint-fixes", "#design", "#review"]

    def test_empty_channels_flag(self):
        """Empty --channels string produces no extra channels."""
        raw = ""
        extra_channels = [
            ch.strip() if ch.strip().startswith("#") else f"#{ch.strip()}"
            for ch in (raw.split(",") if raw else [])
            if ch.strip()
        ]
        assert extra_channels == []

    def test_multi_agent_yaml_extra_channels(self, home):
        """Extra channels are written into the correct agent entry in multi-agent yaml."""
        from culture.cli.boss import _record_worker_boss

        cwd = os.path.join(str(home), "helpers", "multi")
        os.makedirs(cwd, exist_ok=True)
        # Pre-create a multi-agent culture.yaml
        multi_data = {"agents": [{"suffix": "alpha", "backend": "claude"}]}
        with open(os.path.join(cwd, "culture.yaml"), "w") as f:
            yaml.safe_dump(multi_data, f)
        _record_worker_boss(
            cwd,
            "alpha",
            "local-boss",
            extra_channels=["#joint-fixes"],
        )
        with open(os.path.join(cwd, "culture.yaml")) as f:
            data = yaml.safe_load(f)
        entry = data["agents"][0]
        assert "#joint-fixes" in entry["channels"]
        # #team is removed from defaults (AD-4).
        assert "#team" not in entry["channels"]
        assert "#task-alpha" in entry["channels"]


class TestSpawnBossPrefix:
    """Phase 4.8 — when ``--boss <project-name>`` is set, the resulting
    worker nick is ``<project-name>-<worker-suffix>``. We test the
    naming logic by mocking the subprocess.run + IRC calls and asserting
    on the args passed to ``agent create``.
    """

    def _run_spawn(self, home, args, boss_env="local-boss", monkeypatch=None):
        """Drive ``_cmd_spawn`` directly with mocked side effects."""
        import argparse
        from unittest.mock import patch

        from culture.cli import boss as boss_mod

        captured = {"create_args": None, "register_args": None, "start_args": None}

        def fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""

            if isinstance(cmd, list) and "create" in cmd:
                captured["create_args"] = list(cmd)
            elif isinstance(cmd, list) and "register" in cmd:
                captured["register_args"] = list(cmd)
            elif isinstance(cmd, list) and "start" in cmd:
                captured["start_args"] = list(cmd)
            return R()

        ns = argparse.Namespace(
            name=args["name"],
            boss=args.get("boss", ""),
            server=args.get("server"),
            cwd=args.get("cwd"),
            model=args.get("model", ""),
            channels=args.get("channels", ""),
            role=args.get("role", ""),
            topic=args.get("topic", ""),
            config="server.yaml",
        )

        env = {"CULTURE_HOME": str(home), "CULTURE_NICK": boss_env}
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(boss_mod, "subprocess") as sub_mock,
            patch.object(boss_mod, "_boss_irc", lambda *a, **k: {"ok": True}),
            patch.object(boss_mod, "seed_helper_policy", lambda nick: None),
        ):
            sub_mock.run.side_effect = fake_run
            try:
                boss_mod._cmd_spawn(ns)
            except SystemExit as exc:
                if exc.code:
                    raise
        return captured

    def test_explicit_boss_auto_prefixes_worker_nick(self, home):
        captured = self._run_spawn(home, {"name": "qa", "boss": "fork-rearch"})
        # ``agent create --server fork-rearch --nick qa`` produces
        # full_nick ``fork-rearch-qa``.
        assert captured["create_args"] is not None
        cmd = captured["create_args"]
        assert "--server" in cmd
        assert "fork-rearch" in cmd
        assert "--nick" in cmd
        i = cmd.index("--nick")
        assert cmd[i + 1] == "qa"
        # ``agent start`` is called with the full nick.
        assert "fork-rearch-qa" in captured["start_args"]

    def test_explicit_boss_strips_redundant_prefix(self, home):
        """``mesh spawn fork-rearch-qa --boss fork-rearch`` must not
        double-prefix to ``fork-rearch-fork-rearch-qa``."""
        captured = self._run_spawn(home, {"name": "fork-rearch-qa", "boss": "fork-rearch"})
        assert captured["create_args"] is not None
        cmd = captured["create_args"]
        i = cmd.index("--nick")
        # Redundant prefix stripped → suffix should be plain ``qa``.
        assert cmd[i + 1] == "qa"
        assert "fork-rearch-qa" in captured["start_args"]
        assert "fork-rearch-fork-rearch-qa" not in captured["start_args"]

    def test_no_boss_flag_uses_culture_nick_legacy_server(self, home):
        # No --boss → falls back to legacy single-server flow:
        # server = first hyphen-split of CULTURE_NICK.
        captured = self._run_spawn(home, {"name": "qa"}, boss_env="local-boss")
        assert captured["create_args"] is not None
        cmd = captured["create_args"]
        i = cmd.index("--server")
        # local-boss → server "local"
        assert cmd[i + 1] == "local"
        i = cmd.index("--nick")
        assert cmd[i + 1] == "qa"
        # Final nick = local-qa
        assert "local-qa" in captured["start_args"]
