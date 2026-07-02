"""Webhook-side metric — culture.bot.webhook.duration (Plan 7)."""

from __future__ import annotations

import pytest
from aiohttp import ClientSession

from culture_core.bots.config import BotConfig
from tests.telemetry._metrics_helpers import get_histogram_count, get_histogram_sum


@pytest.mark.asyncio
async def test_webhook_duration_records_2xx(metrics_reader, webhook_server):
    _server, mgr, port = webhook_server
    await mgr.create_bot(
        BotConfig(
            name="testserv-hook",
            owner="testserv",
            channels=[],
            template="ok {body}",
        )
    )

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-hook", json={"body": "ping"}
        ) as resp:
            assert resp.status == 200

    n = get_histogram_count(
        metrics_reader,
        "culture.bot.webhook.duration",
        attrs={"bot": "testserv-hook", "status_class": "2xx"},
    )
    assert n == 1


@pytest.mark.asyncio
async def test_webhook_duration_records_4xx_unknown_bot(metrics_reader, webhook_server):
    _server, _mgr, port = webhook_server

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{port}/testserv-nope", json={}) as resp:
            assert resp.status == 404

    n = get_histogram_count(
        metrics_reader,
        "culture.bot.webhook.duration",
        attrs={"bot": "testserv-nope", "status_class": "4xx"},
    )
    assert n == 1


@pytest.mark.asyncio
async def test_webhook_duration_records_4xx_invalid_json(metrics_reader, webhook_server):
    _server, _mgr, port = webhook_server

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-anything",
            data="not json",
            headers={"Content-Type": "application/json"},
        ) as resp:
            assert resp.status == 400

    n = get_histogram_count(
        metrics_reader,
        "culture.bot.webhook.duration",
        attrs={"bot": "testserv-anything", "status_class": "4xx"},
    )
    assert n == 1


@pytest.mark.asyncio
async def test_webhook_duration_records_health_unrouted(metrics_reader, webhook_server):
    _server, _mgr, port = webhook_server

    async with ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/health") as resp:
            assert resp.status == 200

    n = get_histogram_count(
        metrics_reader,
        "culture.bot.webhook.duration",
        attrs={"bot": "_unrouted", "status_class": "2xx"},
    )
    assert n == 1


@pytest.mark.asyncio
async def test_webhook_duration_records_5xx_on_uncaught_exception(
    metrics_reader, webhook_server, monkeypatch
):
    """An uncaught non-ValueError/RuntimeError in dispatch is reported as 5xx."""
    _server, mgr, port = webhook_server
    await mgr.create_bot(
        BotConfig(
            name="testserv-boom",
            owner="testserv",
            channels=[],
            template="ok",
        )
    )

    async def boom(bot_name, payload):
        # Neither ValueError nor RuntimeError → falls through to the
        # generic 500 branch in _handle_webhook.
        raise KeyError("__synthetic_internal_error__")

    monkeypatch.setattr(mgr, "dispatch", boom)

    async with ClientSession() as session:
        async with session.post(f"http://127.0.0.1:{port}/testserv-boom", json={}) as resp:
            assert resp.status == 500

    n = get_histogram_count(
        metrics_reader,
        "culture.bot.webhook.duration",
        attrs={"bot": "testserv-boom", "status_class": "5xx"},
    )
    assert n == 1


@pytest.mark.asyncio
async def test_webhook_duration_value_is_positive(metrics_reader, webhook_server):
    """Recorded duration must be > 0 (catches zero-time / inverted-timer regressions)."""
    _server, mgr, port = webhook_server
    await mgr.create_bot(
        BotConfig(
            name="testserv-timed",
            owner="testserv",
            channels=[],
            template="ok",
        )
    )

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-timed", json={"body": "ping"}
        ) as resp:
            assert resp.status == 200

    total = get_histogram_sum(
        metrics_reader,
        "culture.bot.webhook.duration",
        attrs={"bot": "testserv-timed"},
    )
    assert total > 0.0
