"""Tests for ``culture.watcher.{state,service}`` — cooldown + dispatch (v8.19.19)."""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import pytest

from culture.watcher.alerts import AlertRouter
from culture.watcher.patterns import PatternEvent
from culture.watcher.service import WatcherConfig, WatcherService, load_config
from culture.watcher.state import WatcherState


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# --- WatcherState ----------------------------------------------------------


def test_state_persists_and_reloads(tmp_path):
    path = tmp_path / "state.json"
    s1 = WatcherState(str(path))
    s1.record_firing("p1:nick", now=1000.0)
    s1.save()
    s2 = WatcherState(str(path))
    assert s2.last_fired("p1:nick") == 1000.0


def test_state_cooldown_window(tmp_path):
    s = WatcherState(str(tmp_path / "x.json"))
    s.record_firing("p:w", now=1000.0)
    assert s.in_cooldown("p:w", 60.0, now=1030.0) is True
    assert s.in_cooldown("p:w", 60.0, now=1061.0) is False
    assert s.in_cooldown("never-seen", 60.0, now=1030.0) is False


def test_state_gc_drops_old(tmp_path):
    s = WatcherState(str(tmp_path / "x.json"))
    s.record_firing("old", now=100.0)
    s.record_firing("new", now=1_000_000.0)
    dropped = s.gc(keep_seconds=3600.0, now=1_000_100.0)
    assert dropped == 1
    assert "old" not in s.firings
    assert "new" in s.firings


def test_state_handles_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json")
    s = WatcherState(str(path))
    assert s.firings == {}


# --- WatcherConfig load ----------------------------------------------------


def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg, raw = load_config(str(tmp_path / "missing.yaml"))
    assert cfg.poll_interval_seconds > 0
    assert "silent_death" in cfg.enabled_patterns
    assert raw == {}


def test_load_config_parses_patterns(tmp_path):
    path = tmp_path / "watcher.yaml"
    path.write_text(
        "poll_interval_seconds: 5\n"
        "cooldown_seconds: 120\n"
        "patterns:\n"
        "  - name: crash_burst\n"
        "  - silent_death\n"
        "boss_ceiling:\n"
        "  local-boss:\n"
        "    - mcp__danger__send\n"
    )
    cfg, _raw = load_config(str(path))
    assert cfg.poll_interval_seconds == 5.0
    assert cfg.cooldown_seconds == 120.0
    assert set(cfg.enabled_patterns) == {"crash_burst", "silent_death"}
    assert cfg.boss_ceiling == {"local-boss": ["mcp__danger__send"]}


# --- WatcherService.dispatch + cooldown ------------------------------------


def _build_service(tmp_path, *, send_irc=None) -> WatcherService:
    state = WatcherState(str(tmp_path / "st.json"))
    router = AlertRouter.from_config_dict(
        {"alerts": {"irc": {"enabled": True, "target_nick": "boss", "fallback_channel": "#alerts"}}}
    )
    cfg = WatcherConfig(cooldown_seconds=60.0)
    return WatcherService(config=cfg, state=state, router=router, send_irc=send_irc)


@pytest.mark.asyncio
async def test_dispatch_routes_to_irc(tmp_path):
    sent: list[tuple[str, str]] = []

    async def fake_irc(target, text):
        sent.append((target, text))

    service = _build_service(tmp_path, send_irc=fake_irc)
    ev = PatternEvent(pattern="silent_death", severity="high", target="w", summary="dead")
    shipped = await service.dispatch([ev])
    assert shipped == 1
    # Routed to BOTH the target_nick AND the fallback channel.
    targets = sorted(t for t, _ in sent)
    assert targets == ["#alerts", "boss"]


@pytest.mark.asyncio
async def test_dispatch_cooldown_suppresses_repeat(tmp_path):
    sent: list[tuple[str, str]] = []

    async def fake_irc(target, text):
        sent.append((target, text))

    service = _build_service(tmp_path, send_irc=fake_irc)
    ev = PatternEvent(pattern="silent_death", severity="high", target="w", summary="dead")
    assert await service.dispatch([ev]) == 1
    # Same key inside cooldown — must be suppressed.
    assert await service.dispatch([ev]) == 0


@pytest.mark.asyncio
async def test_dispatch_different_keys_both_fire(tmp_path):
    sent: list[tuple[str, str]] = []

    async def fake_irc(target, text):
        sent.append((target, text))

    service = _build_service(tmp_path, send_irc=fake_irc)
    ev1 = PatternEvent(pattern="silent_death", severity="high", target="w1", summary="x")
    ev2 = PatternEvent(pattern="silent_death", severity="high", target="w2", summary="y")
    assert await service.dispatch([ev1, ev2]) == 2


# --- WatcherService.run_once end-to-end ------------------------------------


@pytest.mark.asyncio
async def test_run_once_detects_silent_death(tmp_path, monkeypatch):
    # Create a fake culture-home with a daemon-log indicating a dead worker.
    home = tmp_path / "home"
    daemon_dir = home / "daemon-log"
    audit_dir = home / "audit"
    runtime_dir = home / "runtime"
    for d in (daemon_dir, audit_dir, runtime_dir):
        d.mkdir(parents=True)
    nick = "local-dead"
    with open(daemon_dir / f"{nick}.jsonl", "w") as fh:
        fh.write(
            json.dumps({"action": "agent_start", "ts": _iso(time.time() - 600), "detail": {}})
            + "\n"
        )
    # Audit empty; pidfile with a dead PID.
    (runtime_dir / f"agent-{nick}.pid").write_text("99999999")

    sent: list[tuple[str, str]] = []

    async def fake_irc(target, text):
        sent.append((target, text))

    # `from X import Y` binds Y at module load — must patch the symbol
    # in every module that consumed it. _daemon_log + _audit both
    # imported culture_home directly.
    monkeypatch.setattr("culture.clients._perm_broker.culture_home", lambda: str(home))
    monkeypatch.setattr("culture.clients._daemon_log.culture_home", lambda: str(home))
    monkeypatch.setattr("culture.clients._audit.culture_home", lambda: str(home))
    monkeypatch.setattr("culture.watcher.service.culture_home", lambda: str(home))
    state = WatcherState(str(tmp_path / "st.json"))
    router = AlertRouter.from_config_dict(
        {"alerts": {"irc": {"enabled": True, "target_nick": "boss", "fallback_channel": "#alerts"}}}
    )
    service = WatcherService(
        config=WatcherConfig(),
        state=state,
        router=router,
        send_irc=fake_irc,
        pidfile_dir=str(runtime_dir),
        sources_glob_daemon_log=str(daemon_dir / "*.jsonl"),
        sources_glob_audit=str(audit_dir / "*.jsonl"),
    )
    shipped = await service.run_once()
    assert shipped == 1
    assert any("dead" in line.lower() or "died" in line.lower() for _, line in sent)
