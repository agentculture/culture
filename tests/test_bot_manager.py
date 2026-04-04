"""Tests for BotManager — bot lifecycle and dispatch."""

import pytest
import pytest_asyncio

from agentirc.bots.bot_manager import BotManager
from agentirc.bots.config import BOTS_DIR, BotConfig, save_bot_config


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
    monkeypatch.setattr("agentirc.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot_manager.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    bot = await mgr.create_bot(sample_config)
    assert bot.active
    assert bot.name in mgr.bots
    assert (tmp_path / sample_config.name / "bot.yaml").exists()
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_load_bots_from_disk(server, sample_config, tmp_path, monkeypatch):
    monkeypatch.setattr("agentirc.bots.bot_manager.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot.BOTS_DIR", tmp_path)

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
    monkeypatch.setattr("agentirc.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot.BOTS_DIR", tmp_path)

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
    monkeypatch.setattr("agentirc.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot.BOTS_DIR", tmp_path)

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
    monkeypatch.setattr("agentirc.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    await mgr.create_bot(sample_config)
    await mgr.stop_bot(sample_config.name)

    with pytest.raises(RuntimeError, match="not active"):
        await mgr.dispatch(sample_config.name, {})


@pytest.mark.asyncio
async def test_list_bots_with_owner(server, tmp_path, monkeypatch):
    monkeypatch.setattr("agentirc.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot.BOTS_DIR", tmp_path)

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
