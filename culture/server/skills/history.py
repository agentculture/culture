# server/skills/history.py
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from culture.protocol import replies
from culture.protocol.message import Message
from culture.server.skill import Event, EventType, Skill

if TYPE_CHECKING:
    from culture.server.client import Client

logger = logging.getLogger(__name__)


@dataclass
class HistoryEntry:
    nick: str
    text: str
    timestamp: float


class HistorySkill(Skill):
    name = "history"
    commands = {"HISTORY"}

    def __init__(self, maxlen: int = 10000, retention_days: int = 30):
        self.maxlen = maxlen
        self.retention_days = retention_days
        self._channels: dict[str, deque[HistoryEntry]] = {}
        self._store = None

    async def start(self, server) -> None:
        await super().start(server)
        self._restore_history()

    async def stop(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def _restore_history(self) -> None:
        """Reload persisted history from SQLite on startup."""
        if not self.server.config.data_dir:
            return
        from culture.server.history_store import HistoryStore

        try:
            store = HistoryStore(self.server.config.data_dir)
            store.prune(self.retention_days)
            channel_data = store.load_channels(self.maxlen)
        except Exception:
            logger.warning(
                "Failed to open history database — falling back to in-memory",
                exc_info=True,
            )
            return

        self._store = store
        for channel, entries in channel_data.items():
            buf = deque(maxlen=self.maxlen)
            for e in entries:
                buf.append(HistoryEntry(nick=e["nick"], text=e["text"], timestamp=e["timestamp"]))
            self._channels[channel] = buf
        total = sum(len(d) for d in self._channels.values())
        if total:
            logger.info(
                "Restored %d history entries across %d channels",
                total,
                len(self._channels),
            )

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
            if self._store is not None:
                self._store.append(event.channel, event.nick, event.data["text"], event.timestamp)

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
