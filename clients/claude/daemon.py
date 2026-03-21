from __future__ import annotations

import asyncio
import logging
import os
import time

from clients.claude.config import DaemonConfig, AgentConfig
from clients.claude.ipc import make_response
from clients.claude.irc_transport import IRCTransport
from clients.claude.message_buffer import MessageBuffer
from clients.claude.socket_server import SocketServer
from clients.claude.webhook import WebhookClient, AlertEvent
from clients.claude.agent_runner import AgentRunner
from clients.claude.supervisor import Supervisor, SupervisorVerdict

logger = logging.getLogger(__name__)

MAX_CRASH_COUNT = 3
CRASH_WINDOW_SECONDS = 300
CRASH_RESTART_DELAY = 5


class AgentDaemon:
    """Central orchestrator that ties together the IRC transport, socket server,
    agent runner, supervisor, and webhook client for a single agent nick."""

    def __init__(
        self,
        config: DaemonConfig,
        agent: AgentConfig,
        socket_dir: str | None = None,
        skip_claude: bool = False,
    ) -> None:
        self.config = config
        self.agent = agent
        self.skip_claude = skip_claude

        self._socket_path = os.path.join(
            socket_dir or os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
            f"agentirc-{agent.nick}.sock",
        )

        self._buffer: MessageBuffer | None = None
        self._transport: IRCTransport | None = None
        self._webhook: WebhookClient | None = None
        self._socket_server: SocketServer | None = None
        self._agent_runner: AgentRunner | None = None
        self._supervisor: Supervisor | None = None

        # Crash-recovery state
        self._crash_times: list[float] = []
        self._circuit_open = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all components in dependency order."""
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

        # 5. Supervisor (placeholder evaluate_fn — real Agent SDK evaluator deferred)
        async def _placeholder_eval(window, task):
            return SupervisorVerdict(action="OK", message="")

        self._supervisor = Supervisor(
            window_size=self.config.supervisor.window_size,
            eval_interval=self.config.supervisor.eval_interval,
            escalation_threshold=self.config.supervisor.escalation_threshold,
            evaluate_fn=_placeholder_eval,
            on_whisper=self._on_supervisor_whisper,
            on_escalation=self._on_supervisor_escalation,
        )

        # 6. Optionally start the Claude agent runner
        if not self.skip_claude:
            await self._start_agent_runner()

        logger.info(
            "AgentDaemon started for %s (socket=%s)", self.agent.nick, self._socket_path
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

        logger.info("AgentDaemon stopped for %s", self.agent.nick)

    # ------------------------------------------------------------------
    # Agent runner helpers
    # ------------------------------------------------------------------

    async def _start_agent_runner(self) -> None:
        command = [
            "claude",
            "--dangerously-skip-permissions",
            "--model", self.agent.model,
            "--directory", self.agent.directory,
        ]
        self._agent_runner = AgentRunner(
            command=command,
            directory=self.agent.directory,
            on_exit=self._on_agent_exit,
        )
        await self._agent_runner.start()
        logger.info("AgentRunner started with command: %s", command)

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

        # Non-zero exit — record crash time and check circuit breaker
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
        claude_md = os.path.join(path, "CLAUDE.md")
        claude_md_content = None
        if os.path.isfile(claude_md):
            with open(claude_md) as f:
                claude_md_content = f.read()
        return make_response(req_id, ok=True, data={
            "directory": path,
            "claude_md": claude_md_content,
        })

    async def _ipc_compact(self, req_id: str) -> dict:
        if self._agent_runner is None or not self._agent_runner.is_running():
            return make_response(req_id, ok=False, error="Agent runner is not running")
        await self._agent_runner.write_stdin("/compact\n")
        return make_response(req_id, ok=True)

    async def _ipc_clear(self, req_id: str) -> dict:
        if self._agent_runner is None or not self._agent_runner.is_running():
            return make_response(req_id, ok=False, error="Agent runner is not running")
        await self._agent_runner.write_stdin("/clear\n")
        return make_response(req_id, ok=True)
