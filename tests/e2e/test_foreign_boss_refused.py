"""E2E: a boss cannot brief a worker registered to another boss.

The team-isolation guarantee — a boss should never be able to inject
tasks into another team's worker — is enforced at the CLI layer by
``_foreign_worker``, which derives ownership from the manifest. The
manifest is spawn-recorded and not worker-writable, which is why
ownership cannot be spoofed via the request payload.

This test pins the refusal end-to-end:

    register worker 'qa' as owned by 'testserv-other'
    boss_cli brief qa "..." with nick=testserv-boss
    → REFUSED exit code (2), stderr says "owned by another boss"
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_brief_to_foreign_worker_is_refused(e2e_ircd, bridge, register_fake_worker, boss_cli):
    # Start the legit boss bridge — it owns nothing in the manifest yet.
    bridge_proc = await bridge("testserv-boss")
    assert bridge_proc.poll() is None

    # qa is registered to testserv-OTHER, not testserv-boss.
    register_fake_worker("qa", boss_nick="testserv-other")

    # testserv-boss tries to brief qa anyway.
    result = await boss_cli("brief", "qa", "should be refused", nick="testserv-boss", timeout=20.0)

    assert result.returncode == 2, (
        f"expected exit code 2 (REFUSED), got {result.returncode}. "
        f"stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    assert "owned by another boss" in result.stderr.lower() or (
        "REFUSED" in result.stderr and "testserv-qa" in result.stderr
    ), (f"expected REFUSED + 'owned by another boss' in stderr. " f"Got: {result.stderr!r}")


@pytest.mark.asyncio
async def test_brief_to_unregistered_worker_is_refused(e2e_ircd, bridge, boss_cli):
    """A worker NOT in the manifest is foreign to every boss (fail closed).

    This is the security invariant: ownership is derived from the
    manifest. An orphan worker (not in manifest) cannot be claimed
    implicitly — it must be adopted explicitly. This test pins
    that fail-closed behavior.
    """
    bridge_proc = await bridge("testserv-boss")
    assert bridge_proc.poll() is None

    # NOTE: no register_fake_worker call. The manifest is empty.
    result = await boss_cli("brief", "ghost", "no such worker", nick="testserv-boss", timeout=20.0)

    assert result.returncode == 2, (
        f"expected exit code 2 (REFUSED — no manifest entry == foreign). "
        f"Got: returncode={result.returncode}, stderr={result.stderr!r}"
    )
