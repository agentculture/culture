# server/client.py
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from protocol.message import Message
from protocol import replies

if TYPE_CHECKING:
    from server.ircd import IRCd
    from server.channel import Channel


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
        self.writer.write(message.format().encode("utf-8"))
        await self.writer.drain()

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
            # Normalize bare \n to \r\n for clients that don't send proper CRLF
            buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line:
                    msg = Message.parse(line)
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
