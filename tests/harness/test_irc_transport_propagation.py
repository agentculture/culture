"""Tests for traceparent inject/extract in packages/agent-harness/irc_transport.py.

Verifies:
- Outbound lines carry @culture.dev/traceparent= when a span is active.
- Outbound lines are plain (no tag) when no span is active or no tracer.
- Inbound lines with valid traceparent open a child span.
- Inbound lines with missing traceparent open a root span with origin=local.
- Inbound lines with malformed traceparent open a root span with
  origin=remote and dropped_reason=malformed.
- Without a tracer, no spans are emitted.
- _do_connect wraps the connection in harness.irc.connect span with attrs.

Uses a captured-write StreamWriter stub and a queue-based StreamReader stub so
no real sockets are needed.  All tests are async (pytest-asyncio).
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch the BACKEND placeholder import before importing irc_transport.
# The reference module contains ``from culture.clients.BACKEND.message_buffer
# import MessageBuffer`` — we inject a mock module so the import succeeds.
# ---------------------------------------------------------------------------

# Build a minimal fake MessageBuffer to satisfy the import.
_fake_mb_module = types.ModuleType("culture.clients.BACKEND.message_buffer")


class _FakeMessageBuffer:
    """Stub MessageBuffer for testing — discards all writes."""

    def __init__(self, *args, **kwargs):
        """Stub for SDK type."""

    def add(self, *args, **kwargs):
        """Stub for SDK type."""


_fake_mb_module.MessageBuffer = _FakeMessageBuffer  # type: ignore[attr-defined]

# Inject into sys.modules BEFORE importing irc_transport.
sys.modules.setdefault("culture.clients.BACKEND", types.ModuleType("culture.clients.BACKEND"))
sys.modules.setdefault(
    "culture.clients.BACKEND.message_buffer",
    _fake_mb_module,
)

# Now import irc_transport from the harness reference.
# conftest.py has already inserted packages/agent-harness into sys.path.
# pylint: disable=import-error,wrong-import-position
from irc_transport import IRCTransport  # noqa: E402

from culture.protocol.message import Message  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class _CaptureWriter:
    """Minimal StreamWriter stub that captures written bytes."""

    def __init__(self):
        self.written: list[bytes] = []
        self._closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        """Stub for SDK type."""

    def close(self) -> None:
        self._closed = True

    async def wait_closed(self) -> None:
        """Stub for SDK type."""

    def get_extra_info(self, key: str, default=None):
        return default


class _QueueReader:
    """Minimal StreamReader stub backed by an asyncio.Queue."""

    def __init__(self):
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._eof = False

    def feed(self, data: bytes) -> None:
        self._queue.put_nowait(data)

    def feed_eof(self) -> None:
        self._queue.put_nowait(b"")

    async def read(self, n: int = -1) -> bytes:
        if self._eof:
            return b""
        chunk = await self._queue.get()
        if chunk == b"":
            self._eof = True
        return chunk


def _make_transport(tracer=None):
    """Build a minimal IRCTransport for testing; injects stub reader/writer."""
    buf = _FakeMessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=6667,
        nick="test-agent",
        user="test-agent",
        channels=["#test"],
        buffer=buf,
        tracer=tracer,
        backend="test",
    )
    return transport


def _inject_rw(transport):
    """Replace transport's reader/writer with capture stubs and return them."""
    reader = _QueueReader()
    writer = _CaptureWriter()
    transport._reader = reader
    transport._writer = writer
    return reader, writer


def _captured_lines(writer: _CaptureWriter) -> list[str]:
    """Decode all written bytes as lines (split on \\r\\n, strip trailing empties)."""
    raw = b"".join(writer.written).decode("utf-8")
    return [line for line in raw.split("\r\n") if line]


# ---------------------------------------------------------------------------
# test_outbound_carries_traceparent_when_span_active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_carries_traceparent_when_span_active(harness_tracing_exporter):
    _exporter, provider = harness_tracing_exporter
    tracer = provider.get_tracer("test")
    transport = _make_transport(tracer=tracer)
    _reader, writer = _inject_rw(transport)

    with tracer.start_as_current_span("outer-span"):
        await transport.send_privmsg("#room", "hello")

    lines = _captured_lines(writer)
    assert lines, "expected at least one line written"
    # The first (and only) PRIVMSG line must carry a traceparent tag prefix.
    privmsg_lines = [l for l in lines if "PRIVMSG" in l]
    assert privmsg_lines, "no PRIVMSG line found"
    line = privmsg_lines[0]
    assert line.startswith(
        "@culture.dev/traceparent=00-"
    ), f"Expected @culture.dev/traceparent prefix; got: {line!r}"
    assert "PRIVMSG #room :hello" in line, f"Expected PRIVMSG body; got: {line!r}"


# ---------------------------------------------------------------------------
# test_outbound_no_tag_when_no_span
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_no_tag_when_no_span(harness_tracing_exporter):
    _exporter, provider = harness_tracing_exporter
    tracer = provider.get_tracer("test")
    transport = _make_transport(tracer=tracer)
    _reader, writer = _inject_rw(transport)

    # No active span — current_traceparent() returns None.
    await transport.send_privmsg("#room", "hello")

    lines = _captured_lines(writer)
    privmsg_lines = [l for l in lines if "PRIVMSG" in l]
    assert privmsg_lines, "no PRIVMSG line found"
    line = privmsg_lines[0]
    assert not line.startswith("@"), f"Expected plain PRIVMSG; got: {line!r}"
    assert line == "PRIVMSG #room :hello", f"Unexpected line: {line!r}"


# ---------------------------------------------------------------------------
# test_inbound_with_valid_traceparent_starts_child_span
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_with_valid_traceparent_starts_child_span(harness_tracing_exporter):
    exporter, provider = harness_tracing_exporter
    tracer = provider.get_tracer("test")
    transport = _make_transport(tracer=tracer)
    reader, _writer = _inject_rw(transport)

    inbound = f"@culture.dev/traceparent={VALID_TRACEPARENT} " ":alice!a@h PRIVMSG #room :hi\r\n"
    reader.feed(inbound.encode())
    reader.feed_eof()

    # Drain the read loop.
    read_task = asyncio.create_task(transport._read_loop())
    try:
        await asyncio.wait_for(read_task, timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        read_task.cancel()
        await asyncio.gather(read_task, return_exceptions=True)

    spans = exporter.get_finished_spans()
    handle_spans = [s for s in spans if s.name == "harness.irc.message.handle"]
    assert (
        handle_spans
    ), f"No harness.irc.message.handle span found; spans: {[s.name for s in spans]}"

    span = handle_spans[0]
    # The span's parent trace_id must match the traceparent's trace-id.
    expected_trace_id = int(VALID_TRACEPARENT.split("-")[1], 16)
    assert (
        span.context.trace_id == expected_trace_id
    ), f"Expected trace_id={expected_trace_id:#034x}, got {span.context.trace_id:#034x}"


# ---------------------------------------------------------------------------
# test_inbound_missing_traceparent_starts_root_span
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_missing_traceparent_starts_root_span(harness_tracing_exporter):
    exporter, provider = harness_tracing_exporter
    tracer = provider.get_tracer("test")
    transport = _make_transport(tracer=tracer)
    reader, _writer = _inject_rw(transport)

    inbound = ":alice!a@h PRIVMSG #room :hi\r\n"
    reader.feed(inbound.encode())
    reader.feed_eof()

    read_task = asyncio.create_task(transport._read_loop())
    try:
        await asyncio.wait_for(read_task, timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        read_task.cancel()
        await asyncio.gather(read_task, return_exceptions=True)

    spans = exporter.get_finished_spans()
    handle_spans = [s for s in spans if s.name == "harness.irc.message.handle"]
    assert (
        handle_spans
    ), f"No harness.irc.message.handle span found; spans: {[s.name for s in spans]}"

    span = handle_spans[0]
    attrs = dict(span.attributes)
    assert attrs.get("culture.trace.origin") == "local", f"Expected origin=local; got attrs={attrs}"
    assert (
        "culture.trace.dropped_reason" not in attrs
    ), f"dropped_reason must not be present on missing traceparent; attrs={attrs}"


# ---------------------------------------------------------------------------
# test_inbound_malformed_traceparent_dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_malformed_traceparent_dropped(harness_tracing_exporter):
    exporter, provider = harness_tracing_exporter
    tracer = provider.get_tracer("test")
    transport = _make_transport(tracer=tracer)
    reader, _writer = _inject_rw(transport)

    inbound = "@culture.dev/traceparent=BAD :alice!a@h PRIVMSG #room :hi\r\n"
    reader.feed(inbound.encode())
    reader.feed_eof()

    read_task = asyncio.create_task(transport._read_loop())
    try:
        await asyncio.wait_for(read_task, timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        read_task.cancel()
        await asyncio.gather(read_task, return_exceptions=True)

    spans = exporter.get_finished_spans()
    handle_spans = [s for s in spans if s.name == "harness.irc.message.handle"]
    assert (
        handle_spans
    ), f"No harness.irc.message.handle span found; spans: {[s.name for s in spans]}"

    span = handle_spans[0]
    attrs = dict(span.attributes)
    assert (
        attrs.get("culture.trace.origin") == "remote"
    ), f"Expected origin=remote for malformed traceparent; got attrs={attrs}"
    assert (
        attrs.get("culture.trace.dropped_reason") == "malformed"
    ), f"Expected dropped_reason=malformed; got attrs={attrs}"


# ---------------------------------------------------------------------------
# test_inbound_too_long_traceparent_dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_too_long_traceparent_dropped(harness_tracing_exporter):
    exporter, provider = harness_tracing_exporter
    tracer = provider.get_tracer("test")
    transport = _make_transport(tracer=tracer)
    reader, _writer = _inject_rw(transport)

    # A traceparent longer than 55 chars triggers the too_long path.
    too_long_tp = VALID_TRACEPARENT + "extra-garbage-that-makes-it-too-long"
    inbound = f"@culture.dev/traceparent={too_long_tp} :alice!a@h PRIVMSG #room :hi\r\n"
    reader.feed(inbound.encode())
    reader.feed_eof()

    read_task = asyncio.create_task(transport._read_loop())
    try:
        await asyncio.wait_for(read_task, timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        read_task.cancel()
        await asyncio.gather(read_task, return_exceptions=True)

    spans = exporter.get_finished_spans()
    handle_spans = [s for s in spans if s.name == "harness.irc.message.handle"]
    assert (
        handle_spans
    ), f"No harness.irc.message.handle span found; spans: {[s.name for s in spans]}"

    span = handle_spans[0]
    attrs = dict(span.attributes)
    assert (
        attrs.get("culture.trace.origin") == "remote"
    ), f"Expected origin=remote for too_long traceparent; got attrs={attrs}"
    assert (
        attrs.get("culture.trace.dropped_reason") == "too_long"
    ), f"Expected dropped_reason=too_long; got attrs={attrs}"


# ---------------------------------------------------------------------------
# test_no_tracer_means_no_spans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tracer_means_no_spans(harness_tracing_exporter):
    exporter, _provider = harness_tracing_exporter
    # Build transport WITHOUT a tracer.
    transport = _make_transport(tracer=None)
    reader, _writer = _inject_rw(transport)

    inbound = ":alice!a@h PRIVMSG #room :hi\r\n"
    reader.feed(inbound.encode())
    reader.feed_eof()

    read_task = asyncio.create_task(transport._read_loop())
    try:
        await asyncio.wait_for(read_task, timeout=1.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        read_task.cancel()
        await asyncio.gather(read_task, return_exceptions=True)

    await transport.send_privmsg("#room", "hello")

    spans = exporter.get_finished_spans()
    harness_spans = [s for s in spans if s.name.startswith("harness.")]
    assert (
        harness_spans == []
    ), f"Expected zero harness spans with no tracer; got: {[s.name for s in harness_spans]}"


# ---------------------------------------------------------------------------
# test_connect_span_wraps_do_connect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_span_wraps_do_connect(harness_tracing_exporter):
    exporter, provider = harness_tracing_exporter
    tracer = provider.get_tracer("test")
    transport = _make_transport(tracer=tracer)

    # Patch asyncio.open_connection so _do_connect doesn't need a real server.
    reader = _QueueReader()
    writer = _CaptureWriter()
    # Feed the EOF so the read_loop exits immediately.
    reader.feed_eof()

    with patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))):
        # _do_connect calls create_task(_read_loop()) — we cancel that task after.
        await transport._do_connect()

    # Cancel the read_loop task to avoid dangling coroutine.
    if transport._read_task is not None:
        transport._read_task.cancel()
        await asyncio.gather(transport._read_task, return_exceptions=True)

    spans = exporter.get_finished_spans()
    connect_spans = [s for s in spans if s.name == "harness.irc.connect"]
    assert connect_spans, f"Expected harness.irc.connect span; spans: {[s.name for s in spans]}"

    span = connect_spans[0]
    attrs = dict(span.attributes)
    assert attrs.get("harness.backend") == "test", f"Missing harness.backend; attrs={attrs}"
    assert attrs.get("harness.nick") == "test-agent", f"Missing harness.nick; attrs={attrs}"
    assert attrs.get("harness.server") == "127.0.0.1:6667", f"Missing harness.server; attrs={attrs}"


# ---------------------------------------------------------------------------
# test_send_raw_direct_carries_traceparent
# Regression test for the send_raw injection bypass bug: callers that use
# send_raw() directly (not via _send_raw) must also get the traceparent tag.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_raw_direct_carries_traceparent(harness_tracing_exporter):
    """send_raw() called directly must inject traceparent when a span is active.

    This is the regression test for the bug where injection lived only in
    _send_raw() — callers that bypassed it (e.g. daemon HISTORY commands)
    silently lost trace context.
    """
    _exporter, provider = harness_tracing_exporter
    tracer = provider.get_tracer("test")
    transport = _make_transport(tracer=tracer)
    _reader, writer = _inject_rw(transport)

    with tracer.start_as_current_span("outer-span"):
        # Call send_raw directly — NOT via _send_raw or send_privmsg.
        await transport.send_raw("PRIVMSG #x :hi")

    lines = _captured_lines(writer)
    assert lines, "expected at least one line written"
    line = lines[0]
    assert line.startswith(
        "@culture.dev/traceparent=00-"
    ), f"send_raw() direct call must inject traceparent; got: {line!r}"
    assert "PRIVMSG #x :hi" in line, f"Expected PRIVMSG body preserved; got: {line!r}"


# ---------------------------------------------------------------------------
# Attention callbacks (#345): on_ambient / on_outgoing
# ---------------------------------------------------------------------------


def _priv(sender: str, target: str, text: str) -> Message:
    """Build a synthetic PRIVMSG Message for the privmsg handler tests."""
    return Message(
        prefix=f"{sender}!~{sender}@host",
        command="PRIVMSG",
        params=[target, text],
    )


def test_on_ambient_fires_for_non_mention_privmsg():
    received: list[tuple[str, str, str]] = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_ambient = lambda t, s, x: received.append((t, s, x))
    transport.on_mention = lambda t, s, x: None

    transport._on_privmsg(_priv("alice", "#dev", "hello world"))
    assert received == [("#dev", "alice", "hello world")]


def test_on_ambient_does_not_fire_for_mentions():
    received_ambient: list = []
    received_mention: list = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_ambient = lambda t, s, x: received_ambient.append((t, s, x))
    transport.on_mention = lambda t, s, x: received_mention.append((t, s, x))

    transport._on_privmsg(_priv("alice", "#dev", "hello @thor-claude"))
    assert received_ambient == []
    assert received_mention == [("#dev", "alice", "hello @thor-claude")]


def test_on_ambient_does_not_fire_for_dm():
    """A DM is always direct (target == own nick)."""
    received_ambient: list = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_ambient = lambda t, s, x: received_ambient.append((t, s, x))
    transport.on_mention = lambda *_: None

    transport._on_privmsg(_priv("alice", "thor-claude", "psst"))
    assert received_ambient == []


def test_on_ambient_skips_system_messages():
    received_ambient: list = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_ambient = lambda t, s, x: received_ambient.append((t, s, x))

    transport._on_privmsg(_priv("system-thor", "#dev", "user.join alice"))
    assert received_ambient == []


@pytest.mark.asyncio
async def test_on_outgoing_fires_after_send():
    received: list = []
    transport = _make_transport()
    transport.nick = "thor-claude"
    transport.on_outgoing = lambda t, line: received.append((t, line))
    _inject_rw(transport)  # capture writer so send_privmsg can complete

    await transport.send_privmsg("#dev", "hi")
    assert received == [("#dev", "hi")]
