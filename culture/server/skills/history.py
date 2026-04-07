# server/skills/history.py
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from culture.protocol import replies
from culture.protocol.message import Message
from culture.server.skill import Event, EventType, Skill

if TYPE_CHECKING:
    from culture.server.client import Client


@dataclass
class HistoryEntry:
    nick: str
    text: str
    timestamp: float


class HistorySkill(Skill):
    name = "history"
    commands = {"HISTORY"}

    def __init__(self, maxlen: int = 10000):
        self.maxlen = maxlen
        self._channels: dict[str, deque[HistoryEntry]] = {}

    async def on_event(self, event: Event) -> None:
        if event.type == EventType.MESSAGE and event.channel is not None:
            buf = self._channels.setdefault(event.channel, deque(maxlen=self.maxlen))
            buf.append(
                HistoryEntry(
                    nick=event.nick,
                    text=event.data["text"],
                    timestamp=event.timestamp,
                )
            )

    def get_recent(self, channel: str, count: int) -> list[HistoryEntry]:
        if count <= 0:
            return []
        buf = self._channels.get(channel)
        if not buf:
            return []
        entries = list(buf)
        return entries[-count:]

    def search(self, channel: str, term: str) -> list[HistoryEntry]:
        buf = self._channels.get(channel)
        if not buf:
            return []
        term_lower = term.lower()
        return [e for e in buf if term_lower in e.text.lower()]

    async def on_command(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 1:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "HISTORY", replies.MSG_NEEDMOREPARAMS
            )
            return

        subcmd = msg.params[0].upper()
        if subcmd == "RECENT":
            await self._handle_recent(client, msg)
        elif subcmd == "SEARCH":
            await self._handle_search(client, msg)
        else:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, f"Unknown HISTORY subcommand: {subcmd}"],
                )
            )

    async def _handle_recent(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 3:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "HISTORY", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel = msg.params[1]
        try:
            count = int(msg.params[2])
        except ValueError:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, "Invalid count"],
                )
            )
            return

        if count < 0:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, "Invalid count"],
                )
            )
            return

        entries = self.get_recent(channel, count)
        for entry in entries:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="HISTORY",
                    params=[channel, entry.nick, str(entry.timestamp), entry.text],
                )
            )
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="HISTORYEND",
                params=[channel, "End of history"],
            )
        )

    async def _handle_search(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 3:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "HISTORY", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel = msg.params[1]
        term = msg.params[2]
        entries = self.search(channel, term)
        for entry in entries:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="HISTORY",
                    params=[channel, entry.nick, str(entry.timestamp), entry.text],
                )
            )
        await client.send(
            Message(
                prefix=self.server.config.name,
                command="HISTORYEND",
                params=[channel, "End of history"],
            )
        )
