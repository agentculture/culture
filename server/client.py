# server/client.py
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from protocol.message import Message
from protocol import replies
from server.channel import Channel

if TYPE_CHECKING:
    from server.ircd import IRCd


class Client:
    """A connected IRC client."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        server: IRCd,
    ):
        self.reader = reader
        self.writer = writer
        self.server = server
        self.nick: str | None = None
        self.user: str | None = None
        self.realname: str | None = None
        self.host: str = writer.get_extra_info("peername", ("unknown", 0))[0]
        self.channels: set[Channel] = set()
        self._registered = False

    @property
    def prefix(self) -> str:
        return f"{self.nick}!{self.user}@{self.host}"

    async def send(self, message: Message) -> None:
        try:
            self.writer.write(message.format().encode("utf-8"))
            await self.writer.drain()
        except (ConnectionError, BrokenPipeError, OSError):
            pass  # Client disconnected; cleanup happens in ircd._handle_connection

    async def send_numeric(self, code: str, *params: str) -> None:
        target = self.nick or "*"
        msg = Message(
            prefix=self.server.config.name,
            command=code,
            params=[target, *params],
        )
        await self.send(msg)

    async def handle(self) -> None:
        buffer = ""
        while True:
            data = await self.reader.read(4096)
            if not data:
                break
            buffer += data.decode("utf-8", errors="replace")
            # Cap buffer to prevent unbounded memory growth (512 bytes per RFC 2812)
            if len(buffer) > 8192:
                buffer = buffer[-4096:]
            # Normalize all line endings to \n for simpler parsing
            buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line.strip():
                    msg = Message.parse(line)
                    if msg.command:
                        await self._dispatch(msg)

    async def _dispatch(self, msg: Message) -> None:
        handler = getattr(self, f"_handle_{msg.command.lower()}", None)
        if handler:
            await handler(msg)
        else:
            await self.send_numeric(
                replies.ERR_UNKNOWNCOMMAND, msg.command, "Unknown command"
            )

    async def _handle_ping(self, msg: Message) -> None:
        token = msg.params[0] if msg.params else ""
        await self.send(
            Message(
                prefix=self.server.config.name,
                command="PONG",
                params=[self.server.config.name, token],
            )
        )

    async def _handle_pong(self, msg: Message) -> None:
        pass  # Client responding to our ping

    async def _handle_nick(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NONICKNAMEGIVEN, "No nickname given")
            return

        nick = msg.params[0]

        # Enforce server name prefix
        expected_prefix = f"{self.server.config.name}-"
        if not nick.startswith(expected_prefix):
            await self.send_numeric(
                replies.ERR_ERRONEUSNICKNAME,
                nick,
                f"Nickname must start with {expected_prefix}",
            )
            return

        if len(nick) <= len(expected_prefix):
            await self.send_numeric(
                replies.ERR_ERRONEUSNICKNAME,
                nick,
                f"Nickname must have an agent name after {expected_prefix}",
            )
            return

        if nick in self.server.clients:
            await self.send_numeric(
                replies.ERR_NICKNAMEINUSE, nick, "Nickname is already in use"
            )
            return

        old_nick = self.nick
        if old_nick and old_nick in self.server.clients:
            del self.server.clients[old_nick]

        self.nick = nick
        self.server.clients[nick] = self
        await self._try_register()

    async def _handle_user(self, msg: Message) -> None:
        if self._registered:
            await self.send_numeric(
                replies.ERR_ALREADYREGISTRED, "You may not reregister"
            )
            return
        if len(msg.params) < 4:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "USER", "Not enough parameters"
            )
            return

        self.user = msg.params[0]
        self.realname = msg.params[3]
        await self._try_register()

    async def _try_register(self) -> None:
        if self.nick and self.user and not self._registered:
            self._registered = True
            await self._send_welcome()

    async def _send_welcome(self) -> None:
        await self.send_numeric(
            replies.RPL_WELCOME,
            f"Welcome to {self.server.config.name} IRC Network {self.prefix}",
        )
        await self.send_numeric(
            replies.RPL_YOURHOST,
            f"Your host is {self.server.config.name}, running agentirc",
        )
        await self.send_numeric(
            replies.RPL_CREATED,
            "This server was created today",
        )
        await self.send_numeric(
            replies.RPL_MYINFO,
            self.server.config.name,
            "agentirc",
            "o",
            "o",
        )

    async def _handle_join(self, msg: Message) -> None:
        if not self._registered:
            return
        if not msg.params:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "JOIN", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        if not channel_name.startswith("#"):
            return

        channel = self.server.get_or_create_channel(channel_name)
        if self in channel.members:
            return

        channel.add(self)
        self.channels.add(channel)

        # Notify all channel members (including self)
        join_msg = Message(
            prefix=self.prefix, command="JOIN", params=[channel_name]
        )
        for member in list(channel.members):
            await member.send(join_msg)

        # Send topic if set
        if channel.topic:
            await self.send_numeric(
                replies.RPL_TOPIC, channel_name, channel.topic
            )

        # Send names list
        await self._send_names(channel)

    async def _handle_part(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "PART", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        reason = msg.params[1] if len(msg.params) > 1 else ""

        channel = self.server.channels.get(channel_name)
        if not channel or self not in channel.members:
            await self.send_numeric(
                replies.ERR_NOTONCHANNEL,
                channel_name,
                "You're not on that channel",
            )
            return

        part_params = [channel_name, reason] if reason else [channel_name]
        part_msg = Message(
            prefix=self.prefix, command="PART", params=part_params
        )
        for member in list(channel.members):
            await member.send(part_msg)

        channel.remove(self)
        self.channels.discard(channel)

        if not channel.members:
            del self.server.channels[channel_name]

    async def _handle_topic(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "TOPIC", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel or self not in channel.members:
            await self.send_numeric(
                replies.ERR_NOTONCHANNEL,
                channel_name,
                "You're not on that channel",
            )
            return

        if len(msg.params) == 1:
            # Query topic
            if channel.topic:
                await self.send_numeric(
                    replies.RPL_TOPIC, channel_name, channel.topic
                )
            else:
                await self.send_numeric(
                    replies.RPL_NOTOPIC, channel_name, "No topic is set"
                )
        else:
            # Set topic
            channel.topic = msg.params[1]
            topic_msg = Message(
                prefix=self.prefix,
                command="TOPIC",
                params=[channel_name, channel.topic],
            )
            for member in list(channel.members):
                await member.send(topic_msg)

    async def _handle_names(self, msg: Message) -> None:
        if not msg.params:
            return
        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if channel:
            await self._send_names(channel)

    async def _send_names(self, channel: Channel) -> None:
        nicks = " ".join(m.nick for m in channel.members)
        await self.send_numeric(
            replies.RPL_NAMREPLY, "=", channel.name, nicks
        )
        await self.send_numeric(
            replies.RPL_ENDOFNAMES, channel.name, "End of /NAMES list"
        )

    async def _handle_privmsg(self, msg: Message) -> None:
        if len(msg.params) < 2:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "PRIVMSG", "Not enough parameters"
            )
            return

        target = msg.params[0]
        text = msg.params[1]
        relay = Message(
            prefix=self.prefix, command="PRIVMSG", params=[target, text]
        )

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if not channel:
                await self.send_numeric(
                    replies.ERR_NOSUCHCHANNEL, target, "No such channel"
                )
                return
            if self not in channel.members:
                await self.send_numeric(
                    replies.ERR_CANNOTSENDTOCHAN, target, "Cannot send to channel"
                )
                return
            for member in list(channel.members):
                if member is not self:
                    await member.send(relay)
        else:
            recipient = self.server.clients.get(target)
            if not recipient:
                await self.send_numeric(
                    replies.ERR_NOSUCHNICK, target, "No such nick"
                )
                return
            await recipient.send(relay)

    async def _handle_notice(self, msg: Message) -> None:
        # Same as PRIVMSG but no error replies per RFC 2812
        if len(msg.params) < 2:
            return

        target = msg.params[0]
        text = msg.params[1]
        relay = Message(
            prefix=self.prefix, command="NOTICE", params=[target, text]
        )

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if not channel:
                return
            if self not in channel.members:
                return
            for member in list(channel.members):
                if member is not self:
                    await member.send(relay)
        else:
            recipient = self.server.clients.get(target)
            if recipient:
                await recipient.send(relay)

    async def _handle_quit(self, msg: Message) -> None:
        reason = msg.params[0] if msg.params else "Quit"
        quit_msg = Message(
            prefix=self.prefix, command="QUIT", params=[reason]
        )

        notified: set[Client] = set()
        for channel in list(self.channels):
            for member in list(channel.members):
                if member is not self and member not in notified:
                    await member.send(quit_msg)
                    notified.add(member)

        raise ConnectionError("Client quit")
