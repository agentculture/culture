"""W3C trace-context extraction and injection for IRCv3 message tags."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from culture.protocol.message import Message

TRACEPARENT_TAG = "culture.dev/traceparent"
TRACESTATE_TAG = "culture.dev/tracestate"

# W3C traceparent: version-traceid-parentid-flags, all lowercase hex, exact lengths.
#   version:  2 hex (must be "00" for W3C v1)
#   trace-id: 32 hex, not all zero
#   parent-id: 16 hex, not all zero
#   flags:    2 hex
_TRACEPARENT_LEN = 55
_TRACEPARENT_RE = re.compile(r"^00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$")
_TRACESTATE_MAX_BYTES = 512

ExtractStatus = Literal["missing", "valid", "malformed", "too_long"]


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of parsing trace context from a Message's IRCv3 tags.

    Status meanings:
      - missing: no traceparent tag on the message.
      - valid: traceparent passed length+regex checks; tracestate (if any
        and within length cap) is also carried.
      - malformed: traceparent was shorter than 55 chars or failed the W3C
        regex (e.g. bad hex, all-zero trace-id or parent-id).
      - too_long: traceparent was longer than 55 chars. Separated from
        `malformed` so metrics can distinguish oversize peers from broken
        peers (per `culture/protocol/extensions/tracing.md`).
    """

    status: ExtractStatus
    traceparent: str | None
    tracestate: str | None
    peer: str | None


def extract_traceparent_from_tags(msg: Message, peer: str | None) -> ExtractResult:
    """Extract W3C trace context from an incoming IRC message's tags.

    Applies the inbound mitigation rules from `culture/protocol/extensions/tracing.md`:
    absent → missing; length/regex failure → malformed; oversize tracestate
    dropped while traceparent retained.
    """
    raw_tp = msg.tags.get(TRACEPARENT_TAG)
    if raw_tp is None:
        return ExtractResult(status="missing", traceparent=None, tracestate=None, peer=peer)

    if len(raw_tp) > _TRACEPARENT_LEN:
        return ExtractResult(status="too_long", traceparent=None, tracestate=None, peer=peer)

    if len(raw_tp) != _TRACEPARENT_LEN or not _TRACEPARENT_RE.match(raw_tp):
        return ExtractResult(status="malformed", traceparent=None, tracestate=None, peer=peer)

    raw_ts = msg.tags.get(TRACESTATE_TAG)
    if raw_ts is not None and len(raw_ts.encode("utf-8")) > _TRACESTATE_MAX_BYTES:
        raw_ts = None

    return ExtractResult(status="valid", traceparent=raw_tp, tracestate=raw_ts, peer=peer)


def inject_traceparent(msg: Message, traceparent: str, tracestate: str | None) -> None:
    """Inject W3C trace context into an outgoing Message's IRCv3 tags.

    Mutates `msg.tags` in place — the message will carry these tags on wire.
    No validation: caller is expected to pass well-formed W3C values.

    When `tracestate is None`, any pre-existing `TRACESTATE_TAG` is removed
    so a reused Message does not leak a stale tracestate value.
    """
    msg.tags[TRACEPARENT_TAG] = traceparent
    if tracestate is not None:
        msg.tags[TRACESTATE_TAG] = tracestate
    else:
        msg.tags.pop(TRACESTATE_TAG, None)
