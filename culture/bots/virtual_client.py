"""Culture's wrapper around agentirc's public VirtualClient.

After Phase A2 (agentirc-cli >= 9.6.0), all virtual-presence behavior —
JOIN/PART, channel broadcasts, DMs, @-mention notices, IRC-text
sanitization — lives in :class:`agentirc.virtual_client.VirtualClient`.
That class was promoted to public in agentirc 9.6.0 (agentculture/agentirc#22)
and is semver-tracked from that release forward.

This module re-exports it as ``culture.bots.virtual_client.VirtualClient``
to keep culture's existing import sites stable. The subclass exists so
culture-specific extensions can land here without forking the upstream
class — none are needed today (the Bot wrapper in ``culture.bots.bot``
already owns the culture-specific glue: BotConfig, template engine,
``fires_event`` chaining, owner DM).
"""

from __future__ import annotations

from agentirc.virtual_client import VirtualClient as _AgentircVirtualClient


class VirtualClient(_AgentircVirtualClient):
    """A culture bot's IRC presence.

    All behavior is inherited from
    :class:`agentirc.virtual_client.VirtualClient`. Subclassed so culture
    can extend without forking the upstream class.
    """


__all__ = ["VirtualClient"]
