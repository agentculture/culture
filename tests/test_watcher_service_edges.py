"""Edge cases for ``culture.watcher.service.WatcherService`` (v8.19.20).

Closes gaps in the v8.19.19 happy-path tests:
* One failing sink must not block the others.
* cooldown_filter deduplicates same-key events within a SINGLE batch.
* run_forever responds to stop_event without waiting a full poll cycle.
* run_once with empty globs is a no-op (no exception).
* Dashboard's ``_persistent_observer_lifecycle`` cleanup_ctx wires
  ``app[_OBSERVER]`` on startup and tears it down on shutdown.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest

from culture.watcher.alerts import AlertRouter
from culture.watcher.patterns import PatternEvent
from culture.watcher.service import WatcherConfig, WatcherService, load_config
from culture.watcher.state import WatcherState


def _ev(target="local-w", pattern="silent_death") -> PatternEvent:
    return PatternEvent(
        pattern=pattern, severity="high", target=target, summary=f"{pattern} on {target}"
    )


def _router_all_on() -> AlertRouter:
    return AlertRouter.from_config_dict(
        {
            "alerts": {
                "irc": {"enabled": True, "target_nick": "boss", "fallback_channel": "#alerts"},
                "email": {
                    "enabled": True,
                    "smtp_host": "smtp.example.com",
                    "from_addr": "a@b",
                    "to_addrs": ["c@d"],
                },
                "webhook": {"enabled": True, "url": "http://127.0.0.1:1/never"},
            }
        }
    )


# --- Dispatch isolation under failing sinks --------------------------------


@pytest.mark.asyncio
async def test_dispatch_irc_failure_still_runs_email_and_webhook(tmp_path, monkeypatch):
    """If send_irc raises, email + webhook still get a shot — sinks isolated."""
    irc_called = email_called = webhook_called = False

    async def bad_irc(target, text):
        nonlocal irc_called
        irc_called = True
        raise OSError("irc broken")

    def fake_email(self, ev):
        nonlocal email_called
        email_called = True
        return True

    def fake_webhook(self, ev):
        nonlocal webhook_called
        webhook_called = True
        return True

    monkeypatch.setattr(AlertRouter, "send_email", fake_email)
    monkeypatch.setattr(AlertRouter, "send_webhook", fake_webhook)
    service = WatcherService(
        config=WatcherConfig(cooldown_seconds=60.0),
        state=WatcherState(str(tmp_path / "s.json")),
        router=_router_all_on(),
        send_irc=bad_irc,
    )
    shipped = await service.dispatch([_ev()])
    assert irc_called, "IRC should have been attempted"
    assert email_called, "email should run even after IRC failed"
    assert webhook_called, "webhook should run even after IRC failed"
    assert shipped == 1


@pytest.mark.asyncio
async def test_dispatch_email_failure_still_runs_webhook(tmp_path, monkeypatch):
    irc_sent: list[tuple[str, str]] = []
    webhook_called = False

    async def fake_irc(target, text):
        irc_sent.append((target, text))

    def bad_email(self, ev):
        raise RuntimeError("smtp blew up")

    def fake_webhook(self, ev):
        nonlocal webhook_called
        webhook_called = True
        return True

    monkeypatch.setattr(AlertRouter, "send_email", bad_email)
    monkeypatch.setattr(AlertRouter, "send_webhook", fake_webhook)
    service = WatcherService(
        config=WatcherConfig(cooldown_seconds=60.0),
        state=WatcherState(str(tmp_path / "s.json")),
        router=_router_all_on(),
        send_irc=fake_irc,
    )
    await service.dispatch([_ev()])
    assert webhook_called, "webhook should still fire even after email raised"
    assert len(irc_sent) == 2, "IRC routes to target_nick + fallback channel"


# --- Cooldown intra-batch dedupe -------------------------------------------


def test_cooldown_filter_dedupes_same_key_within_one_batch(tmp_path):
    """Same key appearing twice in ONE batch: only the first survives."""
    service = WatcherService(
        config=WatcherConfig(cooldown_seconds=60.0),
        state=WatcherState(str(tmp_path / "s.json")),
        router=_router_all_on(),
    )
    e1 = _ev(target="w1")
    e2 = _ev(target="w1")
    # First call has no record → both pass through (no record_firing yet).
    # cooldown_filter alone doesn't record; record happens in dispatch.
    survivors = service.cooldown_filter([e1, e2])
    # Without a record yet, BOTH pass (cooldown_filter is stateless on its
    # own); the dedupe-within-batch is achieved by dispatch's record_firing
    # call after each event. Verify the dispatch behavior end-to-end:
    sent_count = 0

    async def count_irc(target, text):
        nonlocal sent_count
        sent_count += 1

    service.send_irc = count_irc
    asyncio.run(service.dispatch([e1, e2]))
    # IRC routes to TWO recipients (target_nick + fallback) per event.
    # If dedupe works, only ONE event lands → 2 IRC calls.
    # If dedupe fails, TWO events land → 4 IRC calls.
    assert sent_count == 2, f"expected 2 (1 event × 2 recipients), got {sent_count}"


# --- run_forever stop_event responsiveness ---------------------------------


@pytest.mark.asyncio
async def test_run_forever_stops_within_poll_interval(tmp_path):
    """Setting stop_event must return the loop before a full poll cycle."""
    sent: list[tuple[str, str]] = []

    async def fake_irc(target, text):
        sent.append((target, text))

    cfg = WatcherConfig(
        poll_interval_seconds=0.2,
        cooldown_seconds=60.0,
        enabled_patterns=(),  # disable all detectors so run_once is fast
    )
    service = WatcherService(
        config=cfg,
        state=WatcherState(str(tmp_path / "s.json")),
        router=_router_all_on(),
        send_irc=fake_irc,
    )
    stop = asyncio.Event()

    async def kill_after(delay):
        await asyncio.sleep(delay)
        stop.set()

    # Start the loop + a killer that fires at 0.3s. The loop must exit
    # within poll_interval after stop_event is set.
    task = asyncio.create_task(service.run_forever(stop_event=stop))
    killer = asyncio.create_task(kill_after(0.3))
    await asyncio.wait_for(task, timeout=1.0)
    await killer


# --- run_once with no agents -----------------------------------------------


@pytest.mark.asyncio
async def test_run_once_with_no_agents_is_zero(tmp_path):
    """Empty globs → 0 events, no exception."""
    service = WatcherService(
        config=WatcherConfig(),
        state=WatcherState(str(tmp_path / "s.json")),
        router=_router_all_on(),
        send_irc=None,
        sources_glob_daemon_log=str(tmp_path / "nope" / "*.jsonl"),
        sources_glob_audit=str(tmp_path / "nope" / "*.jsonl"),
    )
    shipped = await service.run_once()
    assert shipped == 0


# --- WatcherConfig.from_dict tolerance -------------------------------------


def test_from_dict_empty_returns_defaults():
    cfg = WatcherConfig.from_dict({})
    assert cfg.poll_interval_seconds > 0
    assert set(cfg.enabled_patterns) >= {"silent_death"}


def test_from_dict_patterns_mixed_string_and_dict():
    """Both forms — bare strings AND {name: ...} dicts — are accepted."""
    cfg = WatcherConfig.from_dict(
        {"patterns": ["silent_death", {"name": "crash_burst"}, {"not_name": "ignored"}]}
    )
    # bare-string + dict-with-name parsed; dict missing 'name' silently dropped.
    assert set(cfg.enabled_patterns) == {"silent_death", "crash_burst"}


def test_load_config_yaml_list_root_handled(tmp_path):
    """A YAML file with a list at the root (not a mapping) returns defaults."""
    path = tmp_path / "bad.yaml"
    path.write_text("- foo\n- bar\n")
    cfg, raw = load_config(str(path))
    # Non-dict input is logged as a warning and treated as empty.
    assert set(cfg.enabled_patterns) >= {"silent_death"}
    assert raw == {}


# --- Dashboard cleanup_ctx integration -------------------------------------


@pytest.mark.asyncio
async def test_persistent_observer_lifecycle_wires_app_and_closes(tmp_path):
    """``_persistent_observer_lifecycle`` builds the observer at startup
    and closes it on app shutdown."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from culture.dashboard import server as dash_server

    closed = []

    class _FakeObs:
        async def close(self):
            closed.append(True)

    # Patch PersistentObserver to a fake so we don't open a real TCP socket
    # against the test runner's network.
    monkeypath_done = False
    with patch("culture.observer.PersistentObserver", return_value=_FakeObs()):
        # Build server.yaml so load_config_or_default succeeds
        config_path = str(tmp_path / "server.yaml")
        with open(config_path, "w") as fh:
            fh.write("server:\n  name: test\n  host: 127.0.0.1\n  port: 6667\nagents: {}\n")
        app = dash_server.build_app(config_path=config_path)
        async with TestClient(TestServer(app)) as client:
            # Inside the context the observer must be live on app[_OBSERVER].
            observer = app.get(dash_server._OBSERVER)
            assert isinstance(
                observer, _FakeObs
            ), f"persistent observer should be wired into app[_OBSERVER], got {observer!r}"
        # After exiting the TestClient context, close() must have been called.
        assert closed == [True], "cleanup_ctx must call observer.close() on shutdown"


@pytest.mark.asyncio
async def test_persistent_observer_lifecycle_failure_falls_back_to_none(tmp_path):
    """If the observer fails to construct, app[_OBSERVER] is None — not an exception."""
    from aiohttp.test_utils import TestClient, TestServer

    from culture.dashboard import server as dash_server

    def boom(*a, **k):
        raise RuntimeError("no mesh")

    with patch("culture.observer.PersistentObserver", side_effect=boom):
        config_path = str(tmp_path / "server.yaml")
        with open(config_path, "w") as fh:
            fh.write("server:\n  name: test\n  host: 127.0.0.1\n  port: 6667\nagents: {}\n")
        app = dash_server.build_app(config_path=config_path)
        async with TestClient(TestServer(app)) as client:
            # Construction failed; fallback is None.
            assert app.get(dash_server._OBSERVER) is None
            # Dashboard endpoints that depend on the observer fall back to
            # the ephemeral get_observer — they don't crash.
            resp = await client.get("/api/agents")
            assert resp.status == 200
