"""E2E: server.name drift in server.yaml is detected and fails loud
at the bridge — Plenty BUG 1.

When the bridge subprocess reads ``server.yaml`` whose ``server.name``
disagrees with the running IRCd's ``--name``, the bridge will register
under the wrong prefix and the IRCd will reject every NICK with
``432 ERR_ERRONEUSNICKNAME``. v9.1.7 made the bridge fail loud (daemon
exits 1) instead of silently retrying forever, and the 432 reply carries
actionable reason text including the ``culture server migrate-prefix``
remediation hint.

This test pins the contract:

    seed server.yaml with server.name='drifted'
    spawn bridge for 'drifted-boss'
    IRCd is 'testserv' — every NICK starting with 'drifted-' is rejected
    → bridge subprocess exits non-zero (or never appears on the mesh)
    → stderr surfaces actionable diagnostics
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest


@pytest.mark.asyncio
async def test_drifted_server_name_in_yaml_fails_loud(
    e2e_ircd, e2e_env, e2e_root, seed_server_yaml
):
    """Bridge subprocess detects + reports server-name drift.

    The harness writes a server.yaml with ``server.name=drifted`` but
    points host/port at the real IRCd (which is named ``testserv``).
    The bridge tries to register as ``drifted-boss``, gets a 432, and
    must exit non-zero rather than spin in an infinite reconnect.
    """
    # IRCd is "testserv"; deliberately seed a drifted name.
    drifted_yaml = seed_server_yaml(
        server_name="drifted", host="127.0.0.1", port=e2e_ircd.config.port
    )

    bridge_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "culture.clients.bridge",
            "start",
            "drifted-boss",
            "--config",
            str(drifted_yaml),
        ],
        env=e2e_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )

    # v9.1.7 contract: the bridge daemon must NOT spin forever on the
    # rejected handshake. Give it 8 seconds to either exit or be
    # terminated. If it's still running at the end, the v9.1.7 fail-loud
    # regressed.
    deadline = asyncio.get_event_loop().time() + 8.0
    while asyncio.get_event_loop().time() < deadline:
        if bridge_proc.poll() is not None:
            break
        await asyncio.sleep(0.1)

    if bridge_proc.poll() is None:
        bridge_proc.terminate()
        try:
            bridge_proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            bridge_proc.kill()
            bridge_proc.wait(timeout=2)
        # If we had to kill it, the v9.1.7 fail-loud is broken.
        pytest.fail(
            "v9.1.7 fail-loud regression: bridge did not exit on its "
            "own after the drifted-name 432 rejection. Had to kill it."
        )

    assert bridge_proc.returncode != 0, (
        f"bridge with drifted server.name exited with code 0 (should be "
        f"non-zero per v9.1.7 fail-loud contract). "
        f"returncode={bridge_proc.returncode}"
    )

    # Stderr should carry actionable diagnostic text.
    err = (bridge_proc.stderr.read() if bridge_proc.stderr else b"").decode(errors="replace")
    # The contract from the Plenty handoff: 432 + migrate-prefix hint.
    has_432 = "432" in err or "ERRONEUS" in err.upper()
    has_remedy = "migrate-prefix" in err or "must start with" in err.lower()
    assert has_432 or has_remedy, (
        f"bridge stderr lacks the actionable drift diagnostic. "
        f"Expected '432' or 'migrate-prefix' hint. Got: {err!r}"
    )


@pytest.mark.asyncio
async def test_matching_server_name_in_yaml_succeeds(e2e_ircd, bridge):
    """Sanity: when server.yaml's name matches the IRCd, bridge comes up.

    The drift test relies on the matching case actually working — if both
    cases failed, the drift test would pass for the wrong reason. This
    pair of tests together pins the contract.
    """
    bridge_proc = await bridge("testserv-boss")
    assert bridge_proc.poll() is None, (
        "bridge subprocess died despite matching server.name; " "harness sanity check failed."
    )
