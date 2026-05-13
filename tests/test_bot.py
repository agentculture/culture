"""Tests for the Bot entity."""

import asyncio
import sys
import time

import pytest

from culture.bots import bot as bot_mod
from culture.bots.bot import Bot, _check_rate, _DynamicEventType, _render_data_values
from culture.bots.config import BotConfig, EmitEventSpec


@pytest.fixture
def bot_config():
    return BotConfig(
        name="testserv-ori-ghci",
        owner="testserv-ori",
        description="Test CI bot",
        created="2026-04-03",
        trigger_type="webhook",
        channels=["#builds"],
        dm_owner=False,
        mention=None,
        template="CI {body.action} for {body.repo}",
        fallback="json",
    )


@pytest.mark.asyncio
async def test_bot_start_creates_virtual_client(server, bot_config):
    bot = Bot(bot_config, server)
    await bot.start()
    assert bot.active
    assert bot.virtual_client is not None
    assert bot.virtual_client.nick == "testserv-ori-ghci"
    assert "#builds" in server.channels
    assert bot.virtual_client in server.channels["#builds"].members
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_stop_cleans_up(server, bot_config):
    bot = Bot(bot_config, server)
    await bot.start()
    await bot.stop()
    assert not bot.active
    assert bot.virtual_client is None


@pytest.mark.asyncio
async def test_bot_handle_with_template(server, make_client, bot_config):
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #builds")
    await client.recv_all(timeout=0.5)

    bot = Bot(bot_config, server)
    await bot.start()
    await client.recv_all(timeout=0.3)

    result = await bot.handle({"action": "completed", "repo": "myrepo"})
    assert "CI completed for myrepo" in result

    lines = await client.recv_all(timeout=0.5)
    assert any("CI completed for myrepo" in line for line in lines)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_handle_fallback_json(server, make_client):
    config = BotConfig(
        name="testserv-ori-fallbot",
        channels=["#test"],
        template="Missing: {body.nonexistent}",
        fallback="json",
    )
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #test")
    await client.recv_all(timeout=0.5)

    bot = Bot(config, server)
    await bot.start()
    await client.recv_all(timeout=0.3)

    result = await bot.handle({"key": "value"})
    assert '"key"' in result  # JSON stringified

    await bot.stop()


@pytest.mark.asyncio
async def test_bot_handle_with_mention(server, make_client):
    config = BotConfig(
        name="testserv-ori-mentionbot",
        channels=["#builds"],
        mention="testserv-claude",
        template="Build done: {body.status}",
    )
    client = await make_client("testserv-claude", "claude")
    await client.send("JOIN #builds")
    await client.recv_all(timeout=0.5)

    bot = Bot(config, server)
    await bot.start()
    await client.recv_all(timeout=0.3)

    result = await bot.handle({"status": "success"})
    assert "@testserv-claude" in result

    lines = await client.recv_all(timeout=0.5)
    privmsg_lines = [l for l in lines if "PRIVMSG" in l]
    assert any("@testserv-claude" in l for l in privmsg_lines)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_handle_with_dm_owner(server, make_client):
    config = BotConfig(
        name="testserv-ori-dmbot",
        owner="testserv-ori",
        channels=["#builds"],
        dm_owner=True,
        template="Event: {body.type}",
    )
    owner = await make_client("testserv-ori", "ori")

    bot = Bot(config, server)
    await bot.start()

    await bot.handle({"type": "deploy"})
    lines = await owner.recv_all(timeout=0.5)
    assert any("Event: deploy" in line for line in lines)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_nick_collision(server, make_client, bot_config):
    # Register a real client with the same nick
    await make_client("testserv-ori-ghci", "ghci")

    bot = Bot(bot_config, server)
    with pytest.raises(ValueError, match="already in use"):
        await bot.start()


@pytest.mark.asyncio
async def test_bot_handle_not_active(server, bot_config):
    bot = Bot(bot_config, server)
    with pytest.raises(RuntimeError, match="not active"):
        await bot.handle({"test": True})


@pytest.mark.asyncio
async def test_bot_webhook_url(server, bot_config):
    bot = Bot(bot_config, server)
    url = bot.webhook_url
    assert "testserv-ori-ghci" in url
    assert str(server.config.webhook_port) in url


@pytest.mark.asyncio
async def test_bot_no_template_uses_json(server, make_client):
    config = BotConfig(
        name="testserv-ori-rawbot",
        channels=["#test"],
        template=None,
        fallback="json",
    )
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #test")
    await client.recv_all(timeout=0.5)

    bot = Bot(config, server)
    await bot.start()
    await client.recv_all(timeout=0.3)

    result = await bot.handle({"raw": "data"})
    assert '"raw"' in result
    await bot.stop()


# ---------------------------------------------------------------------------
# Phase 4b additions — dynamic event type, rate limiter, render helper,
# custom-handler.py loader, _maybe_fire_event, _deliver edge cases
# ---------------------------------------------------------------------------


def test_dynamic_event_type_str_returns_value():
    ev = _DynamicEventType("custom.event")
    assert str(ev) == "custom.event"
    assert ev.value == "custom.event"


def test_check_rate_allows_first_burst_then_blocks(monkeypatch):
    # Use a fresh module-level dict by patching the state.
    fresh: dict[str, list[float]] = {}
    monkeypatch.setattr(bot_mod, "_rate_state", fresh)

    # Pin time to a fixed value.
    monkeypatch.setattr(bot_mod.time, "monotonic", lambda: 100.0)
    # _RATE_MAX_PER_SEC is 10; the 11th call within 1s is rate-limited.
    for _ in range(bot_mod._RATE_MAX_PER_SEC):
        assert _check_rate("ratebot") is True
    assert _check_rate("ratebot") is False


def test_check_rate_releases_after_window(monkeypatch):
    fresh: dict[str, list[float]] = {}
    monkeypatch.setattr(bot_mod, "_rate_state", fresh)

    times = iter([100.0] * 10 + [102.0])  # 11th call after window expires
    monkeypatch.setattr(bot_mod.time, "monotonic", lambda: next(times))

    for _ in range(bot_mod._RATE_MAX_PER_SEC):
        assert _check_rate("releasebot") is True
    # 2 seconds later → window is empty, 11th call allowed.
    assert _check_rate("releasebot") is True


def test_render_data_values_handles_strings_and_non_strings():
    rendered = _render_data_values(
        {"greeting": "hello {{name}}", "count": 7, "list": [1, 2]},
        {"name": "world"},
    )
    assert rendered["greeting"] == "hello world"
    assert rendered["count"] == 7
    assert rendered["list"] == [1, 2]


def test_render_data_values_falls_back_on_template_error():
    # Sandbox should reject access to dunder attrs → render fails → fallback.
    bad = _render_data_values({"x": "{{ obj.__class__.__name__ }}"}, {"obj": object()})
    # Sandbox might either rebuild or return raw — both are acceptable. We
    # only care that no exception escapes.
    assert "x" in bad


@pytest.mark.asyncio
async def test_bot_start_when_already_active_is_idempotent(server, bot_config):
    bot = Bot(bot_config, server)
    await bot.start()
    # Calling again is a no-op (covers the early-return at line 95).
    await bot.start()
    assert bot.active
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_handle_empty_message_returns_empty(server, bot_config):
    """If `_resolve_message` returns "", `handle()` returns "" without
    sending or firing events."""
    bot = Bot(bot_config, server)
    await bot.start()

    # Patch _resolve_message to return empty.
    async def _empty(_payload):
        return ""

    bot._resolve_message = _empty  # type: ignore[assignment]
    result = await bot.handle({"action": "noop"})
    assert result == ""
    await bot.stop()


def test_bot_resolve_channels_dynamic_from_event(server):
    """Empty `channels` + `trigger_type=event` + event ctx with channel →
    `_resolve_channels` returns the event channel + dynamic flag True."""
    config = BotConfig(
        name="testserv-ori-evbot",
        channels=[],
        trigger_type="event",
    )
    bot = Bot(config, server)
    payload = {"event": {"channel": "#dynamic-room"}}
    channels, dynamic = bot._resolve_channels(payload)
    assert channels == ["#dynamic-room"]
    assert dynamic is True


def test_bot_resolve_channels_empty_when_no_event_channel(server):
    config = BotConfig(
        name="testserv-ori-evempty",
        channels=[],
        trigger_type="event",
    )
    bot = Bot(config, server)
    channels, dynamic = bot._resolve_channels({"event": {}})
    assert channels == []
    assert dynamic is False


def test_bot_resolve_channels_event_with_non_dict_ctx(server):
    """payload['event'] not a dict → fall back to no channels."""
    config = BotConfig(
        name="testserv-ori-evbad",
        channels=[],
        trigger_type="event",
    )
    bot = Bot(config, server)
    channels, dynamic = bot._resolve_channels({"event": "not-a-dict"})
    assert channels == []
    assert dynamic is False


@pytest.mark.asyncio
async def test_bot_deliver_joins_channel_when_not_a_member(server, make_client):
    """If a channel is not yet joined (e.g. it was created after start),
    `_deliver` triggers a JOIN before sending. This covers the
    not-in-channel branch at line 170."""
    config = BotConfig(
        name="testserv-ori-join",
        channels=["#joined-at-start"],
        template="hello {body.who}",
    )
    bot = Bot(config, server)
    await bot.start()

    # The bot joined #joined-at-start at start. Now manually remove it
    # from the channel so `_deliver` will re-join.
    ch = server.channels.get("#joined-at-start")
    if ch is not None and bot.virtual_client in ch.members:
        ch.members.discard(bot.virtual_client)
        # Also drop the channel from the virtual client's set so the
        # `is None or not in members` predicate trips correctly.
        for vch in list(bot.virtual_client.channels):
            if vch.name == "#joined-at-start":
                bot.virtual_client.channels.discard(vch)

    listener = await make_client("testserv-listen", "listen")
    await listener.send("JOIN #joined-at-start")
    await listener.recv_all(timeout=0.3)

    await bot.handle({"who": "world"})
    lines = await listener.recv_all(timeout=0.5)
    assert any("hello world" in ln for ln in lines)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_maybe_fire_event_emits(server, monkeypatch):
    config = BotConfig(
        name="testserv-ori-emit",
        channels=["#emit"],
        template="ignored",
        fires_event=EmitEventSpec(type="bot.command", data={"k": "{{body.action}}"}),
    )
    bot = Bot(config, server)
    await bot.start()

    captured: list = []

    async def _fake_emit(event):
        captured.append(event)

    monkeypatch.setattr(server, "emit_event", _fake_emit)
    await bot._maybe_fire_event({"body": {"action": "deploy"}})
    assert len(captured) == 1
    event = captured[0]
    # event.type is the EventType enum value or DynamicEventType
    assert str(event.type) == "bot.command" or event.type.value == "bot.command"
    assert event.data == {"k": "deploy"}
    assert event.nick == "testserv-ori-emit"
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_maybe_fire_event_falls_back_to_dynamic_type(server, monkeypatch):
    """Unknown event type → wrapped in `_DynamicEventType`."""
    config = BotConfig(
        name="testserv-ori-dyn",
        channels=["#dyn"],
        fires_event=EmitEventSpec(type="custom.thing", data={}),
    )
    bot = Bot(config, server)
    await bot.start()

    captured: list = []

    async def _fake_emit(event):
        captured.append(event)

    monkeypatch.setattr(server, "emit_event", _fake_emit)
    await bot._maybe_fire_event({})
    assert isinstance(captured[0].type, _DynamicEventType)
    assert captured[0].type.value == "custom.thing"
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_maybe_fire_event_skips_when_no_spec(server, bot_config):
    bot = Bot(bot_config, server)
    await bot.start()
    # fires_event is None on bot_config; this should early-return cleanly.
    await bot._maybe_fire_event({})
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_maybe_fire_event_rejects_invalid_event_type(server, monkeypatch, caplog):
    config = BotConfig(
        name="testserv-ori-badtype",
        channels=["#x"],
        fires_event=EmitEventSpec(type="!!bad", data={}),
    )
    bot = Bot(config, server)
    await bot.start()
    called: list = []

    async def _fake_emit(event):
        called.append(event)

    monkeypatch.setattr(server, "emit_event", _fake_emit)
    with caplog.at_level("WARNING"):
        await bot._maybe_fire_event({})
    assert called == []
    assert any("invalid fires_event.type" in r.message for r in caplog.records)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_maybe_fire_event_rate_limited(server, monkeypatch, caplog):
    """If `_check_rate` says no, the event is dropped with a warning."""
    config = BotConfig(
        name="testserv-ori-ratelim",
        channels=["#rl"],
        fires_event=EmitEventSpec(type="bot.command", data={}),
    )
    bot = Bot(config, server)
    await bot.start()
    monkeypatch.setattr(bot_mod, "_check_rate", lambda _n: False)

    called: list = []

    async def _fake_emit(event):
        called.append(event)

    monkeypatch.setattr(server, "emit_event", _fake_emit)
    with caplog.at_level("WARNING"):
        await bot._maybe_fire_event({})
    assert called == []
    assert any("rate-limited" in r.message for r in caplog.records)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_maybe_fire_event_logs_when_emit_raises(server, monkeypatch, caplog):
    config = BotConfig(
        name="testserv-ori-emiterr",
        channels=["#err"],
        fires_event=EmitEventSpec(type="bot.command", data={}),
    )
    bot = Bot(config, server)
    await bot.start()

    # Raise only for the explicit fires_event call so the bot/server can
    # still emit their normal lifecycle events during teardown.
    real_emit = server.emit_event

    async def _bad_emit(event):
        # bot.command is the fires_event we want to fail; everything
        # else (bot.start, user.part on stop) gets the real emitter.
        if str(getattr(event.type, "value", event.type)) == "bot.command":
            raise RuntimeError("boom")
        await real_emit(event)

    monkeypatch.setattr(server, "emit_event", _bad_emit)
    with caplog.at_level("ERROR"):
        await bot._maybe_fire_event({})
    assert any("failed to emit fires_event" in r.message for r in caplog.records)
    await bot.stop()


# ---- _run_custom_handler — real importlib.util load -------------------------


@pytest.fixture
def bots_dir_with_handler(tmp_path, monkeypatch, request):
    """Redirect BOTS_DIR to tmp_path and write a `handler.py` for the bot.

    The fixture finalizer drops the imported module from `sys.modules` to
    avoid bleed across tests (importlib caches by name).
    """
    monkeypatch.setattr(bot_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)

    def _make(bot_name: str, handler_src: str) -> None:
        bot_dir = tmp_path / bot_name
        bot_dir.mkdir(parents=True, exist_ok=True)
        (bot_dir / "handler.py").write_text(handler_src)
        # Drop any prior import of this module name.
        mod_name = f"bot_handler_{bot_name}"
        sys.modules.pop(mod_name, None)

        def _finalize():
            sys.modules.pop(mod_name, None)

        request.addfinalizer(_finalize)

    return _make


@pytest.mark.asyncio
async def test_bot_run_custom_handler_happy_path(server, bots_dir_with_handler):
    bots_dir_with_handler(
        "testserv-ori-hh1",
        "async def handle(payload, bot):\n    return f'got {payload[\"x\"]}'\n",
    )
    config = BotConfig(
        name="testserv-ori-hh1",
        channels=["#hh"],
    )
    bot = Bot(config, server)
    await bot.start()
    result = await bot._resolve_message({"x": "ping"})
    assert result == "got ping"
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_run_custom_handler_no_handle_function_falls_back(server, bots_dir_with_handler):
    bots_dir_with_handler(
        "testserv-ori-hh2",
        "# no handle()\n",
    )
    config = BotConfig(
        name="testserv-ori-hh2",
        channels=["#hh2"],
        template="fallback {body.k}",
    )
    bot = Bot(config, server)
    await bot.start()
    result = await bot._resolve_message({"k": "v"})
    assert result == "fallback v"
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_run_custom_handler_raises_falls_back(server, bots_dir_with_handler):
    bots_dir_with_handler(
        "testserv-ori-hh3",
        "async def handle(payload, bot):\n    raise RuntimeError('boom')\n",
    )
    config = BotConfig(
        name="testserv-ori-hh3",
        channels=["#hh3"],
        template="fallback for {body.action}",
    )
    bot = Bot(config, server)
    await bot.start()
    result = await bot._resolve_message({"action": "ping"})
    assert result == "fallback for ping"
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_run_custom_handler_returns_none_treated_as_empty(server, bots_dir_with_handler):
    """handler returning None → `_resolve_message` returns "" (and `handle()`
    short-circuits to empty)."""
    bots_dir_with_handler(
        "testserv-ori-hh4",
        "async def handle(payload, bot):\n    return None\n",
    )
    config = BotConfig(
        name="testserv-ori-hh4",
        channels=["#hh4"],
    )
    bot = Bot(config, server)
    await bot.start()
    result = await bot._resolve_message({})
    assert result == ""
    await bot.stop()
