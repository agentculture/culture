"""Load-bearing E2E happy path: does ``culture boss brief`` actually
reach the worker's channel?

This test is the operational claim "no session has gotten past brief"
made by the Plenty dogfood. If brief works in isolation (under
hermetic conditions, with a real IRCd + real bridge subprocess +
fake worker on #task-<suffix>), this test passes. If it fails,
THIS test is the harness's first witness — pin the error and we
have a reproducer.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_brief_reaches_worker_via_real_bridge(
    e2e_ircd, bridge, fake_worker, register_fake_worker, bridge_join, boss_cli
):
    """Full pipeline:

        - IRCd up as 'testserv' on a random port
        - server.yaml seeded with the same name/host/port
        - bridge subprocess for 'testserv-boss' on the mesh
        - fake worker registered as 'testserv-qa' on #task-qa
        - boss CLI: brief qa "hello from e2e"
        - fake worker receives the PRIVMSG on its read pump

    The assertion is **wire-level**: the worker's TCP recv pump
    must show a PRIVMSG line whose target is ``#task-qa`` and
    whose body matches the briefed text. No filesystem assertions
    (those depend on persistence paths the design workflow's
    critique flagged as fabricated). The wire is the ground truth.
    """
    # 1. Bring the bridge up.
    bridge_proc = await bridge("testserv-boss")
    assert bridge_proc.poll() is None, "bridge subprocess died"

    # 2. Register the worker on disk so the ownership check at
    #    ``boss.py::_foreign_worker`` allows the brief through.
    #    Mirrors the side-effects of ``culture boss spawn qa`` but
    #    skips the Claude SDK-dependent daemon spawn.
    register_fake_worker("qa", boss_nick="testserv-boss")

    # 3. Tell the bridge to join the worker's task channel —
    #    mirrors ``culture boss spawn qa``'s
    #    ``_boss_irc("irc_join", channel="#task-qa")`` at boss.py:799.
    #    Without this, the bridge has no idea about #task-qa (its
    #    startup _rejoin_owned_task_channels ran on an empty
    #    manifest before register_fake_worker added the entry).
    join_resp = await bridge_join("testserv-boss", "#task-qa")
    assert join_resp and join_resp.get("ok"), f"bridge IPC irc_join failed: {join_resp!r}"
    assert (
        bridge_proc.poll() is None
    ), f"bridge subprocess exited (rc={bridge_proc.poll()}) after IPC join"

    # 4. Mint a fake worker that joins #task-qa BEFORE brief fires.
    worker = await fake_worker("testserv-qa", channel="#task-qa")

    # 5. Confirm bridge is still up before brief
    assert (
        bridge_proc.poll() is None
    ), f"bridge subprocess exited (rc={bridge_proc.poll()}) before brief"

    # 3. Brief the worker.
    result = await boss_cli(
        "brief",
        "qa",
        "hello from e2e",
        nick="testserv-boss",
        timeout=20.0,
    )

    # 4. Assert the brief succeeded at the CLI layer (returncode 0,
    #    no "could not verify" error). If it failed, the stderr is
    #    the most actionable bit of diagnostic — surface it.
    assert result.returncode == 0, (
        f"boss brief exited {result.returncode}. "
        f"stderr: {result.stderr!r} stdout: {result.stdout!r}"
    )

    # 5. Assert the brief reached the worker on the wire. Give the
    #    bridge a moment to relay the PRIVMSG. The fake worker's
    #    background read pump appends to ``received_privmsgs()``.
    import asyncio

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if any("hello from e2e" in m for m in worker.received_privmsgs()):
            return
        await asyncio.sleep(0.1)

    pytest.fail(
        "brief returned 0 but the fake worker on #task-qa never saw a "
        "PRIVMSG matching 'hello from e2e' within 5s.\n"
        f"Worker received {len(worker.received_privmsgs())} PRIVMSG(s): "
        f"{worker.received_privmsgs()!r}\n"
        f"boss stderr: {result.stderr!r}\n"
        f"boss stdout: {result.stdout!r}"
    )
