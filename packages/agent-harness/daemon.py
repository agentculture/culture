# ASSIMILAI: Replace BACKEND with your backend name (e.g., codex, opencode)
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
import time
from typing import Any

# These imports point to YOUR backend's copies of these files:
from agentirc.clients.BACKEND.config import DaemonConfig, AgentConfig
from agentirc.clients.BACKEND.ipc import make_response
from agentirc.clients.BACKEND.irc_transport import IRCTransport
from agentirc.clients.BACKEND.message_buffer import MessageBuffer
from agentirc.clients.BACKEND.socket_server import SocketServer
from agentirc.clients.BACKEND.webhook import WebhookClient, AlertEvent

logger = logging.getLogger(__name__)


class AgentDaemon:
    """Daemon that bridges an AI agent to the IRC network.

    This is the template. When assimilating into a new backend:
    1. Replace _start_agent_runner() with your agent's startup logic
    2. Replace _build_system_prompt() with your prompt format
    3. Adapt _on_mention() for your agent's prompt format
    """

    def __init__(self, config: DaemonConfig, agent: AgentConfig,
                 *, skip_agent: bool = False, socket_dir: str | None = None):
        self.config = config
        self.agent = agent
        self.skip_agent = skip_agent

        self._socket_path = os.path.join(
            socket_dir or os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
            f"agentirc-{agent.nick}.sock",
        )

        self._transport: IRCTransport | None = None
        self._buffer: MessageBuffer | None = None
        self._socket_server: SocketServer | None = None
        self._webhook: WebhookClient | None = None
        self._stop_event: asyncio.Event | None = None

        # Pause/sleep state
        self._paused: bool = False
        self._last_activation: float | None = None

        # Status query state — for asking the agent what it's doing
        self._status_query_event: asyncio.Event | None = None
        self._status_query_response: str = ""
        self._last_activity_text: str = ""

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

    async def stop(self) -> None:
        """Stop all daemon components."""
        if hasattr(self, "_sleep_task") and self._sleep_task:
            self._sleep_task.cancel()
            try:
                await self._sleep_task
            except asyncio.CancelledError:
                pass
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
            "See agentirc/clients/claude/daemon.py for the Claude implementation."
        )

    def _build_system_prompt(self) -> str:
        """Build the system prompt for the agent. REPLACE as needed."""
        if self.agent.system_prompt:
            return self.agent.system_prompt
        return f"You are {self.agent.nick}, an AI agent on the agentirc IRC network."

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
                    should_sleep = current_minutes >= sleep_minutes or current_minutes < wake_minutes
                else:
                    should_sleep = sleep_minutes <= current_minutes < wake_minutes

                if should_sleep and not self._paused:
                    self._paused = True
                    logger.info("Sleep schedule: pausing %s", self.agent.nick)
                elif not should_sleep and self._paused:
                    self._paused = False
                    logger.info("Sleep schedule: resuming %s", self.agent.nick)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Sleep scheduler error")

    def _on_mention(self, target: str, sender: str, text: str) -> None:
        """Called when the agent is @mentioned. Sends prompt to runner.

        When the mention is inside a thread, provides thread-scoped context.
        """
        if self._paused:
            return
        self._last_activation = time.time()
        if target.startswith("#"):
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
        # Queue the prompt to your agent runner here
        logger.info("@mention from %s in %s: %s", sender, target, text)

    # ------------------------------------------------------------------
    # IPC handler — works for all backends
    # ------------------------------------------------------------------

    async def _handle_ipc(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Handle IPC requests from skill clients."""
        msg_type = msg.get("type", "")
        req_id = msg.get("id", "")

        if msg_type == "irc_send":
            channel = msg.get("channel", "")
            message = msg.get("message", "")
            if self._transport:
                await self._transport.send_privmsg(channel, message)
            return make_response(req_id, ok=True)

        elif msg_type == "irc_read":
            channel = msg.get("channel", "")
            limit = msg.get("limit", 50)
            if self._buffer:
                messages = self._buffer.read(channel, limit=limit)
                return make_response(req_id, ok=True, data={
                    "messages": [
                        {"nick": m.nick, "text": m.text, "timestamp": m.timestamp}
                        for m in messages
                    ]
                })
            return make_response(req_id, ok=False, error="No buffer")

        elif msg_type == "irc_ask":
            channel = msg.get("channel", "")
            message = msg.get("message", "")
            if self._transport and channel:
                await self._transport.send_privmsg(channel, message)
            if self._webhook:
                await self._webhook.fire(AlertEvent(
                    event_type="agent_question",
                    nick=self.agent.nick,
                    message=f"[QUESTION] [{self.agent.nick}] asked in {channel}: {message}",
                ))
            return make_response(req_id, ok=True)

        elif msg_type == "irc_join":
            channel = msg.get("channel", "")
            if self._transport:
                await self._transport.join_channel(channel)
            return make_response(req_id, ok=True)

        elif msg_type == "irc_part":
            channel = msg.get("channel", "")
            if self._transport:
                await self._transport.part_channel(channel)
            return make_response(req_id, ok=True)

        elif msg_type == "irc_who":
            target = msg.get("target", "")
            if self._transport:
                await self._transport.send_who(target)
            return make_response(req_id, ok=True)

        elif msg_type == "irc_channels":
            channels = self._transport.channels if self._transport else []
            return make_response(req_id, ok=True, data={"channels": channels})

        elif msg_type == "compact":
            # Send /compact to agent runner — ADAPT for your backend
            logger.info("IPC compact requested")
            return make_response(req_id, ok=True)

        elif msg_type == "clear":
            # Send /clear to agent runner — ADAPT for your backend
            logger.info("IPC clear requested")
            return make_response(req_id, ok=True)

        elif msg_type == "status":
            return await self._ipc_status(req_id, msg)

        elif msg_type == "pause":
            return await self._ipc_pause(req_id)

        elif msg_type == "resume":
            return await self._ipc_resume(req_id)

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

        elif msg_type == "shutdown":
            if self._stop_event:
                self._stop_event.set()
            else:
                asyncio.create_task(self.stop())
            return make_response(req_id, ok=True)

        else:
            return make_response(req_id, ok=False, error=f"Unknown: {msg_type}")

    # ------------------------------------------------------------------
    # Status / Pause / Resume IPC handlers
    # ------------------------------------------------------------------

    async def _ipc_pause(self, req_id: str) -> dict:
        self._paused = True
        logger.info("Agent %s paused", self.agent.nick)
        return make_response(req_id, ok=True)

    async def _ipc_resume(self, req_id: str) -> dict:
        self._paused = False
        logger.info("Agent %s resumed", self.agent.nick)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_create(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        text = msg.get("message", "")
        if not channel or not thread_name or not text:
            return make_response(req_id, ok=False,
                                 error="Missing 'channel', 'thread', or 'message'")
        if self._transport:
            await self._transport.send_thread_create(channel, thread_name, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_reply(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        text = msg.get("message", "")
        if not channel or not thread_name or not text:
            return make_response(req_id, ok=False,
                                 error="Missing 'channel', 'thread', or 'message'")
        if self._transport:
            await self._transport.send_thread_reply(channel, thread_name, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_threads(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        if self._transport:
            await self._transport.send_threads_list(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_close(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        summary = msg.get("summary", "")
        if not channel or not thread_name:
            return make_response(req_id, ok=False,
                                 error="Missing 'channel' or 'thread'")
        if self._transport:
            await self._transport.send_thread_close(channel, thread_name, summary)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_read(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        limit = int(msg.get("limit", 50))
        if not channel or not thread_name:
            return make_response(req_id, ok=False,
                                 error="Missing 'channel' or 'thread'")
        if self._buffer:
            messages = self._buffer.read_thread(channel, thread_name, limit=limit)
            return make_response(req_id, ok=True, data={
                "messages": [
                    {"nick": m.nick, "text": m.text, "timestamp": m.timestamp,
                     "thread": m.thread}
                    for m in messages
                ]
            })
        return make_response(req_id, ok=False, error="No buffer")

    async def _ipc_status(self, req_id: str, msg: dict | None = None) -> dict:
        # ADAPT: replace with your runner's is_running() check
        running = False  # e.g., self._agent_runner.is_running()
        query = msg.get("query", False) if msg else False
        description = self._describe_activity(live_query=query)

        if query and running and not self._paused:
            description = await self._query_agent_status()

        return make_response(req_id, ok=True, data={
            "running": running,
            "paused": self._paused,
            "turn_count": 0,
            "last_activation": self._last_activation,
            "activity": "paused" if self._paused else ("working" if running else "idle"),
            "description": description,
        })

    def _describe_activity(self, live_query: bool = False) -> str:
        """Return a human-readable description of what the agent is doing."""
        if self._paused:
            return "paused"
        if not self._last_activity_text:
            return "nothing"
        first_line = self._last_activity_text.strip().split("\n")[0]
        if len(first_line) > 120:
            first_line = first_line[:117] + "..."
        return first_line

    async def _query_agent_status(self) -> str:
        """Ask the agent directly what it's working on. ADAPT for your backend."""
        # ADAPT: send a system prompt to your agent runner and wait for response
        # See agentirc/clients/claude/daemon.py for the full implementation
        return "nothing"
