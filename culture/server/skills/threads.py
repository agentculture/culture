"""Threads skill — THREAD (CREATE/REPLY), THREADS, THREADCLOSE (close/promote)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from culture.protocol import replies
from culture.protocol.message import Message
from culture.server.skill import Event, EventType, Skill

if TYPE_CHECKING:
    from culture.server.channel import Channel
    from culture.server.client import Client

# Thread name: alphanumeric + hyphens, 1-32 chars, must start/end with alnum
_THREAD_NAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,30}[a-zA-Z0-9])?$")


@dataclass
class ThreadMessage:
    nick: str
    text: str
    timestamp: float


@dataclass
class Thread:
    name: str
    channel: str
    creator: str
    created_at: float
    messages: list[ThreadMessage] = field(default_factory=list)
    archived: bool = False
    summary: str | None = None
    max_messages: int = 500

    @property
    def participants(self) -> set[str]:
        return {m.nick for m in self.messages}


class ThreadsSkill(Skill):
    name = "threads"
    commands = {"THREAD", "THREADS", "THREADCLOSE"}

    def __init__(self, max_messages: int = 500) -> None:
        # key: (channel_name, thread_name) -> Thread
        self._threads: dict[tuple[str, str], Thread] = {}
        self.max_messages = max_messages

    async def start(self, server) -> None:
        await super().start(server)
        self._restore_threads()

    def _restore_threads(self) -> None:
        """Reload persisted threads from disk on startup."""
        if not self.server.config.data_dir:
            return
        from culture.server.thread_store import ThreadStore

        store = ThreadStore(self.server.config.data_dir)
        for data in store.load_all():
            thread = Thread(
                name=data["name"],
                channel=data["channel"],
                creator=data["creator"],
                created_at=data["created_at"],
                archived=data.get("archived", False),
                summary=data.get("summary"),
                max_messages=self.max_messages,
            )
            for m in data.get("messages", []):
                thread.messages.append(
                    ThreadMessage(
                        nick=m["nick"],
                        text=m["text"],
                        timestamp=m["timestamp"],
                    )
                )
            self._threads[(data["channel"], data["name"])] = thread

    def _persist_thread(self, thread: Thread) -> None:
        """Save a thread to disk if data_dir is configured."""
        if not self.server.config.data_dir:
            return
        from culture.server.thread_store import ThreadStore

        store = ThreadStore(self.server.config.data_dir)
        store.save(
            {
                "name": thread.name,
                "channel": thread.channel,
                "creator": thread.creator,
                "created_at": thread.created_at,
                "archived": thread.archived,
                "summary": thread.summary,
                "messages": [
                    {"nick": m.nick, "text": m.text, "timestamp": m.timestamp}
                    for m in thread.messages
                ],
            }
        )

    async def on_command(self, client: Client, msg: Message) -> None:
        handler = {
            "THREAD": self._handle_thread,
            "THREADS": self._handle_threads,
            "THREADCLOSE": self._handle_threadclose,
        }.get(msg.command)
        if handler:
            await handler(client, msg)

    # ---- THREAD (CREATE / REPLY) ------------------------------------------

    async def _handle_thread(self, client: Client, msg: Message) -> None:
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREAD", replies.MSG_NEEDMOREPARAMS
            )
            return

        subcmd = msg.params[0].upper()
        if subcmd == "CREATE":
            await self._handle_create(client, msg)
        elif subcmd == "REPLY":
            await self._handle_reply(client, msg)
        else:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[client.nick, f"Unknown THREAD subcommand: {subcmd}"],
                )
            )

    async def _handle_create(self, client: Client, msg: Message) -> None:
        # THREAD CREATE #channel thread-name :initial message
        if len(msg.params) < 4:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREAD CREATE", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[1]
        thread_name = msg.params[2]
        text = msg.params[3]

        # Validate channel membership
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, replies.MSG_NOTONCHANNEL
            )
            return

        # Validate thread name format
        if not _THREAD_NAME_RE.match(thread_name):
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="400",
                    params=[
                        client.nick or "*",
                        thread_name,
                        "Invalid thread name (alphanumeric + hyphens, 1-32 chars)",
                    ],
                )
            )
            return

        # Check for duplicate
        key = (channel_name, thread_name)
        if key in self._threads:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="400",
                    params=[client.nick or "*", thread_name, "Thread already exists"],
                )
            )
            return

        # Create thread
        now = time.time()
        thread = Thread(
            name=thread_name,
            channel=channel_name,
            creator=client.nick,
            created_at=now,
            max_messages=self.max_messages,
        )
        thread.messages.append(
            ThreadMessage(
                nick=client.nick,
                text=text,
                timestamp=now,
            )
        )
        self._threads[key] = thread

        # Deliver prefixed PRIVMSG to channel members
        prefixed = await self._deliver_thread_msg(client, channel, thread_name, text)

        # Persist
        self._persist_thread(thread)

        # Emit event
        await self.server.emit_event(
            Event(
                type=EventType.THREAD_CREATE,
                channel=channel_name,
                nick=client.nick,
                data={"text": prefixed, "thread": thread_name, "raw_text": text},
            )
        )

    async def _handle_reply(self, client: Client, msg: Message) -> None:
        # THREAD REPLY #channel thread-name :reply text
        if len(msg.params) < 4:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREAD REPLY", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[1]
        thread_name = msg.params[2]
        text = msg.params[3]

        # Validate channel membership
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, replies.MSG_NOTONCHANNEL
            )
            return

        # Validate thread exists
        key = (channel_name, thread_name)
        thread = self._threads.get(key)
        if not thread:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="404",
                    params=[client.nick or "*", thread_name, replies.MSG_NOSUCHTHREAD],
                )
            )
            return

        # Check if archived
        if thread.archived:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="405",
                    params=[client.nick or "*", thread_name, "Thread is closed"],
                )
            )
            return

        # Append message (cap at max_messages)
        now = time.time()
        thread.messages.append(
            ThreadMessage(
                nick=client.nick,
                text=text,
                timestamp=now,
            )
        )
        if len(thread.messages) > thread.max_messages:
            thread.messages = thread.messages[-thread.max_messages :]

        # Deliver prefixed PRIVMSG to channel members
        prefixed = await self._deliver_thread_msg(client, channel, thread_name, text)

        # Persist
        self._persist_thread(thread)

        # Emit event
        await self.server.emit_event(
            Event(
                type=EventType.THREAD_MESSAGE,
                channel=channel_name,
                nick=client.nick,
                data={"text": prefixed, "thread": thread_name, "raw_text": text},
            )
        )

    @staticmethod
    def _format_thread_msg(thread_name: str, text: str) -> str:
        return f"[thread:{thread_name}] {text}"

    async def _deliver_thread_msg(
        self,
        sender: Client,
        channel: Channel,
        thread_name: str,
        text: str,
    ) -> str:
        """Send a [thread:name] prefixed PRIVMSG to all channel members except sender.

        Returns the prefixed text for use in event data.
        """
        from culture.server.remote_client import RemoteClient

        prefixed = self._format_thread_msg(thread_name, text)
        relay = Message(
            prefix=sender.prefix,
            command="PRIVMSG",
            params=[channel.name, prefixed],
        )
        for member in list(channel.members):
            if member is not sender and not isinstance(member, RemoteClient):
                await member.send(relay)

        await self._notify_mentioned_in_thread(sender, channel, thread_name, text)

        return prefixed

    async def _notify_mentioned_in_thread(
        self,
        sender: Client,
        channel: Channel,
        thread_name: str,
        text: str,
    ) -> None:
        """Find @mentions in text and send a NOTICE to each mentioned user in the channel."""
        from culture.server.remote_client import RemoteClient

        mentioned_nicks = re.findall(r"@(\S+)", text)
        if not mentioned_nicks:
            return
        seen: set[str] = set()
        for raw_nick in mentioned_nicks:
            nick = raw_nick.rstrip(".,;:!?")
            if nick in seen or nick == sender.nick:
                continue
            seen.add(nick)
            target_client = self.server.clients.get(nick)
            if not target_client:
                continue
            if target_client not in channel.members:
                continue
            if isinstance(target_client, RemoteClient):
                continue
            notice = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[
                    nick,
                    f"{sender.nick} mentioned you in thread {thread_name} on {channel.name}",
                ],
            )
            await target_client.send(notice)

    # ---- THREADS (list) ---------------------------------------------------

    async def _handle_threads(self, client: Client, msg: Message) -> None:
        # THREADS #channel
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADS", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, replies.MSG_NOTONCHANNEL
            )
            return

        # List non-archived threads for this channel
        for key, thread in self._threads.items():
            if key[0] == channel_name and not thread.archived:
                await client.send(
                    Message(
                        prefix=self.server.config.name,
                        command="THREADS",
                        params=[
                            channel_name,
                            thread.name,
                            f"{thread.creator} {len(thread.messages)} {int(thread.created_at)}",
                        ],
                    )
                )

        await client.send(
            Message(
                prefix=self.server.config.name,
                command="THREADSEND",
                params=[channel_name, "End of thread list"],
            )
        )

    # ---- THREADCLOSE (close / promote) ------------------------------------

    async def _handle_threadclose(self, client: Client, msg: Message) -> None:
        # THREADCLOSE #channel thread-name :summary
        # THREADCLOSE PROMOTE #channel thread-name [#breakout-name]
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADCLOSE", replies.MSG_NEEDMOREPARAMS
            )
            return

        # Detect PROMOTE subcommand
        if msg.params[0].upper() == "PROMOTE":
            await self._handle_promote(client, msg)
            return

        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADCLOSE", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[0]
        thread_name = msg.params[1]
        summary = msg.params[2] if len(msg.params) > 2 else None

        result = await self._validate_thread_access(client, channel_name, thread_name, "close")
        if result is None:
            return
        channel, thread = result

        # Authorization: thread participants or channel operators
        if client.nick not in thread.participants and not channel.is_operator(client):
            await client.send_numeric(
                replies.ERR_CHANOPRIVSNEEDED, channel_name, "Not authorized to close this thread"
            )
            return

        # Archive thread
        thread.archived = True
        thread.summary = summary

        # Post summary NOTICE to parent channel
        n_participants = len(thread.participants)
        n_messages = len(thread.messages)
        summary_text = self._build_close_summary(thread_name, summary, n_participants, n_messages)
        notice = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=[channel_name, summary_text],
        )
        from culture.server.remote_client import RemoteClient

        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(notice)

        # Persist
        self._persist_thread(thread)

        # Emit event
        await self.server.emit_event(
            Event(
                type=EventType.THREAD_CLOSE,
                channel=channel_name,
                nick=client.nick,
                data={
                    "thread": thread_name,
                    "summary": summary,  # raw summary text from user
                    "participants": n_participants,
                    "messages": n_messages,
                },
            )
        )

    async def _validate_thread_access(
        self,
        client: Client,
        channel_name: str,
        thread_name: str,
        operation_label: str,
    ):
        """Validate channel membership, thread existence, and thread not archived.

        Returns (channel, thread) on success, or sends the appropriate error and returns None.
        Does NOT check authorization — callers enforce that themselves.
        """
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, replies.MSG_NOTONCHANNEL
            )
            return None

        key = (channel_name, thread_name)
        thread = self._threads.get(key)
        if not thread:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="404",
                    params=[client.nick or "*", thread_name, replies.MSG_NOSUCHTHREAD],
                )
            )
            return None

        if thread.archived:
            await client.send(
                Message(
                    prefix=self.server.config.name,
                    command="405",
                    params=[client.nick or "*", thread_name, "Thread is already closed"],
                )
            )
            return None

        return (channel, thread)

    @staticmethod
    def _build_close_summary(
        thread_name: str,
        summary: str | None,
        n_participants: int,
        n_messages: int,
    ) -> str:
        """Format the NOTICE text announcing a thread was closed."""
        if summary:
            return (
                f"[Thread {thread_name} closed] Summary: {summary} "
                f"({n_participants} participants, {n_messages} messages)"
            )
        return (
            f"[Thread {thread_name} closed] "
            f"({n_participants} participants, {n_messages} messages)"
        )

    async def _handle_promote(self, client: Client, msg: Message) -> None:
        # THREADCLOSE PROMOTE #channel thread-name [#breakout-name]
        if len(msg.params) < 3:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADCLOSE PROMOTE", replies.MSG_NEEDMOREPARAMS
            )
            return

        channel_name = msg.params[1]
        thread_name = msg.params[2]
        custom_breakout = msg.params[3] if len(msg.params) > 3 else None

        result = await self._validate_thread_access(client, channel_name, thread_name, "promote")
        if result is None:
            return
        channel, thread = result

        # Authorization: thread creator or channel operators
        if client.nick != thread.creator and not channel.is_operator(client):
            await client.send_numeric(
                replies.ERR_CHANOPRIVSNEEDED, channel_name, "Not authorized to promote this thread"
            )
            return

        # Determine breakout channel name and create it
        channel_base = channel_name  # e.g. "#general"
        breakout_name = custom_breakout or f"{channel_base}-{thread_name}"

        breakout = await self._create_breakout_channel(
            client, channel, thread, channel_name, thread_name, breakout_name
        )
        if breakout is None:
            return

        await self._populate_breakout(thread, breakout, breakout_name)
        await self._replay_thread_history(thread, breakout_name)

        # Archive original thread
        thread.archived = True
        thread.summary = f"Promoted to {breakout_name}"

        # Post promotion notice to parent channel
        from culture.server.remote_client import RemoteClient

        notice = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=[channel_name, f"[thread:{thread_name}] promoted to {breakout_name}"],
        )
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(notice)

        # Persist
        self._persist_thread(thread)

        # Emit event
        await self.server.emit_event(
            Event(
                type=EventType.THREAD_CLOSE,
                channel=channel_name,
                nick=client.nick,
                data={
                    "thread": thread_name,
                    "promoted_to": breakout_name,
                    "summary": thread.summary,
                    "participants": len(thread.participants),
                    "messages": len(thread.messages),
                },
            )
        )

    async def _create_breakout_channel(
        self,
        client: Client,
        channel: Channel,
        thread: Thread,
        channel_name: str,
        thread_name: str,
        breakout_name: str,
    ):
        """Create a breakout channel for the promoted thread.

        Checks for conflicts with existing channels. Returns the Channel on success,
        or sends an error and returns None.
        """
        existing = self.server.channels.get(breakout_name)
        if existing is not None:
            if (
                existing.extra_meta.get("thread_parent") != channel_name
                or existing.extra_meta.get("thread_name") != thread_name
            ):
                await client.send(
                    Message(
                        prefix=self.server.config.name,
                        command="400",
                        params=[client.nick or "*", breakout_name, "Channel already exists"],
                    )
                )
                return None

        breakout = self.server.get_or_create_channel(breakout_name)
        breakout.topic = f"Promoted from thread '{thread_name}' in {channel_name}"
        breakout.extra_meta["thread_parent"] = channel_name
        breakout.extra_meta["thread_name"] = thread_name
        return breakout

    async def _populate_breakout(self, thread: Thread, breakout, breakout_name: str) -> None:
        """Gather local participants from the thread, auto-join them, and send JOIN messages."""
        from culture.server.remote_client import RemoteClient

        participant_nicks = thread.participants
        participants = []
        for nick in participant_nicks:
            c = self.server.clients.get(nick)
            if c and not isinstance(c, RemoteClient):
                participants.append(c)

        # Auto-join participants to breakout
        for member in participants:
            if member not in breakout.members:
                breakout.add(member)
                member.channels.add(breakout)

        # Send JOIN messages to all breakout members
        for member in participants:
            join_msg = Message(prefix=member.prefix, command="JOIN", params=[breakout_name])
            for other in list(breakout.members):
                if not isinstance(other, RemoteClient):
                    await other.send(join_msg)

    async def _replay_thread_history(self, thread: Thread, breakout_name: str) -> None:
        """Replay all thread messages as NOTICE to breakout channel members."""
        from culture.server.remote_client import RemoteClient

        breakout = self.server.channels.get(breakout_name)
        if breakout is None:
            return
        for tmsg in thread.messages:
            replay = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[breakout_name, f"[history] <{tmsg.nick}> {tmsg.text}"],
            )
            for member in list(breakout.members):
                if not isinstance(member, RemoteClient):
                    await member.send(replay)

    # ---- Public helpers ------------------------------------------------------

    def get_thread(self, channel: str, name: str) -> Thread | None:
        return self._threads.get((channel, name))

    def get_thread_messages(self, channel: str, name: str, limit: int = 50) -> list[ThreadMessage]:
        thread = self._threads.get((channel, name))
        if thread is None:
            return []
        return thread.messages[-limit:]
