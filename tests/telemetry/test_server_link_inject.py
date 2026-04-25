"""Wire-level injection tests for ServerLink.send_raw — the single choke point
that re-signs every outbound federation line with the current span's W3C
traceparent (per `culture/protocol/extensions/tracing.md`)."""

from __future__ import annotations

import pytest
from opentelemetry import trace as otel_trace

from culture.agentirc.server_link import ServerLink, _prepend_trace_tags
from culture.telemetry import current_traceparent
from culture.telemetry.context import TRACEPARENT_TAG

VALID_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


# --- _prepend_trace_tags helper unit tests --------------------------------


def test_prepend_to_untagged_line():
    line = ":alpha SMSG #room alpha-bob :hi"
    out = _prepend_trace_tags(line, VALID_TP)
    assert out == f"@{TRACEPARENT_TAG}={VALID_TP} {line}"


def test_prepend_merges_into_existing_tag_block():
    line = "@vendor=foo :alpha SMSG #room alpha-bob :hi"
    out = _prepend_trace_tags(line, VALID_TP)
    assert out == f"@vendor=foo;{TRACEPARENT_TAG}={VALID_TP} :alpha SMSG #room alpha-bob :hi"


def test_prepend_replaces_existing_traceparent_in_tag_block():
    stale = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-00"
    line = f"@{TRACEPARENT_TAG}={stale};vendor=x :alpha SMSG #room alpha-bob :hi"
    out = _prepend_trace_tags(line, VALID_TP)
    expected = f"@{TRACEPARENT_TAG}={VALID_TP};vendor=x :alpha SMSG #room alpha-bob :hi"
    assert out == expected


def test_prepend_empty_line_no_op():
    assert _prepend_trace_tags("", VALID_TP) == ""


def test_prepend_into_tag_only_line_with_no_space():
    # Edge case: line is entirely a tag block with no body. Defensive only —
    # never happens on the wire, but the helper must not raise.
    line = "@vendor=foo"
    out = _prepend_trace_tags(line, VALID_TP)
    assert TRACEPARENT_TAG + "=" + VALID_TP in out
    assert "vendor=foo" in out


def test_prepend_preserves_tag_value_with_equals_signs():
    # Tag values can legally contain '=' inside the value portion.
    line = "@vendor=k=v :alpha SMSG #room alpha-bob :hi"
    out = _prepend_trace_tags(line, VALID_TP)
    assert "vendor=k=v" in out
    assert TRACEPARENT_TAG + "=" + VALID_TP in out


def test_prepend_into_empty_tag_block_no_leading_semicolon():
    # @<empty> rest — must not produce '@;TP=...' (leading empty tag).
    line = "@ :alpha SMSG #room alpha-bob :hi"
    out = _prepend_trace_tags(line, VALID_TP)
    assert ";;" not in out
    assert not out.startswith("@;")
    assert TRACEPARENT_TAG + "=" + VALID_TP in out


# --- ServerLink.send_raw injection (uses ServerLink with a fake writer) ----


class _FakeWriter:
    def __init__(self):
        self.buf: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.buf.append(data)

    async def drain(self) -> None:
        """No-op: tests don't exercise asyncio writer back-pressure."""


@pytest.mark.asyncio
async def test_send_raw_injects_traceparent_when_span_active(tracing_exporter):
    writer = _FakeWriter()
    link = ServerLink(reader=None, writer=writer, server=None, password=None)
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("smoke"):
        await link.send_raw(":alpha SMSG #room alpha-bob :hi")

    assert len(writer.buf) == 1
    line = writer.buf[0].decode("utf-8").rstrip("\r\n")
    prefix, sep, body = line.partition(" ")
    assert sep == " "
    assert prefix.startswith("@")
    tags = dict(t.split("=", 1) for t in prefix[1:].split(";") if "=" in t)
    assert TRACEPARENT_TAG in tags
    # Tag value must be 55-char W3C format
    assert len(tags[TRACEPARENT_TAG]) == 55
    assert body == ":alpha SMSG #room alpha-bob :hi"


@pytest.mark.asyncio
async def test_send_raw_no_injection_when_no_span(tracing_exporter):
    writer = _FakeWriter()
    link = ServerLink(reader=None, writer=writer, server=None, password=None)
    # No span started.
    await link.send_raw(":alpha SMSG #room alpha-bob :hi")
    line = writer.buf[0].decode("utf-8").rstrip("\r\n")
    assert TRACEPARENT_TAG not in line


@pytest.mark.asyncio
async def test_send_raw_traceparent_matches_active_span(tracing_exporter):
    writer = _FakeWriter()
    link = ServerLink(reader=None, writer=writer, server=None, password=None)
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("smoke"):
        expected_tp = current_traceparent()
        await link.send_raw(":alpha SMSG #room alpha-bob :hi")

    line = writer.buf[0].decode("utf-8").rstrip("\r\n")
    assert f"{TRACEPARENT_TAG}={expected_tp}" in line
