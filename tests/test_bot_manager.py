"""Tests for BotManager — bot lifecycle and dispatch."""

import asyncio
from types import SimpleNamespace

import pytest

from culture.bots import bot_manager as bm_mod
from culture.bots.bot_manager import BotManager
from culture.bots.config import BotConfig, save_bot_config
from culture.bots.filter_dsl import FilterParseError


@pytest.fixture
def sample_config():
    return BotConfig(
        name="testserv-ori-mgr",
        owner="testserv-ori",
        description="Manager test bot",
        created="2026-04-03",
        channels=["#test"],
        template="Event: {body.type}",
    )


@pytest.mark.asyncio
async def test_create_bot(server, sample_config, tmp_path, monkeypatch):
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot_manager.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    bot = await mgr.create_bot(sample_config)
    assert bot.active
    assert bot.name in mgr.bots
    assert (tmp_path / sample_config.name / "bot.yaml").exists()
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_load_bots_from_disk(server, sample_config, tmp_path, monkeypatch):
    monkeypatch.setattr("culture.bots.bot_manager.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    # Write config to disk
    bot_dir = tmp_path / sample_config.name
    save_bot_config(bot_dir / "bot.yaml", sample_config)

    mgr = BotManager(server)
    await mgr.load_bots()
    assert sample_config.name in mgr.bots
    assert mgr.bots[sample_config.name].active
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_start_stop_bot(server, sample_config, tmp_path, monkeypatch):
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    bot = await mgr.create_bot(sample_config)
    assert bot.active

    await mgr.stop_bot(sample_config.name)
    assert not bot.active

    await mgr.start_bot(sample_config.name)
    assert bot.active
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_dispatch(server, make_client, sample_config, tmp_path, monkeypatch):
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #test")
    await client.recv_all(timeout=0.5)

    mgr = BotManager(server)
    await mgr.create_bot(sample_config)
    await client.recv_all(timeout=0.3)

    result = await mgr.dispatch(sample_config.name, {"type": "deploy"})
    assert "Event: deploy" in result

    lines = await client.recv_all(timeout=0.5)
    assert any("Event: deploy" in line for line in lines)
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_dispatch_unknown_bot(server):
    mgr = BotManager(server)
    with pytest.raises(ValueError, match="not found"):
        await mgr.dispatch("nonexistent", {})


@pytest.mark.asyncio
async def test_dispatch_stopped_bot(server, sample_config, tmp_path, monkeypatch):
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    await mgr.create_bot(sample_config)
    await mgr.stop_bot(sample_config.name)

    with pytest.raises(RuntimeError, match="not active"):
        await mgr.dispatch(sample_config.name, {})


@pytest.mark.asyncio
async def test_list_bots_with_owner(server, tmp_path, monkeypatch):
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    await mgr.create_bot(
        BotConfig(
            name="testserv-ori-a",
            owner="testserv-ori",
            channels=["#a"],
        )
    )
    await mgr.create_bot(
        BotConfig(
            name="testserv-claude-b",
            owner="testserv-claude",
            channels=["#b"],
        )
    )

    all_bots = mgr.list_bots()
    assert len(all_bots) == 2

    ori_bots = mgr.list_bots(owner="testserv-ori")
    assert len(ori_bots) == 1
    assert ori_bots[0].name == "testserv-ori-a"

    claude_bots = mgr.list_bots(owner="testserv-claude")
    assert len(claude_bots) == 1
    assert claude_bots[0].name == "testserv-claude-b"
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_stop_unknown_bot(server):
    mgr = BotManager(server)
    with pytest.raises(ValueError, match="not found"):
        await mgr.stop_bot("nonexistent")


# ---------------------------------------------------------------------------
# Phase 4b additions — start/stop, load edge cases, dispatch error paths,
# system bot loading, event matching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_http_listener(server, tmp_path, monkeypatch):
    """`BotManager.start()` loads bots + system bots + binds a real HTTP listener.

    Uses the real `HttpListener` against an OS-assigned port (the `server`
    fixture sets `webhook_port=0`) and probes `/health` to confirm the
    listener actually bound.
    """
    import aiohttp

    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    await mgr.start()
    assert mgr._http_listener is not None

    # Confirm the listener actually bound by hitting /health.
    bound_host = mgr._http_listener.host
    bound_port = mgr._http_listener._runner.addresses[0][1]
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://{bound_host}:{bound_port}/health") as resp:
            assert resp.status == 200

    await mgr.stop()
    # After stop the runner is gone.
    assert mgr._http_listener is None


@pytest.mark.asyncio
async def test_start_swallows_listener_oserror(server, tmp_path, monkeypatch, caplog):
    """Listener bind failure (EADDRINUSE etc.) is logged, not fatal.

    OSError can't be deterministically forced on a real `HttpListener`
    without racing another process for a port, so we patch
    `HttpListener.start` itself to raise. The rest of the manager flow
    runs against the real `server` fixture.
    """
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    async def _bad_start(self):
        raise OSError("address already in use")

    monkeypatch.setattr("culture.bots.http_listener.HttpListener.start", _bad_start)

    mgr = BotManager(server)
    with caplog.at_level("WARNING"):
        await mgr.start()
    assert any("Could not start webhook listener" in r.message for r in caplog.records)
    assert mgr._http_listener is None
    await mgr.stop()


@pytest.mark.asyncio
async def test_start_tears_down_on_load_failure(server, tmp_path, monkeypatch):
    """If anything after `load_bots` raises (other than the listener OSError),
    `stop()` is invoked before the exception bubbles. Covers lines 74-78."""
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    mgr = BotManager(server)

    async def _bad_load_bots():
        raise RuntimeError("disk gone")

    mgr.load_bots = _bad_load_bots  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="disk gone"):
        await mgr.start()
    # stop() was invoked → listener stayed None.
    assert mgr._http_listener is None


@pytest.mark.asyncio
async def test_stop_swallows_listener_stop_errors(server, monkeypatch, caplog):
    """`stop()` logs but doesn't propagate listener.stop() exceptions."""

    class _ExplodingListener:
        async def stop(self):
            raise RuntimeError("kaboom")

    mgr = BotManager(server)
    mgr._http_listener = _ExplodingListener()
    with caplog.at_level("ERROR"):
        await mgr.stop()
    assert any("Failed to stop webhook listener" in r.message for r in caplog.records)
    assert mgr._http_listener is None


@pytest.mark.asyncio
async def test_load_bots_missing_dir_returns_quietly(server, tmp_path, monkeypatch):
    """`load_bots` early-returns when BOTS_DIR isn't a directory."""
    missing = tmp_path / "no-such-dir"
    monkeypatch.setattr(bm_mod, "BOTS_DIR", missing)
    mgr = BotManager(server)
    await mgr.load_bots()  # no exception
    assert mgr.bots == {}


@pytest.mark.asyncio
async def test_load_bots_skips_dirs_without_yaml(server, tmp_path, monkeypatch):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)
    (tmp_path / "empty-bot").mkdir()
    mgr = BotManager(server)
    await mgr.load_bots()
    assert mgr.bots == {}


@pytest.mark.asyncio
async def test_load_bots_skips_archived(server, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)
    cfg = BotConfig(
        name="testserv-ori-archived",
        owner="testserv-ori",
        channels=["#a"],
        archived=True,
    )
    save_bot_config(tmp_path / cfg.name / "bot.yaml", cfg)
    mgr = BotManager(server)
    with caplog.at_level("INFO"):
        await mgr.load_bots()
    assert cfg.name not in mgr.bots
    assert any("Skipping archived bot" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_load_bots_skips_invalid_event_filter(
    server,
    tmp_path,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)
    cfg = BotConfig(
        name="testserv-ori-badfilter",
        owner="testserv-ori",
        trigger_type="event",
        event_filter="this is not a valid filter (((",
        channels=["#x"],
    )
    save_bot_config(tmp_path / cfg.name / "bot.yaml", cfg)
    mgr = BotManager(server)
    with caplog.at_level("ERROR"):
        await mgr.load_bots()
    assert cfg.name not in mgr.bots
    assert any("invalid filter" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_load_bots_swallows_exception_per_bot(
    server,
    tmp_path,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    # Write a bot.yaml that load_bot_config will choke on.
    bot_dir = tmp_path / "broken-bot"
    bot_dir.mkdir()
    (bot_dir / "bot.yaml").write_text("not: valid: bot: config: garbage\n")
    mgr = BotManager(server)
    with caplog.at_level("ERROR"):
        await mgr.load_bots()
    assert "broken-bot" not in mgr.bots
    assert any("Failed to load bot" in r.message for r in caplog.records)


def test_register_bot_raises_on_invalid_filter(server, monkeypatch):
    monkeypatch.setattr(
        bm_mod,
        "compile_filter",
        lambda _s: (_ for _ in ()).throw(FilterParseError("bad")),
    )
    mgr = BotManager(server)
    cfg = BotConfig(
        name="testserv-ori-regbad",
        trigger_type="event",
        event_filter="bogus",
    )
    with pytest.raises(ValueError, match="invalid filter"):
        mgr.register_bot(cfg)


@pytest.mark.asyncio
async def test_try_start_bot_idempotent_when_already_starting(server, tmp_path, monkeypatch):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)
    cfg = BotConfig(name="testserv-ori-starting", channels=["#st"])
    mgr = BotManager(server)
    bot = await mgr.create_bot(cfg)
    bot.active = False  # force not-yet-started
    bot._starting = True  # mid-start
    assert await mgr._try_start_bot(bot) is False
    bot._starting = False
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_try_start_bot_logs_and_returns_false_when_start_fails(
    server,
    tmp_path,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)
    cfg = BotConfig(name="testserv-ori-starterror", channels=["#se"])
    mgr = BotManager(server)
    bot = mgr.register_bot(cfg)

    async def _bad_start():
        raise RuntimeError("nope")

    bot.start = _bad_start  # type: ignore[assignment]
    with caplog.at_level("ERROR"):
        result = await mgr._try_start_bot(bot)
    assert result is False
    assert any("failed to start" in r.message for r in caplog.records)


def test_matches_event_skips_non_event_triggers(server):
    """A bot with `trigger_type=webhook` never matches events."""
    mgr = BotManager(server)
    cfg = BotConfig(name="testserv-ori-wh", trigger_type="webhook")
    bot = mgr.register_bot(cfg)
    assert (
        mgr._matches_event(bot, {"type": "anything", "channel": None, "nick": None, "data": {}})
        is False
    )


def test_matches_event_skips_when_no_compiled_filter(server):
    """Event-trigger bot with no filter compiled → no match."""
    mgr = BotManager(server)
    cfg = BotConfig(name="testserv-ori-nofilter", trigger_type="event")
    # register_bot only compiles if event_filter is truthy; here it's None.
    bot = mgr.register_bot(cfg)
    assert (
        mgr._matches_event(bot, {"type": "x", "channel": None, "nick": None, "data": {}}) is False
    )


def test_matches_event_swallows_evaluate_exception(server, monkeypatch, caplog):
    mgr = BotManager(server)
    cfg = BotConfig(
        name="testserv-ori-evalerr",
        trigger_type="event",
        event_filter="type == 'foo'",
    )
    bot = mgr.register_bot(cfg)
    monkeypatch.setattr(
        bm_mod, "evaluate", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("eval"))
    )
    with caplog.at_level("ERROR"):
        result = mgr._matches_event(bot, {"type": "foo", "channel": None, "nick": None, "data": {}})
    assert result is False
    assert any("Filter evaluation failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_dispatch_to_bot_returns_when_start_fails(
    server,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    cfg = BotConfig(name="testserv-ori-startfail", trigger_type="event")
    bot = mgr.register_bot(cfg)
    bot.active = False  # not active

    async def _no_start(_bot):
        return False  # _try_start_bot returns False

    monkeypatch.setattr(mgr, "_try_start_bot", _no_start)

    # Should silently return without calling bot.handle.
    handled: list = []

    async def _spy_handle(_payload):
        handled.append(True)
        return ""

    bot.handle = _spy_handle  # type: ignore[assignment]
    await mgr._dispatch_to_bot(bot, {"type": "foo", "channel": None, "nick": None, "data": {}})
    assert handled == []


@pytest.mark.asyncio
async def test_dispatch_to_bot_logs_handle_exception(
    server,
    tmp_path,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    cfg = BotConfig(
        name="testserv-ori-handlefail",
        trigger_type="event",
        channels=["#hf"],
    )
    mgr = BotManager(server)
    bot = await mgr.create_bot(cfg)

    async def _bad_handle(_payload):
        raise RuntimeError("handler boom")

    bot.handle = _bad_handle  # type: ignore[assignment]
    with caplog.at_level("ERROR"):
        await mgr._dispatch_to_bot(bot, {"type": "foo", "channel": None, "nick": None, "data": {}})
    assert any("handle() failed" in r.message for r in caplog.records)
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_on_event_dispatches_matching_bots(
    server_with_bot,
    server,
    tmp_path,
    monkeypatch,
):
    """An event matching the bot filter triggers `_dispatch_to_bot`."""
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    server, cfg = server_with_bot(
        bot_name="testserv-ori-fire",
        trigger_type="event",
        event_filter="type == 'user.join'",
        channels=["#fire"],
    )

    dispatched: list = []

    async def _spy(bot, ctx):
        dispatched.append(ctx)

    monkeypatch.setattr(server.bot_manager, "_dispatch_to_bot", _spy)
    fake_event = SimpleNamespace(
        type=SimpleNamespace(value="user.join"),
        channel="#fire",
        nick="testserv-claude",
        data={},
    )
    await server.bot_manager.on_event(fake_event)
    assert dispatched and dispatched[0]["type"] == "user.join"


@pytest.mark.asyncio
async def test_start_bot_loads_from_disk_when_not_in_registry(
    server,
    tmp_path,
    monkeypatch,
):
    """Calling `start_bot(name)` for a name not in the registry → load from
    disk + start."""
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", tmp_path)

    cfg = BotConfig(name="testserv-ori-fromdisk", channels=["#fd"])
    save_bot_config(tmp_path / cfg.name / "bot.yaml", cfg)

    mgr = BotManager(server)
    await mgr.start_bot(cfg.name)
    assert cfg.name in mgr.bots
    assert mgr.bots[cfg.name].active
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_start_bot_unknown_raises(server, tmp_path, monkeypatch):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)
    mgr = BotManager(server)
    with pytest.raises(ValueError, match="not found"):
        await mgr.start_bot("nonexistent")


@pytest.mark.asyncio
async def test_stop_all_swallows_exceptions(server, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(bm_mod, "BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture.bots.bot.BOTS_DIR", tmp_path)

    cfg = BotConfig(name="testserv-ori-stopfail", channels=["#sf"])
    mgr = BotManager(server)
    bot = await mgr.create_bot(cfg)

    async def _bad_stop():
        raise RuntimeError("stop kaboom")

    bot.stop = _bad_stop  # type: ignore[assignment]
    with caplog.at_level("ERROR"):
        await mgr.stop_all()
    assert any("Failed to stop bot" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_load_system_bots_skips_duplicate(server, monkeypatch, caplog):
    """If a system bot's name collides with one already registered, skip it."""

    class _DummyCfg:
        def __init__(self, name):
            self.name = name
            self.trigger_type = "event"
            self.event_filter = None

    monkeypatch.setattr(
        "culture.bots.system.discover_system_bots",
        lambda _name, _cfg: [_DummyCfg("testserv-system-x")],
    )

    mgr = BotManager(server)
    # Pre-register the same name.
    mgr.bots["testserv-system-x"] = SimpleNamespace(name="testserv-system-x")
    with caplog.at_level("INFO"):
        mgr.load_system_bots()
    assert any("name already registered" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_load_system_bots_logs_register_failure(server, monkeypatch, caplog):
    """If `register_bot` raises, the failure is logged, not propagated."""

    class _DummyCfg:
        def __init__(self, name):
            self.name = name
            self.trigger_type = "event"
            self.event_filter = None

    monkeypatch.setattr(
        "culture.bots.system.discover_system_bots",
        lambda _name, _cfg: [_DummyCfg("testserv-system-bad")],
    )

    mgr = BotManager(server)

    def _bad_register(_cfg):
        raise RuntimeError("regfail")

    monkeypatch.setattr(mgr, "register_bot", _bad_register)
    with caplog.at_level("ERROR"):
        mgr.load_system_bots()
    assert any("Failed to register system bot" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_load_system_bots_reads_server_config(server, monkeypatch):
    """When `server.config.system_bots` is set, it's forwarded to discover."""
    captured: dict = {}

    def _capture(server_name, cfg):
        captured["server_name"] = server_name
        captured["cfg"] = cfg
        return []

    monkeypatch.setattr("culture.bots.system.discover_system_bots", _capture)
    server.config.system_bots = {"welcome": {"enabled": False}}
    mgr = BotManager(server)
    mgr.load_system_bots()
    assert captured["server_name"] == "testserv"
    assert captured["cfg"] == {"system_bots": {"welcome": {"enabled": False}}}
