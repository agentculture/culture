"""Threads skill — THREAD (CREATE/REPLY), THREADS, THREADCLOSE (close/promote)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agentirc.protocol.message import Message
from agentirc.protocol import replies
from agentirc.server.skill import Event, EventType, Skill

if TYPE_CHECKING:
    from agentirc.server.client import Client

# Thread name: alphanumeric + hyphens, 1-32 chars, must start/end with alnum
_THREAD_NAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,30}[a-zA-Z0-9])?$")


@dataclass
class ThreadMessage:
    nick: str
    text: str
    timestamp: float
    seq: int


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
                replies.ERR_NEEDMOREPARAMS, "THREAD", "Not enough parameters"
            )
            return

        subcmd = msg.params[0].upper()
        if subcmd == "CREATE":
            await self._handle_create(client, msg)
        elif subcmd == "REPLY":
            await self._handle_reply(client, msg)
        else:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"Unknown THREAD subcommand: {subcmd}"],
            ))

    async def _handle_create(self, client: Client, msg: Message) -> None:
        # THREAD CREATE #channel thread-name :initial message
        if len(msg.params) < 4:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREAD CREATE", "Not enough parameters"
            )
            return

        channel_name = msg.params[1]
        thread_name = msg.params[2]
        text = msg.params[3]

        # Validate channel membership
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, "You're not on that channel"
            )
            return

        # Validate thread name format
        if not _THREAD_NAME_RE.match(thread_name):
            await client.send(Message(
                prefix=self.server.config.name,
                command="400",
                params=[client.nick or "*", thread_name, "Invalid thread name (alphanumeric + hyphens, 1-32 chars)"],
            ))
            return

        # Check for duplicate
        key = (channel_name, thread_name)
        if key in self._threads:
            await client.send(Message(
                prefix=self.server.config.name,
                command="400",
                params=[client.nick or "*", thread_name, "Thread already exists"],
            ))
            return

        # Create thread
        now = time.time()
        seq = self.server.next_seq()
        thread = Thread(
            name=thread_name,
            channel=channel_name,
            creator=client.nick,
            created_at=now,
            max_messages=self.max_messages,
        )
        thread.messages.append(ThreadMessage(
            nick=client.nick,
            text=text,
            timestamp=now,
            seq=seq,
        ))
        self._threads[key] = thread

        # Deliver prefixed PRIVMSG to channel members
        prefixed = f"[thread:{thread_name}] {text}"
        await self._deliver_thread_msg(client, channel, thread_name, text)

        # Emit event
        await self.server.emit_event(Event(
            type=EventType.THREAD_CREATE,
            channel=channel_name,
            nick=client.nick,
            data={"text": prefixed, "thread": thread_name, "raw_text": text},
        ))

    async def _handle_reply(self, client: Client, msg: Message) -> None:
        # THREAD REPLY #channel thread-name :reply text
        if len(msg.params) < 4:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREAD REPLY", "Not enough parameters"
            )
            return

        channel_name = msg.params[1]
        thread_name = msg.params[2]
        text = msg.params[3]

        # Validate channel membership
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, "You're not on that channel"
            )
            return

        # Validate thread exists
        key = (channel_name, thread_name)
        thread = self._threads.get(key)
        if not thread:
            await client.send(Message(
                prefix=self.server.config.name,
                command="404",
                params=[client.nick or "*", thread_name, "No such thread"],
            ))
            return

        # Check if archived
        if thread.archived:
            await client.send(Message(
                prefix=self.server.config.name,
                command="405",
                params=[client.nick or "*", thread_name, "Thread is closed"],
            ))
            return

        # Append message (cap at max_messages)
        now = time.time()
        seq = self.server.next_seq()
        thread.messages.append(ThreadMessage(
            nick=client.nick,
            text=text,
            timestamp=now,
            seq=seq,
        ))
        if len(thread.messages) > thread.max_messages:
            thread.messages = thread.messages[-thread.max_messages:]

        # Deliver prefixed PRIVMSG to channel members
        prefixed = f"[thread:{thread_name}] {text}"
        await self._deliver_thread_msg(client, channel, thread_name, text)

        # Emit event
        await self.server.emit_event(Event(
            type=EventType.THREAD_MESSAGE,
            channel=channel_name,
            nick=client.nick,
            data={"text": prefixed, "thread": thread_name, "raw_text": text},
        ))

    async def _deliver_thread_msg(self, sender, channel, thread_name: str, text: str) -> None:
        """Send a [thread:name] prefixed PRIVMSG to all channel members except sender."""
        from agentirc.server.remote_client import RemoteClient

        prefixed_text = f"[thread:{thread_name}] {text}"
        relay = Message(
            prefix=sender.prefix,
            command="PRIVMSG",
            params=[channel.name, prefixed_text],
        )
        for member in list(channel.members):
            if member is not sender and not isinstance(member, RemoteClient):
                await member.send(relay)

    # ---- THREADS (list) ---------------------------------------------------

    async def _handle_threads(self, client: Client, msg: Message) -> None:
        # THREADS #channel
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADS", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, "You're not on that channel"
            )
            return

        # List non-archived threads for this channel
        for key, thread in self._threads.items():
            if key[0] == channel_name and not thread.archived:
                await client.send(Message(
                    prefix=self.server.config.name,
                    command="THREADS",
                    params=[
                        channel_name,
                        thread.name,
                        f"{thread.creator} {len(thread.messages)} {int(thread.created_at)}",
                    ],
                ))

        await client.send(Message(
            prefix=self.server.config.name,
            command="THREADSEND",
            params=[channel_name, "End of thread list"],
        ))

    # ---- THREADCLOSE (close / promote) ------------------------------------

    async def _handle_threadclose(self, client: Client, msg: Message) -> None:
        # THREADCLOSE #channel thread-name :summary
        # THREADCLOSE PROMOTE #channel thread-name [#breakout-name]
        if not msg.params:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADCLOSE", "Not enough parameters"
            )
            return

        # Detect PROMOTE subcommand
        if msg.params[0].upper() == "PROMOTE":
            await self._handle_promote(client, msg)
            return

        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADCLOSE", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        thread_name = msg.params[1]
        summary = msg.params[2] if len(msg.params) > 2 else None

        # Validate channel membership
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, "You're not on that channel"
            )
            return

        # Validate thread exists
        key = (channel_name, thread_name)
        thread = self._threads.get(key)
        if not thread:
            await client.send(Message(
                prefix=self.server.config.name,
                command="404",
                params=[client.nick or "*", thread_name, "No such thread"],
            ))
            return

        if thread.archived:
            await client.send(Message(
                prefix=self.server.config.name,
                command="405",
                params=[client.nick or "*", thread_name, "Thread is already closed"],
            ))
            return

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
        if summary:
            summary_text = (
                f"[Thread {thread_name} closed] Summary: {summary} "
                f"({n_participants} participants, {n_messages} messages)"
            )
        else:
            summary_text = (
                f"[Thread {thread_name} closed] "
                f"({n_participants} participants, {n_messages} messages)"
            )
        notice = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=[channel_name, summary_text],
        )
        from agentirc.server.remote_client import RemoteClient
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(notice)

        # Emit event
        await self.server.emit_event(Event(
            type=EventType.THREAD_CLOSE,
            channel=channel_name,
            nick=client.nick,
            data={"thread": thread_name, "summary": summary_text},
        ))

    async def _handle_promote(self, client: Client, msg: Message) -> None:
        # THREADCLOSE PROMOTE #channel thread-name [#breakout-name]
        if len(msg.params) < 3:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADCLOSE PROMOTE", "Not enough parameters"
            )
            return

        channel_name = msg.params[1]
        thread_name = msg.params[2]
        custom_breakout = msg.params[3] if len(msg.params) > 3 else None

        # Validate channel membership
        channel = self.server.channels.get(channel_name)
        if not channel or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, "You're not on that channel"
            )
            return

        # Validate thread exists
        key = (channel_name, thread_name)
        thread = self._threads.get(key)
        if not thread:
            await client.send(Message(
                prefix=self.server.config.name,
                command="404",
                params=[client.nick or "*", thread_name, "No such thread"],
            ))
            return

        if thread.archived:
            await client.send(Message(
                prefix=self.server.config.name,
                command="405",
                params=[client.nick or "*", thread_name, "Thread is already closed"],
            ))
            return

        # Authorization: thread creator or channel operators
        if client.nick != thread.creator and not channel.is_operator(client):
            await client.send_numeric(
                replies.ERR_CHANOPRIVSNEEDED, channel_name, "Not authorized to promote this thread"
            )
            return

        # Determine breakout channel name
        channel_base = channel_name  # e.g. "#general"
        breakout_name = custom_breakout or f"{channel_base}-{thread_name}"

        # Create breakout channel
        breakout = self.server.get_or_create_channel(breakout_name)
        breakout.topic = f"Promoted from thread '{thread_name}' in {channel_name}"
        breakout.extra_meta["thread_parent"] = channel_name
        breakout.extra_meta["thread_name"] = thread_name

        # Gather participants (unique nicks who posted in the thread)
        from agentirc.server.remote_client import RemoteClient
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
            join_msg = Message(
                prefix=member.prefix, command="JOIN", params=[breakout_name]
            )
            for other in list(breakout.members):
                if not isinstance(other, RemoteClient):
                    await other.send(join_msg)

        # Replay thread history as NOTICE messages
        for tmsg in thread.messages:
            replay = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[breakout_name, f"[history] <{tmsg.nick}> {tmsg.text}"],
            )
            for member in list(breakout.members):
                if not isinstance(member, RemoteClient):
                    await member.send(replay)

        # Archive original thread
        thread.archived = True
        thread.summary = f"Promoted to {breakout_name}"

        # Post promotion notice to parent channel
        notice = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=[channel_name, f"[thread:{thread_name}] promoted to {breakout_name}"],
        )
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(notice)

        # Emit event
        await self.server.emit_event(Event(
            type=EventType.THREAD_CLOSE,
            channel=channel_name,
            nick=client.nick,
            data={
                "thread": thread_name,
                "promoted": True,
                "breakout": breakout_name,
            },
        ))

    # ---- Public helpers ------------------------------------------------------

    def get_thread(self, channel: str, name: str) -> Thread | None:
        return self._threads.get((channel, name))

    def get_thread_messages(self, channel: str, name: str,
                            limit: int = 50) -> list[ThreadMessage]:
        thread = self._threads.get((channel, name))
        if thread is None:
            return []
        return thread.messages[-limit:]
