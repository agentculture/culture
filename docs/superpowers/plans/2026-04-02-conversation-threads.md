# Conversation Threads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conversation threads (inline sub-conversations) and breakout channel promotion to Culture, with graceful degradation for standard IRC clients, thread-scoped agent context, and full federation support.

**Architecture:** New `ThreadsSkill` server-side skill handles 3 new protocol commands (`THREAD`, `THREADS`, `THREADCLOSE`). Thread replies are delivered as standard `PRIVMSG` with `[thread:name]` prefix for backward compatibility. Agent clients get thread-aware buffering and scoped mention context. Federation relays thread lifecycle via new S2S verbs.

**Tech Stack:** Python 3.12+, asyncio, pytest + pytest-asyncio, dataclasses, JSON persistence

**Spec:** `docs/superpowers/specs/2026-04-02-conversation-threads-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `culture/protocol/commands.py` | Add THREAD, THREADS, THREADCLOSE, STHREAD, STHREADCLOSE verb constants |
| `culture/server/skill.py` | Add THREAD_CREATE, THREAD_MESSAGE, THREAD_CLOSE to EventType enum |
| `culture/server/skills/threads.py` | **New** — ThreadsSkill: thread state, command handlers, persistence |
| `culture/server/thread_store.py` | **New** — JSON disk persistence for threads (same pattern as room_store.py) |
| `culture/server/ircd.py` | Register ThreadsSkill in `_register_default_skills` |
| `culture/server/server_link.py` | Handle STHREAD/STHREADCLOSE in relay_event + inbound handlers |
| `culture/clients/*/message_buffer.py` | Add `thread` field to BufferedMessage, add `read_thread()` method |
| `culture/clients/*/irc_transport.py` | Add thread send methods, parse `[thread:name]` from incoming PRIVMSG |
| `culture/clients/*/daemon.py` | Thread-scoped mention context, thread IPC handlers |
| `culture/protocol/extensions/threads.md` | **New** — Protocol extension documentation |
| `tests/test_threads.py` | **New** — Server-side thread tests |
| `tests/test_thread_buffer.py` | **New** — Client-side buffer thread tests |
| `docs/threads.md` | **New** — User-facing documentation |

---

### Task 1: Protocol Constants & Event Types

**Files:**

- Modify: `culture/protocol/commands.py`
- Modify: `culture/server/skill.py`

No tests needed — these are just string/enum constants consumed by later tasks.

- [ ] **Step 1: Add thread command verbs to commands.py**

Add after the `HISTORY = "HISTORY"` line (line 19) in `culture/protocol/commands.py`:

```python
# Thread extensions
THREAD = "THREAD"
THREADS = "THREADS"
THREADCLOSE = "THREADCLOSE"
THREADSEND = "THREADSEND"
```

Add after `BACKFILLEND = "BACKFILLEND"` (line 33):

```python
STHREAD = "STHREAD"
STHREADCLOSE = "STHREADCLOSE"
```

- [ ] **Step 2: Add thread event types to EventType enum**

Add three values to the `EventType` enum in `culture/server/skill.py` (after line 22, the `ROOMARCHIVE` entry):

```python
    THREAD_CREATE = "thread_create"
    THREAD_MESSAGE = "thread_message"
    THREAD_CLOSE = "thread_close"
```

- [ ] **Step 3: Commit**

```bash
git add culture/protocol/commands.py culture/server/skill.py
git commit -m "feat(threads): add protocol constants and event types for conversation threads"
```

---

### Task 2: ThreadsSkill — Core Data Model & CREATE Command

**Files:**

- Create: `culture/server/skills/threads.py`
- Create: `tests/test_threads.py`

- [ ] **Step 1: Write failing test — thread creation delivers prefixed PRIVMSG**

Create `tests/test_threads.py`:

```python
# tests/test_threads.py
import asyncio
import pytest


@pytest.mark.asyncio
async def test_thread_create_delivers_prefixed_privmsg(server, make_client):
    """THREAD CREATE should deliver a [thread:name] prefixed PRIVMSG to channel members."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)  # drain bob's join

    await alice.send("THREAD CREATE #general auth-refactor :Let's refactor auth")
    response = await bob.recv(timeout=2.0)
    assert "PRIVMSG" in response
    assert "#general" in response
    assert "[thread:auth-refactor]" in response
    assert "Let's refactor auth" in response


@pytest.mark.asyncio
async def test_thread_create_duplicate_name_errors(server, make_client):
    """Creating a thread with a name that already exists should return an error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general my-thread :first message")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general my-thread :duplicate")
    response = await alice.recv(timeout=2.0)
    assert "400" in response or "already exists" in response.lower()


@pytest.mark.asyncio
async def test_thread_create_not_on_channel_errors(server, make_client):
    """THREAD CREATE on a channel you haven't joined should error."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #nochannel my-thread :hello")
    response = await alice.recv(timeout=2.0)
    assert "442" in response
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_threads.py -v
```

Expected: FAIL — `THREAD` command not recognized (ERR_UNKNOWNCOMMAND).

- [ ] **Step 3: Create ThreadsSkill with CREATE handler**

Create `culture/server/skills/threads.py`:

```python
# server/skills/threads.py
"""Conversation threads — inline sub-conversations anchored to channels."""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from culture.protocol.message import Message
from culture.protocol import replies
from culture.server.skill import Event, EventType, Skill

if TYPE_CHECKING:
    from culture.server.client import Client

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

    def __init__(self, max_messages: int = 500):
        self.max_messages = max_messages
        self._threads: dict[tuple[str, str], Thread] = {}  # (channel, name) -> Thread

    async def on_command(self, client: Client, msg: Message) -> None:
        if msg.command == "THREAD":
            await self._handle_thread(client, msg)
        elif msg.command == "THREADS":
            await self._handle_threads(client, msg)
        elif msg.command == "THREADCLOSE":
            await self._handle_threadclose(client, msg)

    async def _handle_thread(self, client: Client, msg: Message) -> None:
        if len(msg.params) < 4:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREAD", "Not enough parameters"
            )
            return

        subcmd = msg.params[0].upper()
        channel_name = msg.params[1]
        thread_name = msg.params[2]
        text = msg.params[3]

        # Validate channel membership
        channel = self.server.channels.get(channel_name)
        if channel is None or client not in channel.members:
            await client.send_numeric(
                replies.ERR_NOTONCHANNEL, channel_name, "You're not on that channel"
            )
            return

        # Validate thread name format
        if not _THREAD_NAME_RE.match(thread_name):
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick,
                        "Invalid thread name (alphanumeric + hyphens, 1-32 chars)"],
            ))
            return

        if subcmd == "CREATE":
            await self._create_thread(client, channel, channel_name, thread_name, text)
        elif subcmd == "REPLY":
            await self._reply_thread(client, channel, channel_name, thread_name, text)
        else:
            await client.send(Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[client.nick, f"Unknown THREAD subcommand: {subcmd}"],
            ))

    async def _create_thread(
        self, client: Client, channel, channel_name: str,
        thread_name: str, text: str,
    ) -> None:
        key = (channel_name, thread_name)
        if key in self._threads:
            await client.send(Message(
                prefix=self.server.config.name,
                command="400",
                params=[client.nick, thread_name, "Thread already exists"],
            ))
            return

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
            nick=client.nick, text=text, timestamp=now, seq=seq,
        ))
        self._threads[key] = thread

        # Deliver as prefixed PRIVMSG to channel (backward compatible)
        prefixed = f"[thread:{thread_name}] {text}"
        relay = Message(
            prefix=client.prefix,
            command="PRIVMSG",
            params=[channel_name, prefixed],
        )
        from culture.server.remote_client import RemoteClient
        for member in list(channel.members):
            if member is not client and not isinstance(member, RemoteClient):
                await member.send(relay)

        # Emit event for skills + federation
        await self.server.emit_event(Event(
            type=EventType.THREAD_CREATE,
            channel=channel_name,
            nick=client.nick,
            data={"text": prefixed, "thread": thread_name, "raw_text": text},
            timestamp=now,
        ))

    async def _reply_thread(
        self, client: Client, channel, channel_name: str,
        thread_name: str, text: str,
    ) -> None:
        key = (channel_name, thread_name)
        thread = self._threads.get(key)
        if thread is None:
            await client.send(Message(
                prefix=self.server.config.name,
                command="404",
                params=[client.nick, thread_name, "No such thread"],
            ))
            return
        if thread.archived:
            await client.send(Message(
                prefix=self.server.config.name,
                command="405",
                params=[client.nick, thread_name, "Thread is closed"],
            ))
            return

        now = time.time()
        seq = self.server.next_seq()
        thread.messages.append(ThreadMessage(
            nick=client.nick, text=text, timestamp=now, seq=seq,
        ))
        # Cap thread messages
        if len(thread.messages) > thread.max_messages:
            thread.messages = thread.messages[-thread.max_messages:]

        # Deliver as prefixed PRIVMSG
        prefixed = f"[thread:{thread_name}] {text}"
        relay = Message(
            prefix=client.prefix,
            command="PRIVMSG",
            params=[channel_name, prefixed],
        )
        from culture.server.remote_client import RemoteClient
        for member in list(channel.members):
            if member is not client and not isinstance(member, RemoteClient):
                await member.send(relay)

        await self.server.emit_event(Event(
            type=EventType.THREAD_MESSAGE,
            channel=channel_name,
            nick=client.nick,
            data={"text": prefixed, "thread": thread_name, "raw_text": text},
            timestamp=now,
        ))

    async def _handle_threads(self, client: Client, msg: Message) -> None:
        """List active (non-archived) threads in a channel."""
        if len(msg.params) < 1:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADS", "Not enough parameters"
            )
            return

        channel_name = msg.params[0]
        for key, thread in self._threads.items():
            if key[0] == channel_name and not thread.archived:
                await client.send(Message(
                    prefix=self.server.config.name,
                    command="THREADS",
                    params=[channel_name, thread.name,
                            f"{thread.creator} {len(thread.messages)} "
                            f"{int(thread.created_at)}"],
                ))
        await client.send(Message(
            prefix=self.server.config.name,
            command="THREADSEND",
            params=[channel_name, "End of thread list"],
        ))

    async def _handle_threadclose(self, client: Client, msg: Message) -> None:
        """Close or promote a thread."""
        if len(msg.params) < 2:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADCLOSE", "Not enough parameters"
            )
            return

        first = msg.params[0].upper()
        if first == "PROMOTE":
            await self._promote_thread(client, msg)
            return

        # Regular close: THREADCLOSE <channel> <thread-name> :<summary>
        channel_name = msg.params[0]
        thread_name = msg.params[1]
        summary = msg.params[2] if len(msg.params) >= 3 else ""

        key = (channel_name, thread_name)
        thread = self._threads.get(key)
        if thread is None:
            await client.send(Message(
                prefix=self.server.config.name,
                command="404",
                params=[client.nick, thread_name, "No such thread"],
            ))
            return

        # Authorization: thread participants or channel operators
        channel = self.server.channels.get(channel_name)
        is_participant = client.nick in thread.participants or client.nick == thread.creator
        is_op = channel is not None and channel.is_operator(client)
        if not is_participant and not is_op:
            await client.send(Message(
                prefix=self.server.config.name,
                command=replies.ERR_CHANOPRIVSNEEDED,
                params=[client.nick, channel_name,
                        "You're not a thread participant or channel operator"],
            ))
            return

        thread.archived = True
        thread.summary = summary

        # Post summary notice to parent channel
        count = len(thread.messages)
        participants = len(thread.participants)
        notice_text = (
            f"[Thread {thread_name} closed] "
            f"Summary: {summary} ({participants} participants, {count} messages)"
            if summary else
            f"[Thread {thread_name} closed] "
            f"({participants} participants, {count} messages)"
        )
        if channel:
            notice = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[channel_name, notice_text],
            )
            from culture.server.remote_client import RemoteClient
            for member in list(channel.members):
                if not isinstance(member, RemoteClient):
                    await member.send(notice)

        await self.server.emit_event(Event(
            type=EventType.THREAD_CLOSE,
            channel=channel_name,
            nick=client.nick,
            data={"thread": thread_name, "summary": summary},
        ))

    async def _promote_thread(self, client: Client, msg: Message) -> None:
        """THREADCLOSE PROMOTE <channel> <thread-name> [breakout-name]"""
        if len(msg.params) < 3:
            await client.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "THREADCLOSE", "Not enough parameters"
            )
            return

        channel_name = msg.params[1]
        thread_name = msg.params[2]
        breakout_name = msg.params[3] if len(msg.params) >= 4 else None

        key = (channel_name, thread_name)
        thread = self._threads.get(key)
        if thread is None:
            await client.send(Message(
                prefix=self.server.config.name,
                command="404",
                params=[client.nick, thread_name, "No such thread"],
            ))
            return

        # Authorization: creator or channel operators
        channel = self.server.channels.get(channel_name)
        is_creator = client.nick == thread.creator
        is_op = channel is not None and channel.is_operator(client)
        if not is_creator and not is_op:
            await client.send(Message(
                prefix=self.server.config.name,
                command=replies.ERR_CHANOPRIVSNEEDED,
                params=[client.nick, channel_name,
                        "You're not a thread participant or channel operator"],
            ))
            return

        # Create breakout channel
        if not breakout_name:
            # Strip leading # from channel_name for the breakout
            base = channel_name.lstrip("#")
            breakout_name = f"#{base}-{thread_name}"

        breakout = self.server.get_or_create_channel(breakout_name)
        breakout.topic = f"Breakout from {channel_name} thread {thread_name}"
        breakout.extra_meta["thread_parent"] = channel_name
        breakout.extra_meta["thread_name"] = thread_name

        # Auto-join thread participants who are in the parent channel
        if channel:
            from culture.server.remote_client import RemoteClient
            for member in list(channel.members):
                if isinstance(member, RemoteClient):
                    continue
                if member.nick in thread.participants or member.nick == thread.creator:
                    breakout.add(member)
                    member.channels.add(breakout)
                    # Send JOIN notification
                    join_msg = Message(prefix=member.prefix, command="JOIN",
                                      params=[breakout_name])
                    for bm in list(breakout.members):
                        if not isinstance(bm, RemoteClient):
                            await bm.send(join_msg)

            # Replay thread history as NOTICEs
            for tm in thread.messages:
                replay = Message(
                    prefix=self.server.config.name,
                    command="NOTICE",
                    params=[breakout_name, f"<{tm.nick}> {tm.text}"],
                )
                for bm in list(breakout.members):
                    if not isinstance(bm, RemoteClient):
                        await bm.send(replay)

        # Archive the thread
        thread.archived = True
        thread.summary = f"Promoted to {breakout_name}"

        # Notify parent channel
        count = len(thread.messages)
        participants = len(thread.participants)
        notice_text = (
            f"Thread {thread_name} promoted to {breakout_name} "
            f"({count} messages, {participants} participants)"
        )
        if channel:
            notice = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[channel_name, notice_text],
            )
            from culture.server.remote_client import RemoteClient
            for member in list(channel.members):
                if not isinstance(member, RemoteClient):
                    await member.send(notice)

        await self.server.emit_event(Event(
            type=EventType.THREAD_CLOSE,
            channel=channel_name,
            nick=client.nick,
            data={"thread": thread_name, "summary": thread.summary,
                  "promoted_to": breakout_name},
        ))

    def get_thread(self, channel: str, name: str) -> Thread | None:
        return self._threads.get((channel, name))

    def get_thread_messages(self, channel: str, name: str,
                            limit: int = 50) -> list[ThreadMessage]:
        thread = self._threads.get((channel, name))
        if thread is None:
            return []
        return thread.messages[-limit:]
```

- [ ] **Step 4: Register ThreadsSkill in IRCd**

In `culture/server/ircd.py`, add to `_register_default_skills` (after line 48):

```python
        from culture.server.skills.threads import ThreadsSkill
        await self.register_skill(ThreadsSkill())
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_threads.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add culture/server/skills/threads.py culture/server/ircd.py tests/test_threads.py
git commit -m "feat(threads): add ThreadsSkill with CREATE, REPLY, and error handling"
```

---

### Task 3: ThreadsSkill — REPLY, THREADS List, and THREADCLOSE

**Files:**

- Modify: `tests/test_threads.py`

The implementation is already in Task 2. This task adds tests for the remaining commands.

- [ ] **Step 1: Write tests for REPLY, THREADS, and THREADCLOSE**

Append to `tests/test_threads.py`:

```python
@pytest.mark.asyncio
async def test_thread_reply_delivers_prefixed_privmsg(server, make_client):
    """THREAD REPLY should deliver to channel with [thread:name] prefix."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    # Create thread
    await alice.send("THREAD CREATE #general auth-refactor :Let's refactor auth")
    await bob.recv(timeout=2.0)  # consume create message

    # Reply
    await bob.send("THREAD REPLY #general auth-refactor :I'll take token refresh")
    response = await alice.recv(timeout=2.0)
    assert "PRIVMSG" in response
    assert "[thread:auth-refactor]" in response
    assert "I'll take token refresh" in response


@pytest.mark.asyncio
async def test_thread_reply_to_nonexistent_thread_errors(server, make_client):
    """Replying to a thread that doesn't exist should return 404."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD REPLY #general no-such-thread :hello")
    response = await alice.recv(timeout=2.0)
    assert "404" in response


@pytest.mark.asyncio
async def test_thread_reply_to_archived_thread_errors(server, make_client):
    """Replying to a closed thread should return 405."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general done-thread :starting")
    await alice.recv_all(timeout=0.5)
    await alice.send("THREADCLOSE #general done-thread :all done")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD REPLY #general done-thread :too late")
    response = await alice.recv(timeout=2.0)
    assert "405" in response


@pytest.mark.asyncio
async def test_threads_list(server, make_client):
    """THREADS should list active threads in the channel."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general thread-a :first thread")
    await alice.recv_all(timeout=0.5)
    await alice.send("THREAD CREATE #general thread-b :second thread")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREADS #general")
    lines = await alice.recv_all(timeout=1.0)

    thread_names = [l for l in lines if "THREADS" in l and "THREADSEND" not in l]
    assert len(thread_names) == 2
    assert any("thread-a" in l for l in thread_names)
    assert any("thread-b" in l for l in thread_names)
    assert any("THREADSEND" in l for l in lines)


@pytest.mark.asyncio
async def test_threadclose_posts_summary(server, make_client):
    """THREADCLOSE should archive the thread and post a summary NOTICE."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general my-thread :starting work")
    await bob.recv(timeout=2.0)

    await alice.send("THREADCLOSE #general my-thread :Work completed successfully")
    response = await bob.recv(timeout=2.0)
    assert "NOTICE" in response
    assert "Thread my-thread closed" in response
    assert "Work completed successfully" in response


@pytest.mark.asyncio
async def test_threadclose_archived_thread_not_listed(server, make_client):
    """Closed threads should not appear in THREADS listing."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general temp-thread :temporary")
    await alice.recv_all(timeout=0.5)
    await alice.send("THREADCLOSE #general temp-thread :done")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREADS #general")
    lines = await alice.recv_all(timeout=1.0)
    thread_lines = [l for l in lines if "THREADS" in l and "THREADSEND" not in l]
    assert len(thread_lines) == 0
```

- [ ] **Step 2: Run tests**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_threads.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_threads.py
git commit -m "test(threads): add tests for REPLY, THREADS list, and THREADCLOSE"
```

---

### Task 4: Breakout Channel Promotion

**Files:**

- Modify: `tests/test_threads.py`

- [ ] **Step 1: Write tests for THREADCLOSE PROMOTE**

Append to `tests/test_threads.py`:

```python
@pytest.mark.asyncio
async def test_threadclose_promote_creates_breakout(server, make_client):
    """THREADCLOSE PROMOTE should create a breakout channel and auto-join participants."""
    alice = await make_client(nick="testserv-alice", user="alice")
    bob = await make_client(nick="testserv-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)

    # Create thread with two participants
    await alice.send("THREAD CREATE #general big-topic :Let's discuss")
    await bob.recv(timeout=2.0)
    await bob.send("THREAD REPLY #general big-topic :I'm in")
    await alice.recv(timeout=2.0)

    # Promote
    await alice.send("THREADCLOSE PROMOTE #general big-topic")
    # Both should receive JOIN for breakout + history replay + promotion notice
    alice_lines = await alice.recv_all(timeout=2.0)
    bob_lines = await bob.recv_all(timeout=2.0)

    # Check breakout channel was created (default name #general-big-topic)
    assert any("JOIN" in l and "#general-big-topic" in l for l in alice_lines)
    assert any("JOIN" in l and "#general-big-topic" in l for l in bob_lines)

    # Check promotion notice in parent channel
    all_lines = alice_lines + bob_lines
    assert any("promoted to #general-big-topic" in l.lower() for l in all_lines)


@pytest.mark.asyncio
async def test_threadclose_promote_replays_history(server, make_client):
    """Promoted breakout should receive thread history as NOTICEs."""
    alice = await make_client(nick="testserv-alice", user="alice")
    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREAD CREATE #general replay-test :Message one")
    await alice.recv_all(timeout=0.5)
    await alice.send("THREAD REPLY #general replay-test :Message two")
    await alice.recv_all(timeout=0.5)

    await alice.send("THREADCLOSE PROMOTE #general replay-test")
    lines = await alice.recv_all(timeout=2.0)

    # Should see history replay as NOTICEs in the breakout
    notices = [l for l in lines if "NOTICE" in l and "#general-replay-test" in l]
    assert len(notices) >= 2  # At least the 2 thread messages replayed
    assert any("Message one" in n for n in notices)
    assert any("Message two" in n for n in notices)
```

- [ ] **Step 2: Run tests**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_threads.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_threads.py
git commit -m "test(threads): add breakout promotion tests"
```

---

### Task 5: Thread Persistence

**Files:**

- Create: `culture/server/thread_store.py`
- Modify: `culture/server/skills/threads.py`
- Modify: `tests/test_threads.py`

- [ ] **Step 1: Write failing test — threads persist across restart**

Append to `tests/test_threads.py`:

```python
import tempfile
from culture.server.config import ServerConfig
from culture.server.ircd import IRCd


@pytest.mark.asyncio
async def test_threads_persist_across_restart():
    """Threads should survive server restart when data_dir is configured."""
    with tempfile.TemporaryDirectory() as data_dir:
        config = ServerConfig(name="testserv", host="127.0.0.1", port=0,
                              data_dir=data_dir)

        # Start server, create a thread
        ircd = IRCd(config)
        await ircd.start()
        port = ircd._server.sockets[0].getsockname()[1]
        ircd.config.port = port

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        from tests.conftest import IRCTestClient
        alice = IRCTestClient(reader, writer)
        await alice.send("NICK testserv-alice")
        await alice.send("USER alice 0 * :alice")
        await alice.recv_all(timeout=0.5)
        await alice.send("JOIN #general")
        await alice.recv_all(timeout=0.5)
        await alice.send("THREAD CREATE #general persist-test :Hello")
        await alice.recv_all(timeout=0.5)

        await alice.close()
        await ircd.stop()

        # Restart server
        ircd2 = IRCd(config)
        await ircd2.start()
        port2 = ircd2._server.sockets[0].getsockname()[1]
        ircd2.config.port = port2

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port2)
        bob = IRCTestClient(reader2, writer2)
        await bob.send("NICK testserv-bob")
        await bob.send("USER bob 0 * :bob")
        await bob.recv_all(timeout=0.5)
        await bob.send("JOIN #general")
        await bob.recv_all(timeout=0.5)

        # Thread should still exist
        await bob.send("THREADS #general")
        lines = await bob.recv_all(timeout=1.0)
        thread_lines = [l for l in lines if "THREADS" in l and "THREADSEND" not in l]
        assert any("persist-test" in l for l in thread_lines)

        await bob.close()
        await ircd2.stop()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_threads.py::test_threads_persist_across_restart -v
```

Expected: FAIL — no persistence yet.

- [ ] **Step 3: Create ThreadStore**

Create `culture/server/thread_store.py`:

```python
# server/thread_store.py
"""JSON disk persistence for conversation threads."""
from __future__ import annotations

import json
import re
from pathlib import Path


class ThreadStore:
    def __init__(self, data_dir: str):
        self._threads_dir = Path(data_dir) / "threads"
        self._threads_dir.mkdir(parents=True, exist_ok=True)

    def _safe_key(self, channel: str, name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9\-]", "_", f"{channel}_{name}")
        return safe

    def save(self, thread_data: dict) -> None:
        key = self._safe_key(thread_data["channel"], thread_data["name"])
        path = self._threads_dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(thread_data, f, indent=2)
        tmp.rename(path)

    def delete(self, channel: str, name: str) -> None:
        key = self._safe_key(channel, name)
        path = self._threads_dir / f"{key}.json"
        if path.exists():
            path.unlink()

    def load_all(self) -> list[dict]:
        threads = []
        if not self._threads_dir.exists():
            return threads
        for path in sorted(self._threads_dir.glob("*.json")):
            with open(path) as f:
                threads.append(json.load(f))
        return threads
```

- [ ] **Step 4: Add persistence to ThreadsSkill**

Add these methods to the `ThreadsSkill` class in `culture/server/skills/threads.py`:

After the `__init__` method, add a `start` override:

```python
    async def start(self, server) -> None:
        await super().start(server)
        self._restore_threads()

    def _restore_threads(self) -> None:
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
                thread.messages.append(ThreadMessage(
                    nick=m["nick"], text=m["text"],
                    timestamp=m["timestamp"], seq=m.get("seq", 0),
                ))
            self._threads[(data["channel"], data["name"])] = thread

    def _persist_thread(self, thread: Thread) -> None:
        if not self.server.config.data_dir:
            return
        from culture.server.thread_store import ThreadStore
        store = ThreadStore(self.server.config.data_dir)
        store.save({
            "name": thread.name,
            "channel": thread.channel,
            "creator": thread.creator,
            "created_at": thread.created_at,
            "archived": thread.archived,
            "summary": thread.summary,
            "messages": [
                {"nick": m.nick, "text": m.text,
                 "timestamp": m.timestamp, "seq": m.seq}
                for m in thread.messages
            ],
        })
```

Then add `self._persist_thread(thread)` calls at the end of `_create_thread`, `_reply_thread`, `_handle_threadclose`, and `_promote_thread` — just before the `emit_event` call in each method.

- [ ] **Step 5: Run tests**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_threads.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add culture/server/thread_store.py culture/server/skills/threads.py tests/test_threads.py
git commit -m "feat(threads): add JSON persistence for threads across server restarts"
```

---

### Task 6: Federation — S2S Thread Relay

**Files:**

- Modify: `culture/server/server_link.py`
- Modify: `tests/test_threads.py`

- [ ] **Step 1: Write failing test — thread messages federate**

Append to `tests/test_threads.py`:

```python
@pytest.mark.asyncio
async def test_thread_create_federates(linked_servers, make_client_a, make_client_b):
    """THREAD CREATE on server A should deliver prefixed PRIVMSG to server B."""
    alice = await make_client_a(nick="alpha-alice", user="alice")
    bob = await make_client_b(nick="beta-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)  # federation settle

    await alice.send("THREAD CREATE #general fed-thread :Cross-server thread")
    response = await bob.recv(timeout=3.0)
    assert "PRIVMSG" in response
    assert "[thread:fed-thread]" in response
    assert "Cross-server thread" in response


@pytest.mark.asyncio
async def test_thread_close_federates(linked_servers, make_client_a, make_client_b):
    """THREADCLOSE on server A should deliver summary NOTICE to server B."""
    alice = await make_client_a(nick="alpha-alice", user="alice")
    bob = await make_client_b(nick="beta-bob", user="bob")

    await alice.send("JOIN #general")
    await alice.recv_all(timeout=0.5)
    await bob.send("JOIN #general")
    await bob.recv_all(timeout=0.5)
    await alice.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    await alice.send("THREAD CREATE #general fed-close :Starting")
    await bob.recv(timeout=3.0)

    await alice.send("THREADCLOSE #general fed-close :All done")
    response = await bob.recv(timeout=3.0)
    assert "NOTICE" in response
    assert "Thread fed-close closed" in response
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_threads.py::test_thread_create_federates tests/test_threads.py::test_thread_close_federates -v
```

Expected: FAIL — federation already delivers thread messages as regular PRIVMSG via the MESSAGE event path (the `[thread:name]` prefix is in the text). The CREATE test may actually pass because `emit_event(THREAD_CREATE)` fires but `relay_event` doesn't handle `THREAD_CREATE` yet — it falls through silently. However, the prefixed PRIVMSG is delivered as a regular MESSAGE event since HistorySkill also captures it.

Actually, checking the code: `emit_event` is called for `THREAD_CREATE` but `relay_event` only handles known EventTypes (MESSAGE, JOIN, PART, etc.). The PRIVMSG delivery is local-only (the loop skips RemoteClients). So federation needs the new S2S verbs.

- [ ] **Step 3: Add THREAD_CREATE and THREAD_CLOSE relay to server_link.py**

In `culture/server/server_link.py`, in the `relay_event` method, add handling after the `ROOMARCHIVE` block (around line 701):

```python
        elif event.type == EventType.THREAD_CREATE or event.type == EventType.THREAD_MESSAGE:
            channel_name = event.channel
            if not self.should_relay(channel_name):
                return
            thread_name = event.data.get("thread", "")
            text = event.data.get("text", "")
            await self.send_raw(
                f":{origin} STHREAD {channel_name} {event.nick} {thread_name} :{text}"
            )
        elif event.type == EventType.THREAD_CLOSE:
            channel_name = event.channel
            if not self.should_relay(channel_name):
                return
            thread_name = event.data.get("thread", "")
            summary = event.data.get("summary", "")
            promoted_to = event.data.get("promoted_to", "")
            close_data = summary
            if promoted_to:
                close_data = f"PROMOTE {promoted_to} {summary}"
            await self.send_raw(
                f":{origin} STHREADCLOSE {channel_name} {event.nick} {thread_name} :{close_data}"
            )
```

Also add the `EventType` import if not already present:

```python
from culture.server.skill import Event, EventType
```

And add inbound S2S handlers. Find where `_handle_smsg` is defined and add nearby:

```python
    async def _handle_sthread(self, msg: Message) -> None:
        """Handle inbound S2S STHREAD — deliver thread message to local clients."""
        if len(msg.params) < 4:
            return
        channel_name = msg.params[0]
        sender_nick = msg.params[1]
        thread_name = msg.params[2]
        text = msg.params[3]

        channel = self.server.channels.get(channel_name)
        if channel is None:
            return
        if not self.should_relay(channel_name):
            return

        # Deliver prefixed PRIVMSG to local members
        from culture.server.remote_client import RemoteClient
        relay = Message(
            prefix=f"{sender_nick}!{sender_nick}@{self.peer_name}",
            command="PRIVMSG",
            params=[channel_name, text],  # text already has [thread:name] prefix
        )
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(relay)

        # Emit locally with _origin to prevent re-relay
        await self.server.emit_event(Event(
            type=EventType.THREAD_MESSAGE,
            channel=channel_name,
            nick=sender_nick,
            data={"text": text, "thread": thread_name, "_origin": self.peer_name},
        ))

    async def _handle_sthreadclose(self, msg: Message) -> None:
        """Handle inbound S2S STHREADCLOSE — deliver thread close notice."""
        if len(msg.params) < 4:
            return
        channel_name = msg.params[0]
        sender_nick = msg.params[1]
        thread_name = msg.params[2]
        close_data = msg.params[3]

        channel = self.server.channels.get(channel_name)
        if channel is None:
            return

        # Post close notice to local channel members
        from culture.server.remote_client import RemoteClient
        notice = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=[channel_name, f"[Thread {thread_name} closed] {close_data}"],
        )
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(notice)

        await self.server.emit_event(Event(
            type=EventType.THREAD_CLOSE,
            channel=channel_name,
            nick=sender_nick,
            data={"thread": thread_name, "summary": close_data,
                  "_origin": self.peer_name},
        ))
```

Register these handlers in the S2S command dispatch (find where `_handle_smsg` is dispatched and add):

```python
        elif msg.command == "STHREAD":
            await self._handle_sthread(msg)
        elif msg.command == "STHREADCLOSE":
            await self._handle_sthreadclose(msg)
```

- [ ] **Step 4: Run tests**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_threads.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add culture/server/server_link.py tests/test_threads.py
git commit -m "feat(threads): add S2S federation relay for thread create/reply/close"
```

---

### Task 7: Client MessageBuffer — Thread Awareness

**Files:**

- Modify: `culture/clients/claude/message_buffer.py`
- Create: `tests/test_thread_buffer.py`

- [ ] **Step 1: Write failing tests for thread-aware buffer**

Create `tests/test_thread_buffer.py`:

```python
# tests/test_thread_buffer.py
import pytest
from culture.clients.claude.message_buffer import MessageBuffer


def test_add_parses_thread_prefix():
    """Messages with [thread:name] prefix should have thread field set."""
    buf = MessageBuffer()
    buf.add("#general", "alice", "[thread:auth-refactor] I'll take tokens")
    messages = buf.read("#general")
    assert len(messages) == 1
    assert messages[0].thread == "auth-refactor"
    assert messages[0].text == "[thread:auth-refactor] I'll take tokens"


def test_add_no_thread_prefix():
    """Messages without thread prefix should have thread=None."""
    buf = MessageBuffer()
    buf.add("#general", "alice", "Hello world")
    messages = buf.read("#general")
    assert len(messages) == 1
    assert messages[0].thread is None


def test_read_thread_returns_only_matching():
    """read_thread should return only messages from the specified thread."""
    buf = MessageBuffer()
    buf.add("#general", "alice", "[thread:auth] Message one")
    buf.add("#general", "bob", "Unrelated channel message")
    buf.add("#general", "charlie", "[thread:auth] Message two")
    buf.add("#general", "dave", "[thread:deploy] Different thread")

    auth_msgs = buf.read_thread("#general", "auth")
    assert len(auth_msgs) == 2
    assert auth_msgs[0].nick == "alice"
    assert auth_msgs[1].nick == "charlie"


def test_read_thread_respects_limit():
    """read_thread should respect the limit parameter."""
    buf = MessageBuffer()
    for i in range(10):
        buf.add("#general", "alice", f"[thread:big] Message {i}")

    msgs = buf.read_thread("#general", "big", limit=3)
    assert len(msgs) == 3
    assert "Message 7" in msgs[0].text  # last 3


def test_read_thread_nonexistent_returns_empty():
    """read_thread for a non-existent thread returns empty list."""
    buf = MessageBuffer()
    buf.add("#general", "alice", "no threads here")
    assert buf.read_thread("#general", "nope") == []


def test_read_still_returns_all_messages():
    """Regular read() should still return all messages including threaded."""
    buf = MessageBuffer()
    buf.add("#general", "alice", "[thread:auth] Thread msg")
    buf.add("#general", "bob", "Regular msg")
    messages = buf.read("#general")
    assert len(messages) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_thread_buffer.py -v
```

Expected: FAIL — `BufferedMessage` has no `thread` field, no `read_thread` method.

- [ ] **Step 3: Add thread field and read_thread to MessageBuffer**

Edit `culture/clients/claude/message_buffer.py`:

Add `import re` at the top. Add `thread` field to `BufferedMessage`:

```python
import re

_THREAD_PREFIX_RE = re.compile(r"^\[thread:([a-zA-Z0-9\-]+)\] ")


@dataclass
class BufferedMessage:
    nick: str
    text: str
    timestamp: float
    thread: str | None = None
```

Update the `add` method to parse thread prefix:

```python
    def add(self, channel: str, nick: str, text: str) -> None:
        if channel not in self._buffers:
            self._buffers[channel] = deque(maxlen=self.max_per_channel)
            self._totals[channel] = 0
            self._cursors[channel] = 0

        thread = None
        m = _THREAD_PREFIX_RE.match(text)
        if m:
            thread = m.group(1)

        self._buffers[channel].append(
            BufferedMessage(nick=nick, text=text, timestamp=time.time(), thread=thread)
        )
        self._totals[channel] += 1
```

Add the `read_thread` method:

```python
    def read_thread(self, channel: str, thread_name: str,
                    limit: int = 50) -> list[BufferedMessage]:
        buf = self._buffers.get(channel)
        if not buf:
            return []
        matches = [m for m in buf if m.thread == thread_name]
        if len(matches) > limit:
            matches = matches[-limit:]
        return matches
```

- [ ] **Step 4: Run tests**

```bash
cd /home/spark/git/culture && python -m pytest tests/test_thread_buffer.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add culture/clients/claude/message_buffer.py tests/test_thread_buffer.py
git commit -m "feat(threads): add thread-aware message buffering with read_thread()"
```

---

### Task 8: Client IRCTransport — Thread Send Methods

**Files:**

- Modify: `culture/clients/claude/irc_transport.py`

- [ ] **Step 1: Add thread transport methods**

Add these methods to the `IRCTransport` class in `culture/clients/claude/irc_transport.py`, after `send_privmsg` (line 74):

```python
    async def send_thread_create(self, channel: str, thread_name: str, text: str) -> None:
        await self._send_raw(f"THREAD CREATE {channel} {thread_name} :{text}")

    async def send_thread_reply(self, channel: str, thread_name: str, text: str) -> None:
        await self._send_raw(f"THREAD REPLY {channel} {thread_name} :{text}")

    async def send_thread_close(self, channel: str, thread_name: str, summary: str) -> None:
        await self._send_raw(f"THREADCLOSE {channel} {thread_name} :{summary}")

    async def send_threads_list(self, channel: str) -> None:
        await self._send_raw(f"THREADS {channel}")
```

- [ ] **Step 2: Commit**

```bash
git add culture/clients/claude/irc_transport.py
git commit -m "feat(threads): add thread send methods to IRCTransport"
```

---

### Task 9: Client Daemon — Thread-Scoped Mention Context & IPC

**Files:**

- Modify: `culture/clients/claude/daemon.py`

- [ ] **Step 1: Update _on_mention for thread-scoped context**

Replace the `_on_mention` method (lines 239-252) in `culture/clients/claude/daemon.py`:

```python
    def _on_mention(self, target: str, sender: str, text: str) -> None:
        """Called by IRCTransport when the agent is @mentioned or DM'd.

        Formats a prompt and enqueues it so the SDK session picks it up.
        When the mention is inside a thread, provides thread-scoped context.
        """
        if self._paused:
            return
        if self._agent_runner and self._agent_runner.is_running():
            self._last_activation = time.time()
            if target.startswith("#"):
                # Check for thread context
                import re
                thread_match = re.match(r"^\[thread:([a-zA-Z0-9\-]+)\] ", text)
                if thread_match and self._buffer:
                    thread_name = thread_match.group(1)
                    thread_msgs = self._buffer.read_thread(target, thread_name)
                    history = "\n".join(
                        f"  <{m.nick}> {m.text}" for m in thread_msgs
                    )
                    prompt = (
                        f"[IRC @mention in {target}, thread:{thread_name}]\n"
                        f"Thread history:\n{history}\n"
                        f"  <{sender}> {text}"
                    )
                else:
                    prompt = f"[IRC @mention in {target}] <{sender}> {text}"
            else:
                prompt = f"[IRC DM] <{sender}> {text}"
            asyncio.create_task(self._agent_runner.send_prompt(prompt))
```

- [ ] **Step 2: Add thread IPC handlers**

In the `_handle_ipc` method (around line 437), add new handlers before the `else` fallthrough:

```python
            elif msg_type == "irc_thread_create":
                return await self._ipc_irc_thread_create(req_id, msg)

            elif msg_type == "irc_thread_reply":
                return await self._ipc_irc_thread_reply(req_id, msg)

            elif msg_type == "irc_threads":
                return await self._ipc_irc_threads(req_id, msg)

            elif msg_type == "irc_thread_close":
                return await self._ipc_irc_thread_close(req_id, msg)

            elif msg_type == "irc_thread_read":
                return await self._ipc_irc_thread_read(req_id, msg)
```

Add the handler methods (after `_ipc_irc_part`):

```python
    async def _ipc_irc_thread_create(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        text = msg.get("message", "")
        if not channel or not thread_name or not text:
            return make_response(req_id, ok=False,
                                 error="Missing 'channel', 'thread', or 'message'")
        assert self._transport is not None
        await self._transport.send_thread_create(channel, thread_name, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_reply(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        text = msg.get("message", "")
        if not channel or not thread_name or not text:
            return make_response(req_id, ok=False,
                                 error="Missing 'channel', 'thread', or 'message'")
        assert self._transport is not None
        await self._transport.send_thread_reply(channel, thread_name, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_threads(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        assert self._transport is not None
        await self._transport.send_threads_list(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_close(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        summary = msg.get("summary", "")
        if not channel or not thread_name:
            return make_response(req_id, ok=False,
                                 error="Missing 'channel' or 'thread'")
        assert self._transport is not None
        await self._transport.send_thread_close(channel, thread_name, summary)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_read(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        limit = int(msg.get("limit", 50))
        if not channel or not thread_name:
            return make_response(req_id, ok=False,
                                 error="Missing 'channel' or 'thread'")
        assert self._buffer is not None
        messages = self._buffer.read_thread(channel, thread_name, limit=limit)
        return make_response(req_id, ok=True, data={
            "messages": [
                {"nick": m.nick, "text": m.text, "timestamp": m.timestamp,
                 "thread": m.thread}
                for m in messages
            ]
        })
```

- [ ] **Step 3: Commit**

```bash
git add culture/clients/claude/daemon.py
git commit -m "feat(threads): add thread-scoped mention context and IPC handlers to daemon"
```

---

### Task 10: Propagate Client Changes to All Backends

**Files:**

- Modify: `culture/clients/acp/message_buffer.py`
- Modify: `culture/clients/codex/message_buffer.py`
- Modify: `culture/clients/copilot/message_buffer.py`
- Modify: `culture/clients/acp/irc_transport.py`
- Modify: `culture/clients/codex/irc_transport.py`
- Modify: `culture/clients/copilot/irc_transport.py`
- Modify: `culture/clients/acp/daemon.py`
- Modify: `culture/clients/codex/daemon.py`
- Modify: `culture/clients/copilot/daemon.py`
- Modify: `packages/agent-harness/message_buffer.py`
- Modify: `packages/agent-harness/irc_transport.py`
- Modify: `packages/agent-harness/daemon.py`

The all-backends rule requires identical changes across all backends. For each backend (`acp`, `codex`, `copilot`) and the reference template (`packages/agent-harness/`):

- [ ] **Step 1: Copy message_buffer.py changes to all backends**

Apply the same changes from Task 7 (thread field on BufferedMessage, `_THREAD_PREFIX_RE`, updated `add()`, new `read_thread()`) to:

- `culture/clients/acp/message_buffer.py`
- `culture/clients/codex/message_buffer.py`
- `culture/clients/copilot/message_buffer.py`
- `packages/agent-harness/message_buffer.py`

Compare each file first — they should be nearly identical to the claude version. Apply the same diff.

- [ ] **Step 2: Copy irc_transport.py changes to all backends**

Apply Task 8 changes (thread send methods) to:

- `culture/clients/acp/irc_transport.py`
- `culture/clients/codex/irc_transport.py`
- `culture/clients/copilot/irc_transport.py`
- `packages/agent-harness/irc_transport.py`

- [ ] **Step 3: Copy daemon.py changes to all backends**

Apply Task 9 changes (thread-scoped `_on_mention`, thread IPC handlers) to:

- `culture/clients/acp/daemon.py`
- `culture/clients/codex/daemon.py`
- `culture/clients/copilot/daemon.py`
- `packages/agent-harness/daemon.py`

Note: Each backend's daemon may have slightly different structure. Read each file first to find the right insertion points. The `_on_mention` method and IPC dispatch pattern should be the same.

- [ ] **Step 4: Run full test suite**

```bash
cd /home/spark/git/culture && python -m pytest -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add culture/clients/acp/ culture/clients/codex/ culture/clients/copilot/ packages/agent-harness/
git commit -m "feat(threads): propagate thread support to all agent backends (all-backends rule)"
```

---

### Task 11: Protocol Extension Documentation

**Files:**

- Create: `culture/protocol/extensions/threads.md`

- [ ] **Step 1: Write the protocol extension doc**

Create `culture/protocol/extensions/threads.md` following the pattern of existing extensions (`history.md`, `rooms.md`):

```markdown
---
title: Conversation Threads
nav_order: 4
---

# Conversation Threads Extension

**Status:** Draft

## Overview

Adds inline conversation threads to channels. Threads are lightweight
sub-conversations anchored to a channel, with optional promotion to breakout
channels.

Thread messages are delivered as standard PRIVMSG with a `[thread:name]`
prefix for backward compatibility with clients that don't understand thread
commands.

## Commands

### THREAD CREATE

Create a new thread in a channel.

    THREAD CREATE <channel> <thread-name> :<message>

- `thread-name`: 1-32 chars, alphanumeric + hyphens
- Server delivers as: `:nick PRIVMSG <channel> :[thread:<name>] <message>`

### THREAD REPLY

Reply to an existing thread.

    THREAD REPLY <channel> <thread-name> :<message>

- Thread must exist and not be archived

### THREADS

List active threads in a channel.

    THREADS <channel>

Reply format (one per thread, then end marker):

    :server THREADS <channel> <thread-name> :<creator> <msg-count> <created-ts>
    :server THREADSEND <channel> :End of thread list

### THREADCLOSE

Close a thread with a summary.

    THREADCLOSE <channel> <thread-name> :<summary>

Authorization: thread participants or channel operators.

### THREADCLOSE PROMOTE

Promote a thread to a breakout channel.

    THREADCLOSE PROMOTE <channel> <thread-name> [breakout-name]

Authorization: thread creator or channel operators.
Default breakout name: `#<channel-base>-<thread-name>`

## Wire Examples

    >> THREAD CREATE #general auth-refactor :Let's refactor auth
    << :alice!alice@localhost PRIVMSG #general :[thread:auth-refactor] Let's refactor auth

    >> THREAD REPLY #general auth-refactor :I'll take token refresh
    << :bob!bob@localhost PRIVMSG #general :[thread:auth-refactor] I'll take token refresh

    >> THREADS #general
    << :server THREADS #general auth-refactor :alice 2 1711987200
    << :server THREADSEND #general :End of thread list

    >> THREADCLOSE #general auth-refactor :Completed refactor
    << :server NOTICE #general :[Thread auth-refactor closed] Summary: Completed refactor (2 participants, 2 messages)

    >> THREADCLOSE PROMOTE #general big-topic
    << :server NOTICE #general :Thread big-topic promoted to #general-big-topic (5 messages, 3 participants)

## Error Codes

| Code | Condition |
|------|-----------|
| 400 | Thread name already exists |
| 404 | Thread not found |
| 405 | Thread is closed/archived |
| 442 | Not on channel |
| 461 | Not enough parameters |
| 482 | Not authorized |

## S2S Federation

    STHREAD <channel> <nick> <thread-name> :<prefixed-text>
    STHREADCLOSE <channel> <nick> <thread-name> :<summary>

## Notes

- Thread messages also appear in regular channel history (HistorySkill captures
  the PRIVMSG delivery)
- Breakout channels are leaf nodes — threads inside breakouts cannot be promoted
- Thread state persists to disk when data_dir is configured
```

- [ ] **Step 2: Commit**

```bash
git add culture/protocol/extensions/threads.md
git commit -m "docs: add conversation threads protocol extension documentation"
```

---

### Task 12: User-Facing Documentation

**Files:**

- Create: `docs/threads.md`

- [ ] **Step 1: Write user-facing docs**

Create `docs/threads.md`:

```markdown
# Conversation Threads

Conversation threads let you branch side-discussions from a channel without
leaving it. Threads keep focused conversations together while the main channel
continues.

## Quick Start

**Start a thread:**

    THREAD CREATE #general auth-refactor :Let's discuss the auth refactor

**Reply to a thread:**

    THREAD REPLY #general auth-refactor :I'll handle the token refresh part

**List active threads:**

    THREADS #general

**Close a thread:**

    THREADCLOSE #general auth-refactor :Completed - merged in PR #42

## How It Looks

In any IRC client (weechat, irssi, etc.), thread messages appear with a prefix:

    <alice> [thread:auth-refactor] Let's discuss the auth refactor
    <bob> [thread:auth-refactor] I'll handle the token refresh part
    <dave> Deploying v2.1 now
    <charlie> [thread:auth-refactor] Need help with tests?

Thread-aware clients can group these messages together.

## Breakout Channels

When a thread grows too big for inline discussion, promote it to a full channel:

    THREADCLOSE PROMOTE #general auth-refactor

This creates `#general-auth-refactor`, auto-joins all thread participants, and
replays the thread history. The original thread is archived.

## Agent Integration

When an AI agent is @mentioned inside a thread, it receives only the thread's
message history as context — not the full channel. This keeps agent responses
focused and reduces noise.

Agents can use thread tools via IPC:

- `irc_thread_create` — start a thread
- `irc_thread_reply` — reply to a thread
- `irc_threads` — list active threads
- `irc_thread_close` — close a thread
- `irc_thread_read` — read thread messages

## Thread Names

Thread names must be 1-32 characters, using letters, numbers, and hyphens.
Examples: `auth-refactor`, `deploy-issue`, `bug42`.

## Thread Lifecycle

1. **Create** — any channel member can start a thread
2. **Reply** — any channel member can reply to an open thread
3. **Close** — thread participants or channel operators can close with a summary
4. **Promote** — thread creator or channel operators can promote to a breakout

Closed threads cannot receive new replies. Their summary is posted to the parent
channel.

## Federation

Threads work across federated servers. Thread messages, creation, and close
events relay via S2S links just like regular channel messages.
```

- [ ] **Step 2: Commit**

```bash
git add docs/threads.md
git commit -m "docs: add user-facing conversation threads documentation"
```

---

### Task 13: Final Integration Test & Cleanup

**Files:**

- Modify: `tests/test_threads.py`

- [ ] **Step 1: Run the full test suite**

```bash
cd /home/spark/git/culture && python -m pytest -v
```

Verify all existing tests still pass and no regressions.

- [ ] **Step 2: Run markdownlint on new docs**

```bash
markdownlint-cli2 "docs/threads.md" "culture/protocol/extensions/threads.md" "docs/superpowers/specs/2026-04-02-conversation-threads-design.md"
```

Fix any lint issues.

- [ ] **Step 3: Manual smoke test**

Start a local server and test with a standard IRC client:

```bash
cd /home/spark/git/culture && culture server start --name testserv
```

Connect with weechat or irssi, then:

1. Join `#general`
2. `THREAD CREATE #general test-thread :Hello thread`
3. Verify `[thread:test-thread] Hello thread` appears
4. `THREADS #general` — verify listing
5. `THREADCLOSE #general test-thread :Done` — verify summary notice

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "fix: address lint and integration issues from thread feature"
```

---

## Verification Checklist

- [ ] `pytest -v` — all tests pass (server + buffer + federation)
- [ ] Standard IRC client sees `[thread:name]` prefix on thread messages
- [ ] THREADS lists active threads, closed threads excluded
- [ ] THREADCLOSE posts summary NOTICE to parent channel
- [ ] THREADCLOSE PROMOTE creates breakout, auto-joins participants, replays history
- [ ] Thread messages federate across S2S links
- [ ] All 4 agent backends + packages/ have identical thread support
- [ ] markdownlint clean on all new/modified .md files
