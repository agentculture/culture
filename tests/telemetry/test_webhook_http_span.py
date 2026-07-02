"""Inbound HTTP span via opentelemetry-instrumentation-aiohttp-server (Plan 7)."""

from __future__ import annotations

import pytest
from aiohttp import ClientSession

from culture_core.bots.config import BotConfig


@pytest.mark.asyncio
async def test_webhook_emits_aiohttp_server_span_parenting_bot_run(
    tracing_exporter, webhook_server
):
    _server, mgr, port = webhook_server
    await mgr.create_bot(
        BotConfig(
            name="testserv-spanhook",
            owner="testserv",
            channels=[],
            template="ok {body}",
        )
    )

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-spanhook", json={"body": "x"}
        ) as resp:
            assert resp.status == 200

    spans = tracing_exporter.get_finished_spans()
    aiohttp_spans = [
        s
        for s in spans
        if s.instrumentation_scope.name == "opentelemetry.instrumentation.aiohttp_server"
    ]
    assert aiohttp_spans, [s.instrumentation_scope.name for s in spans]
    aiohttp_span = aiohttp_spans[0]

    run_spans = [s for s in spans if s.name == "bot.run"]
    assert len(run_spans) == 1
    run = run_spans[0]
    assert run.context.trace_id == aiohttp_span.context.trace_id
    assert run.parent is not None
    assert run.parent.span_id == aiohttp_span.context.span_id
