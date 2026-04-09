"""ACP agent daemon — bridges any ACP-compatible agent to the IRC network.

Supports Cline (cline --acp), OpenCode (opencode acp), and any other agent
that implements the Agent Client Protocol over stdio.

Uses ACPAgentRunner (JSON-RPC/stdio) and an SDK-based Supervisor
(Claude Agent SDK for periodic evaluation).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import time
from collections import deque

from culture.aio import maybe_await
from culture.clients.acp.agent_runner import ACPAgentRunner
from culture.clients.acp.config import AgentConfig, DaemonConfig
from culture.clients.acp.ipc import make_response
from culture.clients.acp.irc_transport import IRCTransport
from culture.clients.acp.message_buffer import MessageBuffer
from culture.clients.acp.socket_server import SocketServer
from culture.clients.acp.supervisor import Supervisor, make_sdk_evaluate_fn
from culture.clients.acp.webhook import AlertEvent, WebhookClient
from culture.pidfile import remove_pid, write_pid

logger = logging.getLogger(__name__)

# IPC validation error messages
_ERR_MISSING_CHANNEL = "Missing 'channel'"
_ERR_MISSING_CHANNEL_THREAD = "Missing 'channel' or 'thread'"
_ERR_MISSING_CHANNEL_THREAD_MSG = "Missing 'channel', 'thread', or 'message'"

MAX_CRASH_COUNT = 3
CRASH_WINDOW_SECONDS = 300
CRASH_RESTART_DELAY = 5
MAX_CONSECUTIVE_TURN_FAILURES = 3


class ACPDaemon:
    """Central orchestrator that ties together the IRC transport, socket server,
    ACP agent runner, supervisor, and webhook client for a single agent nick."""

    def __init__(
        self,
        config: DaemonConfig,
        agent: AgentConfig,
        socket_dir: str | None = None,
        skip_agent: bool = False,
    ) -> None:
        self.config = config
        self.agent = agent
        self.skip_agent = skip_agent

        self._socket_path = os.path.join(
            socket_dir or os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
            f"culture-{agent.nick}.sock",
        )

        self._buffer: MessageBuffer | None = None
        self._transport: IRCTransport | None = None
        self._webhook: WebhookClient | None = None
        self._socket_server: SocketServer | None = None
        self._agent_runner: ACPAgentRunner | None = None
        self._supervisor: Supervisor | None = None

        # FIFO queue of relay targets — each @mention enqueues a target,
        # each agent response dequeues one, ensuring correct routing even
        # when multiple mentions arrive while the agent is busy.
        self._mention_targets: deque[str] = deque()

        # Crash-recovery state
        self._crash_times: list[float] = []
        self._circuit_open = False
        self._consecutive_turn_failures: int = 0

        # Pause/sleep state
        self._paused: bool = False
        self._manually_paused: bool = False
        self._last_activation: float | None = None

        # Status query state — for asking the agent what it's doing
        self._status_query_event: asyncio.Event | None = None
        self._status_query_response: str = ""
        self._last_activity_text: str = ""

        # Graceful shutdown
        self._stop_event: asyncio.Event | None = None
        self._pid_name: str = ""

        # Background task tracking (prevent GC of fire-and-forget tasks)
        self._background_tasks: set[asyncio.Task] = set()

        # IPC dispatch table — maps message type → bound handler method
        self._ipc_dispatch: dict = {
            "irc_send": self._ipc_irc_send,
            "irc_read": self._ipc_irc_read,
            "irc_join": self._ipc_irc_join,
            "irc_part": self._ipc_irc_part,
            "irc_channels": self._ipc_irc_channels,
            "irc_who": self._ipc_irc_who,
            "irc_topic": self._ipc_irc_topic,
            "irc_ask": self._ipc_irc_ask,
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all components in dependency order."""
        cmd_label = " ".join(self.agent.acp_command)

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
            tags=list(self.agent.tags),
            on_roominvite=self._on_roominvite,
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

        # 5. SDK-based supervisor (vendor-agnostic)
        self._supervisor = Supervisor(
            window_size=self.config.supervisor.window_size,
            eval_interval=self.config.supervisor.eval_interval,
            escalation_threshold=self.config.supervisor.escalation_threshold,
            evaluate_fn=make_sdk_evaluate_fn(
                model=self.config.supervisor.model,
                thinking=self.config.supervisor.thinking or None,
                prompt_override=self.config.supervisor.prompt_override,
            ),
            on_whisper=self._on_supervisor_whisper,
            on_escalation=self._on_supervisor_escalation,
        )

        # 6. Optionally start the ACP agent runner
        if not self.skip_agent:
            try:
                await self._start_agent_runner()
            except Exception:
                logger.exception(
                    "Failed to start agent runner for %s (%s), scheduling retry",
                    self.agent.nick,
                    cmd_label,
                )
                self._agent_runner = None
                self._crash_times.append(time.time())
                task = asyncio.create_task(self._delayed_restart())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

        # 7. Sleep scheduler background task
        self._sleep_task = asyncio.create_task(self._sleep_scheduler())

        # 8. Channel poll background task
        self._poll_task = asyncio.create_task(self._poll_loop())

        logger.info(
            "ACPDaemon started for %s (cmd=%s, socket=%s)",
            self.agent.nick,
            cmd_label,
            self._socket_path,
        )

    async def stop(self) -> None:
        """Cleanly shut down all components."""
        if hasattr(self, "_poll_task") and self._poll_task:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None

        if hasattr(self, "_sleep_task") and self._sleep_task:
            self._sleep_task.cancel()
            await asyncio.gather(self._sleep_task, return_exceptions=True)
            self._sleep_task = None

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

        logger.info("ACPDaemon stopped for %s", self.agent.nick)

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
                    # Overnight: e.g., 23:00-08:00
                    should_sleep = (
                        current_minutes >= sleep_minutes or current_minutes < wake_minutes
                    )
                else:
                    # Same day: e.g., 13:00-14:00
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
                self._process_poll_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Poll loop error")

    def _process_poll_cycle(self) -> None:
        if self._paused or not self._agent_runner or not self._agent_runner.is_running():
            return
        for channel in self.agent.channels:
            self._send_channel_poll(channel)

    def _send_channel_poll(self, channel) -> None:
        msgs = self._buffer.read(channel)
        if not msgs:
            return
        # Filter out messages that @mention this agent (already handled by _on_mention)
        nick = self.agent.nick
        short = nick.split("-", 1)[1] if "-" in nick else None
        msgs = [
            m
            for m in msgs
            if not re.search(rf"@{re.escape(nick)}\b", m.text)
            and not (short and re.search(rf"@{re.escape(short)}\b", m.text))
        ]
        if not msgs:
            return
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

    async def _on_turn_error(self) -> None:
        """Send error feedback to IRC and clean up stale relay target."""
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
            if self._webhook:
                await self._webhook.fire(
                    AlertEvent(
                        event_type="agent_spiraling",
                        nick=self.agent.nick,
                        message=(
                            f"Agent {self.agent.nick} paused after "
                            f"{self._consecutive_turn_failures} consecutive turn failures."
                        ),
                    )
                )

    async def _start_agent_runner(self) -> None:
        self._agent_runner = ACPAgentRunner(
            model=self.agent.model,
            directory=self.agent.directory,
            acp_command=self.agent.acp_command,
            system_prompt=self._build_system_prompt(),
            on_exit=self._on_agent_exit,
            on_message=self._on_agent_message,
            on_turn_error=self._on_turn_error,
        )
        # Absorb the system prompt response without relaying to IRC
        self._mention_targets.append(None)
        await self._agent_runner.start()
        logger.info(
            "ACPAgentRunner started for %s (cmd=%s)",
            self.agent.nick,
            " ".join(self.agent.acp_command),
        )

    def _on_mention(self, target: str, sender: str, text: str) -> None:
        """Called by IRCTransport when the agent is @mentioned or DM'd.

        When the mention is inside a thread, provides thread-scoped context.
        """
        if self._paused:
            return
        if not (self._agent_runner and self._agent_runner.is_running()):
            return
        self._last_activation = time.time()
        # Enqueue relay target (FIFO matches prompt queue order)
        self._mention_targets.append(target if target.startswith("#") else sender)
        if target.startswith("#"):
            prompt = self._build_channel_prompt(target, sender, text)
        else:
            prompt = self._build_dm_prompt(sender, text)
        task = asyncio.create_task(self._agent_runner.send_prompt(prompt))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

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

    def _on_roominvite(self, channel: str, meta_text: str) -> None:
        """Called by IRCTransport when a ROOMINVITE is received."""
        task = asyncio.create_task(self._handle_roominvite(channel, meta_text))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _handle_roominvite(self, channel: str, meta_text: str) -> None:
        """Evaluate a room invitation using the agent's LLM."""
        from culture.server.rooms_util import parse_room_meta

        meta = parse_room_meta(meta_text)
        purpose = meta.get("purpose", "")
        instructions = meta.get("instructions", "")
        tags = meta.get("tags", "")
        _ = meta.get("requestor")

        prompt = (
            f"You've been invited to join IRC room {channel}.\n"
            f"Purpose: {purpose}\n"
            f"Instructions: {instructions}\n"
            f"Room tags: {tags}\n"
            f"Your tags: {','.join(self.agent.tags)}\n\n"
            "Think step-by-step about whether this room fits your current work "
            "and capabilities. Then decide: should you join? Answer YES or NO."
        )

        if self._agent_runner is None or not self._agent_runner.is_running():
            # No live agent — auto-join without evaluation
            logger.info(
                "ROOMINVITE for %s: no agent runner active, auto-joining %s",
                self.agent.nick,
                channel,
            )
            assert self._transport is not None
            await self._transport.send_raw(f"JOIN {channel}")
            return

        # Use the agent runner to evaluate
        # Enqueue a None relay target so the evaluation response doesn't
        # steal a real mention's relay target from the deque.
        self._mention_targets.append(None)
        await self._agent_runner.send_prompt(prompt)
        logger.info(
            "ROOMINVITE for %s on %s — evaluation prompt sent to agent",
            self.agent.nick,
            channel,
        )

    async def _relay_response_to_irc(self, msg: dict) -> None:
        """Dequeue the next relay target and send agent text lines to IRC."""
        relay_target = self._mention_targets.popleft() if self._mention_targets else None
        if self._transport and relay_target:
            content = msg.get("content", [])
            for item in content:
                if item.get("type") == "text":
                    text = item["text"].strip()
                    if text:
                        for line in text.split("\n"):
                            line = line.strip()
                            if line:
                                await self._transport.send_privmsg(relay_target, line)

    def _capture_agent_status(self, msg: dict) -> None:
        """Capture the last assistant text for status reporting and fulfill any pending query."""
        if msg.get("type") == "assistant":
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    self._last_activity_text = block["text"]
                    break
                elif isinstance(block, str):
                    self._last_activity_text = block
                    break

            # If a status query is pending, fulfill it
            if self._status_query_event and not self._status_query_event.is_set():
                self._status_query_response = self._last_activity_text
                self._status_query_event.set()

    async def _on_agent_message(self, msg: dict) -> None:
        """Relay agent text to IRC and feed to supervisor."""
        self._consecutive_turn_failures = 0
        await self._relay_response_to_irc(msg)

        if self._supervisor:
            await self._supervisor.observe(msg)

        self._capture_agent_status(msg)

    def _build_system_prompt(self) -> str:
        if self.agent.system_prompt:
            return self.agent.system_prompt
        return (
            f"You are {self.agent.nick}, an AI agent on the culture IRC network.\n"
            "You have IRC tools available via the irc skill. Use them to communicate.\n"
            f"Your working directory is {self.agent.directory}.\n"
            "Check IRC channels periodically with irc_read() for new messages.\n"
            "When you finish a task, share results in the appropriate channel with irc_send()."
        )

    async def _record_crash_time(self, exit_code: int) -> None:
        """Log a crash warning, prune the sliding window, record the new crash, fire agent_error."""
        now = time.time()
        logger.warning("Agent %s crashed with exit code %d", self.agent.nick, exit_code)
        self._crash_times = [t for t in self._crash_times if now - t < CRASH_WINDOW_SECONDS]
        self._crash_times.append(now)
        if self._webhook:
            await self._webhook.fire(
                AlertEvent(
                    event_type="agent_error",
                    nick=self.agent.nick,
                    message=f"Agent {self.agent.nick} crashed (exit {exit_code}).",
                )
            )

    async def _evaluate_circuit_breaker(self) -> bool:
        """Open the circuit breaker if crash count reached the threshold.

        Returns True if the circuit was opened (caller should stop restart logic).
        """
        if len(self._crash_times) >= MAX_CRASH_COUNT:
            self._circuit_open = True
            logger.error(
                "Agent %s crashed %d times in %ds — circuit breaker opened, not restarting",
                self.agent.nick,
                len(self._crash_times),
                CRASH_WINDOW_SECONDS,
            )
            if self._webhook:
                await self._webhook.fire(
                    AlertEvent(
                        event_type="agent_spiraling",
                        nick=self.agent.nick,
                        message=(
                            f"Agent {self.agent.nick} has crashed {len(self._crash_times)} times "
                            f"in {CRASH_WINDOW_SECONDS}s — escalating, not restarting."
                        ),
                    )
                )
            return True
        return False

    async def _on_agent_exit(self, exit_code: int) -> None:
        """Handle agent process exit with crash recovery and circuit breaker."""
        if exit_code == 0:
            logger.info("Agent %s exited cleanly", self.agent.nick)
            if self._webhook:
                await self._webhook.fire(
                    AlertEvent(
                        event_type="agent_complete",
                        nick=self.agent.nick,
                        message=f"Agent {self.agent.nick} completed successfully.",
                    )
                )
            return

        await self._record_crash_time(exit_code)
        if await self._evaluate_circuit_breaker():
            return

        # Schedule restart after delay
        logger.info(
            "Restarting agent %s in %ds (crash %d/%d in window)",
            self.agent.nick,
            CRASH_RESTART_DELAY,
            len(self._crash_times),
            MAX_CRASH_COUNT,
        )
        task = asyncio.create_task(self._delayed_restart())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _delayed_restart(self) -> None:
        await asyncio.sleep(CRASH_RESTART_DELAY)
        if not self._circuit_open and self._transport is not None:
            try:
                await self._start_agent_runner()
            except Exception:
                logger.exception(
                    "Failed to restart agent runner for %s",
                    self.agent.nick,
                )
                # Record as a crash so the circuit breaker can track it
                await self._on_agent_exit(-1)

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
            await self._webhook.fire(
                AlertEvent(
                    event_type="agent_spiraling",
                    nick=self.agent.nick,
                    message=f"[ESCALATION] {self.agent.nick}: {message}",
                )
            )

    # ------------------------------------------------------------------
    # IPC handler
    # ------------------------------------------------------------------

    async def _handle_ipc(self, msg: dict) -> dict:
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
    # IPC sub-handlers
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
        # NOTE: Catch-up on missed messages is not yet implemented.
        # IRCTransport does not process HISTORY responses into the buffer.
        # The agent resumes and will see new messages going forward.
        return make_response(req_id, ok=True)

    async def _ipc_status(self, req_id: str, msg: dict) -> dict:
        running = self._agent_runner is not None and self._agent_runner.is_running()
        turn_count = self._supervisor._turn_count if self._supervisor else 0

        # Determine activity description
        query = msg.get("query", False)
        description = self._describe_activity(live_query=query)

        # If live query requested and agent is active, ask the agent directly
        if query and running and not self._paused:
            description = await self._query_agent_status()

        if self._paused:
            activity = "paused"
        elif running:
            activity = "working"
        else:
            activity = "idle"

        return make_response(
            req_id,
            ok=True,
            data={
                "running": running,
                "paused": self._paused,
                "circuit_open": self._circuit_open,
                "turn_count": turn_count,
                "last_activation": self._last_activation,
                "activity": activity,
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
        """Ask the agent directly what it's working on."""
        if not self._agent_runner or not self._agent_runner.is_running():
            return "nothing"

        self._status_query_event = asyncio.Event()
        self._status_query_response = ""

        try:
            # Enqueue a None relay target so the status response doesn't
            # steal a real mention's relay target from the deque.
            self._mention_targets.append(None)
            await self._agent_runner.send_prompt(
                "[SYSTEM] Briefly describe what you are currently working on "
                "in one sentence. Reply with just the description, no preamble."
            )
            async with asyncio.timeout(10.0):
                await self._status_query_event.wait()
            return self._truncate_first_line(self._status_query_response) or "nothing"
        except asyncio.TimeoutError:
            return "busy (no response)"
        finally:
            self._status_query_event = None
            self._status_query_response = ""

    async def _ipc_irc_send(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        text = msg.get("message", "")
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        if not text or not text.strip():
            return make_response(req_id, ok=False, error="Missing 'message'")
        assert self._transport is not None
        if channel.startswith("#") and channel not in self._transport.channels:
            return make_response(req_id, ok=False, error=f"Not joined to {channel}")
        await self._transport.send_privmsg(channel, text)
        return make_response(req_id, ok=True)

    def _ipc_irc_read(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        limit = int(msg.get("limit", 50))
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        assert self._buffer is not None
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

    async def _ipc_irc_join(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        if not channel.startswith("#"):
            return make_response(req_id, ok=False, error="Channel name must start with '#'")
        assert self._transport is not None
        await self._transport.join_channel(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_part(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        if not channel.startswith("#"):
            return make_response(req_id, ok=False, error="Channel name must start with '#'")
        assert self._transport is not None
        await self._transport.part_channel(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_create(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        text = msg.get("message", "")
        if not channel or not thread_name or not text:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL_THREAD_MSG)
        assert self._transport is not None
        await self._transport.send_thread_create(channel, thread_name, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_reply(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        text = msg.get("message", "")
        if not channel or not thread_name or not text:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL_THREAD_MSG)
        assert self._transport is not None
        await self._transport.send_thread_reply(channel, thread_name, text)
        return make_response(req_id, ok=True)

    async def _ipc_irc_threads(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        assert self._transport is not None
        await self._transport.send_threads_list(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_thread_close(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        summary = msg.get("summary", "")
        if not channel or not thread_name:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL_THREAD)
        assert self._transport is not None
        await self._transport.send_thread_close(channel, thread_name, summary)
        return make_response(req_id, ok=True)

    def _ipc_irc_thread_read(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        thread_name = msg.get("thread", "")
        limit = int(msg.get("limit", 50))
        if not channel or not thread_name:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL_THREAD)
        assert self._buffer is not None
        messages = self._buffer.read_thread(channel, thread_name, limit=limit)
        return make_response(
            req_id,
            ok=True,
            data={
                "messages": [
                    {"nick": m.nick, "text": m.text, "timestamp": m.timestamp, "thread": m.thread}
                    for m in messages
                ]
            },
        )

    def _ipc_irc_channels(self, req_id: str, msg: dict) -> dict:
        assert self._transport is not None
        return make_response(req_id, ok=True, data={"channels": self._transport.channels})

    async def _ipc_irc_who(self, req_id: str, msg: dict) -> dict:
        target = msg.get("target", "")
        if not target:
            return make_response(req_id, ok=False, error="Missing 'target'")
        assert self._transport is not None
        await self._transport.send_who(target)
        return make_response(req_id, ok=True)

    async def _ipc_irc_topic(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        assert self._transport is not None
        topic = msg.get("topic")  # None means query, string means set
        await self._transport.send_topic(channel, topic)
        return make_response(req_id, ok=True)

    async def _ipc_irc_ask(self, req_id: str, msg: dict) -> dict:
        """Send a PRIVMSG and fire a question webhook. Response matching is TODO."""
        channel = msg.get("channel", "")
        question = msg.get("message", "")
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        if not question or not question.strip():
            return make_response(req_id, ok=False, error="Missing 'message'")
        assert self._transport is not None
        await self._transport.send_privmsg(channel, question)
        if self._webhook:
            await self._webhook.fire(
                AlertEvent(
                    event_type="agent_question",
                    nick=self.agent.nick,
                    message=f"[QUESTION] [{self.agent.nick}] asked in {channel}: {question}",
                )
            )
        # Response matching is TODO
        return make_response(req_id, ok=True)

    async def _ipc_compact(self, req_id: str, msg: dict) -> dict:
        if self._agent_runner is None or not self._agent_runner.is_running():
            return make_response(req_id, ok=False, error="Agent runner is not running")
        await self._agent_runner.send_prompt("/compact")
        return make_response(req_id, ok=True)

    async def _ipc_clear(self, req_id: str, msg: dict) -> dict:
        if self._agent_runner is None or not self._agent_runner.is_running():
            return make_response(req_id, ok=False, error="Agent runner is not running")
        await self._agent_runner.send_prompt("/clear")
        return make_response(req_id, ok=True)

    def _ipc_shutdown(self, req_id: str, msg: dict) -> dict:
        task = asyncio.create_task(self._graceful_shutdown())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return make_response(req_id, ok=True)
