from opentelemetry import trace as otel_trace

from culture.protocol.message import Message
from culture.telemetry import context_from_traceparent, current_traceparent
from culture.telemetry.context import (
    TRACEPARENT_TAG,
    TRACESTATE_TAG,
    ExtractResult,
    extract_traceparent_from_tags,
    inject_traceparent,
)

VALID_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_extract_absent_returns_missing():
    msg = Message(command="PRIVMSG", params=["#c", "hi"])
    result = extract_traceparent_from_tags(msg, peer=None)
    assert result.status == "missing"
    assert result.traceparent is None
    assert result.tracestate is None


def test_extract_valid_passes_through():
    msg = Message(
        tags={TRACEPARENT_TAG: VALID_TP, TRACESTATE_TAG: "vendor=abc"},
        command="PRIVMSG",
        params=["#c", "hi"],
    )
    result = extract_traceparent_from_tags(msg, peer="thor")
    assert result.status == "valid"
    assert result.traceparent == VALID_TP
    assert result.tracestate == "vendor=abc"
    assert result.peer == "thor"


def test_extract_malformed_traceparent_is_dropped():
    msg = Message(tags={TRACEPARENT_TAG: "not-a-traceparent"}, command="PRIVMSG")
    result = extract_traceparent_from_tags(msg, peer="thor")
    assert result.status == "malformed"
    assert result.traceparent is None


def test_extract_wrong_length_traceparent_is_dropped():
    # Valid hex, but wrong length (trace-id is 30 hex instead of 32)
    bad = "00-4bf92f3577b34da6a3ce929d0e0e47-00f067aa0ba902b7-01"
    msg = Message(tags={TRACEPARENT_TAG: bad}, command="PRIVMSG")
    result = extract_traceparent_from_tags(msg, peer=None)
    assert result.status == "malformed"


def test_extract_oversize_traceparent_returns_too_long():
    # Valid prefix but padded past the 55-char W3C length.
    oversize_tp = VALID_TP + "extra"
    msg = Message(tags={TRACEPARENT_TAG: oversize_tp}, command="PRIVMSG")
    result = extract_traceparent_from_tags(msg, peer="thor")
    assert result.status == "too_long"
    assert result.traceparent is None
    assert result.peer == "thor"


def test_inject_none_tracestate_clears_stale_tag():
    msg = Message(tags={TRACESTATE_TAG: "stale=leftover"}, command="PRIVMSG", params=["#c", "hi"])
    inject_traceparent(msg, traceparent=VALID_TP, tracestate=None)
    assert msg.tags[TRACEPARENT_TAG] == VALID_TP
    assert TRACESTATE_TAG not in msg.tags


def test_extract_oversize_tracestate_is_dropped_tp_retained():
    oversize = "x=" + ("y" * 520)
    msg = Message(
        tags={TRACEPARENT_TAG: VALID_TP, TRACESTATE_TAG: oversize},
        command="PRIVMSG",
    )
    result = extract_traceparent_from_tags(msg, peer=None)
    assert result.status == "valid"
    assert result.traceparent == VALID_TP
    assert result.tracestate is None  # dropped for length


def test_inject_roundtrip():
    msg = Message(command="PRIVMSG", params=["#c", "hi"])
    inject_traceparent(msg, traceparent=VALID_TP, tracestate="vendor=abc")
    assert msg.tags[TRACEPARENT_TAG] == VALID_TP
    assert msg.tags[TRACESTATE_TAG] == "vendor=abc"

    result = extract_traceparent_from_tags(msg, peer=None)
    assert result.status == "valid"
    assert result.traceparent == VALID_TP
    assert result.tracestate == "vendor=abc"


def test_inject_none_tracestate_does_not_set_tag():
    msg = Message(command="PRIVMSG", params=["#c", "hi"])
    inject_traceparent(msg, traceparent=VALID_TP, tracestate=None)
    assert TRACEPARENT_TAG in msg.tags
    assert TRACESTATE_TAG not in msg.tags


def test_wire_roundtrip_through_parse_format():
    msg = Message(command="PRIVMSG", params=["#c", "hi"])
    inject_traceparent(msg, traceparent=VALID_TP, tracestate="vendor=abc")
    wire = msg.format()
    reparsed = Message.parse(wire)
    result = extract_traceparent_from_tags(reparsed, peer="alpha")
    assert result.status == "valid"
    assert result.traceparent == VALID_TP
    assert result.tracestate == "vendor=abc"


def test_current_traceparent_no_active_span_returns_none(tracing_exporter):
    # No span started -> returns None even with provider installed.
    assert current_traceparent() is None


def test_current_traceparent_returns_w3c_string_for_active_span(tracing_exporter):
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("smoke"):
        tp = current_traceparent()
    assert tp is not None
    parts = tp.split("-")
    assert parts[0] == "00"
    assert len(parts[1]) == 32  # trace-id
    assert len(parts[2]) == 16  # parent-id
    assert len(parts[3]) == 2  # flags


def test_context_from_traceparent_parents_child_span(tracing_exporter):
    parent_ctx = context_from_traceparent(VALID_TP)
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("child", context=parent_ctx) as child:
        child_ctx = child.get_span_context()
    # Child span shares the trace-id from the synthesized parent.
    assert format(child_ctx.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"
    # And is_remote on the parent flowed through (the child is a local span,
    # so we can't assert is_remote on the child — but matching trace_id is
    # the load-bearing assertion: the parent context was honored).


def test_current_traceparent_roundtrip_through_context_from_traceparent(tracing_exporter):
    parent_ctx = context_from_traceparent(VALID_TP)
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("child", context=parent_ctx):
        out = current_traceparent()
    assert out is not None
    # Trace-id preserved across the synthesized-parent → child handoff.
    assert out.split("-")[1] == "4bf92f3577b34da6a3ce929d0e0e4736"
