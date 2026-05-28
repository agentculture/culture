"""Tests for the broker's on_request notification callback (boss-agent layer)."""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from claude_agent_sdk import PermissionResultAllow, ToolPermissionContext  # noqa: E402

from culture.clients._perm_broker import PermissionBroker, write_default_policy  # noqa: E402


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def _ctx() -> ToolPermissionContext:
    return ToolPermissionContext(signal=None, suggestions=[])


async def _wait_for_request(queue_dir: str, timeout: float = 2.0) -> str:
    async def _poll() -> str:
        while True:
            try:
                entries = [e for e in os.listdir(queue_dir) if e.endswith(".json")]
            except FileNotFoundError:
                entries = []
            if entries:
                return entries[0][: -len(".json")]
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(_poll(), timeout=timeout)


def _write_decision(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)


class TestRequestIdValidation:
    def test_valid_and_invalid_ids(self, culture_root):
        from culture.clients._perm_broker import valid_request_id

        assert valid_request_id("req-2026-05-29T10-00-00-000000-abc123")
        assert not valid_request_id("../../etc/passwd")
        assert not valid_request_id("req-a/b")
        assert not valid_request_id("notareq")
        assert not valid_request_id("")

    @pytest.mark.asyncio
    async def test_write_decision_rejects_traversal_id(self, culture_root):
        from culture.clients._perm_broker import InvalidRequestIdError, write_decision

        with pytest.raises(InvalidRequestIdError):
            write_decision("../../evil", verdict="allow")

    def test_read_request_rejects_traversal_id(self, culture_root):
        from culture.clients._perm_broker import read_request

        assert read_request("../../etc/passwd") is None


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


class TestOnRequestCallback:
    @pytest.mark.asyncio
    async def test_callback_fires_once_with_payload(self, culture_root):
        write_default_policy("local-w")
        seen: list[dict] = []

        async def on_request(payload: dict) -> None:
            seen.append(payload)

        broker = PermissionBroker(nick="local-w", on_request=on_request)
        gate = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _ctx()))

        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        rid = await _wait_for_request(queue_dir)
        _write_decision(
            os.path.join(decisions_dir, f"{rid}.json"),
            {"id": rid, "verdict": "allow", "scope": "once"},
        )
        result = await asyncio.wait_for(gate, timeout=2.0)

        assert isinstance(result, PermissionResultAllow)
        assert len(seen) == 1
        assert seen[0]["tool_name"] == "Edit"
        assert seen[0]["helper_nick"] == "local-w"
        assert seen[0]["id"] == rid

    @pytest.mark.asyncio
    async def test_callback_not_fired_on_policy_fast_path(self, culture_root):
        write_default_policy("local-w")
        seen: list[dict] = []

        async def on_request(payload: dict) -> None:
            seen.append(payload)

        broker = PermissionBroker(nick="local-w", on_request=on_request)
        # Read auto-allows → no boss routing → callback must not fire.
        result = await asyncio.wait_for(
            broker.gate("Read", {"file_path": "/x"}, _ctx()), timeout=1.0
        )
        assert isinstance(result, PermissionResultAllow)
        assert seen == []

    @pytest.mark.asyncio
    async def test_raising_callback_is_swallowed_gate_still_resolves(self, culture_root):
        write_default_policy("local-w")

        async def on_request(payload: dict) -> None:
            raise RuntimeError("transport down")

        broker = PermissionBroker(nick="local-w", on_request=on_request)
        gate = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _ctx()))

        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        rid = await _wait_for_request(queue_dir)
        # Despite the callback raising, the request file exists and the gate
        # resolves normally once the decision lands.
        _write_decision(
            os.path.join(decisions_dir, f"{rid}.json"),
            {"id": rid, "verdict": "allow", "scope": "once"},
        )
        result = await asyncio.wait_for(gate, timeout=2.0)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_no_callback_is_fine(self, culture_root):
        # on_request=None must preserve the existing gate flow.
        write_default_policy("local-w")
        broker = PermissionBroker(nick="local-w")  # no on_request
        gate = asyncio.create_task(broker.gate("Edit", {"file_path": "/x"}, _ctx()))
        queue_dir = os.path.join(str(culture_root), "perm-queue")
        decisions_dir = os.path.join(str(culture_root), "perm-decisions")
        rid = await _wait_for_request(queue_dir)
        _write_decision(
            os.path.join(decisions_dir, f"{rid}.json"),
            {"id": rid, "verdict": "allow", "scope": "once"},
        )
        result = await asyncio.wait_for(gate, timeout=2.0)
        assert isinstance(result, PermissionResultAllow)
