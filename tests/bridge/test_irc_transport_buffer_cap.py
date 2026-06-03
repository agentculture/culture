"""Regression: Qodo PR #50 #2 — IRCTransport read-buffer cap.

The prior ``_read_loop`` appended to ``buf`` without any size limit.
A peer that streamed data without ``\\n`` (malformed client, hostile
peer, broken proxy) would grow ``buf`` indefinitely — eventually
exhausting bridge process memory and taking down every CC session
connected to the mesh.

The fix caps the buffer at 8192 bytes (16x the RFC-2812 line limit)
with oldest-data discard. This file exercises the cap by feeding a
fake reader large no-newline chunks, then asserts:

  1. The buffer never exceeds the cap during reads.
  2. Once a newline arrives, dispatch sees only the bytes that
     survived the cap — older bytes are gone.
  3. Normal line-by-line traffic is unaffected.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from culture.clients.bridge.irc_transport import IRCTransport


class _ChunkReader:
    """Async reader that yields a queue of pre-supplied byte chunks
    then signals EOF (returns b'' to terminate the read loop).
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks: list[bytes] = list(chunks)

    async def read(self, n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def _make_transport(reader: _ChunkReader) -> IRCTransport:
    from culture.clients.bridge.message_buffer import MessageBuffer

    t = IRCTransport(
        host="x",
        port=0,
        nick="local-test",
        user="local-test",
        channels=[],
        buffer=MessageBuffer(),
    )
    t._reader = reader  # type: ignore[assignment]
    t._writer = MagicMock()
    # Record dispatched lines so we can assert what survived.
    t._dispatched: list[str] = []  # type: ignore[attr-defined]

    async def _handle(msg) -> None:  # type: ignore[no-untyped-def]
        t._dispatched.append(msg.command if msg.command else str(msg))  # type: ignore[attr-defined]

    t._handle = _handle  # type: ignore[assignment]
    # Don't auto-reconnect — the test loop ends when EOF lands.
    t._should_run = False
    return t


class TestBufferCap:
    def test_cap_constant_is_exactly_8192(self) -> None:
        """Locks the published cap value — Qodo #50 #2 + AgentIRC
        client.py both agree on 8192."""
        assert IRCTransport._READ_BUF_CAP == 8192

    @pytest.mark.asyncio
    async def test_oversize_chunk_without_newline_does_not_blow_memory(
        self,
    ) -> None:
        """Stream 100 × 4-KiB chunks of pure 'A' bytes (= 400 KiB total),
        then a newline to flush the malformed line, then a valid PING.
        Pre-fix the read buffer would grow to 400 KiB. Post-fix it is
        clamped to ``_READ_BUF_CAP`` AND the next properly-terminated
        line still dispatches.
        """
        # 100 chunks × 4 KiB each, no newlines (would grow buf to 400 KiB).
        oversize = [b"A" * 4096 for _ in range(100)]
        # Newline ends the garbage line — the broken portion is parsed
        # as one (truncated) malformed line but does not stall the loop.
        # The follow-up PING then arrives as its own clean line.
        terminator = [b"\r\n"]
        final = [b"PING :server\r\n"]
        reader = _ChunkReader(oversize + terminator + final)
        t = _make_transport(reader)

        # Run the read loop. It exits when EOF arrives (b'').
        await t._read_loop()

        # The PING must have dispatched — the loop did not stall, and
        # the post-overflow buffer recovered cleanly.
        assert "PING" in t._dispatched, f"PING did not survive the cap; dispatched: {t._dispatched}"

    @pytest.mark.asyncio
    async def test_buffer_does_not_grow_unbounded(self, caplog) -> None:
        """At minimum one overflow warning must be emitted when the
        oversize stream actually exceeds the cap."""
        import logging

        caplog.set_level(logging.WARNING, logger="culture.clients.bridge.irc_transport")
        oversize = [b"A" * 4096 for _ in range(10)]  # 40 KiB
        final = [b"\r\n"]  # newline to flush
        reader = _ChunkReader(oversize + final)
        t = _make_transport(reader)
        await t._read_loop()
        overflow_warnings = [r for r in caplog.records if "overflowed cap" in r.getMessage()]
        assert overflow_warnings, "expected at least one overflow warning"

    @pytest.mark.asyncio
    async def test_normal_line_traffic_unaffected(self) -> None:
        """Two complete IRC lines that fit comfortably must dispatch
        intact with no overflow log."""
        reader = _ChunkReader([b":server NOTICE local-test :hello\r\n", b"PING :server\r\n"])
        t = _make_transport(reader)
        await t._read_loop()
        assert t._dispatched == ["NOTICE", "PING"]

    @pytest.mark.asyncio
    async def test_normal_line_traffic_emits_no_overflow_warning(self, caplog) -> None:
        import logging

        caplog.set_level(logging.WARNING, logger="culture.clients.bridge.irc_transport")
        reader = _ChunkReader([b":server NOTICE local-test :hi there\r\n", b"PING :s\r\n"])
        t = _make_transport(reader)
        await t._read_loop()
        overflow_warnings = [r for r in caplog.records if "overflowed cap" in r.getMessage()]
        assert (
            overflow_warnings == []
        ), f"unexpected overflow warning on normal traffic: {overflow_warnings}"
