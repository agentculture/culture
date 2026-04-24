"""Shared test doubles for client-side telemetry unit tests.

Integration coverage (real TCP to a real IRCd) lives in
`tests/telemetry/test_privmsg_span.py`. The fake writer below is for
targeted byte-level assertions on `Client.send` / `Client.send_raw`
injection behavior, where a full server round-trip would add cost
without extra signal.
"""

from __future__ import annotations


class FakeWriter:
    """Minimal asyncio StreamWriter stand-in for client-side unit tests."""

    def __init__(self) -> None:
        self.buf: list[bytes] = []

    def get_extra_info(self, key: str, default=None):
        return ("testaddr", 12345) if key == "peername" else default

    def write(self, data: bytes) -> None:
        self.buf.append(data)

    async def drain(self) -> None:
        pass
