# ASSIMILAI: Replace BACKEND with your backend name (e.g., codex, opencode)
# pylint: skip-file  # Template uses stub BACKEND imports; pylint cannot resolve them
"""Generic agent daemon — template for new backends.

Copy this file into your backend directory and replace:
- _start_agent_runner() — wire up your agent's SDK/CLI
- _build_system_prompt() — customize the system prompt
- _on_mention() — customize how @mentions become prompts

Everything else (IRC transport, IPC, socket server, webhooks) works as-is.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import time
from typing import Any

# These imports point to YOUR backend's copies of these files:
from culture.aio import maybe_await
from culture.clients.BACKEND.config import AgentConfig, DaemonConfig
from culture.clients.BACKEND.ipc import make_response
from culture.clients.BACKEND.irc_transport import IRCTransport
from culture.clients.BACKEND.message_buffer import MessageBuffer
from culture.clients.BACKEND.socket_server import SocketServer
from culture.clients.BACKEND.webhook import AlertEvent, WebhookClient

MAX_CONSECUTIVE_TURN_FAILURES = 3

# IPC validation error messages
_ERR_MISSING_CHANNEL = "Missing 'channel'"
_ERR_MISSING_CHANNEL_THREAD = "Missing 'channel' or 'thread'"
_ERR_MISSING_CHANNEL_THREAD_MSG = "Missing 'channel', 'thread', or 'message'"

logger = logging.getLogger(__name__)


class AgentDaemon:
    """Daemon that bridges an AI agent to the IRC network.

    This is the template. When assimilating into a new backend:
    1. Replace _start_agent_runner() with your agent's startup logic
    2. Replace _build_system_prompt() with your prompt format
    3. Adapt _on_mention() for your agent's prompt format
    """

    def __init__(
        self,
        config: DaemonConfig,
        agent: AgentConfig,
        *,
        skip_agent: bool = False,
        socket_dir: str | None = None,
    ):
        self.config = config
        self.agent = agent
        self.skip_agent = skip_agent

        self._socket_path = os.path.join(
            socket_dir or os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
            f"culture-{agent.nick}.sock",
        )

        self._transport: IRCTransport | None = None
        self._buffer: MessageBuffer | None = None
        self._socket_server: SocketServer | None = None
        self._webhook: WebhookClient | None = None
        self._agent_runner: Any = None
        self._stop_event: asyncio.Event | None = None

        # FIFO queue of relay targets — each @mention or poll enqueues a
        # target, each agent response dequeues one for correct routing.
        from collections import deque

        self._mention_targets: deque[str] = deque()

        # Crash-recovery state
        self._consecutive_turn_failures: int = 0
        self._circuit_open: bool = False

        # Pause/sleep state
        self._paused: bool = False
        self._manually_paused: bool = False
        self._last_activation: float | None = None

        # Background tasks — prevent fire-and-forget create_task GC
        self._background_tasks: set[asyncio.Task] = set()

        # Status query state — for asking the agent what it's doing
        self._status_query_event: asyncio.Event | None = None
        self._status_query_response: str = ""
        self._last_activity_text: str = ""

        # IPC dispatch table — maps message type → bound handler method
        self._ipc_dispatch: dict = {
            "irc_send": self._ipc_irc_send,
            "irc_read": self._ipc_irc_read,
            "irc_ask": self._ipc_irc_ask,
            "irc_join": self._ipc_irc_join,
            "irc_part": self._ipc_irc_part,
            "irc_who": self._ipc_irc_who,
            "irc_topic": self._ipc_irc_topic,
            "irc_channels": self._ipc_irc_channels,
            "compact": self._ipc_compact,
            "clear": self._ipc_clear,
            "status": self._ipc_status,
            "pause": self._ipc_pause,
            "resume": self._ipc_resume,
            "irc_thread_create": self._ipc_irc_thread_create,
            "irc_thread_reply": self._ipc_irc_thread_reply,
            "irc_threads": self._ipc_irc_threads,
            "irc_thread_close": self._ipc_irc_thread_close,
            "irc_thread_read": self._ipc_irc_thread_read,
            "shutdown": self._ipc_shutdown,
        }

    def set_stop_event(self, event: asyncio.Event) -> None:
        """Register an external stop event for coordinated shutdown."""
        self._stop_event = event

    async def start(self) -> None:
        """Start all daemon components."""
        # 1. Message buffer
        self._buffer = MessageBuffer(max_per_channel=self.config.buffer_size)

        # 2. IRC transport
        self._transport = IRCTransport(
            host=self.config.server.host,
            port=self.config.server.port,
            nick=self.agent.nick,
            user=self.agent.nick,
            channels=list(self.agent.channels),
            buffer=self._buffer,
            on_mention=self._on_mention,
        )
        await self._transport.connect()

        # 3. Webhook client
        self._webhook = WebhookClient(
            config=self.config.webhooks,
            irc_send=self._transport.send_privmsg,
        )

        # 4. Unix socket server
        self._socket_server = SocketServer(
            path=self._socket_path,
            handler=self._handle_ipc,
        )
        await self._socket_server.start()

        # 5. Start agent runner (REPLACE THIS in your backend)
        if not self.skip_agent:
            await self._start_agent_runner()

        # 6. Sleep scheduler background task
        self._sleep_task = asyncio.create_task(self._sleep_scheduler())

        # 7. Channel poll background task
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop all daemon components."""
        if hasattr(self, "_poll_task") and self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None

        if hasattr(self, "_sleep_task") and self._sleep_task:
            self._sleep_task.cancel()
            await asyncio.gather(self._sleep_task, return_exceptions=True)
            self._sleep_task = None

        if self._socket_server:
            await self._socket_server.stop()
        if self._transport:
            await self._transport.disconnect()

    # ------------------------------------------------------------------
    # REPLACE THESE METHODS in your backend
    # ------------------------------------------------------------------

    async def _start_agent_runner(self) -> None:
        """Start the agent. REPLACE with your backend's agent startup."""
        raise NotImplementedError(
            "Replace _start_agent_runner() with your agent backend's startup logic. "
            "See culture/clients/claude/daemon.py for the Claude implementation."
        )

    def _build_system_prompt(self) -> str:
        """Build the system prompt for the agent. REPLACE as needed."""
        if self.agent.system_prompt:
            return self.agent.system_prompt
        return f"You are {self.agent.nick}, an AI agent on the culture IRC network."

    def _parse_sleep_schedule(self) -> tuple[int, int] | None:
        """Parse sleep_start/sleep_end into minutes. Returns None if invalid."""
        try:
            sh, sm = (int(x) for x in self.config.sleep_start.split(":"))
            wh, wm = (int(x) for x in self.config.sleep_end.split(":"))
            if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= wh <= 23 and 0 <= wm <= 59):
                raise ValueError("hours/minutes out of range")
            return (sh * 60 + sm, wh * 60 + wm)
        except (ValueError, AttributeError):
            logger.warning(
                "Invalid sleep schedule '%s'-'%s' for %s — scheduler disabled",
                getattr(self.config, "sleep_start", None),
                getattr(self.config, "sleep_end", None),
                self.agent.nick,
            )
            return None

    async def _sleep_scheduler(self) -> None:
        """Background task that auto-pauses/resumes based on sleep schedule."""
        schedule = self._parse_sleep_schedule()
        if schedule is None:
            return
        sleep_minutes, wake_minutes = schedule

        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                now = datetime.datetime.now()
                current_minutes = now.hour * 60 + now.minute

                if sleep_minutes > wake_minutes:
                    should_sleep = (
                        current_minutes >= sleep_minutes or current_minutes < wake_minutes
                    )
                else:
                    should_sleep = sleep_minutes <= current_minutes < wake_minutes

                if should_sleep and not self._paused:
                    self._paused = True
                    logger.info("Sleep schedule: pausing %s", self.agent.nick)
                elif not should_sleep and self._paused and not self._manually_paused:
                    self._paused = False
                    logger.info("Sleep schedule: resuming %s", self.agent.nick)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sleep scheduler error")

    async def _poll_loop(self) -> None:
        """Background task that periodically checks channels for unread messages."""
        interval = self.config.poll_interval
        if interval <= 0:
            return
        while True:
            try:
                await asyncio.sleep(interval)
                if self._paused or not self._agent_runner or not self._agent_runner.is_running():
                    continue
                for channel in self.agent.channels:
                    msgs = self._buffer.read(channel)
                    if not msgs:
                        continue
                    # Filter out @mention messages (already handled by _on_mention)
                    nick = self.agent.nick
                    short = nick.split("-", 1)[1] if "-" in nick else None
                    msgs = [
                        m
                        for m in msgs
                        if not re.search(rf"@{re.escape(nick)}\b", m.text)
                        and not (short and re.search(rf"@{re.escape(short)}\b", m.text))
                    ]
                    if not msgs:
                        continue
                    lines = "\n".join(f"  <{m.nick}> {m.text}" for m in msgs)
                    prompt = (
                        f"[IRC Channel Poll: {channel}] Recent unread messages:\n"
                        f"{lines}\n\n"
                        "Respond naturally if any messages need your attention."
                    )
                    self._mention_targets.append(channel)
                    task = asyncio.create_task(self._agent_runner.send_prompt(prompt))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Poll loop error")

    async def _on_turn_error(self) -> None:
        """Send error feedback to IRC and clean up stale relay target.

        Wire this as the ``on_turn_error`` callback on your agent runner so
        the ``_mention_targets`` deque stays in sync with the prompt queue.
        """
        if self._mention_targets:
            relay_target = self._mention_targets.popleft()
            if self._transport and relay_target:
                await self._transport.send_privmsg(
                    relay_target,
                    "Sorry, I encountered an error processing your request.",
                )
        self._consecutive_turn_failures += 1
        if self._consecutive_turn_failures >= MAX_CONSECUTIVE_TURN_FAILURES:
            self._paused = True
            self._manually_paused = True
            logger.error(
                "Agent %s paused after %d consecutive turn failures",
                self.agent.nick,
                self._consecutive_turn_failures,
            )

    def _on_mention(self, target: str, sender: str, text: str) -> None:
        """Called when the agent is @mentioned. Sends prompt to runner.

        When the mention is inside a thread, provides thread-scoped context.
        """
        if self._paused:
            return
        self._last_activation = time.time()
        if target.startswith("#"):
            prompt = self._build_channel_prompt(target, sender, text)
        else:
            prompt = self._build_dm_prompt(sender, text)
        # Queue the prompt to your agent runner here:
        # await self._agent_runner.send_prompt(prompt)
        logger.info("@mention prompt (%d chars) from %s in %s", len(prompt), sender, target)

    def _build_channel_prompt(self, target: str, sender: str, text: str) -> str:
        """Build a prompt for a channel @mention, including thread context if present."""
        import re

        thread_match = re.match(r"^\[thread:([a-zA-Z0-9\-]+)\] ", text)
        if thread_match and self._buffer:
            thread_name = thread_match.group(1)
            thread_msgs = self._buffer.read_thread(target, thread_name)
            history = "\n".join(f"  <{m.nick}> {m.text}" for m in thread_msgs)
            return (
                f"[IRC @mention in {target}, thread:{thread_name}]\n"
                f"Thread history:\n{history}\n"
                f"  <{sender}> {text}"
            )
        return f"[IRC @mention in {target}] <{sender}> {text}"

    @staticmethod
    def _build_dm_prompt(sender: str, text: str) -> str:
        """Build a prompt for a direct message."""
        return f"[IRC DM] <{sender}> {text}"

    # ------------------------------------------------------------------
    # IPC handler — works for all backends
    # ------------------------------------------------------------------

    async def _handle_ipc(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Route an IPC request to the appropriate handler."""
        req_id = msg.get("id", "")
        msg_type = msg.get("type", "")
        try:
            handler = self._ipc_dispatch.get(msg_type)
            if handler is None:
                return make_response(req_id, ok=False, error=f"Unknown message type: {msg_type!r}")
            return await maybe_await(handler(req_id, msg))
        except Exception as exc:
            logger.exception("IPC handler error for type %r", msg_type)
            return make_response(req_id, ok=False, error=str(exc))

    # ------------------------------------------------------------------
    # Extracted IPC handlers (inline logic from original _handle_ipc)
    # ------------------------------------------------------------------

    async def _ipc_irc_send(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        message = msg.get("message", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        if not message or not message.strip():
            return make_response(req_id, ok=False, error="Missing 'message'")
        if self._transport:
            if channel.startswith("#") and channel not in self._transport.channels:
                return make_response(req_id, ok=False, error=f"Not joined to {channel}")
            await self._transport.send_privmsg(channel, message)
        return make_response(req_id, ok=True)

    def _ipc_irc_read(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        limit = msg.get("limit", 50)
        if self._buffer:
            messages = self._buffer.read(channel, limit=limit)
            return make_response(
                req_id,
                ok=True,
                data={
                    "messages": [
                        {"nick": m.nick, "text": m.text, "timestamp": m.timestamp} for m in messages
                    ]
                },
            )
        return make_response(req_id, ok=False, error="No buffer")

    async def _ipc_irc_ask(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        message = msg.get("message", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        if not message or not message.strip():
            return make_response(req_id, ok=False, error="Missing 'message'")
        if self._transport and channel:
            await self._transport.send_privmsg(channel, message)
        if self._webhook:
            await self._webhook.fire(
                AlertEvent(
                    event_type="agent_question",
                    nick=self.agent.nick,
                    message=f"[QUESTION] [{self.agent.nick}] asked in {channel}: {message}",
                )
            )
        return make_response(req_id, ok=True)

    async def _ipc_irc_join(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel.startswith("#"):
            return make_response(req_id, ok=False, error="Channel name must start with '#'")
        if self._transport:
            await self._transport.join_channel(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_part(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel.startswith("#"):
            return make_response(req_id, ok=False, error="Channel name must start with '#'")
        if self._transport:
            await self._transport.part_channel(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_who(self, req_id: str, msg: dict) -> dict:
        target = msg.get("target", "")
        if self._transport:
            await self._transport.send_who(target)
        return make_response(req_id, ok=True)

    async def _ipc_irc_topic(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        if self._transport:
            topic = msg.get("topic")  # None means query, string means set
            await self._transport.send_topic(channel, topic)
        return make_response(req_id, ok=True)

    def _ipc_irc_channels(self, req_id: str, msg: dict) -> dict:
        channels = self._transport.channels if self._transport else []
        return make_response(req_id, ok=True, data={"channels": channels})

    def _ipc_compact(self, req_id: str, msg: dict) -> dict:
        # Send /compact to agent runner — ADAPT for your backend
        logger.info("IPC compact requested")
        return make_response(req_id, ok=True)

    def _ipc_clear(self, req_id: str, msg: dict) -> dict:
        # Send /clear to agent runner — ADAPT for your backend
        logger.info("IPC clear requested")
        return make_response(req_id, ok=True)

    def _ipc_shutdown(self, req_id: str, msg: dict) -> dict:
        if self._stop_event:
            self._stop_event.set()
        else:
            task = asyncio.create_task(self.stop())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        return make_response(req_id, ok=True)

    # ------------------------------------------------------------------
    # Status / Pause / Resume IPC handlers
    # ------------------------------------------------------------------

    def _ipc_pause(self, req_id: str, msg: dict) -> dict:
        self._paused = True
        self._manually_paused = True
        logger.info("Agent %s paused (manual)", self.agent.nick)
        return make_response(req_id, ok=True)

    def _ipc_resume(self, req_id: str, msg: dict) -> dict:
        self._paused = False
        self._manually_paused = False
        logger.info("Agent %s resumed", self.agent.nick)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_create(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        text = msg.get("message", "")
        if not channel or not thread_name or not text:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL_THREAD_MSG)
        if self._transport:
            await self._transport.send_thread_create(channel, thread_name, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_reply(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        text = msg.get("message", "")
        if not channel or not thread_name or not text:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL_THREAD_MSG)
        if self._transport:
            await self._transport.send_thread_reply(channel, thread_name, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_threads(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        if self._transport:
            await self._transport.send_threads_list(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_close(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        summary = msg.get("summary", "")
        if not channel or not thread_name:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL_THREAD)
        if self._transport:
            await self._transport.send_thread_close(channel, thread_name, summary)
        return make_response(req_id, ok=True)

    def _ipc_irc_thread_read(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        limit = int(msg.get("limit", 50))
        if not channel or not thread_name:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL_THREAD)
        if self._buffer:
            messages = self._buffer.read_thread(channel, thread_name, limit=limit)
            return make_response(
                req_id,
                ok=True,
                data={
                    "messages": [
                        {
                            "nick": m.nick,
                            "text": m.text,
                            "timestamp": m.timestamp,
                            "thread": m.thread,
                        }
                        for m in messages
                    ]
                },
            )
        return make_response(req_id, ok=False, error="No buffer")

    async def _ipc_status(self, req_id: str, msg: dict) -> dict:
        # ADAPT: replace with your runner's is_running() check
        running = False  # e.g., self._agent_runner.is_running()
        query = msg.get("query", False)
        description = self._describe_activity(live_query=query)

        if query and running and not self._paused:
            description = await self._query_agent_status()

        return make_response(
            req_id,
            ok=True,
            data={
                "running": running,
                "paused": self._paused,
                "circuit_open": self._circuit_open,
                "turn_count": 0,
                "last_activation": self._last_activation,
                "activity": "paused" if self._paused else ("working" if running else "idle"),
                "description": description,
            },
        )

    @staticmethod
    def _truncate_first_line(text: str, max_len: int = 120) -> str:
        """Return the first line of *text*, truncated to *max_len* characters."""
        first_line = text.strip().split("\n")[0]
        if len(first_line) > max_len:
            return first_line[: max_len - 3] + "..."
        return first_line

    def _describe_activity(self, live_query: bool = False) -> str:
        """Return a human-readable description of what the agent is doing."""
        if self._paused:
            return "paused"
        if not self._last_activity_text:
            return "nothing"
        return self._truncate_first_line(self._last_activity_text)

    async def _query_agent_status(self) -> str:
        """Ask the agent directly what it's working on. ADAPT for your backend."""
        # ADAPT: send a system prompt to your agent runner and wait for response
        # See culture/clients/claude/daemon.py for the full implementation
        return "nothing"
