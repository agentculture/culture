# server/remote_client.py
from __future__ import annotations

from typing import TYPE_CHECKING

from culture.protocol.message import Message

if TYPE_CHECKING:
    from agentirc.channel import Channel
    from agentirc.server_link import ServerLink


class RemoteClient:
    """Ghost of a client connected to a peer server.

    Lives in channel.members so NAMES/WHO/WHOIS work transparently.
    send() is a no-op -- relay happens at the event/link level.
    """

    def __init__(
        self,
        nick: str,
        user: str,
        host: str,
        realname: str,
        server_name: str,
        link: ServerLink,
    ):
        self.nick = nick
        self.user = user
        self.host = host
        self.realname = realname
        self.server_name = server_name
        self.link = link
        self.channels: set[Channel] = set()
        self.tags: list[str] = []

    @property
    def prefix(self) -> str:
        return f"{self.nick}!{self.user}@{self.host}"

    async def send(self, message: Message) -> None:
        pass  # No-op: relay happens through ServerLink
