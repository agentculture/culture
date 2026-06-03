"""Phase 5.6 — ``_await_decision`` uses watchdog push when available.

Asserts that:

  * ``_HAS_WATCHDOG`` is True on Linux + macOS (watchdog ships
    inotify and FSEvents backends respectively).
  * The push path returns a decision in well under the legacy 250 ms
    polling cadence (we measure < 200 ms to leave headroom for slow CI).
  * The fallback polling path still works when ``_HAS_WATCHDOG`` is
    monkeypatched to False.

Real ``PermissionBroker`` + real filesystem under ``CULTURE_HOME``.
"""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402
from claude_agent_sdk import (  # noqa: E402
    PermissionResultAllow,
    ToolPermissionContext,
)

from culture.clients._perm_broker import (  # noqa: E402
    _HAS_WATCHDOG,
    PermissionBroker,
    write_default_policy,
)

WORKER_NICK = "testserv-watchdog-helper"


def _empty_context() -> ToolPermissionContext:
    return ToolPermissionContext(signal=None, suggestions=[])


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


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
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout=timeout)


def _write_decision_atomic(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def test_has_watchdog_is_true_on_linux_macos() -> None:
    """watchdog 6.0.0 is pinned in pyproject — must be importable on supported platforms."""
    import sys

    if sys.platform.startswith(("linux", "darwin")):
        assert _HAS_WATCHDOG, "watchdog must be available on Linux/macOS"


@pytest.mark.asyncio
async def test_await_decision_watchdog_path_returns_fast(culture_root) -> None:
    """Push path: decision file appears, broker resolves it via Observer."""
    if not _HAS_WATCHDOG:
        pytest.skip("watchdog unavailable; push path not exercisable here")

    write_default_policy(WORKER_NICK)
    broker = PermissionBroker(nick=WORKER_NICK, boss="testserv-boss")

    queue_dir = os.path.join(str(culture_root), "perm-queue")
    decisions_dir = os.path.join(str(culture_root), "perm-decisions")

    # Edit falls through to the boss path (no auto-allow rule).
    gate_task = asyncio.create_task(
        broker.gate("Edit", {"file_path": "/tmp/anywhere"}, _empty_context())
    )

    request_id = await _wait_for_request(queue_dir)

    # Insert a small delay so the watchdog Observer has time to be
    # scheduled before the decision file lands. This reflects the real
    # boss-decision flow.
    await asyncio.sleep(0.05)

    decision_path = os.path.join(decisions_dir, f"{request_id}.json")
    start = time.monotonic()
    _write_decision_atomic(
        decision_path,
        {
            "id": request_id,
            "verdict": "allow",
            "scope": "once",
            "decided_by": "testserv-boss",
        },
    )

    result = await asyncio.wait_for(gate_task, timeout=2.0)
    elapsed_ms = (time.monotonic() - start) * 1000.0
    assert isinstance(result, PermissionResultAllow)
    # The watchdog path should resolve well under the legacy 250 ms
    # poll cadence. We give significant CI headroom but still assert
    # the path is materially faster than the worst-case pre-Phase-5.6
    # poll latency (one full 250 ms tick).
    assert elapsed_ms < 200.0, (
        f"watchdog path took {elapsed_ms:.1f} ms — "
        f"expected significantly faster than the 250 ms poll fallback"
    )


@pytest.mark.asyncio
async def test_await_decision_polling_fallback_still_works(
    culture_root,
    monkeypatch,
) -> None:
    """When ``_HAS_WATCHDOG=False`` is forced, broker uses the 250 ms poll."""
    write_default_policy(WORKER_NICK)
    # Force the fallback path.
    import culture.clients._perm_broker as broker_mod

    monkeypatch.setattr(broker_mod, "_HAS_WATCHDOG", False)

    broker = PermissionBroker(nick=WORKER_NICK, boss="testserv-boss")

    queue_dir = os.path.join(str(culture_root), "perm-queue")
    decisions_dir = os.path.join(str(culture_root), "perm-decisions")

    gate_task = asyncio.create_task(
        broker.gate("Edit", {"file_path": "/tmp/poll"}, _empty_context())
    )
    request_id = await _wait_for_request(queue_dir)
    _write_decision_atomic(
        os.path.join(decisions_dir, f"{request_id}.json"),
        {
            "id": request_id,
            "verdict": "allow",
            "scope": "once",
            "decided_by": "testserv-boss",
        },
    )
    result = await asyncio.wait_for(gate_task, timeout=2.0)
    assert isinstance(result, PermissionResultAllow)


@pytest.mark.asyncio
async def test_fast_path_decision_already_exists(culture_root) -> None:
    """Decision file already present at call time -> immediate resolve."""
    write_default_policy(WORKER_NICK)
    broker = PermissionBroker(nick=WORKER_NICK, boss="testserv-boss")

    # Hand-craft a decision file at a known path; bypass _request_from_boss
    # by calling _await_decision directly.
    decisions_dir = os.path.join(str(culture_root), "perm-decisions")
    os.makedirs(decisions_dir, exist_ok=True)
    request_id = "req-2026-06-03T00-00-00-000000-fast111"
    decision_path = os.path.join(decisions_dir, f"{request_id}.json")
    _write_decision_atomic(
        decision_path,
        {
            "id": request_id,
            "verdict": "allow",
            "scope": "once",
            "decided_by": "testserv-boss",
        },
    )
    decision = await asyncio.wait_for(
        broker._await_decision(decision_path, request_id=request_id),
        timeout=1.0,
    )
    assert decision["verdict"] == "allow"


@pytest.mark.asyncio
async def test_watchdog_path_falls_back_on_observer_failure(
    culture_root,
    monkeypatch,
) -> None:
    """If the Observer setup raises, broker falls back to polling."""
    write_default_policy(WORKER_NICK)
    broker = PermissionBroker(nick=WORKER_NICK, boss="testserv-boss")

    # Force the watchdog path to raise so the outer _await_decision
    # falls through to the polling path. Mirrors a real-world failure
    # mode (e.g. fd exhaustion under load).
    async def _failing_watchdog(*_args, **_kwargs):
        raise RuntimeError("simulated Observer failure")

    monkeypatch.setattr(broker, "_await_decision_watchdog", _failing_watchdog)

    queue_dir = os.path.join(str(culture_root), "perm-queue")
    decisions_dir = os.path.join(str(culture_root), "perm-decisions")
    gate_task = asyncio.create_task(broker.gate("Edit", {"file_path": "/tmp/x"}, _empty_context()))
    request_id = await _wait_for_request(queue_dir)
    _write_decision_atomic(
        os.path.join(decisions_dir, f"{request_id}.json"),
        {
            "id": request_id,
            "verdict": "allow",
            "scope": "once",
            "decided_by": "testserv-boss",
        },
    )
    result = await asyncio.wait_for(gate_task, timeout=2.0)
    assert isinstance(result, PermissionResultAllow)
