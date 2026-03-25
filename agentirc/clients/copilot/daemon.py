"""Copilot agent daemon — bridges a GitHub Copilot agent to the IRC network.

Uses CopilotAgentRunner (github-copilot-sdk) and
CopilotSupervisor (Copilot SDK for periodic evaluation).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from typing import Any

from agentirc.clients.copilot.config import DaemonConfig, AgentConfig
from agentirc.clients.copilot.ipc import make_response
from agentirc.clients.copilot.irc_transport import IRCTransport
from agentirc.clients.copilot.message_buffer import MessageBuffer
from agentirc.clients.copilot.socket_server import SocketServer
from agentirc.clients.copilot.webhook import WebhookClient, AlertEvent
from agentirc.clients.copilot.agent_runner import CopilotAgentRunner
from agentirc.clients.copilot.supervisor import CopilotSupervisor
from agentirc.pidfile import write_pid, remove_pid

logger = logging.getLogger(__name__)

MAX_CRASH_COUNT = 3
CRASH_WINDOW_SECONDS = 300
CRASH_RESTART_DELAY = 5


class CopilotDaemon:
    """Central orchestrator that ties together the IRC transport, socket server,
    Copilot agent runner, supervisor, and webhook client for a single agent nick."""

    def __init__(
        self,
        config: DaemonConfig,
        agent: AgentConfig,
        socket_dir: str | None = None,
        skip_copilot: bool = False,
    ) -> None:
        self.config = config
        self.agent = agent
        self.skip_copilot = skip_copilot

        self._socket_path = os.path.join(
            socket_dir or os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
            f"agentirc-{agent.nick}.sock",
        )

        self._buffer: MessageBuffer | None = None
        self._transport: IRCTransport | None = None
        self._webhook: WebhookClient | None = None
        self._socket_server: SocketServer | None = None
        self._agent_runner: CopilotAgentRunner | None = None
        self._supervisor: CopilotSupervisor | None = None

        # FIFO queue of relay targets — each @mention enqueues a target,
        # each agent response dequeues one, ensuring correct routing even
        # when multiple mentions arrive while the agent is busy.
        self._mention_targets: deque[str] = deque()

        # Crash-recovery state
        self._crash_times: list[float] = []
        self._circuit_open = False

        # Graceful shutdown
        self._stop_event: asyncio.Event | None = None
        self._pid_name: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all components in dependency order."""
        # 0. Write PID file for this agent
        self._pid_name = f"agent-{self.agent.nick}"
        write_pid(self._pid_name, os.getpid())

        # 1. Message buffer
        self._buffer = MessageBuffer(max_per_channel=self.config.buffer_size)

        # 2. IRC transport (with @mention -> agent activation)
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

        # 3. Webhook client (uses transport for IRC-based alerts)
        self._webhook = WebhookClient(
            config=self.config.webhooks,
            irc_send=self._transport.send_privmsg,
        )

        # 4. Unix socket server with IPC handler
        self._socket_server = SocketServer(
            path=self._socket_path,
            handler=self._handle_ipc,
        )
        await self._socket_server.start()

        # 5. Supervisor
        self._supervisor = CopilotSupervisor(
            model=self.config.supervisor.model,
            window_size=self.config.supervisor.window_size,
            eval_interval=self.config.supervisor.eval_interval,
            escalation_threshold=self.config.supervisor.escalation_threshold,
            on_whisper=self._on_supervisor_whisper,
            on_escalation=self._on_supervisor_escalation,
        )

        # 6. Optionally start the Copilot agent runner
        if not self.skip_copilot:
            await self._start_agent_runner()

        logger.info(
            "CopilotDaemon started for %s (socket=%s)", self.agent.nick, self._socket_path
        )

    async def stop(self) -> None:
        """Cleanly shut down all components."""
        if self._agent_runner is not None:
            await self._agent_runner.stop()
            self._agent_runner = None

        if self._socket_server is not None:
            await self._socket_server.stop()
            self._socket_server = None

        if self._transport is not None:
            await self._transport.disconnect()
            self._transport = None

        # Remove PID file
        if self._pid_name:
            remove_pid(self._pid_name)

        logger.info("CopilotDaemon stopped for %s", self.agent.nick)

    async def _graceful_shutdown(self) -> None:
        """Trigger a graceful shutdown, signaling any waiting stop event."""
        logger.info("Graceful shutdown requested for %s", self.agent.nick)
        if self._stop_event is not None:
            self._stop_event.set()
        else:
            # No external stop_event -- stop directly
            await self.stop()

    def set_stop_event(self, event: asyncio.Event) -> None:
        """Register an external stop event that _graceful_shutdown will signal."""
        self._stop_event = event

    # ------------------------------------------------------------------
    # Agent runner helpers
    # ------------------------------------------------------------------

    async def _start_agent_runner(self) -> None:
        # Resolve installed skill path for the Copilot session
        skill_dirs: list[str] = []
        copilot_skill = os.path.expanduser("~/.copilot_skills/agentirc-irc/SKILL.md")
        if os.path.isfile(copilot_skill):
            skill_dirs.append(copilot_skill)

        self._agent_runner = CopilotAgentRunner(
            model=self.agent.model,
            directory=self.agent.directory,
            system_prompt=self._build_system_prompt(),
            skill_directories=skill_dirs,
            on_exit=self._on_agent_exit,
            on_message=self._on_agent_message,
        )
        await self._agent_runner.start()
        logger.info("CopilotAgentRunner started for %s", self.agent.nick)

    def _on_mention(self, target: str, sender: str, text: str) -> None:
        """Called by IRCTransport when the agent is @mentioned or DM'd.

        Formats a prompt and enqueues it so the Copilot session picks it up.
        """
        if self._agent_runner and self._agent_runner.is_running():
            # Enqueue relay target (FIFO matches prompt queue order)
            self._mention_targets.append(target if target.startswith("#") else sender)
            if target.startswith("#"):
                prompt = f"[IRC @mention in {target}] <{sender}> {text}"
            else:
                prompt = f"[IRC DM] <{sender}> {text}"
            asyncio.create_task(self._agent_runner.send_prompt(prompt))

    async def _on_agent_message(self, msg: dict) -> None:
        """Relay agent text to IRC and feed to supervisor."""
        # Dequeue the relay target that corresponds to this turn
        relay_target = self._mention_targets.popleft() if self._mention_targets else None
        if self._transport and relay_target:
            content = msg.get("content", [])
            for item in content:
                if item.get("type") == "text":
                    text = item["text"].strip()
                    if text:
                        # Split long messages into IRC-friendly chunks
                        for line in text.split("\n"):
                            line = line.strip()
                            if line:
                                await self._transport.send_privmsg(
                                    relay_target, line
                                )

        if self._supervisor:
            await self._supervisor.observe(msg)

    def _build_system_prompt(self) -> str:
        return (
            f"You are {self.agent.nick}, an AI agent on the agentirc IRC network.\n"
            f"You have IRC tools available via the irc skill. Use them to communicate.\n"
            f"Your working directory is {self.agent.directory}.\n"
            f"Check IRC channels periodically with irc_read() for new messages.\n"
            f"When you finish a task, share results in the appropriate channel with irc_send()."
        )

    async def _on_agent_exit(self, exit_code: int) -> None:
        """Handle agent process exit with crash recovery and circuit breaker."""
        now = time.time()

        if exit_code == 0:
            logger.info("Agent %s exited cleanly", self.agent.nick)
            if self._webhook:
                await self._webhook.fire(AlertEvent(
                    event_type="agent_complete",
                    nick=self.agent.nick,
                    message=f"Agent {self.agent.nick} completed successfully.",
                ))
            return

        # Non-zero exit -- record crash time and check circuit breaker
        logger.warning("Agent %s crashed with exit code %d", self.agent.nick, exit_code)

        # Prune old crash times outside the window
        self._crash_times = [
            t for t in self._crash_times if now - t < CRASH_WINDOW_SECONDS
        ]
        self._crash_times.append(now)

        if self._webhook:
            await self._webhook.fire(AlertEvent(
                event_type="agent_error",
                nick=self.agent.nick,
                message=f"Agent {self.agent.nick} crashed (exit {exit_code}).",
            ))

        if len(self._crash_times) >= MAX_CRASH_COUNT:
            self._circuit_open = True
            logger.error(
                "Agent %s crashed %d times in %ds — circuit breaker opened, not restarting",
                self.agent.nick, len(self._crash_times), CRASH_WINDOW_SECONDS,
            )
            if self._webhook:
                await self._webhook.fire(AlertEvent(
                    event_type="agent_spiraling",
                    nick=self.agent.nick,
                    message=(
                        f"Agent {self.agent.nick} has crashed {len(self._crash_times)} times "
                        f"in {CRASH_WINDOW_SECONDS}s — escalating, not restarting."
                    ),
                ))
            return

        # Schedule restart after delay
        logger.info(
            "Restarting agent %s in %ds (crash %d/%d in window)",
            self.agent.nick, CRASH_RESTART_DELAY,
            len(self._crash_times), MAX_CRASH_COUNT,
        )
        asyncio.create_task(self._delayed_restart())

    async def _delayed_restart(self) -> None:
        await asyncio.sleep(CRASH_RESTART_DELAY)
        if not self._circuit_open and self._transport is not None:
            await self._start_agent_runner()

    # ------------------------------------------------------------------
    # Supervisor callbacks
    # ------------------------------------------------------------------

    async def _on_supervisor_whisper(self, message: str, whisper_type: str) -> None:
        """Deliver a supervisor whisper to the skill client via socket."""
        if self._socket_server:
            await self._socket_server.send_whisper(message, whisper_type)

    async def _on_supervisor_escalation(self, message: str) -> None:
        """Escalate via webhook + IRC when supervisor exhausts whispers."""
        if self._webhook:
            await self._webhook.fire(AlertEvent(
                event_type="agent_spiraling",
                nick=self.agent.nick,
                message=f"[ESCALATION] {self.agent.nick}: {message}",
            ))

    # ------------------------------------------------------------------
    # IPC handler
    # ------------------------------------------------------------------

    async def _handle_ipc(self, msg: dict) -> dict:
        """Route an IPC request to the appropriate component."""
        req_id = msg.get("id", "")
        msg_type = msg.get("type", "")

        try:
            if msg_type == "irc_send":
                return await self._ipc_irc_send(req_id, msg)

            elif msg_type == "irc_read":
                return await self._ipc_irc_read(req_id, msg)

            elif msg_type == "irc_join":
                return await self._ipc_irc_join(req_id, msg)

            elif msg_type == "irc_part":
                return await self._ipc_irc_part(req_id, msg)

            elif msg_type == "irc_channels":
                return await self._ipc_irc_channels(req_id)

            elif msg_type == "irc_who":
                return await self._ipc_irc_who(req_id, msg)

            elif msg_type == "irc_ask":
                return await self._ipc_irc_ask(req_id, msg)

            elif msg_type == "set_directory":
                return await self._ipc_set_directory(req_id, msg)

            elif msg_type == "compact":
                return await self._ipc_compact(req_id)

            elif msg_type == "clear":
                return await self._ipc_clear(req_id)

            elif msg_type == "shutdown":
                asyncio.create_task(self._graceful_shutdown())
                return make_response(req_id, ok=True)

            else:
                return make_response(req_id, ok=False, error=f"Unknown message type: {msg_type!r}")

        except Exception as exc:
            logger.exception("IPC handler error for type %r", msg_type)
            return make_response(req_id, ok=False, error=str(exc))

    # ------------------------------------------------------------------
    # IPC sub-handlers
    # ------------------------------------------------------------------

    async def _ipc_irc_send(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        text = msg.get("message", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        if not text:
            return make_response(req_id, ok=False, error="Missing 'message'")
        assert self._transport is not None
        await self._transport.send_privmsg(channel, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_read(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        limit = int(msg.get("limit", 50))
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        assert self._buffer is not None
        messages = self._buffer.read(channel, limit=limit)
        return make_response(req_id, ok=True, data={
            "messages": [
                {"nick": m.nick, "text": m.text, "timestamp": m.timestamp}
                for m in messages
            ]
        })

    async def _ipc_irc_join(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        assert self._transport is not None
        await self._transport.join_channel(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_part(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        assert self._transport is not None
        await self._transport.part_channel(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_channels(self, req_id: str) -> dict:
        assert self._transport is not None
        return make_response(req_id, ok=True, data={"channels": self._transport.channels})

    async def _ipc_irc_who(self, req_id: str, msg: dict) -> dict:
        target = msg.get("target", "")
        if not target:
            return make_response(req_id, ok=False, error="Missing 'target'")
        assert self._transport is not None
        await self._transport.send_who(target)
        return make_response(req_id, ok=True)

    async def _ipc_irc_ask(self, req_id: str, msg: dict) -> dict:
        """Send a PRIVMSG and fire a question webhook. Response matching is TODO."""
        channel = msg.get("channel", "")
        question = msg.get("message", "")
        if not channel:
            return make_response(req_id, ok=False, error="Missing 'channel'")
        if not question:
            return make_response(req_id, ok=False, error="Missing 'message'")
        assert self._transport is not None
        await self._transport.send_privmsg(channel, question)
        if self._webhook:
            await self._webhook.fire(AlertEvent(
                event_type="agent_question",
                nick=self.agent.nick,
                message=f"[QUESTION] [{self.agent.nick}] asked in {channel}: {question}",
            ))
        # Response matching is TODO
        return make_response(req_id, ok=True)

    async def _ipc_set_directory(self, req_id: str, msg: dict) -> dict:
        path = msg.get("path", "")
        if not path:
            return make_response(req_id, ok=False, error="Missing 'path'")
        new_cwd = os.path.abspath(path)
        if not os.path.isdir(new_cwd):
            return make_response(req_id, ok=False, error=f"Not a directory: {new_cwd}")
        # Update the daemon's working directory
        self.agent.directory = new_cwd
        # Check for .github/copilot-instructions.md (Copilot's project instructions)
        copilot_instructions = os.path.join(new_cwd, ".github", "copilot-instructions.md")
        instructions_content = None
        if os.path.isfile(copilot_instructions):
            with open(copilot_instructions) as f:
                instructions_content = f.read()
        return make_response(req_id, ok=True, data={
            "directory": new_cwd,
            "copilot_instructions": instructions_content,
        })

    async def _ipc_compact(self, req_id: str) -> dict:
        if self._agent_runner is None or not self._agent_runner.is_running():
            return make_response(req_id, ok=False, error="Agent runner is not running")
        await self._agent_runner.send_prompt("/compact")
        return make_response(req_id, ok=True)

    async def _ipc_clear(self, req_id: str) -> dict:
        if self._agent_runner is None or not self._agent_runner.is_running():
            return make_response(req_id, ok=False, error="Agent runner is not running")
        await self._agent_runner.send_prompt("/clear")
        return make_response(req_id, ok=True)
