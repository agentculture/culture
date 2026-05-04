"""Culture's IRC transport — TCP client + remote-server-link client.

Moved here in culture 9.0.0 (Phase A3 of the agentirc extraction).
Before A3 these lived under `culture/agentirc/` inside the bundled IRCd
fork; once that fork was deleted, the transport classes needed a
culture-owned home that wasn't going away. They were never IRCd code in
spirit — culture just bundled them with the IRCd because everything
shipped together.
"""

from __future__ import annotations

from culture.transport.client import Client
from culture.transport.remote_client import RemoteClient

__all__ = ["Client", "RemoteClient"]
