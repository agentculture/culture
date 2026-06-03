"""Concurrency tests for the bridge inbox spool.

Closes Qodo PR #50 finding #1: ``_ipc_inbox_drain`` previously did
``read … then os.unlink``, which lost any entry appended between the
read loop ending and the unlink. The fix moved both producer
(:func:`spool_inbound`) and consumer (:func:`drain_inbox`) under an
exclusive ``fcntl.flock`` over the same path.

These tests run a writer thread alongside the drain to assert that no
entries are dropped under contention, and that the file ends up empty
after the drain regardless of writer interleaving.
"""

from __future__ import annotations

import json
import threading

import pytest

from culture.clients.bridge._spool import drain_inbox, inbox_path, spool_inbound


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Hermetic CULTURE_HOME so spool writes don't escape the tmp dir."""
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def test_drain_empty_returns_empty_list(home):
    """Draining a non-existent spool returns [] without raising."""
    assert drain_inbox("local-w") == []


def test_drain_returns_all_entries_after_writes(home):
    """Every spooled entry is returned by the next drain, oldest first."""
    spool_inbound("local-w", "inbound_dm", sender="boss", text="hi 1")
    spool_inbound("local-w", "inbound_dm", sender="boss", text="hi 2")
    spool_inbound("local-w", "inbound_mention", sender="peer", text="@local-w yo")

    entries = drain_inbox("local-w")
    assert [e["text"] for e in entries] == ["hi 1", "hi 2", "@local-w yo"]
    assert [e["kind"] for e in entries] == [
        "inbound_dm",
        "inbound_dm",
        "inbound_mention",
    ]


def test_drain_truncates_so_second_drain_is_empty(home):
    """After a drain, the next drain sees only entries spooled afterwards."""
    spool_inbound("local-w", "inbound_dm", sender="boss", text="first")
    assert len(drain_inbox("local-w")) == 1
    # Spool file is now empty (truncate-in-place), so a second drain
    # against the same path returns [] without raising.
    assert drain_inbox("local-w") == []
    spool_inbound("local-w", "inbound_dm", sender="boss", text="second")
    second = drain_inbox("local-w")
    assert [e["text"] for e in second] == ["second"]


def test_concurrent_writes_during_drain_are_not_lost(home):
    """Race-safety: a writer that takes the lock just after the drain
    completes truncation must see an empty file (not the pre-drain
    contents), and its append must survive the next drain — i.e. it is
    NOT lost to the read-then-unlink window Qodo flagged.

    Strategy: seed the spool with N records, then on a writer thread
    spam M more `spool_inbound` calls while the main thread does a
    single drain. After both finish, drain again and assert
    `seed_count + post_drain_count == N + M`. With the pre-fix
    `os.unlink` flow, post-drain writes were lost when the writer was
    mid-`open()` at the unlink instant.
    """
    nick = "local-w"
    seed_count = 50
    extra_count = 200
    for i in range(seed_count):
        spool_inbound(nick, "inbound_dm", sender="boss", text=f"seed-{i}")

    drain_done = threading.Event()
    writer_done = threading.Event()
    drain_result: list[dict] = []
    writer_errors: list[BaseException] = []

    def _writer() -> None:
        try:
            for i in range(extra_count):
                spool_inbound(nick, "inbound_dm", sender="peer", text=f"race-{i}")
        except BaseException as exc:  # noqa: BLE001
            writer_errors.append(exc)
        finally:
            writer_done.set()

    def _drainer() -> None:
        try:
            drain_result.extend(drain_inbox(nick))
        finally:
            drain_done.set()

    writer = threading.Thread(target=_writer)
    drainer = threading.Thread(target=_drainer)
    writer.start()
    drainer.start()
    writer.join(timeout=10.0)
    drainer.join(timeout=10.0)
    assert not writer_errors, f"writer raised: {writer_errors[0]!r}"
    assert drain_done.is_set() and writer_done.is_set()

    # Whatever the drain saw + whatever's still on disk == every event
    # that was ever spooled. No loss.
    leftover = drain_inbox(nick)
    seed_texts = {f"seed-{i}" for i in range(seed_count)}
    race_texts = {f"race-{i}" for i in range(extra_count)}
    all_texts = {e["text"] for e in drain_result} | {e["text"] for e in leftover}
    assert (
        seed_texts | race_texts == all_texts
    ), f"lost events: {(seed_texts | race_texts) - all_texts}"

    # And the final state must be a clean truncate — no torn JSON.
    path = inbox_path(nick)
    import os as _os

    if _os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    # Anything left must round-trip — no partial writes.
                    json.loads(line)


def test_malformed_line_skipped_not_aborting(home):
    """A torn write from a crashed prior process must not poison the drain."""
    path = inbox_path("local-w")
    import os as _os

    _os.makedirs(_os.path.dirname(path), mode=0o700, exist_ok=True)
    # Write a malformed line BETWEEN two valid ones.
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "inbound_dm", "text": "good-1"}) + "\n")
        fh.write("{not json\n")
        fh.write(json.dumps({"kind": "inbound_dm", "text": "good-2"}) + "\n")
    entries = drain_inbox("local-w")
    assert [e["text"] for e in entries] == ["good-1", "good-2"]
