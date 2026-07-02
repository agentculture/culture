"""Federation audit records — federated events land in the receiver's
audit log with origin=federated, peer=<peer_name>."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


def _read_records(audit_dir: Path, server_name: str) -> list[dict]:
    files = sorted(audit_dir.glob(f"{server_name}-*.jsonl*"))
    out: list[dict] = []
    for f in files:
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


@pytest.mark.asyncio
async def test_federated_message_event_audit_record(
    linked_servers, make_client_a, make_client_b, tmp_path
):
    """A PRIVMSG that crosses alpha→beta should produce an audit record on
    BOTH servers: origin=local on alpha, origin=federated peer=alpha on beta."""
    server_a, server_b = linked_servers

    # Both clients join a shared channel.
    client_a = await make_client_a(nick="alpha-alice", user="alice")
    client_b = await make_client_b(nick="beta-bob", user="bob")
    await client_a.send("JOIN #fed-audit")
    await client_a.recv_all(timeout=0.5)
    await client_b.send("JOIN #fed-audit")
    await client_b.recv_all(timeout=0.5)
    # Allow membership burst.
    await asyncio.sleep(0.3)

    # alpha-alice sends a channel message; it should arrive on beta as
    # a federated MESSAGE event.
    await client_a.send("PRIVMSG #fed-audit :hello-from-alpha")
    await client_a.recv_all(timeout=0.5)

    # Allow propagation + audit drain.
    await asyncio.sleep(0.5)
    await asyncio.wait_for(server_a.audit.queue.join(), timeout=2.0)
    await asyncio.wait_for(server_b.audit.queue.join(), timeout=2.0)

    audit_alpha = tmp_path / "audit_alpha"
    audit_beta = tmp_path / "audit_beta"

    records_alpha = _read_records(audit_alpha, "alpha")
    records_beta = _read_records(audit_beta, "beta")

    # On alpha: origin=local, peer="" for the originating MESSAGE.
    alpha_msgs = [
        r
        for r in records_alpha
        if r["event_type"] == "message" and r.get("payload", {}).get("text") == "hello-from-alpha"
    ]
    assert alpha_msgs, (
        f"expected origin=local message on alpha, got: "
        f"{[r['event_type'] for r in records_alpha]}"
    )
    assert alpha_msgs[0]["origin"] == "local"
    assert alpha_msgs[0]["peer"] == ""
    assert alpha_msgs[0]["actor"]["nick"] == "alpha-alice"

    # On beta: origin=federated, peer=alpha for the relayed MESSAGE.
    beta_msgs = [
        r
        for r in records_beta
        if r["event_type"] == "message" and r.get("payload", {}).get("text") == "hello-from-alpha"
    ]
    assert beta_msgs, (
        f"expected origin=federated message on beta, got: "
        f"{[(r['event_type'], r.get('origin')) for r in records_beta]}"
    )
    assert beta_msgs[0]["origin"] == "federated"
    assert beta_msgs[0]["peer"] == "alpha"
    assert beta_msgs[0]["actor"]["nick"] == "alpha-alice"


@pytest.mark.asyncio
async def test_federated_join_no_user_join_audit_on_receiver(
    linked_servers, make_client_a, tmp_path
):
    """Documents current behavior: SJOIN (federated JOIN) does NOT produce a
    user.join audit record on the receiving server.

    ServerLink._handle_sjoin processes SJOIN wire messages purely at the
    channel-membership level — it does not call server.emit_event, so no
    user.join record lands in the receiver's audit log.  Only events relayed
    via SEVENT (e.g. PRIVMSG → message) flow through emit_event and produce
    audit records.

    This is a known gap: a future improvement should emit a federated user.join
    event from _handle_sjoin so the audit is complete.  Until then this test
    pins the current behavior so a regression is immediately visible if
    _handle_sjoin is changed to emit the event.
    """
    _server_a, server_b = linked_servers
    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #fed-join")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    await asyncio.wait_for(server_b.audit.queue.join(), timeout=2.0)

    audit_beta = tmp_path / "audit_beta"
    records = _read_records(audit_beta, "beta")
    event_types = [r["event_type"] for r in records]

    # Confirm the server.link events ARE present (federation link audit works).
    assert "server.link" in event_types, f"expected server.link records on beta, got: {event_types}"

    # The gap: user.join for the federated client is NOT currently produced.
    # This assertion documents that and will break loudly if the behavior
    # changes — at which point the test should be updated to assert
    # origin=federated + peer=alpha on the new record.
    join_records = [
        r
        for r in records
        if r["event_type"] == "user.join" and r.get("actor", {}).get("nick") == "alpha-alice"
    ]
    assert not join_records, (
        "SJOIN now emits a user.join audit record on the receiver — "
        "update this test to assert origin=federated, peer=alpha on that record. "
        f"Unexpected records: {join_records}"
    )
