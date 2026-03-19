# server/client.py
from __future__ import annotations

import asyncio
import re
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
            "ov",
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
        nicks = " ".join(
            f"{channel.get_prefix(m)}{m.nick}" for m in channel.members
        )
        await self.send_numeric(
            replies.RPL_NAMREPLY, "=", channel.name, nicks
        )
        await self.send_numeric(
            replies.RPL_ENDOFNAMES, channel.name, "End of /NAMES list"
        )

    async def _handle_mode(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "MODE", "Not enough parameters"
            )
            return

        target = msg.params[0]
        if target.startswith("#"):
            await self._handle_channel_mode(msg)
        else:
            await self._handle_user_mode(msg)

    async def _handle_channel_mode(self, msg: Message) -> None:
        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel:
            await self.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, "No such channel"
            )
            return

        if len(msg.params) == 1:
            await self.send_numeric(
                replies.RPL_CHANNELMODEIS, channel_name, "+"
            )
            return

        modestring = msg.params[1]
        if not channel.is_operator(self):
            await self.send_numeric(
                replies.ERR_CHANOPRIVSNEEDED,
                channel_name,
                "You're not channel operator",
            )
            return

        if len(msg.params) < 3:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "MODE", "Not enough parameters"
            )
            return

        target_nick = msg.params[2]
        target_client = self.server.clients.get(target_nick)
        if not target_client or target_client not in channel.members:
            await self.send_numeric(
                replies.ERR_USERNOTINCHANNEL,
                target_nick,
                channel_name,
                "They aren't on that channel",
            )
            return

        adding = True
        applied_modes = []
        for ch in modestring:
            if ch == "+":
                adding = True
            elif ch == "-":
                adding = False
            elif ch == "o":
                if adding:
                    channel.operators.add(target_client)
                else:
                    channel.operators.discard(target_client)
                applied_modes.append(("+" if adding else "-") + "o")
            elif ch == "v":
                if adding:
                    channel.voiced.add(target_client)
                else:
                    channel.voiced.discard(target_client)
                applied_modes.append(("+" if adding else "-") + "v")

        if applied_modes:
            mode_msg = Message(
                prefix=self.prefix,
                command="MODE",
                params=[channel_name, "".join(applied_modes), target_nick],
            )
            for member in list(channel.members):
                await member.send(mode_msg)

    async def _handle_user_mode(self, msg: Message) -> None:
        target_nick = msg.params[0]
        if target_nick != self.nick:
            await self.send_numeric(
                replies.ERR_USERSDONTMATCH,
                "Can't change mode for other users",
            )
            return
        await self.send_numeric(replies.RPL_UMODEIS, "+")

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
            await self._notify_mentions(target, text)
        else:
            recipient = self.server.clients.get(target)
            if not recipient:
                await self.send_numeric(
                    replies.ERR_NOSUCHNICK, target, "No such nick"
                )
                return
            await recipient.send(relay)
            await self._notify_mentions(None, text)

    async def _notify_mentions(
        self, channel_name: str | None, text: str
    ) -> None:
        mentioned_nicks = re.findall(r"@(\S+)", text)
        if not mentioned_nicks:
            return
        seen: set[str] = set()
        channel = (
            self.server.channels.get(channel_name) if channel_name else None
        )
        source = channel_name or "a direct message"
        for raw_nick in mentioned_nicks:
            nick = raw_nick.rstrip(".,;:!?")
            if nick in seen or nick == self.nick:
                continue
            seen.add(nick)
            target_client = self.server.clients.get(nick)
            if not target_client:
                continue
            if channel and target_client not in channel.members:
                continue
            notice = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[
                    nick,
                    f"{self.nick} mentioned you in {source}: {text}",
                ],
            )
            await target_client.send(notice)

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

    async def _handle_who(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(
                replies.RPL_ENDOFWHO, "*", "End of WHO list"
            )
            return

        target = msg.params[0]
        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if channel:
                for member in list(channel.members):
                    flags = "H"
                    if channel.is_operator(member):
                        flags += "@"
                    elif channel.is_voiced(member):
                        flags += "+"
                    await self.send_numeric(
                        replies.RPL_WHOREPLY,
                        target,
                        member.user or "*",
                        member.host,
                        self.server.config.name,
                        member.nick,
                        flags,
                        f"0 {member.realname or ''}",
                    )
            await self.send_numeric(
                replies.RPL_ENDOFWHO, target, "End of WHO list"
            )
        else:
            client = self.server.clients.get(target)
            if client:
                chan_name = "*"
                flags = "H"
                for ch in client.channels:
                    chan_name = ch.name
                    if ch.is_operator(client):
                        flags += "@"
                    elif ch.is_voiced(client):
                        flags += "+"
                    break
                await self.send_numeric(
                    replies.RPL_WHOREPLY,
                    chan_name,
                    client.user or "*",
                    client.host,
                    self.server.config.name,
                    client.nick,
                    flags,
                    f"0 {client.realname or ''}",
                )
            await self.send_numeric(
                replies.RPL_ENDOFWHO, target, "End of WHO list"
            )

    async def _handle_whois(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(
                replies.ERR_NONICKNAMEGIVEN, "No nickname given"
            )
            return

        target_nick = msg.params[0]
        target = self.server.clients.get(target_nick)
        if not target:
            await self.send_numeric(
                replies.ERR_NOSUCHNICK, target_nick, "No such nick/channel"
            )
            await self.send_numeric(
                replies.RPL_ENDOFWHOIS, target_nick, "End of WHOIS list"
            )
            return

        await self.send_numeric(
            replies.RPL_WHOISUSER,
            target.nick,
            target.user or "*",
            target.host,
            "*",
            target.realname or "",
        )
        await self.send_numeric(
            replies.RPL_WHOISSERVER,
            target.nick,
            self.server.config.name,
            "agentirc",
        )
        if target.channels:
            chan_list = " ".join(
                f"{ch.get_prefix(target)}{ch.name}" for ch in target.channels
            )
            await self.send_numeric(
                replies.RPL_WHOISCHANNELS, target.nick, chan_list
            )
        await self.send_numeric(
            replies.RPL_ENDOFWHOIS, target.nick, "End of WHOIS list"
        )

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
