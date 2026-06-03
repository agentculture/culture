"""Tests for the file-backed permission broker.

Per project convention: real I/O against an isolated tmp dir.  No mocks of
the broker's filesystem layer.  Tests honor ``CULTURE_HOME`` via monkeypatch
so they are xdist-safe.
"""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
import yaml  # noqa: E402
from claude_agent_sdk import (  # noqa: E402
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from culture.clients._perm_broker import (  # noqa: E402
    DEFAULT_POLICY,
    PermissionBroker,
    culture_home,
    has_policy_file,
    match_policy,
    policy_path_for,
    write_default_policy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    """Isolate ``CULTURE_HOME`` per test."""
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def _empty_context() -> ToolPermissionContext:
    return ToolPermissionContext(signal=None, suggestions=[])


# ---------------------------------------------------------------------------
# Pure policy matcher
# ---------------------------------------------------------------------------


class TestPolicyMatcher:
    def test_exact_tool_match_allows(self):
        policy = {"auto_allow": [{"tool": "Read"}]}
        assert match_policy("Read", {}, policy) == "allow"

    def test_exact_tool_match_misses(self):
        policy = {"auto_allow": [{"tool": "Read"}]}
        assert match_policy("Write", {}, policy) is None

    def test_regex_tool_pattern(self):
        policy = {"auto_allow": [{"tool": "mcp__.*"}]}
        assert match_policy("mcp__gmail__send", {}, policy) == "allow"
        assert match_policy("Bash", {}, policy) is None

    def test_auto_deny_takes_priority_over_auto_allow(self):
        policy = {
            "auto_deny": [{"tool": "Bash"}],
            "auto_allow": [{"tool": "Bash"}],
        }
        assert match_policy("Bash", {"command": "ls"}, policy) == "deny"

    def test_bash_input_regex_match(self):
        policy = {"auto_allow": [{"tool": "Bash", "input_regex": r"^ls\b"}]}
        assert match_policy("Bash", {"command": "ls -la"}, policy) == "allow"
        assert match_policy("Bash", {"command": "rm -rf /"}, policy) is None

    def test_bash_default_safe_read_regex(self):
        policy = DEFAULT_POLICY
        for cmd in ("ls", "ls -la", "git status", "git diff HEAD", "rg foo src/"):
            assert match_policy("Bash", {"command": cmd}, policy) == "allow", cmd
        for cmd in ("rm -rf /", "curl https://evil.tld", "git push"):
            assert match_policy("Bash", {"command": cmd}, policy) is None, cmd

    def test_empty_policy_falls_through(self):
        assert match_policy("Read", {}, {}) is None
        assert match_policy("Read", {}, {"auto_allow": []}) is None

    def test_malformed_rule_is_skipped(self):
        policy = {"auto_allow": [{"not_a_tool_field": 1}, {"tool": "Read"}]}
        assert match_policy("Read", {}, policy) == "allow"

    def test_input_regex_against_non_projectable_tool_does_not_match(self):
        # ``Read`` has no input projection; a rule with input_regex against
        # Read should never match.
        policy = {"auto_allow": [{"tool": "Read", "input_regex": ".*"}]}
        assert match_policy("Read", {"file_path": "x"}, policy) is None


# ---------------------------------------------------------------------------
# Policy file seed / load
# ---------------------------------------------------------------------------


class TestPolicyFile:
    def test_write_default_policy_creates_file(self, culture_root):
        path = write_default_policy("local-foo")
        assert os.path.exists(path)
        with open(path) as f:
            policy = yaml.safe_load(f)
        assert policy == DEFAULT_POLICY

    def test_write_default_policy_is_idempotent(self, culture_root):
        path = write_default_policy("local-foo")
        # Mutate the file then re-seed; existing file must not be overwritten.
        with open(path, "w") as f:
            yaml.safe_dump({"auto_allow": [{"tool": "Custom"}]}, f)
        write_default_policy("local-foo")
        with open(path) as f:
            policy = yaml.safe_load(f)
        assert policy == {"auto_allow": [{"tool": "Custom"}]}

    def test_has_policy_file_reflects_filesystem(self, culture_root):
        assert has_policy_file("local-foo") is False
        write_default_policy("local-foo")
        assert has_policy_file("local-foo") is True

    def test_has_policy_file_empty_nick(self, culture_root):
        write_default_policy("local-foo")
        assert has_policy_file("") is False
        assert has_policy_file(None) is False  # type: ignore[arg-type]

    def test_policy_path_uses_culture_home(self, culture_root):
        assert policy_path_for("x").startswith(str(culture_root))
        assert culture_home() == str(culture_root)


# ---------------------------------------------------------------------------
# Broker end-to-end (real filesystem)
# ---------------------------------------------------------------------------


class TestBrokerEndToEnd:
    @pytest.mark.asyncio
    async def test_policy_match_returns_immediately_allow(self, culture_root):
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        result = await asyncio.wait_for(
            broker.gate("Read", {"file_path": "/x"}, _empty_context()),
            timeout=1.0,
        )
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_policy_match_returns_immediately_deny(self, culture_root):
        # Write a custom policy that hard-denies Bash.
        path = policy_path_for("local-helper")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump({"auto_deny": [{"tool": "Bash"}], "auto_allow": []}, f)
        broker = PermissionBroker(nick="local-helper")
        result = await asyncio.wait_for(
            broker.gate("Bash", {"command": "rm -rf /"}, _empty_context()),
            timeout=1.0,
        )
        assert isinstance(result, PermissionResultDeny)
        assert result.interrupt is False

    @pytest.mark.asyncio
    async def test_unmatched_tool_routes_to_boss_and_allows_on_decision(self, culture_root):
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")

        gate_task = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _empty_context()))

        # Boss-side: poll for the request, write a decision.
        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")

        request_id = await _wait_for_request(queue_dir)
        _write_decision_atomic(
            os.path.join(decisions_dir, f"{request_id}.json"),
            {
                "id": request_id,
                "verdict": "allow",
                "scope": "once",
                "reason": "",
                "decided_by": "test",
            },
        )

        result = await asyncio.wait_for(gate_task, timeout=2.0)
        assert isinstance(result, PermissionResultAllow)

        # Queue and decision files are consumed on success.
        assert not os.path.exists(os.path.join(queue_dir, f"{request_id}.json"))
        assert not os.path.exists(os.path.join(decisions_dir, f"{request_id}.json"))

    @pytest.mark.asyncio
    async def test_boss_deny_propagates_reason(self, culture_root):
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")

        gate_task = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _empty_context()))

        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        request_id = await _wait_for_request(queue_dir)
        _write_decision_atomic(
            os.path.join(decisions_dir, f"{request_id}.json"),
            {
                "id": request_id,
                "verdict": "deny",
                "scope": "once",
                "reason": "not approved",
            },
        )
        result = await asyncio.wait_for(gate_task, timeout=2.0)
        assert isinstance(result, PermissionResultDeny)
        assert "not approved" in result.message
        assert result.interrupt is False

    @pytest.mark.asyncio
    async def test_scope_always_appends_to_policy(self, culture_root):
        # Edit is a high-risk tool — a sticky --always allow now REQUIRES an
        # input_regex (T3 / NT-12). Pass one so the rule lands instead of
        # being demoted to scope=once.
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")

        gate_task = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _empty_context()))

        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        request_id = await _wait_for_request(queue_dir)
        _write_decision_atomic(
            os.path.join(decisions_dir, f"{request_id}.json"),
            {
                "id": request_id,
                "verdict": "allow",
                "scope": "always",
                "input_regex": r"^/x$",
            },
        )
        await asyncio.wait_for(gate_task, timeout=2.0)

        with open(policy_path_for("local-helper")) as f:
            policy = yaml.safe_load(f)
        rules = policy.get("auto_allow", [])
        edit_rules = [r for r in rules if r.get("tool") == "Edit"]
        assert edit_rules, f"Expected Edit auto_allow rule, got {rules!r}"
        assert edit_rules[0].get("input_regex") == r"^/x$"

    @pytest.mark.asyncio
    async def test_scope_always_with_bare_high_risk_raises(self, culture_root):
        # T3 / NT-12: a sticky --always allow for a high-risk tool without
        # input_regex must NOT land as a bare auto_allow. The broker demotes
        # the approval to scope=once (the in-flight call is honored) and
        # records a demote notice so the boss/dashboard can surface the demote.
        from culture.clients._perm_broker import demote_notice_path_for

        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")

        gate_task = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _empty_context()))

        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        request_id = await _wait_for_request(queue_dir)
        _write_decision_atomic(
            os.path.join(decisions_dir, f"{request_id}.json"),
            {
                "id": request_id,
                "verdict": "allow",
                "scope": "always",
                # NO input_regex — the bare-sticky case the guard refuses.
            },
        )
        result = await asyncio.wait_for(gate_task, timeout=2.0)
        # The in-flight call is honored (allow), but no bare sticky rule lands.
        assert isinstance(result, PermissionResultAllow)
        with open(policy_path_for("local-helper")) as f:
            policy = yaml.safe_load(f) or {}
        bare_edit = [
            r
            for r in policy.get("auto_allow", [])
            if r.get("tool") == "Edit" and not r.get("input_regex")
        ]
        assert not bare_edit, f"Bare sticky Edit rule must not be written: {bare_edit!r}"
        # Demote-notice was written so the watchdog observer can surface it.
        assert os.path.exists(demote_notice_path_for(request_id))

    @pytest.mark.asyncio
    async def test_scope_always_with_pattern_uses_pattern(self, culture_root):
        # Edit is high-risk — a sticky --always allow now REQUIRES an
        # input_regex (T3 / NT-12). Pass one so the rule lands; the test's
        # point is that the ``pattern`` field overrides the ``tool`` key in
        # the resulting policy entry.
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")

        # Use Edit — guaranteed to fall through to boss (no auto-allow for it).
        gate_task = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _empty_context()))

        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        request_id = await _wait_for_request(queue_dir)
        _write_decision_atomic(
            os.path.join(decisions_dir, f"{request_id}.json"),
            {
                "id": request_id,
                "verdict": "allow",
                "scope": "always",
                "pattern": "Custom.*",  # pattern field overrides tool match
                "input_regex": r".*",  # narrowing required for high-risk Edit
            },
        )
        await asyncio.wait_for(gate_task, timeout=2.0)

        with open(policy_path_for("local-helper")) as f:
            policy = yaml.safe_load(f)
        tools_allowed = [rule.get("tool") for rule in policy.get("auto_allow", [])]
        # The pattern is stored verbatim as the tool field.
        assert "Custom.*" in tools_allowed

    @pytest.mark.asyncio
    async def test_cancellation_cleans_up_queue_file(self, culture_root):
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")

        gate_task = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _empty_context()))

        queue_dir = os.path.join(str(culture_root), "perm-queue")
        await _wait_for_request(queue_dir)

        gate_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await gate_task

        # Queue dir should be empty — the in-flight request was cleaned up.
        entries = [e for e in os.listdir(queue_dir) if not e.startswith(".")]
        assert entries == []

    @pytest.mark.asyncio
    async def test_missing_policy_file_routes_everything_to_boss(self, culture_root):
        # No policy file written; broker treats as empty → boss-routed.
        broker = PermissionBroker(nick="local-orphan")

        gate_task = asyncio.create_task(broker.gate("Read", {"file_path": "/x"}, _empty_context()))

        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        request_id = await _wait_for_request(queue_dir)
        _write_decision_atomic(
            os.path.join(decisions_dir, f"{request_id}.json"),
            {"id": request_id, "verdict": "allow", "scope": "once"},
        )
        result = await asyncio.wait_for(gate_task, timeout=2.0)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_handoff_auto_allow_is_exact_path_only(self, culture_root):
        # SECURITY: the handoff auto-allow rule must match ONLY the helper's
        # own handoff file at the exact absolute path under CULTURE_HOME. A
        # tail-anchored regex matched any /handoff/<nick>.md tail anywhere on
        # disk (or via traversal), which is a write-anywhere primitive given
        # the worker's auto-allowed Write tool.
        from culture.clients._perm_broker import (
            handoff_path_for,
            seed_helper_policy,
        )

        seed_helper_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        canonical = handoff_path_for("local-helper")
        # The legitimate path → allow (no boss roundtrip).
        result = await asyncio.wait_for(
            broker.gate("Write", {"file_path": canonical}, _empty_context()),
            timeout=1.0,
        )
        assert isinstance(result, PermissionResultAllow)
        # A tail-spoofed path → must NOT auto-allow; goes through the boss.
        # Cancel quickly to assert "did not return allow immediately".
        import contextlib

        evil = "/etc/secrets/handoff/local-helper.md"
        gate_task = asyncio.create_task(broker.gate("Write", {"file_path": evil}, _empty_context()))
        queue_dir = os.path.join(str(culture_root), "perm-queue")
        rid = await _wait_for_request(queue_dir, timeout=1.0)
        assert rid  # it was queued, i.e. NOT auto-allowed
        gate_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await gate_task

    @pytest.mark.asyncio
    async def test_perm_gate_times_out_with_auto_deny(self, culture_root, monkeypatch):
        # A dead or unresponsive boss must NOT hang the worker forever. The
        # broker times out and returns a synthetic deny so the SDK can proceed.
        import culture.clients._perm_broker as broker_mod

        monkeypatch.setattr(broker_mod, "_PERM_DECISION_TIMEOUT_SECONDS", 0.5)
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        result = await asyncio.wait_for(
            broker.gate("Edit", {"file_path": "/x"}, _empty_context()),
            timeout=2.0,
        )
        assert isinstance(result, PermissionResultDeny)
        assert "timeout" in result.message.lower()
        # Queue file should NOT linger (gate cleaned up its own request).
        queue_dir = os.path.join(str(culture_root), "perm-queue")
        entries = (
            [e for e in os.listdir(queue_dir) if not e.startswith(".")]
            if os.path.exists(queue_dir)
            else []
        )
        assert entries == []


# ---------------------------------------------------------------------------
# Request-id validation + request payload owner attribution + queue GC
# Moved here from a deleted neighboring test file in Phase 5.7 — these tests
# exercise broker invariants that remain in force after the boss-notify
# cascade was removed.
# ---------------------------------------------------------------------------


class TestRequestIdValidation:
    def test_valid_and_invalid_ids(self, culture_root):
        from culture.clients._perm_broker import valid_request_id

        assert valid_request_id("req-2026-05-29T10-00-00-000000-abc123")
        assert not valid_request_id("../../etc/passwd")
        assert not valid_request_id("req-a/b")
        assert not valid_request_id("notareq")
        assert not valid_request_id("")

    def test_non_string_ids_rejected_not_crash(self, culture_root):
        # Untrusted JSON bodies can send {"id": 123} / true / [...] / {} — these
        # must be rejected, not raise TypeError (which became a dashboard 500).
        from culture.clients._perm_broker import valid_request_id

        for bad in (123, True, None, ["x"], {"a": 1}, 1.5):
            assert valid_request_id(bad) is False

    @pytest.mark.asyncio
    async def test_write_decision_rejects_traversal_id(self, culture_root):
        from culture.clients._perm_broker import InvalidRequestIdError, write_decision

        with pytest.raises(InvalidRequestIdError):
            write_decision("../../evil", verdict="allow")

    def test_read_request_rejects_traversal_id(self, culture_root):
        from culture.clients._perm_broker import read_request

        assert read_request("../../etc/passwd") is None


class TestRequestRecordsOwner:
    @pytest.mark.asyncio
    async def test_request_payload_records_boss(self, culture_root):
        # The broker records the owning boss IN the request so approvers can
        # attribute ownership without re-reading the worker's culture.yaml.
        from culture.clients._perm_broker import write_default_policy as _seed_policy

        _seed_policy("local-w")
        broker = PermissionBroker(nick="local-w", boss="local-boss2")
        gate = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _empty_context()))
        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        rid = await _wait_for_request(queue_dir)
        with open(os.path.join(queue_dir, f"{rid}.json"), encoding="utf-8") as f:
            assert json.load(f)["boss"] == "local-boss2"
        _write_decision_atomic(
            os.path.join(decisions_dir, f"{rid}.json"),
            {"id": rid, "verdict": "allow", "scope": "once"},
        )
        await asyncio.wait_for(gate, timeout=2.0)


class TestCleanupStale:
    def test_removes_dead_helper_requests_and_orphan_decisions(self, culture_root):
        from culture.clients._perm_broker import cleanup_stale

        qdir = os.path.join(str(culture_root), "perm-queue")
        ddir = os.path.join(str(culture_root), "perm-decisions")
        os.makedirs(qdir, exist_ok=True)
        os.makedirs(ddir, exist_ok=True)
        # alive helper request (keep), dead helper request (stale), orphan decision.
        for rid, nick in (("req-alive", "local-alive"), ("req-dead", "local-dead")):
            with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
                json.dump({"id": rid, "helper_nick": nick, "tool_name": "Edit"}, f)
        with open(os.path.join(ddir, "req-orphan.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "req-orphan", "verdict": "allow"}, f)

        result = cleanup_stale(running_nicks={"local-alive"})
        assert result == {"stale_requests": 1, "orphan_decisions": 1}
        assert os.path.exists(os.path.join(qdir, "req-alive.json"))
        assert not os.path.exists(os.path.join(qdir, "req-dead.json"))
        assert not os.path.exists(os.path.join(ddir, "req-orphan.json"))

    def test_empty_dirs_no_error(self, culture_root):
        from culture.clients._perm_broker import cleanup_stale

        assert cleanup_stale(running_nicks=set()) == {"stale_requests": 0, "orphan_decisions": 0}


class TestListPendingExcludesDecided:
    def test_decided_request_excluded_from_pending(self, culture_root):
        from culture.clients._perm_broker import list_pending

        qdir = os.path.join(str(culture_root), "perm-queue")
        ddir = os.path.join(str(culture_root), "perm-decisions")
        os.makedirs(qdir, exist_ok=True)
        os.makedirs(ddir, exist_ok=True)
        for rid in ("req-a", "req-b"):
            with open(os.path.join(qdir, f"{rid}.json"), "w", encoding="utf-8") as f:
                json.dump({"id": rid, "helper_nick": "local-w", "tool_name": "Edit"}, f)
        # Decide req-a only.
        with open(os.path.join(ddir, "req-a.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "req-a", "verdict": "allow"}, f)
        ids = [r["id"] for r in list_pending()]
        assert ids == ["req-b"]  # req-a is decided, awaiting worker consumption


# ---------------------------------------------------------------------------
# Helpers — boss-side simulation
# ---------------------------------------------------------------------------


async def _wait_for_request(queue_dir: str, timeout: float = 2.0) -> str:
    """Poll until one request file appears in ``queue_dir``; return its ID."""

    async def _poll() -> str:
        while True:
            try:
                entries = [
                    e
                    for e in os.listdir(queue_dir)
                    if e.endswith(".json") and not e.startswith(".")
                ]
            except FileNotFoundError:
                entries = []
            if entries:
                return entries[0][: -len(".json")]
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(_poll(), timeout=timeout)


def _write_decision_atomic(path: str, payload: dict[str, Any]) -> None:
    """Mirror the boss-script atomic-write contract for tests."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
