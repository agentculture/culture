"""E2E: brief to worker A does NOT leak to worker B's task channel.

The "no cross-talk" guarantee is core to the boss/worker model. If a
brief targeted at one suffix ever reaches another's #task channel,
the whole supervision model breaks (worker B reads it, thinks it was
assigned the task, and starts work in the wrong project).

This test pins the wire-level guarantee:

    boss_cli brief qa "hello A"
    → worker qa's read pump sees "hello A" in #task-qa
    → worker docs's read pump sees NOTHING in #task-docs
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_brief_to_qa_does_not_leak_to_docs(
    e2e_ircd, bridge, fake_worker, register_fake_worker, bridge_join, boss_cli
):
    bridge_proc = await bridge("testserv-boss")
    assert bridge_proc.poll() is None

    register_fake_worker("qa", boss_nick="testserv-boss")
    register_fake_worker("docs", boss_nick="testserv-boss")

    assert (await bridge_join("testserv-boss", "#task-qa")).get("ok")
    assert (await bridge_join("testserv-boss", "#task-docs")).get("ok")

    qa = await fake_worker("testserv-qa", channel="#task-qa")
    docs = await fake_worker("testserv-docs", channel="#task-docs")

    result = await boss_cli("brief", "qa", "hello A", nick="testserv-boss", timeout=20.0)
    assert (
        result.returncode == 0
    ), f"brief failed: stderr={result.stderr!r} stdout={result.stdout!r}"

    # Give the relay 2s — the brief must arrive before this window closes.
    deadline = asyncio.get_event_loop().time() + 2.0
    qa_saw = False
    while asyncio.get_event_loop().time() < deadline:
        if any("hello A" in m for m in qa.received_privmsgs()):
            qa_saw = True
            break
        await asyncio.sleep(0.05)
    assert qa_saw, (
        f"qa should have received 'hello A' in #task-qa. " f"Saw: {qa.received_privmsgs()!r}"
    )

    # docs MUST NOT have seen the brief. Give it the same window the
    # qa receiver got so we're not just racing the deadline.
    docs_msgs = [m for m in docs.received_privmsgs() if "hello A" in m]
    assert docs_msgs == [], (
        f"CROSS-CONTAMINATION: docs received the brief intended for qa. "
        f"Leaked messages: {docs_msgs!r}"
    )
