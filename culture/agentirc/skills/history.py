# server/skills/history.py
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from culture.agentirc.events import NO_SURFACE_EVENT_TYPES, render_event
from culture.agentirc.skill import Event, EventType, Skill
from culture.constants import SYSTEM_CHANNEL, SYSTEM_USER_PREFIX
from culture.protocol import replies
from culture.protocol.message import Message

if TYPE_CHECKING:
    from culture.agentirc.client import Client

logger = logging.getLogger(__name__)


@dataclass
class HistoryEntry:
    nick: str
    text: str
    timestamp: float


class HistorySkill(Skill):
    name = "history"
    commands = {"HISTORY"}

    _NO_STORE_TYPES = NO_SURFACE_EVENT_TYPES

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
        from culture.agentirc.history_store import HistoryStore

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
            return

        # Skip event types that are delivered via their own IRC verbs
        # (THREAD_*, TOPIC) — they have dedicated storage. MESSAGE was
        # already handled above.
        type_wire = event.type.value if hasattr(event.type, "value") else str(event.type)
        if type_wire in self._NO_STORE_TYPES:
            return

        # Store lifecycle events (agent.connect, server.wake, etc.)
        target = event.channel or SYSTEM_CHANNEL
        origin = event.data.get("_origin") or self.server.config.name
        nick = f"{SYSTEM_USER_PREFIX}{origin}"
        payload = {k: v for k, v in event.data.items() if not k.startswith("_")}
        if event.nick:
            payload.setdefault("nick", event.nick)
        if event.channel:
            payload.setdefault("channel", event.channel)
        body = event.data.get("_render") or render_event(type_wire, payload, event.channel)

        buf = self._channels.setdefault(target, deque(maxlen=self.maxlen))
        buf.append(HistoryEntry(nick=nick, text=body, timestamp=event.timestamp))
        if self._store is not None:
            self._store.append(target, nick, body, event.timestamp)

    def get_recent(self, channel: str, count: int) -> list[HistoryEntry]:
        if count <= 0:
            return []
        buf = self._channels.get(channel)
        if not buf:
            return []
        entries = list(buf)
        return entries[-count:]

    def _client_may_read_history(self, client: Client, channel_name: str) -> bool:
        """A client may read a channel's history only if it is a current
        member of the channel. Mirrors the gate used by PART / TOPIC /
        PRIVMSG-to-channel paths in client.py. An unknown channel is
        treated as forbidden — a client that joins it implicitly creates
        it, but until then the history is not theirs to see.
        """
        channel_obj = self.server.channels.get(channel_name)
        if channel_obj is None:
            return False
        return client in channel_obj.members

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
        # SECURITY (v8.18.2-B #1): without a membership check, any registered
        # client could read every channel's full history — leaking
        # conversation content + potentially credentials. Match the pattern
        # used by client.py:_handle_part / _handle_topic etc.
        if not self._client_may_read_history(client, channel):
            await client.send_numeric(replies.ERR_NOTONCHANNEL, channel, replies.MSG_NOTONCHANNEL)
            return
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
        # SECURITY (v8.18.2-B #1) — same gate as _handle_recent.
        if not self._client_may_read_history(client, channel):
            await client.send_numeric(replies.ERR_NOTONCHANNEL, channel, replies.MSG_NOTONCHANNEL)
            return
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
