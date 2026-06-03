from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import time
from typing import Any

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
from culture.clients import _mission as _mission_persistence
from culture.clients._perm_broker import handoff_path_for
from culture.clients._socket_link import ensure_socket_symlink, remove_socket_symlink
from culture.clients.claude.agent_runner import AgentRunner
from culture.clients.claude.config import AgentConfig, DaemonConfig
from culture.clients.claude.ipc import make_response
from culture.clients.claude.irc_transport import IRCTransport
from culture.clients.claude.message_buffer import MessageBuffer
from culture.clients.claude.socket_server import SocketServer
from culture.clients.claude.supervisor import Supervisor, make_sdk_evaluate_fn
from culture.clients.claude.telemetry import init_harness_telemetry
from culture.clients.claude.webhook import AlertEvent, WebhookClient
from culture.pidfile import is_process_alive, read_pid, remove_pid, write_pid

logger = logging.getLogger(__name__)

MAX_CRASH_COUNT = 3
CRASH_WINDOW_SECONDS = 300
CRASH_RESTART_DELAY = 5
# A worker that produces no turn within this window after start has never
# engaged (wrong channel / never briefed) — flag it back to the boss.
IDLE_GRACE_SECONDS = 90

# After the worker has been activated (mention/poll/invite landed) OR has
# already produced at least one turn, this is the maximum gap between
# AssistantMessages before we surface a "stall" warning to the boss. Generous
# enough to cover slow first turns, extended thinking, and long-running tool
# calls; tight enough that a genuinely hung worker is noticed within minutes.
STALL_GRACE_SECONDS = 300

# Periodic re-check interval for the idle watchdog. Short relative to grace
# windows so a state-change is surfaced promptly; long enough to keep wakeups
# cheap.
WATCHDOG_POLL_SECONDS = 30

# Threshold for the consecutive-failed-turns watchdog class. The
# context-watch dogfood showed a pattern where a worker alternates
# fail (Write → Stream closed) and succeed (Bash workaround); each
# success refreshed _last_turn_completed_at so v8.18.4's
# stalled_in_retry_loop stayed silent. This counter resets on every
# clean turn and increments on every failed turn — exceeding the
# threshold means the failure rate is elevated even if some turns
# squeak through.
CONSECUTIVE_FAILED_TURN_THRESHOLD = 5

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
        # Cross-process watchdog for owned worker daemons that died
        # without writing ``agent_exit`` (v8.19.8 — silent-death after
        # DONE-FINAL pattern, observed live during v8.18.7 fleet ship).
        self._silent_death_task: asyncio.Task | None = None
        self._silent_death_warned: set[str] = set()
        # Last AssistantMessage timestamp — drives the unified stall watchdog
        # (catches "engaged then silent" as well as "activated then silent").
        # None until the first turn lands.
        self._last_assistant_message_at: float | None = None
        # Last successful TURN completion timestamp. Distinct from
        # ``_last_assistant_message_at`` (which fires every AssistantMessage,
        # including the tool_use AssistantMessages that come BEFORE a
        # ResultMessage). When a worker retries a failing tool call (e.g.
        # the SDK CLI's "Stream closed" pattern), each retry is a new
        # AssistantMessage and refreshes _last_assistant_message_at — but
        # the turn never completes, so this stays stale. The watchdog's
        # ``stalled_in_retry_loop`` class catches that gap.
        self._last_turn_completed_at: float | None = None
        # Consecutive failed-turn counter. Drives v8.18.5's
        # ``stalled_in_failed_retry`` watchdog class: when a worker is
        # alternating fail/succeed (the SDK CLI Stream-closed-then-Bash-
        # workaround pattern from the context-watch dogfood), each
        # successful turn resets _last_turn_completed_at so v8.18.4's
        # stalled_in_retry_loop stays silent. But the failure rate is
        # still elevated; this counter catches it.
        self._consecutive_failed_turns: int = 0
        # Recorded-resolved-model latch. The ``agent_start`` daemon-log entry
        # carries the YAML's declared model — which is empty by design on
        # bosses (so workers inherit at spawn time). When YAML omits the
        # model the SDK picks a default at session start; that resolved
        # model is only observable in the first AssistantMessage. We latch
        # it once into a ``model_resolved`` daemon-log action so
        # ``_boss_inherits`` can fall back to the real runtime model when
        # ``agent_start.model`` is empty — closing the leak where workers
        # spawned from a YAML-less boss were inheriting the SDK CLI's
        # hardcoded default instead of whatever the boss actually runs.
        self._resolved_model_recorded: bool = False

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
        self._socket_link_path: str | None = None

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

        # 2.5. If this daemon is a boss for any registered workers, rejoin
        # their #task-<suffix> channels. Without this, after a boss restart
        # `culture boss brief <worker>` fails the channel-membership
        # pre-check until the operator manually rejoins (v8.18.2-C).
        await self._rejoin_owned_task_channels()

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
        self._socket_link_path = ensure_socket_symlink(self._socket_path, self.agent.nick)

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
        """Cleanly shut down all components.

        Cancels every spawned background task — watchdog, poll, sleep
        scheduler, fire-and-forget background_tasks set, supervisor
        evaluation tasks — so the asyncio loop drains and the process
        actually exits. Without the comprehensive cancel, the daemon-log
        records ``agent_stop`` but the Python process stays alive holding
        its IRC nick + socket file, and the watchdog inside it can keep
        firing post-stop (v8.18.2-D: observed live during v8.18.1
        verification).
        """
        if self._idle_task is not None:
            self._idle_task.cancel()
            await asyncio.gather(self._idle_task, return_exceptions=True)
            self._idle_task = None

        if self._silent_death_task is not None:
            self._silent_death_task.cancel()
            await asyncio.gather(self._silent_death_task, return_exceptions=True)
            self._silent_death_task = None

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

        # Drain in-flight supervisor evaluations: these run off the SDK
        # consumer pump (v8.18.0 #8) and would otherwise keep the loop
        # alive past stop().
        if self._supervisor is not None:
            await self._supervisor.wait_for_evals()

        # Cancel any remaining fire-and-forget background tasks. Without
        # this, an in-flight DM send / hand-off / send_prompt would keep
        # the asyncio loop pinned and the process zombied.
        if self._background_tasks:
            for t in list(self._background_tasks):
                if not t.done():
                    t.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        remove_socket_symlink(self._socket_link_path)
        self._socket_link_path = None
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
                    if self._agent_runner is not None:
                        self._agent_runner.set_paused(True)
                    logger.info("Sleep schedule: pausing %s", self.agent.nick)
                elif not should_sleep and self._paused and not self._manually_paused:
                    self._paused = False
                    if self._agent_runner is not None:
                        self._agent_runner.set_paused(False)
                    logger.info("Sleep schedule: resuming %s", self.agent.nick)
                    self._maybe_rearm_watchdog()
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
            on_turn_complete=self._on_turn_complete,
            on_turn_failed=self._on_turn_failed,
            metrics=self._metrics,
            nick=self.agent.nick,
            boss=_boss_nick(self.agent),
        )
        await self._agent_runner.start()
        logger.info("AgentRunner started via SDK for %s", self.agent.nick)
        await self._daemon_log.record(
            "agent_start",
            model=self.agent.model,
            thinking=self.agent.thinking,
            directory=self.agent.directory,
        )
        # Reset the resolved-model latch so a restarted session can re-record
        # if the SDK happens to pick a different default this run (e.g. CLI
        # version bump between starts).
        self._resolved_model_recorded = False
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
            self._last_assistant_message_at = None
            self._last_turn_completed_at = None
            self._consecutive_failed_turns = 0
            self._idle_task = asyncio.create_task(self._idle_watchdog())

        # Silent-death watchdog (v8.19.8). When THIS agent is a boss,
        # also start a cross-process watcher for its owned workers:
        # detects worker daemons whose PIDs are dead but never wrote
        # ``agent_exit`` to their daemon-log (e.g., SIGKILL, OOM,
        # uncaught asyncio loop exception from the bundled CLI's
        # ``Stream closed`` bug). The existing intra-process
        # _idle_watchdog can't catch this — it only watches the
        # boss's own SDK state, not its workers' liveness.
        if "boss" in (getattr(self.agent, "tags", []) or []):
            if hasattr(self, "_silent_death_task") and self._silent_death_task is not None:
                self._silent_death_task.cancel()
            self._silent_death_warned: set[str] = set()
            self._silent_death_task = asyncio.create_task(self._silent_death_watchdog())

    async def _idle_watchdog(self) -> None:
        """Detect three classes of silent boss-owned worker, DM the boss each.

        Runs as a periodic poll (not one-shot) so a worker that engaged-then-
        went-silent is still caught — the bug behind the original one-shot
        watchdog that returned silently the moment ``_last_activation`` was
        set, regardless of whether the worker ever actually produced output.

        Classes (each warned at most once until state changes):

        * ``never_briefed`` — alive > IDLE_GRACE_SECONDS with no mention/poll/
          invite ever landed.
        * ``stalled_pre_engagement`` — brief landed (_last_activation set) but
          no AssistantMessage produced after STALL_GRACE_SECONDS. SDK hang
          (rate-limit, extended-thinking never resolving, etc).
        * ``stalled_post_engagement`` — already engaged but no new
          AssistantMessage in STALL_GRACE_SECONDS. The class the old watchdog
          could never see because _engaged=True disabled it.

        On resume from pause, the watchdog is re-armed (see ``_maybe_rearm_watchdog``).
        """
        armed_at = time.time()
        state: dict = {"warned_state": None}
        try:
            while True:
                try:
                    await asyncio.sleep(WATCHDOG_POLL_SECONDS)
                except asyncio.CancelledError:
                    return
                if await self._watchdog_tick(armed_at, state):
                    return
        except asyncio.CancelledError:
            return

    async def _watchdog_tick(self, armed_at: float, state: dict) -> bool:
        """One iteration of the watchdog loop. Returns True if the caller
        should stop looping (paused / runner dead). Public for tests so the
        watchdog can be driven deterministically without sleeping."""
        if self._paused:
            return True
        if self._agent_runner is None or not self._agent_runner.is_running():
            return True
        now = time.time()
        new_state: str | None = None
        since_ts: float = now
        if not self._engaged:
            if self._last_activation is None:
                if now - armed_at >= IDLE_GRACE_SECONDS:
                    new_state = "never_briefed"
                    since_ts = armed_at
            else:
                if now - self._last_activation >= STALL_GRACE_SECONDS:
                    new_state = "stalled_pre_engagement"
                    since_ts = self._last_activation
        else:
            last_msg = self._last_assistant_message_at
            last_turn = self._last_turn_completed_at
            # "Looping with no progress": worker is producing AssistantMessages
            # (so _last_assistant_message_at keeps refreshing) but no turn has
            # completed in STALL_GRACE_SECONDS. Symptom of the SDK CLI
            # ``Stream closed`` retry pattern: same tool_use re-issued on
            # every loop iteration, each time as a fresh AssistantMessage,
            # but the turn never finishes. Distinct from stalled_post_
            # engagement (which fires when AssistantMessages stop entirely).
            # Check FIRST so the more-specific class wins over post-engagement.
            if (
                last_msg is not None
                and last_turn is not None
                and now - last_msg < STALL_GRACE_SECONDS
                and now - last_turn >= STALL_GRACE_SECONDS
            ):
                new_state = "stalled_in_retry_loop"
                since_ts = last_turn
            elif last_msg is not None and now - last_msg >= STALL_GRACE_SECONDS:
                new_state = "stalled_post_engagement"
                since_ts = last_msg
            elif self._consecutive_failed_turns >= CONSECUTIVE_FAILED_TURN_THRESHOLD:
                # The intermittent-success retry pattern caught here: even
                # if turns occasionally complete (resetting last_turn_
                # completed_at and last_msg), a sustained failure rate
                # means the worker is mostly thrashing. Use last_msg as
                # the timestamp since we don't track when the failures
                # started; the boss DM names the failure count.
                new_state = "stalled_in_failed_retry"
                since_ts = last_msg if last_msg is not None else now
        if not new_state or new_state == state.get("warned_state"):
            return False
        state["warned_state"] = new_state
        since = int(now - since_ts)
        await self._notify_boss(
            "idle_warning",
            self._stall_message(new_state, since),
            reason=new_state,
            since_seconds=since,
        )
        return False

    async def _silent_death_watchdog(self) -> None:
        """Detect worker daemons that died without writing ``agent_exit``.

        The intra-process ``_idle_watchdog`` watches the boss's own SDK
        state — it cannot see worker daemons crash. When the SDK CLI's
        ``Stream closed`` bug propagates an uncaught exception up to
        the worker's asyncio loop (observed live during the v8.18.7
        fleet), the worker's daemon process exits without going through
        ``daemon.stop()``, so no ``agent_exit`` record lands in its
        daemon-log. The boss is left believing the worker is healthy.

        This watchdog polls every ``WATCHDOG_POLL_SECONDS`` and for each
        owned worker (from the manifest):

        1. Reads the worker's PID file.
        2. Confirms the process is alive.
        3. If dead AND the daemon-log's last action isn't
           ``agent_exit`` / ``agent_stop``, surfaces ``idle_warning``
           with ``reason=silent_death_after_done`` to the boss.

        One-shot per worker per session — the warned set prevents
        repeated DMs for the same dead worker; cleared on watchdog
        restart.
        """
        # Defer the import so a non-boss daemon never pays the cost.
        from culture.config import load_config_or_default

        try:
            while True:
                await asyncio.sleep(WATCHDOG_POLL_SECONDS)
                if self._paused:
                    continue
                # Re-read the manifest each tick — workers spawned mid-
                # session need to be picked up. Fail soft on any I/O error
                # so a transient manifest hiccup never kills the watchdog.
                try:
                    # No config-path attribute on DaemonConfig, so resolve
                    # from CULTURE_HOME (mirrors what culture.cli does).
                    from culture.clients._perm_broker import culture_home

                    manifest_path = os.path.join(culture_home(), "server.yaml")
                    cfg = load_config_or_default(manifest_path)
                    owned = [
                        a.nick
                        for a in cfg.agents
                        if (getattr(a, "boss", "") == self.agent.nick)
                        and not getattr(a, "archived", False)
                    ]
                except Exception:  # noqa: BLE001
                    continue
                for nick in owned:
                    if nick in self._silent_death_warned:
                        continue
                    pid = read_pid(f"agent-{nick}")
                    # No pidfile → worker never started or cleanly stopped;
                    # no diagnosis to make. Alive PID → still running.
                    if not pid or is_process_alive(pid):
                        continue
                    # Process dead. Check daemon-log for a clean exit
                    # marker. If the tail is anything other than
                    # ``agent_exit`` or ``agent_stop``, this was a silent
                    # death.
                    if self._daemon_log_indicates_clean_exit(nick):
                        continue
                    self._silent_death_warned.add(nick)
                    await self._notify_boss(
                        "idle_warning",
                        f"worker {nick} died without writing agent_exit "
                        "(silent death — likely SDK CLI Stream-closed "
                        "or OOM). PID is dead; daemon-log has no exit "
                        "marker. Investigate or re-spawn.",
                        reason="silent_death_after_done",
                        worker=nick,
                    )
        except asyncio.CancelledError:
            return

    def _daemon_log_indicates_clean_exit(self, nick: str) -> bool:
        """True if *nick*'s daemon-log tail contains a clean exit marker.

        Reads only the last 4 KiB of the file (lifecycle events are
        small — at most a few hundred bytes per action; 4 KiB is plenty
        to find the most recent ``agent_exit`` / ``agent_stop``).
        Returns False on any I/O error — better to false-positive a
        silent death warning than to silently miss one.
        """
        from culture.clients._daemon_log import daemon_log_path_for

        path = daemon_log_path_for(nick)
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 4096))
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            return False
        # Walk lines in reverse — newest action first.
        import json as _json

        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            action = rec.get("action")
            if action in ("agent_exit", "agent_stop"):
                return True
            # Stop at the most recent action of any kind — only the tail
            # matters for "did the process exit cleanly".
            return False
        return False

    async def _rejoin_owned_task_channels(self) -> None:
        """Rejoin ``#task-<suffix>`` channels for workers whose manifest-
        recorded boss is this daemon's nick. Without this, after a boss
        restart, ``culture boss brief <worker>`` fails the channel-
        membership pre-check until the operator manually rejoins via IPC
        — a real bug observed live during v8.18.1 dogfooding.

        Best-effort: a transport / manifest read error logs a warning but
        does not block startup. A channel already in ``self.agent.channels``
        was joined by transport.connect() and is skipped.
        """
        if self._transport is None:
            return
        try:
            from culture.clients._perm_broker import culture_home
            from culture.config import load_config_or_default

            server_yaml = os.path.join(culture_home(), "server.yaml")
            config = load_config_or_default(server_yaml, fallback=server_yaml)
        except Exception:  # noqa: BLE001 — bad manifest must not block startup
            logger.warning("Failed to load manifest for task-channel rejoin", exc_info=True)
            return
        me = self.agent.nick
        already = set(self.agent.channels)
        for ag in config.agents:
            if getattr(ag, "boss", "") != me:
                continue
            nick = getattr(ag, "nick", "")
            suffix = nick.split("-", 1)[1] if "-" in nick else nick
            channel = f"#task-{suffix}"
            if channel in already:
                continue
            try:
                await self._transport.join_channel(channel)
                logger.info("Rejoined owned task channel %s", channel)
            except Exception:  # noqa: BLE001 — one channel failure must not stop the rest
                logger.warning("Failed to rejoin %s", channel, exc_info=True)

    def _maybe_rearm_watchdog(self) -> None:
        """Start a fresh idle watchdog task if this is a boss-owned worker and
        the previous task is gone (the watchdog returns when paused, so resume
        must respawn it to keep coverage)."""
        if not _boss_nick(self.agent):
            return
        if self._idle_task is not None and not self._idle_task.done():
            return
        if self._agent_runner is None or not self._agent_runner.is_running():
            return
        self._idle_task = asyncio.create_task(self._idle_watchdog())

    def _stall_message(self, reason: str, since: int) -> str:
        nick = self.agent.nick
        if reason == "never_briefed":
            return (
                f"[idle] worker {nick} has produced no activity {since}s "
                f"after start — it may not be in its #task channel or was "
                f"never briefed. Check and re-drive it."
            )
        if reason == "stalled_pre_engagement":
            return (
                f"[stall] worker {nick} received its brief {since}s ago but "
                f"has not produced any output — the SDK call may have hung. "
                f"Check its audit, consider re-driving or restarting."
            )
        if reason == "stalled_in_retry_loop":
            return (
                f"[stall] worker {nick} has been issuing AssistantMessages "
                f"(tool calls) but has not COMPLETED a turn in {since}s — "
                f"likely a tool-retry loop (e.g. SDK CLI 'Stream closed' on "
                f"every Write). Check its audit for the repeating tool_use, "
                f"consider re-driving with a different approach."
            )
        if reason == "stalled_in_failed_retry":
            return (
                f"[stall] worker {nick} has accumulated "
                f"{self._consecutive_failed_turns} consecutive failed turns "
                f"(intermittent successes between SDK errors). The work is "
                f"thrashing — check its audit for the recurring error pattern "
                f"and consider re-driving with a different tool or approach."
            )
        return (
            f"[stall] worker {nick} engaged but has been silent for {since}s "
            f"(no new turns). Check its audit, consider re-driving."
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
        # Persist for boss agents so a restart can re-load the brief.
        # All-backends rule + Qodo pattern-b finding.
        if _mission_persistence.is_boss_agent(self.agent):
            _mission_persistence.persist_mention(
                self.agent.nick,
                sender,
                text,
            )

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
        # Latch the model the SDK actually picked. Only matters when the
        # YAML omitted ``model`` (so ``agent_start.model`` is empty) — in
        # that case the only way for ``_boss_inherits`` to know the real
        # runtime model is to observe it on a live AssistantMessage.
        # ``msg["model"]`` is the resolved model string from the SDK.
        #
        # The latch is set AFTER the write, not before — ``DaemonLog.record``
        # swallows I/O failures (logs + returns) for fault-tolerance, so
        # setting the latch before the write would permanently mask the leak
        # if the first write hit a transient filesystem error.
        # Per Qodo PR #24 #2.
        if not self._resolved_model_recorded:
            observed = msg.get("model") if isinstance(msg, dict) else None
            if isinstance(observed, str) and observed:
                await self._daemon_log.record("model_resolved", model=observed)
                self._resolved_model_recorded = True
        # Drive the stall watchdog: every AssistantMessage resets the "time
        # since last turn" timer. A worker that engaged-then-went-silent is
        # only catchable because we track this.
        self._last_assistant_message_at = time.time()
        await self._audit.write(msg)

        if self._supervisor:
            await self._supervisor.observe(msg)

        self._capture_agent_status(msg)

    async def _on_turn_failed(self) -> None:
        """Track consecutive failed turns. Fires when AgentRunner's
        _process_turn catches an exception (CLIConnectionError, Stream
        closed, etc) — non-fatal turn errors that the session recovers
        from. The watchdog's `stalled_in_failed_retry` class uses this
        counter to catch intermittent-success retry loops that v8.18.4's
        stalled_in_retry_loop misses (a Bash-workaround turn between
        failed Writes keeps that timer fresh).
        """
        self._consecutive_failed_turns += 1

    async def _on_turn_complete(self) -> None:
        """Record the timestamp of the last cleanly-completed turn.

        Fires from ``AgentRunner._process_turn`` when its ``async for
        query()`` loop ends without raising — i.e. the SDK yielded a
        final ``ResultMessage`` and the session is back in the queue-wait
        state. Distinct from ``_on_agent_message`` (which fires for every
        AssistantMessage including mid-turn tool_use messages), so the
        stall watchdog can distinguish "engaged + making progress" from
        "engaged + stuck in a tool-retry loop" (v8.18.4 — observed live
        when a worker hit the SDK CLI's ``Stream closed`` error on every
        ``Write`` and looped retrying). Also resets the consecutive-
        failed-turn counter (v8.18.5).
        """
        self._last_turn_completed_at = time.time()
        self._consecutive_failed_turns = 0

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

    async def _notify_boss(
        self, action: str, message: str, *, also_daemon_log: bool = True, **detail: Any
    ) -> bool:
        """Send a structured notification to the parent boss with daemon-log
        fallback. Returns True if the IRC DM succeeded.

        Every boss-facing alert (stall, circuit_open, perm_request, supervisor
        escalation) routes through this helper so the daemon-log is the
        single source of truth for "what was raised" — the dashboard can read
        and surface it even when IRC is broken / boss is dead. The IRC DM is
        best-effort; transport errors do not raise.

        ``action`` is the daemon-log action name. ``detail`` is the daemon-log
        detail dict, so caller can use any keys (including 'reason') without
        colliding with this helper's signature.
        """
        if also_daemon_log:
            await self._daemon_log.record(action, **detail)
        boss = _boss_nick(self.agent)
        if not boss or self._transport is None:
            return False
        try:
            await self._transport.send_privmsg(boss, message)
            return True
        except Exception:  # noqa: BLE001 — DM is advisory; daemon-log already landed
            logger.warning("Failed to DM boss %s with %s", boss, action, exc_info=True)
            return False

    async def _on_perm_request(self, payload: dict) -> None:
        """Surface a worker permission request to its boss.

        Fired by this worker's PermissionBroker when a tool call routes to the
        boss. DMs the owning boss (``self.agent.boss``) AND records to
        daemon-log so the dashboard sees the request even if the DM fails.
        """
        boss = _boss_nick(self.agent)
        if not boss:
            return
        tool = payload.get("tool_name", "?")
        req_id = payload.get("id", "?")
        preview = self._perm_input_preview(tool, payload.get("input", {}))
        notice = (
            f"[perm] worker {self.agent.nick} wants {tool}: {preview} "
            f"— id {req_id} (approve/deny)"
        )
        await self._notify_boss(
            "perm_request_notified",
            notice,
            tool=tool,
            request_id=req_id,
        )

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
            base = self.agent.system_prompt
        else:
            base = (
                f"You are {self.agent.nick}, an AI agent on the culture IRC network.\n"
                "You have IRC tools available via the irc skill. Use them to communicate.\n"
                f"Your working directory is {self.agent.directory}.\n"
                "To talk to your boss, reply in your task channel. Your boss reads "
                "channel replies via the bridge. There is no IRC tool — do not search for one.\n"
                "When you finish a task, share results in the appropriate channel with irc_send()."
            )
        # Boss agents: append persisted mission so a restart re-loads
        # the brief into the SDK system context (all-backends).
        extension = _mission_persistence.build_system_prompt_extension(self.agent.nick)
        # v8.19.24: worker agents get the living channel brief for every
        # task channel they're configured to join. So a worker spawned
        # into an in-flight Channel boots with "here's what's been
        # decided / done / open" rather than just IRC HISTORY. The
        # brief is the team's running onboarding doc; the seed (the
        # immutable initial mission) is already linked separately.
        from culture.clients import _channel_brief

        for channel in self.agent.channels or []:
            if (
                isinstance(channel, str)
                and channel.startswith("#")
                and _channel_brief.has_brief(channel)
            ):
                extension += _channel_brief.system_prompt_extension(channel)
        return base + extension

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

        On open, the boss is notified via THREE channels so the message lands
        even when one is broken:
        1. daemon-log (local FS, always succeeds — the dashboard reads this)
        2. webhook (for ops dashboards / Slack — fires if configured)
        3. direct DM to the parent boss over IRC (the boss may not monitor the
           webhook channel; an in-team DM is the channel a boss agent actually
           reads). Without this, audit1's finding #4 stands: an open circuit
           is invisible to the boss until someone manually queries status.
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
            await self._notify_boss(
                "circuit_open",
                (
                    f"[circuit_open] worker {self.agent.nick} crashed "
                    f"{len(self._crash_times)} times in {CRASH_WINDOW_SECONDS}s "
                    f"— circuit breaker opened, NOT restarting. Investigate "
                    f"its audit log and decide whether to restart manually."
                ),
                count=len(self._crash_times),
                window_s=CRASH_WINDOW_SECONDS,
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
        """Escalate via webhook + daemon-log + IRC DM when supervisor
        exhausts whispers. Without the boss DM the escalation is invisible
        to the agent that's supposed to act on it (boss reads IRC, not the
        webhook channel)."""
        if self._webhook:
            await self._webhook.fire(
                AlertEvent(
                    event_type="agent_spiraling",
                    nick=self.agent.nick,
                    message=f"[ESCALATION] {self.agent.nick}: {message}",
                )
            )
        await self._notify_boss(
            "supervisor_escalation",
            f"[escalation] worker {self.agent.nick}: {message}",
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
        # Make pause authoritative at the SDK runner too — a half-pause that
        # only gates mention/poll surfaces still lets queued handoff/compact
        # work run, so the operator's halt is not actually a halt.
        if self._agent_runner is not None:
            self._agent_runner.set_paused(True)
        logger.info("Agent %s paused (manual)", self.agent.nick)
        self._log_action_bg("pause", manual=True)
        return make_response(req_id, ok=True)

    def _ipc_resume(self, req_id: str, msg: dict) -> dict:
        self._paused = False
        self._manually_paused = False
        if self._agent_runner is not None:
            self._agent_runner.set_paused(False)
        # Supervisor sets paused=True on escalation and has no auto-reset, so
        # without this an escalated worker stays unsupervised forever even
        # after the operator un-pauses it. (Workflow finding: "supervisor
        # self-pauses on first escalation and never un-pauses".)
        if self._supervisor is not None:
            self._supervisor.resume()
        logger.info("Agent %s resumed", self.agent.nick)
        self._log_action_bg("resume", manual=True)
        # Re-arm the watchdog: it returns when _paused is True, so resuming
        # without restarting it leaves the worker un-monitored.
        self._maybe_rearm_watchdog()
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
        # Validate channel name against CR/LF/NUL injection (Qodo PR #30 #2).
        # The prefix check is subsumed by validate_channel_name (which also
        # accepts &/+/! per RFC 2812).
        from culture.agentirc.irc_targets import InvalidIRCTarget, validate_channel_name

        try:
            validate_channel_name(channel)
        except InvalidIRCTarget as exc:
            return make_response(req_id, ok=False, error=str(exc))
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
        reason = (msg.get("reason") or "").strip()
        # When the orchestrator supplies a reason (typically when switching
        # the agent to a new task), inject it as a prompt BEFORE the compact
        # so the agent's handoff captures the transition context — then the
        # post-compact reminder explains why the context shrunk.
        if reason:
            await self._agent_runner.send_prompt(
                "[orchestrator-compact] You are about to compact your "
                "context. Reason from orchestrator: " + reason + "\n\n"
                "Write your handoff with this reason in mind, then let the "
                "compact happen — you'll be reminded to re-read on the next "
                "turn."
            )
        await self._agent_runner.send_prompt("/compact")
        if reason:
            await self._agent_runner.send_prompt(
                "[post-compact] You just compacted at the orchestrator's "
                "request. The reason was: " + reason + "\n\n"
                "Re-read your handoff at handoff/<your-nick>.md to recover "
                "the prior context, then continue with the new direction."
            )
        await self._daemon_log.record("compact", trigger="ipc", reason=reason)
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
