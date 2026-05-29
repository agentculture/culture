from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import time

from culture.aio import maybe_await
from culture.clients._audit import AuditWriter
from culture.clients._context_watch import (
    ContextWatchState,
    WatchAction,
)
from culture.clients._context_watch import evaluate as context_evaluate
from culture.clients._context_watch import fraction as context_fraction
from culture.clients._context_watch import (
    mark_reminder_pending,
    take_reminder,
)
from culture.clients._daemon_log import DaemonLog
from culture.clients._perm_broker import handoff_path_for
from culture.clients.claude.agent_runner import AgentRunner
from culture.clients.claude.config import AgentConfig, DaemonConfig
from culture.clients.claude.ipc import make_response
from culture.clients.claude.irc_transport import IRCTransport
from culture.clients.claude.message_buffer import MessageBuffer
from culture.clients.claude.socket_server import SocketServer
from culture.clients.claude.supervisor import Supervisor, make_sdk_evaluate_fn
from culture.clients.claude.telemetry import init_harness_telemetry
from culture.clients.claude.webhook import AlertEvent, WebhookClient
from culture.pidfile import remove_pid, write_pid

logger = logging.getLogger(__name__)

MAX_CRASH_COUNT = 3
CRASH_WINDOW_SECONDS = 300
CRASH_RESTART_DELAY = 5
# A worker that produces no turn within this window after start has never
# engaged (wrong channel / never briefed) — flag it back to the boss.
IDLE_GRACE_SECONDS = 90

# IPC validation error messages
_ERR_MISSING_CHANNEL = "Missing 'channel'"
_ERR_MISSING_CHANNEL_THREAD = "Missing 'channel' or 'thread'"
_ERR_MISSING_CHANNEL_THREAD_MSG = "Missing 'channel', 'thread', or 'message'"
_ERR_CHANNEL_PREFIX = "Channel name must start with '#'"

# Regex to extract @mentioned nicks from messages
_MENTION_RE = re.compile(r"@([\w-]+)")


def _cw_float(value: object, default: float) -> float:
    """Coerce a threshold to float; fall back on a bad value (e.g. a quoted YAML
    number like ``high_water: '0.9'``) so it can't crash the per-turn evaluate()."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _cw_bool(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value)


def _context_watch_state(agent) -> ContextWatchState:
    """Normalize an agent's context_watch config (dict / object / None) to state.

    Runtime config (``culture.config.AgentConfig``) exposes it as a dict from
    ``culture.yaml`` extras; the backend-specific config exposes a
    ``ContextWatchConfig`` object; either may be empty/absent → defaults. Values
    are coerced (a quoted YAML number arrives as a str and must not crash).
    """
    cw = getattr(agent, "context_watch", None)
    if not cw:
        return ContextWatchState()
    get = cw.get if isinstance(cw, dict) else (lambda k, d: getattr(cw, k, d))
    return ContextWatchState(
        enabled=_cw_bool(get("enabled", True), True),
        high_water=_cw_float(get("high_water", 0.90), 0.90),
        low_water=_cw_float(get("low_water", 0.50), 0.50),
    )


def _boss_nick(agent) -> str:
    """The owning boss nick, or '' — works for both config flavors."""
    return getattr(agent, "boss", "") or ""


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
            f"culture-{agent.nick}.sock",
        )

        self._buffer: MessageBuffer | None = None
        self._transport: IRCTransport | None = None
        self._webhook: WebhookClient | None = None
        self._socket_server: SocketServer | None = None
        self._agent_runner: AgentRunner | None = None
        self._supervisor: Supervisor | None = None
        self._tracer = None
        self._metrics = None
        self._audit: AuditWriter = AuditWriter(nick=agent.nick)
        self._daemon_log: DaemonLog = DaemonLog(nick=agent.nick)

        # Context-watermark handoff state (Claude exposes per-turn input_tokens).
        # `agent.context_watch` is a dict on the runtime config (culture.config,
        # from culture.yaml extras) or a ContextWatchConfig object on the
        # backend-specific config (tests) — normalize both.
        self._context_watch = _context_watch_state(agent)

        # Crash-recovery state
        self._crash_times: list[float] = []
        self._circuit_open = False

        # Idle watchdog: a worker that comes up but never produces a turn (e.g.
        # spawned into the wrong channel / never briefed) would silently sit idle
        # while its boss believes it's working. We detect "never engaged" and push
        # the truth back to the boss instead of relying on anyone to notice.
        self._engaged: bool = False
        self._idle_task: asyncio.Task | None = None

        # Pause/sleep state
        self._paused: bool = False
        self._manually_paused: bool = False
        self._last_activation: float | None = None

        # Status query state — for asking the agent what it's doing
        self._status_query_event: asyncio.Event | None = None
        self._status_query_response: str = ""
        self._last_activity_text: str = ""

        # Background tasks (prevent GC of fire-and-forget tasks)
        self._background_tasks: set[asyncio.Task] = set()

        # Graceful shutdown
        self._stop_event: asyncio.Event | None = None
        self._pid_name: str = ""

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
        # 0. Write PID file for this agent
        self._pid_name = f"agent-{self.agent.nick}"
        write_pid(self._pid_name, os.getpid())

        # 0.5. OTEL telemetry (if telemetry.enabled, installs SDK providers; else no-op).
        self._tracer, self._metrics = init_harness_telemetry(self.config)

        # 1. Message buffer
        self._buffer = MessageBuffer(max_per_channel=self.config.buffer_size)

        # 2. IRC transport (with @mention → agent activation)
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
            tracer=self._tracer,
            metrics=self._metrics,
            backend="claude",
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

        # 5. Supervisor with SDK-based evaluator
        self._supervisor = Supervisor(
            window_size=self.config.supervisor.window_size,
            eval_interval=self.config.supervisor.eval_interval,
            escalation_threshold=self.config.supervisor.escalation_threshold,
            evaluate_fn=make_sdk_evaluate_fn(
                model=self.config.supervisor.model,
                thinking=self.config.supervisor.thinking,
                prompt_override=self.config.supervisor.prompt_override,
            ),
            on_whisper=self._on_supervisor_whisper,
            on_escalation=self._on_supervisor_escalation,
        )

        # 6. Optionally start the Claude agent runner
        if not self.skip_claude:
            await self._start_agent_runner()

        # 7. Sleep scheduler background task
        self._sleep_task = asyncio.create_task(self._sleep_scheduler())

        # 8. Channel poll background task
        self._poll_task = asyncio.create_task(self._poll_loop())

        logger.info("AgentDaemon started for %s (socket=%s)", self.agent.nick, self._socket_path)

    async def stop(self) -> None:
        """Cleanly shut down all components."""
        if self._idle_task is not None:
            self._idle_task.cancel()
            await asyncio.gather(self._idle_task, return_exceptions=True)
            self._idle_task = None

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
            await self._daemon_log.record("agent_stop")

        if self._socket_server is not None:
            await self._socket_server.stop()
            self._socket_server = None

        if self._transport is not None:
            await self._transport.disconnect()
            self._transport = None

        # Remove PID file
        if self._pid_name:
            remove_pid(self._pid_name)

        logger.info("AgentDaemon stopped for %s", self.agent.nick)

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

    def _send_channel_poll(self, channel: str) -> None:
        msgs = self._buffer.read(channel)
        if not msgs:
            return
        # Filter out messages that @mention this agent (already handled)
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
        prompt = self._maybe_prepend_reminder(prompt)
        # Poll-driven work counts as activation (a boss may post task context
        # without an @mention) — so a busy worker isn't falsely flagged idle.
        self._last_activation = time.time()
        task = asyncio.create_task(self._agent_runner.send_prompt(prompt))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _graceful_shutdown(self) -> None:
        """Trigger a graceful shutdown, signaling any waiting stop event."""
        logger.info("Graceful shutdown requested for %s", self.agent.nick)
        if self._stop_event is not None:
            self._stop_event.set()
        else:
            # No external stop_event — stop directly
            await self.stop()

    def set_stop_event(self, event: asyncio.Event) -> None:
        """Register an external stop event that _graceful_shutdown will signal."""
        self._stop_event = event

    # ------------------------------------------------------------------
    # Agent runner helpers
    # ------------------------------------------------------------------

    async def _start_agent_runner(self) -> None:
        self._agent_runner = AgentRunner(
            model=self.agent.model,
            directory=self.agent.directory,
            system_prompt=self._build_system_prompt(),
            on_exit=self._on_agent_exit,
            on_message=self._on_agent_message,
            on_usage=self._on_agent_usage,
            on_perm_request=self._on_perm_request,
            metrics=self._metrics,
            nick=self.agent.nick,
            boss=_boss_nick(self.agent),
        )
        await self._agent_runner.start()
        logger.info("AgentRunner started via SDK for %s", self.agent.nick)
        await self._daemon_log.record(
            "agent_start", model=self.agent.model, directory=self.agent.directory
        )
        # Arm the idle watchdog for boss-owned workers (the ones a boss expects to
        # be working). It fires once if the worker is never even triggered within
        # the grace window. Re-arming (crash-restart) starts a fresh evaluation:
        # cancel any prior watchdog and reset engagement/activation so a worker
        # that engaged-then-crashed-then-went-idle is re-detected.
        if _boss_nick(self.agent):
            if self._idle_task is not None:
                self._idle_task.cancel()
            self._engaged = False
            self._last_activation = None
            self._idle_task = asyncio.create_task(self._idle_watchdog())

    async def _idle_watchdog(self) -> None:
        """If a boss-owned worker is never triggered within the grace window,
        record it and DM the boss — so an idle/mis-briefed worker surfaces itself
        instead of the boss falsely believing it's working."""
        try:
            await asyncio.sleep(IDLE_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        if self._engaged or self._paused or self._agent_runner is None:
            return
        # A worker that WAS triggered (mentioned/briefed) but hasn't finished its
        # first turn yet (slow model, extended thinking, long first tool call) is
        # busy, not idle — only flag one that was never even activated.
        if self._last_activation is not None:
            return
        boss = _boss_nick(self.agent)
        await self._daemon_log.record("idle_warning", detail={"since_seconds": IDLE_GRACE_SECONDS})
        if boss and self._transport is not None:
            await self._transport.send_privmsg(
                boss,
                f"[idle] worker {self.agent.nick} has produced no activity "
                f"{IDLE_GRACE_SECONDS}s after start — it may not be in its "
                f"#task channel or was never briefed. Check and re-drive it.",
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
        if target.startswith("#"):
            prompt = self._build_channel_prompt(target, sender, text)
        else:
            prompt = self._build_dm_prompt(sender, text)
        prompt = self._maybe_prepend_reminder(prompt)
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
        from culture.agentirc.rooms_util import parse_room_meta

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

        # Use the agent runner to evaluate (room-invite work counts as activation)
        self._last_activation = time.time()
        await self._agent_runner.send_prompt(prompt)
        # Note: the agent runner processes the prompt asynchronously via the
        # SDK session loop. The agent is expected to use irc_join() / irc tools
        # to act on the decision within its normal turn. We log the invite so
        # the agent has the context to decide.
        logger.info(
            "ROOMINVITE for %s on %s — evaluation prompt sent to agent",
            self.agent.nick,
            channel,
        )

    async def _on_agent_message(self, msg: dict) -> None:
        """Feed agent activity to the supervisor for observation."""
        if not self._engaged:
            # First real turn → record `engaged` so the dashboard's idle signal
            # (which reads the daemon-log, not audit size) clears authoritatively.
            self._engaged = True
            await self._daemon_log.record("engaged")
        await self._audit.write(msg)

        if self._supervisor:
            await self._supervisor.observe(msg)

        self._capture_agent_status(msg)

    async def _on_agent_usage(self, input_tokens: int | None) -> None:
        """Evaluate context utilization after a turn; drive the handoff cycle."""
        action = context_evaluate(self._context_watch, input_tokens, self.agent.model)
        if action is WatchAction.REMINDER_DUE:
            # Context actually dropped after the compact — arm the reminder now,
            # so an activation that interleaved with the handoff/compact turns
            # can't consume it early.
            mark_reminder_pending(self._context_watch)
            await self._daemon_log.record("handoff_reminder_armed")
            return
        if action is not WatchAction.WRITE_HANDOFF:
            return
        if self._agent_runner is None or not self._agent_runner.is_running():
            return
        pct = context_fraction(input_tokens, self.agent.model) or 0.0
        handoff_path = handoff_path_for(self.agent.nick)
        logger.info(
            "Context watermark reached for %s (%.0f%%) — requesting handoff",
            self.agent.nick,
            pct * 100,
        )
        await self._daemon_log.record("handoff_written", pct=round(pct, 3), path=handoff_path)
        handoff_prompt = (
            "[context-handoff] You are approaching your context limit "
            f"({pct * 100:.0f}% full). Before it fills, write a concise handoff "
            f"for your post-compact self to {handoff_path}. Include: what you are "
            "working on, key decisions made, what remains, and important file "
            "paths. Use your Write tool (this path is pre-approved). Then stop."
        )
        await self._agent_runner.send_prompt(handoff_prompt)
        # Compact runs as the next queued turn, after the handoff is written.
        await self._agent_runner.send_prompt("/compact")
        await self._daemon_log.record("compact", trigger="context_watermark", pct=round(pct, 3))

    async def _on_perm_request(self, payload: dict) -> None:
        """Surface a worker permission request to its boss over IRC (best-effort).

        Fired by this worker's PermissionBroker when a tool call routes to the
        boss. DMs the owning boss (``self.agent.boss``) so the boss's activation
        handler fires and it can approve/deny. If no boss is configured (the
        human-supervised case from PR #411) we post nothing — the human finds the
        request via ``culture boss pending`` or the Mission Control dashboard.
        """
        boss = _boss_nick(self.agent)
        if not boss or self._transport is None:
            return
        tool = payload.get("tool_name", "?")
        req_id = payload.get("id", "?")
        preview = self._perm_input_preview(tool, payload.get("input", {}))
        notice = (
            f"[perm] worker {self.agent.nick} wants {tool}: {preview} "
            f"— id {req_id} (approve/deny)"
        )
        await self._transport.send_privmsg(boss, notice)

    @staticmethod
    def _perm_input_preview(tool: str, input_dict: dict) -> str:
        """Short one-line preview of a tool's input for the perm notice."""
        if tool == "Bash":
            value = input_dict.get("command", "")
        elif tool in ("Edit", "Write"):
            value = input_dict.get("file_path", "")
        else:
            try:
                import json as _json

                value = _json.dumps(input_dict)
            except (TypeError, ValueError):
                value = repr(input_dict)
        return str(value)[:80]

    def _maybe_prepend_reminder(self, prompt: str) -> str:
        """Prepend a post-compact handoff reminder when one is owed."""
        if take_reminder(self._context_watch):
            handoff_path = handoff_path_for(self.agent.nick)
            reminder = (
                "[context-handoff] You recently compacted. Read your handoff at "
                f"{handoff_path} before continuing.\n\n"
            )
            self._log_action_bg("handoff_reminder", path=handoff_path)
            return reminder + prompt
        return prompt

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
        await self._daemon_log.record("crash", exit_code=exit_code, count=len(self._crash_times))
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
            await self._daemon_log.record(
                "circuit_open",
                count=len(self._crash_times),
                window_s=CRASH_WINDOW_SECONDS,
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

    async def _on_agent_exit(self, exit_code: int) -> None:
        """Handle agent process exit with crash recovery and circuit breaker."""
        await self._daemon_log.record("agent_exit", exit_code=exit_code)
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

    def _log_action_bg(self, action: str, **detail) -> None:
        """Fire-and-forget a daemon-log record from a synchronous context."""
        task = asyncio.create_task(self._daemon_log.record(action, **detail))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _ipc_pause(self, req_id: str, msg: dict) -> dict:
        self._paused = True
        self._manually_paused = True
        logger.info("Agent %s paused (manual)", self.agent.nick)
        self._log_action_bg("pause", manual=True)
        return make_response(req_id, ok=True)

    def _ipc_resume(self, req_id: str, msg: dict) -> dict:
        self._paused = False
        self._manually_paused = False
        logger.info("Agent %s resumed", self.agent.nick)
        self._log_action_bg("resume", manual=True)
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

    def _check_mention_warnings(self, text: str) -> list[str]:
        """Return warnings for @mentioned nicks not seen in any buffer."""
        mentions = _MENTION_RE.findall(text)
        if not mentions or not self._buffer:
            return []
        known_nicks = self._buffer.known_nicks()
        warnings = []
        for nick in mentions:
            if nick not in known_nicks:
                warnings.append(f"Mentioned nick not found: {nick}")
        return warnings

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
        warnings = self._check_mention_warnings(text)
        resp = make_response(req_id, ok=True)
        if warnings:
            resp["warnings"] = warnings
        return resp

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
            return make_response(req_id, ok=False, error=_ERR_CHANNEL_PREFIX)
        assert self._transport is not None
        await self._transport.join_channel(channel)
        return make_response(req_id, ok=True)

    async def _ipc_irc_part(self, req_id: str, msg: dict) -> dict:
        channel = msg.get("channel", "")
        if not channel:
            return make_response(req_id, ok=False, error=_ERR_MISSING_CHANNEL)
        if not channel.startswith("#"):
            return make_response(req_id, ok=False, error=_ERR_CHANNEL_PREFIX)
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
        if not channel.startswith("#"):
            return make_response(req_id, ok=False, error=_ERR_CHANNEL_PREFIX)
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
        await self._daemon_log.record("compact", trigger="ipc")
        return make_response(req_id, ok=True)

    async def _ipc_clear(self, req_id: str, msg: dict) -> dict:
        if self._agent_runner is None or not self._agent_runner.is_running():
            return make_response(req_id, ok=False, error="Agent runner is not running")
        await self._agent_runner.send_prompt("/clear")
        await self._daemon_log.record("clear")
        return make_response(req_id, ok=True)

    def _ipc_shutdown(self, req_id: str, msg: dict) -> dict:
        task = asyncio.create_task(self._graceful_shutdown())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return make_response(req_id, ok=True)
