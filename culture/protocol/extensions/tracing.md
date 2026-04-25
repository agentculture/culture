# Extension: W3C Trace Context over IRC

**Tags:**

- `culture.dev/traceparent` — the W3C `traceparent` header value, verbatim (55 chars, format `00-<32-hex-trace-id>-<16-hex-parent-id>-<2-hex-flags>`). MUST match the W3C regex. If malformed, servers MUST silently drop the tag (treat as absent).
- `culture.dev/tracestate` — the W3C `tracestate` header value (comma-separated `key=value` list, vendor-specific). Optional. MUST be ≤ 512 bytes after IRCv3 unescape. If longer, servers MUST drop the tag.

**Scope:** outbound on every client-originated message when a span context is active (PRIVMSG, NOTICE, JOIN, PART, KICK, MODE, SEVENT, SMSG, SNOTICE). On federation relay, the tags are re-injected from the *current* server's span context — they are not copied verbatim from the received message. This produces a parent-per-hop span tree instead of collapsing all hops into one.

**Inbound handling.** On every ingress (local client `_dispatch` and S2S `_dispatch`):

1. Tag absent → start a new root span. Attribute: `culture.trace.origin=local`.
2. Tag present and valid → start a child span linked to the extracted context. Attributes: `culture.trace.origin=remote`, `culture.federation.peer=<peer>`.
3. Tag present but malformed or over the length cap → drop the tag, start a new root span. Attributes: `culture.trace.origin=remote`, `culture.trace.dropped_reason=malformed|too_long`, `culture.federation.peer=<peer>`. Log a rate-limited warning. Increment `culture.trace.inbound{result=malformed|too_long, peer=<peer>}`.

**Length caps** (hard-coded, not configurable):

- `traceparent`: exactly 55 characters.
- `tracestate`: ≤ 512 bytes post-unescape.

**Non-goal.** Trace context is not authenticated. Federation peer trust is the authorization mechanism; tracing is observability only. An operator who cares about forged trace IDs from upstream peers should rely on peer trust, not on tag validation.

**Compat.** Additive IRCv3 tag. Peers that don't recognize the tag pass it through on relay (standard IRCv3 tag behavior). No wire version bump. Project version bumps as a minor per `CLAUDE.md`.

**Example.**

User on `spark` sends:

    @culture.dev/traceparent=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01 PRIVMSG #general :hi

Server `spark` starts span `irc.command.PRIVMSG` as a child of the extracted context, relays the event via SEVENT to `thor`, re-injecting its own span's traceparent. Server `thor` sees trace ID `4bf92f35…4736` and starts another child span — the collector stitches both spans into one trace.

**Re-sign per hop on the wire.** Concretely, the SEVENT line `spark` sends to `thor` looks like:

    @culture.dev/traceparent=00-4bf92f3577b34da6a3ce929d0e0e4736-aaaaaaaaaaaaaaaa-01 :spark SEVENT spark 42 message #general :<base64>

The trace-id (`4bf92f35…4736`) is preserved across the hop. The parent-id changes (`00f067aa…02b7` → `aaaaaaaa…aaaa`) because `spark` re-injects its own relay span's id, not the inbound parent-id verbatim. This produces a parent-per-hop span tree that mirrors the federation topology rather than collapsing every hop into one flat span.
