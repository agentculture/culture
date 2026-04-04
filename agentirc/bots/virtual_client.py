"""Virtual IRC client for bot presence in channels."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agentirc.protocol.message import Message
from agentirc.server.skill import Event, EventType

if TYPE_CHECKING:
    from agentirc.server.channel import Channel
    from agentirc.server.ircd import IRCd

logger = logging.getLogger(__name__)


class VirtualClient:
    """A bot's IRC presence — appears in channels but has no TCP connection.

    Duck-types the same interface as Client/RemoteClient so it works
    in channel.members, NAMES, WHO, and WHOIS transparently.
    """

    def __init__(self, nick: str, user: str, server: IRCd):
        self.nick = nick
        self.user = user
        self.host = "bot"
        self.realname = f"Bot {nick}"
        self.server = server
        self.server_name = server.config.name
        self.channels: set[Channel] = set()
        self.tags: list[str] = ["bot"]

    @property
    def prefix(self) -> str:
        return f"{self.nick}!{self.user}@{self.host}"

    async def send(self, message: Message) -> None:
        """No-op — bots don't receive messages from others."""

    async def join_channel(self, channel_name: str) -> None:
        """Join a channel, notify members, and emit events."""
        channel = self.server.get_or_create_channel(channel_name)
        if self in channel.members:
            return

        channel.members.add(self)
        self.channels.add(channel)

        # Ensure bot is never auto-promoted to operator
        channel.operators.discard(self)

        join_msg = Message(
            prefix=self.prefix,
            command="JOIN",
            params=[channel_name],
        )
        for member in list(channel.members):
            if member is not self:
                await member.send(join_msg)

        await self.server.emit_event(
            Event(type=EventType.JOIN, channel=channel_name, nick=self.nick)
        )

    async def part_channel(self, channel_name: str) -> None:
        """Leave a channel, notify members, and emit events."""
        channel = self.server.channels.get(channel_name)
        if not channel or self not in channel.members:
            return

        part_msg = Message(
            prefix=self.prefix,
            command="PART",
            params=[channel_name],
        )
        for member in list(channel.members):
            if member is not self:
                await member.send(part_msg)

        await self.server.emit_event(
            Event(
                type=EventType.PART,
                channel=channel_name,
                nick=self.nick,
                data={"reason": "bot stopped"},
            )
        )

        channel.members.discard(self)
        self.channels.discard(channel)
        channel.operators.discard(self)

        if not channel.members and not channel.persistent:
            del self.server.channels[channel_name]

    async def send_to_channel(self, channel_name: str, text: str) -> None:
        """Post a PRIVMSG to a channel as this bot."""
        channel = self.server.channels.get(channel_name)
        if not channel or self not in channel.members:
            logger.warning("Bot %s not in channel %s", self.nick, channel_name)
            return

        relay = Message(
            prefix=self.prefix,
            command="PRIVMSG",
            params=[channel_name, text],
        )
        for member in list(channel.members):
            if member is not self:
                await member.send(relay)

        await self.server.emit_event(
            Event(
                type=EventType.MESSAGE,
                channel=channel_name,
                nick=self.nick,
                data={"text": text},
            )
        )

        # Trigger @mention notifications for mentioned agents
        await self._notify_mentions(channel_name, text)

    async def send_dm(self, target_nick: str, text: str) -> None:
        """Send a direct PRIVMSG to a specific user."""
        from agentirc.server.remote_client import RemoteClient

        recipient = self.server.get_client(target_nick)
        if not recipient:
            logger.warning("Bot %s: DM target %s not found", self.nick, target_nick)
            return

        relay = Message(
            prefix=self.prefix,
            command="PRIVMSG",
            params=[target_nick, text],
        )
        if isinstance(recipient, RemoteClient):
            await recipient.link.send_raw(
                f":{self.server.config.name} SMSG {target_nick} {self.nick} :{text}"
            )
        else:
            await recipient.send(relay)

    async def _notify_mentions(
        self,
        channel_name: str,
        text: str,
    ) -> None:
        """Send NOTICE to any @mentioned users in the text."""
        import re

        from agentirc.server.remote_client import RemoteClient

        mentioned_nicks = re.findall(r"@(\S+)", text)
        if not mentioned_nicks:
            return
        channel = self.server.channels.get(channel_name)
        seen: set[str] = set()
        for raw_nick in mentioned_nicks:
            nick = raw_nick.rstrip(".,;:!?")
            if nick in seen or nick == self.nick:
                continue
            seen.add(nick)
            target_client = self.server.get_client(nick)
            if not target_client:
                continue
            if channel and target_client not in channel.members:
                continue
            notice = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[
                    nick,
                    f"{self.nick} mentioned you in {channel_name}: {text}",
                ],
            )
            if isinstance(target_client, RemoteClient):
                await target_client.link.send_raw(
                    f":{self.server.config.name} SNOTICE {nick} {self.server.config.name} "
                    f":{self.nick} mentioned you in {channel_name}: {text}"
                )
            else:
                await target_client.send(notice)
