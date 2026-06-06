"""E2E: archive → unarchive preserves boss ownership (Plenty BUG 2).

The Plenty agent reported that workers spawned by an old boss, then
``culture agent archive``'d, then later ``culture agent restore``'d
came back **RUNNING but unmanageable by any boss** —
``boss brief`` returned ``REFUSED: <w> is not your worker (owned by
another boss)`` even for the original spawning boss.

The dogfood findings (``docs/v9.1.7-plenty-session-dogfood-findings.md``)
proposed the likely cause as either (a) unarchive doesn't re-link
``boss_owner``, or (b) the ``plenty-`` vs ``local-`` server-prefix
split (BUG 1) breaks the ownership match key.

This test pins the invariant: with a CONSISTENT server prefix
(no drift), archive→unarchive must preserve ownership. If this
passes, BUG 2 was actually a symptom of BUG 1's prefix split,
not an independent unarchive defect — which is fixed by the
v9.1.7-v9.1.8 fail-loud + write-prevention work AND by this
PR's CULTURE_HOME isolation closing the test-pollution path.
"""

from __future__ import annotations

import asyncio
import os
import subprocess as _subprocess
import sys

import pytest


async def _run_culture_agent(env: dict[str, str], *args, timeout: float = 15.0):
    """Async wrapper around ``python -m culture agent ...`` so we
    don't block the event loop the in-process IRCd runs on."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "culture",
        "agent",
        *args,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return _subprocess.CompletedProcess(
        args=list(args),
        returncode=proc.returncode,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


@pytest.mark.asyncio
async def test_archive_then_unarchive_preserves_boss_ownership(
    e2e_ircd,
    bridge,
    e2e_env,
    e2e_root,
    fake_worker,
    register_fake_worker,
    bridge_join,
    boss_cli,
):
    bridge_proc = await bridge("testserv-boss")
    assert bridge_proc.poll() is None

    register_fake_worker("qa", boss_nick="testserv-boss")
    assert (await bridge_join("testserv-boss", "#task-qa")).get("ok")
    worker = await fake_worker("testserv-qa", channel="#task-qa")

    # Round 1: brief works.
    r1 = await boss_cli("brief", "qa", "round 1", nick="testserv-boss", timeout=20.0)
    assert r1.returncode == 0, f"round 1 brief failed: {r1.stderr!r}"

    # Archive qa.
    server_yaml = str(e2e_root / ".culture" / "server.yaml")
    arch = await _run_culture_agent(
        e2e_env,
        "archive",
        "--config",
        server_yaml,
        "testserv-qa",
        "--reason",
        "test archive cycle",
    )
    assert arch.returncode == 0, f"archive failed: stderr={arch.stderr!r} stdout={arch.stdout!r}"

    # Verify the boss field is STILL set after archive — Plenty's BUG 2
    # claim was that ownership got severed. We assert at the file level
    # because that's the source of truth read by the manifest loader.
    import yaml as _yaml

    culture_yaml_path = e2e_root / ".culture" / "helpers" / "qa" / "culture.yaml"
    raw = _yaml.safe_load(culture_yaml_path.read_text())
    # The yaml may be a list of agents or a single dict — the
    # register_fake_worker fixture writes a single dict.
    if isinstance(raw, list):
        agents_in_yaml = raw
    elif isinstance(raw, dict) and "agents" in raw:
        agents_in_yaml = raw["agents"]
    else:
        agents_in_yaml = [raw]
    qa_entries = [a for a in agents_in_yaml if a.get("suffix") == "qa"]
    assert qa_entries, f"qa entry missing from culture.yaml: {raw!r}"
    assert qa_entries[0].get("boss") == "testserv-boss", (
        f"BUG 2 REPRODUCED: archive severed boss link. "
        f"qa entry after archive: {qa_entries[0]!r}"
    )

    # Unarchive qa.
    unarch = await _run_culture_agent(
        e2e_env,
        "restore",
        "--config",
        server_yaml,
        "testserv-qa",
    )
    assert (
        unarch.returncode == 0
    ), f"unarchive failed: stderr={unarch.stderr!r} stdout={unarch.stdout!r}"

    # Verify boss field SURVIVED the round-trip.
    raw = _yaml.safe_load(culture_yaml_path.read_text())
    if isinstance(raw, list):
        agents_in_yaml = raw
    elif isinstance(raw, dict) and "agents" in raw:
        agents_in_yaml = raw["agents"]
    else:
        agents_in_yaml = [raw]
    qa_entries = [a for a in agents_in_yaml if a.get("suffix") == "qa"]
    assert qa_entries[0].get("boss") == "testserv-boss", (
        f"BUG 2 REPRODUCED: unarchive severed boss link. "
        f"qa entry after unarchive: {qa_entries[0]!r}"
    )

    # Re-register at the manifest level (archive doesn't remove from
    # manifest but the bridge's cache may have cleared). Flush the
    # ACL cache so the next brief reads fresh.
    from culture.agentirc.client import _invalidate_owner_map_cache

    _invalidate_owner_map_cache()

    # Round 2: brief must succeed — qa is back, owned by testserv-boss.
    r2 = await boss_cli("brief", "qa", "round 2", nick="testserv-boss", timeout=20.0)
    assert r2.returncode == 0, (
        f"BUG 2 SURFACED: post-unarchive brief returned {r2.returncode}. "
        f"stderr={r2.stderr!r} stdout={r2.stdout!r}"
    )

    # Wait for the message on the wire.
    deadline = asyncio.get_event_loop().time() + 3.0
    while asyncio.get_event_loop().time() < deadline:
        if any("round 2" in m for m in worker.received_privmsgs()):
            return
        await asyncio.sleep(0.05)
    pytest.fail(
        f"post-unarchive brief returned 0 but worker never received it. "
        f"Received: {worker.received_privmsgs()!r}"
    )
