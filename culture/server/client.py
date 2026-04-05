# server/client.py
from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from culture.protocol import replies
from culture.protocol.message import Message
from culture.server.channel import Channel
from culture.server.skill import Event, EventType

if TYPE_CHECKING:
    from culture.server.ircd import IRCd


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
        self.tags: list[str] = []
        self.modes: set[str] = set()
        self.icon: str | None = None

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

    async def handle(self, initial_msg: str | None = None) -> None:
        buffer = ""
        if initial_msg:
            buffer = initial_msg
            buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line.strip():
                    msg = Message.parse(line)
                    if msg.command:
                        await self._dispatch(msg)
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
            skill = self.server.get_skill_for_command(msg.command)
            if skill and self._registered:
                try:
                    await skill.on_command(self, msg)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Skill %s failed on command %s", skill.name, msg.command
                    )
            else:
                await self.send_numeric(replies.ERR_UNKNOWNCOMMAND, msg.command, "Unknown command")

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
            await self.send_numeric(replies.ERR_NICKNAMEINUSE, nick, "Nickname is already in use")
            return

        old_nick = self.nick
        if old_nick and old_nick in self.server.clients:
            del self.server.clients[old_nick]

        self.nick = nick
        self.server.clients[nick] = self
        await self._try_register()

    async def _handle_user(self, msg: Message) -> None:
        if self._registered:
            await self.send_numeric(replies.ERR_ALREADYREGISTRED, "You may not reregister")
            return
        if len(msg.params) < 4:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "USER", "Not enough parameters")
            return

        self.user = msg.params[0]
        self.realname = msg.params[3]
        await self._try_register()

    async def _try_register(self) -> None:
        if self.nick and self.user and not self._registered:
            self._registered = True
            await self._send_welcome()
            # Announce to linked peers
            for link in self.server.links.values():
                await link.send_raw(f"SNICK {self.nick} {self.user} {self.host} :{self.realname}")

    async def _send_welcome(self) -> None:
        await self.send_numeric(
            replies.RPL_WELCOME,
            f"Welcome to {self.server.config.name} IRC Network {self.prefix}",
        )
        await self.send_numeric(
            replies.RPL_YOURHOST,
            f"Your host is {self.server.config.name}, running culture",
        )
        await self.send_numeric(
            replies.RPL_CREATED,
            "This server was created today",
        )
        await self.send_numeric(
            replies.RPL_MYINFO,
            self.server.config.name,
            "culture",
            "o",
            "ov",
        )

    async def _handle_join(self, msg: Message) -> None:
        if not self._registered:
            return
        if not msg.params:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "JOIN", "Not enough parameters")
            return

        channel_name = msg.params[0]
        if not channel_name.startswith("#"):
            return

        # Block joins to archived rooms
        existing = self.server.channels.get(channel_name)
        if existing and existing.archived:
            await self.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[self.nick, f"{channel_name} is archived and cannot be joined"],
                )
            )
            return

        channel = self.server.get_or_create_channel(channel_name)
        if self in channel.members:
            return

        channel.add(self)
        self.channels.add(channel)

        # Notify all channel members (including self)
        join_msg = Message(prefix=self.prefix, command="JOIN", params=[channel_name])
        for member in list(channel.members):
            await member.send(join_msg)

        await self.server.emit_event(
            Event(type=EventType.JOIN, channel=channel_name, nick=self.nick)
        )

        # Send topic if set
        if channel.topic:
            await self.send_numeric(replies.RPL_TOPIC, channel_name, channel.topic)

        # Send names list
        await self._send_names(channel)

    async def _handle_part(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "PART", "Not enough parameters")
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
        part_msg = Message(prefix=self.prefix, command="PART", params=part_params)
        for member in list(channel.members):
            await member.send(part_msg)

        await self.server.emit_event(
            Event(
                type=EventType.PART,
                channel=channel_name,
                nick=self.nick,
                data={"reason": reason},
            )
        )

        channel.remove(self)
        self.channels.discard(channel)

        if not channel.members and not channel.persistent:
            del self.server.channels[channel_name]

    async def _handle_topic(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "TOPIC", "Not enough parameters")
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
                await self.send_numeric(replies.RPL_TOPIC, channel_name, channel.topic)
            else:
                await self.send_numeric(replies.RPL_NOTOPIC, channel_name, "No topic is set")
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
            await self.server.emit_event(
                Event(
                    type=EventType.TOPIC,
                    channel=channel_name,
                    nick=self.nick,
                    data={"topic": channel.topic},
                )
            )

    async def _handle_names(self, msg: Message) -> None:
        if not msg.params:
            return
        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if channel:
            await self._send_names(channel)

    async def _send_names(self, channel: Channel) -> None:
        nicks = " ".join(f"{channel.get_prefix(m)}{m.nick}" for m in channel.members)
        await self.send_numeric(replies.RPL_NAMREPLY, "=", channel.name, nicks)
        await self.send_numeric(replies.RPL_ENDOFNAMES, channel.name, "End of /NAMES list")

    async def _handle_list(self, msg: Message) -> None:
        for name, channel in self.server.channels.items():
            topic = channel.topic or ""
            await self.send_numeric(replies.RPL_LIST, name, str(len(channel.members)), topic)
        await self.send_numeric(replies.RPL_LISTEND, "End of LIST")

    async def _handle_mode(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "MODE", "Not enough parameters")
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
            await self.send_numeric(replies.ERR_NOSUCHCHANNEL, channel_name, "No such channel")
            return

        if len(msg.params) == 1:
            await self.send_numeric(replies.RPL_CHANNELMODEIS, channel_name, "+")
            return

        modestring = msg.params[1]
        if not channel.is_operator(self):
            await self.send_numeric(
                replies.ERR_CHANOPRIVSNEEDED,
                channel_name,
                "You're not channel operator",
            )
            return

        param_queue = list(msg.params[2:])
        param_modes = {"o", "v", "S"}

        adding = True
        applied_modes = []
        applied_params: list[str] = []
        for ch in modestring:
            if ch == "+":
                adding = True
            elif ch == "-":
                adding = False
            elif ch == "R":
                # +R / -R — restrict channel from federation
                if adding:
                    channel.restricted = True
                else:
                    channel.restricted = False
                applied_modes.append(("+" if adding else "-") + ch)
            elif ch in param_modes:
                if not param_queue:
                    continue
                param_value = param_queue.pop(0)
                if ch == "S":
                    # +S <server> / -S <server> — share with specific servers
                    if adding:
                        channel.shared_with.add(param_value)
                    else:
                        channel.shared_with.discard(param_value)
                    applied_modes.append(("+" if adding else "-") + ch)
                    applied_params.append(param_value)
                else:
                    target_nick = param_value
                    target_client = self.server.clients.get(target_nick)
                    if not target_client or target_client not in channel.members:
                        await self.send_numeric(
                            replies.ERR_USERNOTINCHANNEL,
                            target_nick,
                            channel_name,
                            "They aren't on that channel",
                        )
                        continue
                    if ch == "o":
                        if adding:
                            channel.operators.add(target_client)
                        else:
                            channel.operators.discard(target_client)
                    elif ch == "v":
                        if adding:
                            channel.voiced.add(target_client)
                        else:
                            channel.voiced.discard(target_client)
                    applied_modes.append(("+" if adding else "-") + ch)
                    applied_params.append(target_nick)

        # Auto-promote if no operators remain
        if not channel.operators and channel.members:
            channel.operators.add(min(channel.members, key=lambda m: m.nick))

        if applied_modes:
            mode_msg = Message(
                prefix=self.prefix,
                command="MODE",
                params=[channel_name, "".join(applied_modes)] + applied_params,
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
        if len(msg.params) > 1:
            modestring = msg.params[1]
            adding = True
            for ch in modestring:
                if ch == "+":
                    adding = True
                elif ch == "-":
                    adding = False
                elif ch in ("H", "A", "B"):
                    if adding:
                        self.modes.add(ch)
                    else:
                        self.modes.discard(ch)
        mode_str = "+" + "".join(sorted(self.modes)) if self.modes else "+"
        await self.send_numeric(replies.RPL_UMODEIS, mode_str)

    async def _handle_privmsg(self, msg: Message) -> None:
        if len(msg.params) < 2:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "PRIVMSG", "Not enough parameters")
            return

        target = msg.params[0]
        text = msg.params[1]
        relay = Message(prefix=self.prefix, command="PRIVMSG", params=[target, text])

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if not channel:
                await self.send_numeric(replies.ERR_NOSUCHCHANNEL, target, "No such channel")
                return
            if self not in channel.members:
                await self.send_numeric(
                    replies.ERR_CANNOTSENDTOCHAN, target, "Cannot send to channel"
                )
                return
            for member in list(channel.members):
                if member is not self:
                    await member.send(relay)
            await self.server.emit_event(
                Event(
                    type=EventType.MESSAGE,
                    channel=target,
                    nick=self.nick,
                    data={"text": text},
                )
            )
            await self._notify_mentions(target, text)
        else:
            from culture.server.remote_client import RemoteClient

            recipient = self.server.get_client(target)
            if not recipient:
                await self.send_numeric(replies.ERR_NOSUCHNICK, target, "No such nick")
                return
            if isinstance(recipient, RemoteClient):
                # Send DM through the S2S link
                await recipient.link.send_raw(
                    f":{self.server.config.name} SMSG {target} {self.nick} :{text}"
                )
            else:
                await recipient.send(relay)
            await self.server.emit_event(
                Event(
                    type=EventType.MESSAGE,
                    channel=None,
                    nick=self.nick,
                    data={"text": text, "target": target},
                )
            )
            await self._notify_mentions(None, text)

    async def _notify_mentions(self, channel_name: str | None, text: str) -> None:
        from culture.server.remote_client import RemoteClient

        mentioned_nicks = re.findall(r"@(\S+)", text)
        if not mentioned_nicks:
            return
        seen: set[str] = set()
        channel = self.server.channels.get(channel_name) if channel_name else None
        source = channel_name or "a direct message"
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
                    f"{self.nick} mentioned you in {source}: {text}",
                ],
            )
            if isinstance(target_client, RemoteClient):
                # Send mention notice through S2S link
                await target_client.link.send_raw(
                    f":{self.server.config.name} SNOTICE {nick}"
                    f" {self.server.config.name}"
                    f" :{self.nick} mentioned you in {source}: {text}"
                )
            else:
                await target_client.send(notice)

    async def _handle_notice(self, msg: Message) -> None:
        # Same as PRIVMSG but no error replies per RFC 2812
        if len(msg.params) < 2:
            return

        target = msg.params[0]
        text = msg.params[1]
        relay = Message(prefix=self.prefix, command="NOTICE", params=[target, text])

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if not channel:
                return
            if self not in channel.members:
                return
            for member in list(channel.members):
                if member is not self:
                    await member.send(relay)
            await self.server.emit_event(
                Event(
                    type=EventType.MESSAGE,
                    channel=target,
                    nick=self.nick,
                    data={"text": text, "notice": True},
                )
            )
        else:
            from culture.server.remote_client import RemoteClient

            recipient = self.server.get_client(target)
            if recipient:
                if isinstance(recipient, RemoteClient):
                    await recipient.link.send_raw(
                        f":{self.server.config.name} SNOTICE {target} {self.nick} :{text}"
                    )
                else:
                    await recipient.send(relay)
                await self.server.emit_event(
                    Event(
                        type=EventType.MESSAGE,
                        channel=None,
                        nick=self.nick,
                        data={"text": text, "notice": True, "target": target},
                    )
                )

    async def _handle_who(self, msg: Message) -> None:
        from culture.server.remote_client import RemoteClient

        if not msg.params:
            await self.send_numeric(replies.RPL_ENDOFWHO, "*", "End of WHO list")
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
                    if hasattr(member, "modes") and member.modes:
                        flags += "[" + "".join(sorted(member.modes)) + "]"
                    if hasattr(member, "icon") and member.icon:
                        flags += "{" + member.icon + "}"
                    server_name = (
                        member.server_name
                        if isinstance(member, RemoteClient)
                        else self.server.config.name
                    )
                    await self.send_numeric(
                        replies.RPL_WHOREPLY,
                        target,
                        member.user or "*",
                        member.host,
                        server_name,
                        member.nick,
                        flags,
                        f"0 {member.realname or ''}",
                    )
            await self.send_numeric(replies.RPL_ENDOFWHO, target, "End of WHO list")
        else:
            client = self.server.get_client(target)
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
                server_name = (
                    client.server_name
                    if isinstance(client, RemoteClient)
                    else self.server.config.name
                )
                await self.send_numeric(
                    replies.RPL_WHOREPLY,
                    chan_name,
                    client.user or "*",
                    client.host,
                    server_name,
                    client.nick,
                    flags,
                    f"0 {client.realname or ''}",
                )
            await self.send_numeric(replies.RPL_ENDOFWHO, target, "End of WHO list")

    async def _handle_whois(self, msg: Message) -> None:
        from culture.server.remote_client import RemoteClient

        if not msg.params:
            await self.send_numeric(replies.ERR_NONICKNAMEGIVEN, "No nickname given")
            return

        target_nick = msg.params[0]
        target = self.server.get_client(target_nick)
        if not target:
            await self.send_numeric(replies.ERR_NOSUCHNICK, target_nick, "No such nick/channel")
            await self.send_numeric(replies.RPL_ENDOFWHOIS, target_nick, "End of WHOIS list")
            return

        await self.send_numeric(
            replies.RPL_WHOISUSER,
            target.nick,
            target.user or "*",
            target.host,
            "*",
            target.realname or "",
        )
        server_name = (
            target.server_name if isinstance(target, RemoteClient) else self.server.config.name
        )
        await self.send_numeric(
            replies.RPL_WHOISSERVER,
            target.nick,
            server_name,
            "culture",
        )
        if target.channels:
            chan_list = " ".join(f"{ch.get_prefix(target)}{ch.name}" for ch in target.channels)
            await self.send_numeric(replies.RPL_WHOISCHANNELS, target.nick, chan_list)
        await self.send_numeric(replies.RPL_ENDOFWHOIS, target.nick, "End of WHOIS list")

    async def _handle_quit(self, msg: Message) -> None:
        reason = msg.params[0] if msg.params else "Quit"
        quit_msg = Message(prefix=self.prefix, command="QUIT", params=[reason])

        notified: set[Client] = set()
        channel_names = [ch.name for ch in self.channels]
        for channel in list(self.channels):
            for member in list(channel.members):
                if member is not self and member not in notified:
                    await member.send(quit_msg)
                    notified.add(member)

        await self.server.emit_event(
            Event(
                type=EventType.QUIT,
                channel=None,
                nick=self.nick,
                data={"reason": reason, "channels": channel_names},
            )
        )

        raise ConnectionError("Client quit")
