"""NT-12: high-risk sticky-allow narrowing gate (Task 5.1).

A boss-supervised worker's policy must never carry a bare sticky-allow rule
for a high-risk tool (Bash / Edit / Write / mcp__*). A bare ``--always allow``
for any of those would whitelist every invocation of the tool — one approved
``Bash ls`` would auto-allow ``rm -rf /``. The broker refuses the bare write
and the boss CLI / dashboard demote to ``scope=once`` instead.

These tests exercise the guard directly on :func:`_append_sticky_rule` (the
write site) and the demote-rather-than-fail path on :func:`_request_from_boss`
(the gate site).
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
    ToolPermissionContext,
)

from culture.clients._perm_broker import (  # noqa: E402
    BareStickyApproveRefusedError,
    PermissionBroker,
    demote_notice_path_for,
    policy_path_for,
    write_default_policy,
)


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    """Isolate ``CULTURE_HOME`` per test."""
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def _empty_context() -> ToolPermissionContext:
    return ToolPermissionContext(signal=None, suggestions=[])


async def _wait_for_request(queue_dir: str, timeout: float = 2.0) -> str:
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class TestAppendStickyRuleGate:
    """Direct exercise of the write-site guard.

    ``_append_sticky_rule`` is the lowest layer that turns a decision into a
    persistent policy entry; if the guard there is wrong, every higher-layer
    demote / refuse / UX message inherits the bug.
    """

    def test_bare_bash_sticky_raises(self, culture_root):
        # The canonical T3 case: a ``--always allow Bash`` decision with no
        # input_regex would whitelist every Bash invocation.
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        with pytest.raises(BareStickyApproveRefusedError):
            broker._append_sticky_rule("allow", "Bash", {})

    def test_bare_pattern_bypass_raises(self, culture_root):
        # iter-3 B-1 finding: tool=Foo + pattern=Bash would write a rule whose
        # ``tool`` key is ``Bash`` and bypass a guard that only checks
        # ``tool_name``. The guard MUST inspect both.
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        with pytest.raises(BareStickyApproveRefusedError):
            broker._append_sticky_rule("allow", "Foo", {"pattern": "Bash"})

    def test_bare_edit_sticky_raises(self, culture_root):
        # Edit is also high-risk — a bare sticky for it would whitelist any
        # ``file_path`` Edit (including /etc/* / arbitrary paths under repo).
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        with pytest.raises(BareStickyApproveRefusedError):
            broker._append_sticky_rule("allow", "Edit", {})

    def test_bare_write_sticky_raises(self, culture_root):
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        with pytest.raises(BareStickyApproveRefusedError):
            broker._append_sticky_rule("allow", "Write", {})

    def test_bare_mcp_sticky_raises(self, culture_root):
        # Any ``mcp__*`` tool routes to an external server — a bare sticky
        # for one mcp tool name (or pattern) is just as dangerous as Bash.
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        with pytest.raises(BareStickyApproveRefusedError):
            broker._append_sticky_rule("allow", "mcp__gmail__send", {})

    def test_with_input_regex_passes(self, culture_root):
        # The narrowing case: a non-empty input_regex makes the sticky rule
        # specific enough to be safe. The rule is written and contains the
        # regex verbatim.
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        broker._append_sticky_rule("allow", "Bash", {"input_regex": r"^ls .*$"})
        with open(policy_path_for("local-helper")) as f:
            policy = yaml.safe_load(f) or {}
        bash_rules = [r for r in policy.get("auto_allow", []) if r.get("tool") == "Bash"]
        narrow = [r for r in bash_rules if r.get("input_regex") == r"^ls .*$"]
        assert narrow, f"Expected narrow Bash rule, got {bash_rules!r}"

    def test_bare_low_risk_tool_passes(self, culture_root):
        # The guard is targeted: a sticky for ``Read`` (or any tool not on the
        # high-risk list) is fine without an input_regex.
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")
        broker._append_sticky_rule("allow", "Read", {})
        with open(policy_path_for("local-helper")) as f:
            policy = yaml.safe_load(f) or {}
        tools_allowed = [r.get("tool") for r in policy.get("auto_allow", [])]
        assert "Read" in tools_allowed


class TestDemoteToOnce:
    """End-to-end exercise of the broker's demote-rather-than-fail path.

    When a bare sticky-allow decision lands on the file bus, the broker honors
    the in-flight call (scope=once semantics) and drops a demote-notice JSON
    so the boss / dashboard can surface the demote. No bare sticky rule lands
    in the worker's policy file.
    """

    @pytest.mark.asyncio
    async def test_demote_to_once(self, culture_root):
        write_default_policy("local-helper")
        broker = PermissionBroker(nick="local-helper")

        # Use a Bash command that does NOT match the default safe-read
        # auto-allow (`ls`, `cat`, etc) so the gate actually routes to the
        # boss. ``rm -rf`` is the canonical above-policy invocation.
        gate_task = asyncio.create_task(
            broker.gate("Bash", {"command": "rm -rf /tmp/foo"}, _empty_context())
        )
        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        request_id = await _wait_for_request(queue_dir)

        # Approver writes a sticky --always allow for Bash WITHOUT input_regex.
        # The broker must NOT land a bare Bash sticky rule.
        _write_decision_atomic(
            os.path.join(decisions_dir, f"{request_id}.json"),
            {
                "id": request_id,
                "verdict": "allow",
                "scope": "always",
            },
        )
        result = await asyncio.wait_for(gate_task, timeout=2.0)
        # In-flight call is honored — the operator did intend to allow it,
        # just not as a sticky rule.
        assert isinstance(result, PermissionResultAllow)

        # No bare sticky rule landed in the policy.
        with open(policy_path_for("local-helper")) as f:
            policy = yaml.safe_load(f) or {}
        bare_bash = [
            r
            for r in policy.get("auto_allow", [])
            if r.get("tool") == "Bash" and not r.get("input_regex")
        ]
        assert not bare_bash, f"Bare sticky Bash rule must not be written: {bare_bash!r}"

        # The demote notice file was dropped at the documented path so the
        # bridge / dashboard can surface the demote to the boss.
        notice_path = demote_notice_path_for(request_id)
        assert os.path.exists(notice_path), f"Expected demote notice at {notice_path}"
        with open(notice_path) as f:
            notice = json.load(f)
        assert notice["request_id"] == request_id
        assert notice["original_tool"] == "Bash"
        assert "no input_regex" in notice["demote_reason"]
