"""Bridge daemon — IRC + IPC + audit + daemon-log surface, NO SDK loop.

The bridge is the rearchitected ``local-boss`` (and project-named-boss)
process under the CC-IS-the-boss design. It holds the IRC connection,
audit log, daemon log, IPC socket, MessageBuffer, and watchdogs that
don't need an SDK. It does NOT spawn an ``AgentRunner`` and does NOT
run an autonomous Claude Agent SDK loop. The CC session connects via
IPC and is the boss brain.

This file was forked from ``culture/clients/claude/daemon.py`` per
the cite-don't-import rule (``CLAUDE.md``); references to the SDK
runner, supervisor, sleep scheduler, poll loop, context-watch handoff,
and crash-restart circuit breaker have been removed. The IPC surface
preserves the 13 IRC/thread verbs, repurposes ``compact`` as a
daemon-log writer, reshapes ``status`` (drops ``circuit_open``, adds
``cc_connected``), and adds NET-NEW verbs for the CC → bridge handoff
(``cc_session_start``, ``cc_session_end``, ``set_runtime_model``,
``sdk_event``, ``daemon_log_record``, ``inbound_*_ack``, etc.). The
``inbound_dm`` / ``inbound_mention`` / ``inbound_roominvite`` /
``perm_request`` / ``perm_decision`` verbs are PUSHED by the bridge
to CC via the existing whisper queue.

See ``docs/superpowers/specs/2026-06-03-mesh-rearchitecture-plan.md``
Phase 2 + ``protocol/extensions/bridge-ipc.md`` for the verb table.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any  # noqa: F401 — used in IPC verb signatures

from culture.aio import maybe_await
from culture.clients._audit import AuditWriter
from culture.clients._daemon_log import DaemonLog
from culture.clients._socket_link import ensure_socket_symlink, remove_socket_symlink
from culture.clients.bridge._spool import spool_inbound
from culture.clients.bridge.ipc import make_response
from culture.clients.bridge.irc_transport import IRCTransport
from culture.clients.bridge.message_buffer import MessageBuffer
from culture.clients.bridge.socket_server import SocketServer
from culture.clients.claude.config import AgentConfig, DaemonConfig
from culture.clients.claude.telemetry import init_harness_telemetry
from culture.clients.claude.webhook import AlertEvent, WebhookClient
from culture.pidfile import is_process_alive, read_pid, remove_pid, write_pid

logger = logging.getLogger(__name__)

# Cross-process worker watchdog cadence. Independent of any SDK-loop
# pacing because the bridge has no SDK loop.
WATCHDOG_POLL_SECONDS = 30

# IPC validation error messages
_ERR_MISSING_CHANNEL = "Missing 'channel'"
_ERR_MISSING_CHANNEL_THREAD = "Missing 'channel' or 'thread'"
_ERR_MISSING_CHANNEL_THREAD_MSG = "Missing 'channel', 'thread', or 'message'"
_ERR_CHANNEL_PREFIX = "Channel name must start with '#'"


class ManifestInvariantError(RuntimeError):
    """Bridge refused to start because the server.yaml manifest is in a
    state the bridge cannot safely adopt (e.g. two ``boss``-tagged entries
    sharing the same nick, or workers pointing at a nick the bridge does
    not own)."""


class AgentDaemon:
    """Central orchestrator for the bridge process: IRC transport,
    socket server, audit log, daemon log. NO SDK runner, NO supervisor,
    NO sleep scheduler, NO poll loop, NO context-watch."""

    def __init__(
        self,
        config: DaemonConfig,
        agent: AgentConfig,
        socket_dir: str | None = None,
        skip_claude: bool = True,
    ) -> None:
        # Bridge is SDK-less by construction. ``skip_claude`` is kept as a
        # constructor flag for symmetry with the claude backend (so existing
        # call sites can pass it explicitly during the transition), but the
        # bridge refuses to ever spawn an AgentRunner regardless. A caller
        # that passes ``skip_claude=False`` here is logged and corrected.
        if not skip_claude:
            logger.warning(
                "AgentDaemon (bridge) ignoring skip_claude=False — bridge "
                "never owns an SDK loop. CC is the boss; the bridge is a "
                "thin transport-only surface."
            )
        self.config = config
        self.agent = agent
        self.skip_claude = True

        self._socket_path = self._resolve_socket_path(socket_dir, agent.nick)

        self._buffer: MessageBuffer | None = None
        self._transport: IRCTransport | None = None
        self._webhook: WebhookClient | None = None
        self._socket_server: SocketServer | None = None
        self._tracer = None
        self._metrics = None
        self._audit: AuditWriter = AuditWriter(nick=agent.nick)
        self._daemon_log: DaemonLog = DaemonLog(nick=agent.nick)

        # CC session state — set by ``cc_session_start`` IPC verb,
        # cleared by ``cc_session_end`` or socket disconnect. The
        # ``status`` IPC verb returns this as ``cc_connected``.
        self._cc_connected: bool = False
        # Runtime model resolved at CC's first AssistantMessage; pushed
        # via the ``set_runtime_model`` verb. Replaces the SDK-side
        # ``model_resolved`` latch in the old boss daemon.
        self._runtime_model: str = ""

        # Cross-process watchdog for owned worker daemons that died
        # without writing ``agent_exit`` (v8.19.8 — silent-death after
        # DONE-FINAL pattern).
        self._silent_death_task: asyncio.Task | None = None
        self._silent_death_warned: set[str] = set()
        # MessageBuffer cursor persistence path (Phase 2.7); set when
        # the bridge starts so ``stop()`` can re-save before exit.
        self._cursor_path: str | None = None

        # Phase 3 — pending CHATHISTORY drain: msg_id → entry awaiting
        # CC ack. On ack, the bridge issues CHATHISTORY DELETE <msg_id>
        # to the server which marks the spool row delivered.
        self._pending_chathistory: dict[str, dict] = {}

        # Background tasks (prevent GC of fire-and-forget tasks).
        self._background_tasks: set[asyncio.Task] = set()
        self._socket_link_path: str | None = None

        # Graceful shutdown
        self._stop_event: asyncio.Event | None = None
        self._pid_name: str = ""

        # IPC dispatch table. See ``protocol/extensions/bridge-ipc.md``
        # for the verb contract. Preserved IRC/thread verbs (13),
        # repurposed (``compact`` — daemon-log only), reshaped
        # (``status``, ``shutdown``), and NET-NEW (cc_session_*,
        # set_runtime_model, sdk_event, daemon_log_record, inbound_*_ack,
        # perm_decision_ack).
        self._ipc_dispatch: dict = {
            # Preserved (13 verbs)
            "irc_send": self._ipc_irc_send,
            "irc_read": self._ipc_irc_read,
            "irc_join": self._ipc_irc_join,
            "irc_part": self._ipc_irc_part,
            "irc_channels": self._ipc_irc_channels,
            "irc_who": self._ipc_irc_who,
            "irc_topic": self._ipc_irc_topic,
            "irc_ask": self._ipc_irc_ask,
            "irc_thread_create": self._ipc_irc_thread_create,
            "irc_thread_reply": self._ipc_irc_thread_reply,
            "irc_threads": self._ipc_irc_threads,
            "irc_thread_close": self._ipc_irc_thread_close,
            "irc_thread_read": self._ipc_irc_thread_read,
            # Repurposed: now daemon-log-only (no SDK to compact).
            "compact": self._ipc_compact,
            # Preserved with reshaped response.
            "status": self._ipc_status,
            "shutdown": self._ipc_shutdown,
            # NET-NEW
            "cc_session_start": self._ipc_cc_session_start,
            "cc_session_end": self._ipc_cc_session_end,
            "set_runtime_model": self._ipc_set_runtime_model,
            "sdk_event": self._ipc_sdk_event,
            "daemon_log_record": self._ipc_daemon_log_record,
            "inbound_dm_ack": self._ipc_inbound_dm_ack,
            "inbound_mention_ack": self._ipc_inbound_mention_ack,
            "inbound_roominvite_ack": self._ipc_inbound_roominvite_ack,
            "perm_decision_ack": self._ipc_perm_decision_ack,
        }

    # ------------------------------------------------------------------
    # Socket path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_socket_path(socket_dir: str | None, nick: str) -> str:
        """Resolve the bridge IPC socket path.

        Priority:
        1. Explicit ``socket_dir`` argument (test / caller override).
        2. ``$XDG_RUNTIME_DIR/culture/<nick>.sock`` when set.
        3. macOS: ``~/Library/Caches/culture/run/<nick>.sock`` — a
           user-private location, NOT the world-traversable ``/tmp``.
        4. Linux fallback: ``/tmp/culture-<nick>.sock``.

        macOS's ``/tmp`` parent dir is world-traversable; using it would
        leak the socket path to other users. The user-private
        ``~/Library/Caches/culture/run/`` directory is the correct
        fallback (Phase 2.5 + TV-4 of the rearchitecture plan).
        """
        if socket_dir is not None:
            return os.path.join(socket_dir, f"culture-{nick}.sock")
        xdg = os.environ.get("XDG_RUNTIME_DIR", "")
        if xdg:
            xdg_culture = os.path.join(xdg, "culture")
            try:
                os.makedirs(xdg_culture, mode=0o700, exist_ok=True)
            except OSError:
                pass
            return os.path.join(xdg_culture, f"{nick}.sock")
        # macOS: prefer ~/Library/Caches/culture/run/ over /tmp.
        import sys

        if sys.platform == "darwin":
            home = os.path.expanduser("~")
            mac_dir = os.path.join(home, "Library", "Caches", "culture", "run")
            try:
                os.makedirs(mac_dir, mode=0o700, exist_ok=True)
            except OSError:
                pass
            return os.path.join(mac_dir, f"{nick}.sock")
        return os.path.join("/tmp", f"culture-{nick}.sock")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all bridge components in dependency order.

        Order:
            1. PID file
            2. OTEL harness telemetry
            3. Manifest invariant check (refuses to start on conflict)
            4. MessageBuffer (with cursor restore)
            5. IRC transport (push-shaped via on_mention / on_roominvite)
            6. Rejoin owned ``#task-*`` channels (preserved from claude daemon)
            7. Webhook client (used by ``irc_ask`` notifications)
            8. Unix socket server (peer-uid-checked IPC)
            9. Silent-death watchdog (only if this bridge is a ``boss``-tagged
               manifest entry)
        """
        # 0. PID file
        self._pid_name = f"agent-{self.agent.nick}"
        write_pid(self._pid_name, os.getpid())

        # 0.5. OTEL telemetry (no-op if telemetry.enabled=False)
        self._tracer, self._metrics = init_harness_telemetry(self.config)

        # 0.7. Manifest invariant (Phase 2.6 — refuse to start on conflict).
        self._enforce_manifest_invariant()

        # 1. Message buffer (with cursor restore from disk if present).
        self._buffer = MessageBuffer(max_per_channel=self.config.buffer_size)
        self._cursor_path = self._resolve_cursor_path(self.agent.nick)
        try:
            self._buffer.load(self._cursor_path)
        except Exception:  # noqa: BLE001 — cursor load must not block startup
            logger.warning(
                "Failed to load MessageBuffer cursors from %s; starting fresh",
                self._cursor_path,
                exc_info=True,
            )

        # 2. IRC transport
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
            backend="bridge",
            # Phase 3 — DM spool drain on connect.
            on_welcome=self._on_irc_welcome,
            on_chathistory_entry=self._on_chathistory_entry,
            on_chathistory_end=self._on_chathistory_end,
        )
        await self._transport.connect()

        # 2.5. Rejoin owned task channels.
        await self._rejoin_owned_task_channels()

        # 3. Webhook client.
        self._webhook = WebhookClient(
            config=self.config.webhooks,
            irc_send=self._transport.send_privmsg,
        )

        # 4. Unix socket server with IPC handler.
        self._socket_server = SocketServer(
            path=self._socket_path,
            handler=self._handle_ipc,
        )
        await self._socket_server.start()
        self._socket_link_path = ensure_socket_symlink(self._socket_path, self.agent.nick)

        # 5. Silent-death watchdog (only for boss-tagged bridges).
        if "boss" in (getattr(self.agent, "tags", []) or []):
            self._silent_death_task = asyncio.create_task(self._silent_death_watchdog())

        logger.info(
            "Bridge AgentDaemon started for %s (socket=%s)",
            self.agent.nick,
            self._socket_path,
        )

    async def stop(self) -> None:
        """Graceful shutdown.

        Order (Phase 2.8 — EL-6 lesson: CHANARCHIVE before PART):
            1. Save MessageBuffer cursors.
            2. CHANARCHIVE every owned ``#task-*`` channel still in
               ``transport.channels`` (so the channel survives the bridge
               going offline; otherwise the IRCd auto-deletes it on last
               PART).
            3. PART each remaining channel.
            4. Close IRC transport.
            5. Cancel watchdogs + drain background tasks.
            6. Remove socket symlink + close socket server.
            7. Remove PID file.

        Audit writer is process-local and flushes on every write; no
        explicit close call needed (the OS closes the fd on process
        exit).
        """
        # 1. Save MessageBuffer cursors (Phase 2.7).
        if self._buffer is not None and self._cursor_path:
            try:
                self._buffer.save(self._cursor_path)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to save MessageBuffer cursors to %s",
                    self._cursor_path,
                    exc_info=True,
                )

        # 2 + 3. CHANARCHIVE owned #task-* channels, then PART everything.
        if self._transport is not None:
            owned_task_channels = self._owned_task_channels()
            for channel in owned_task_channels:
                try:
                    await self._transport.send_raw(f"CHANARCHIVE {channel}")
                    logger.info("CHANARCHIVE %s (owned task channel)", channel)
                except Exception:  # noqa: BLE001 — best effort
                    logger.warning(
                        "Failed to CHANARCHIVE %s during shutdown",
                        channel,
                        exc_info=True,
                    )
            for channel in list(self._transport.channels):
                try:
                    await self._transport.part_channel(channel)
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to PART %s during shutdown", channel, exc_info=True)

        # 4. Close IRC transport.
        if self._transport is not None:
            await self._transport.disconnect()
            self._transport = None

        # 5. Cancel watchdogs + drain background tasks.
        if self._silent_death_task is not None:
            self._silent_death_task.cancel()
            await asyncio.gather(self._silent_death_task, return_exceptions=True)
            self._silent_death_task = None

        if self._background_tasks:
            for t in list(self._background_tasks):
                if not t.done():
                    t.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        # 6. Remove socket symlink + close socket server.
        remove_socket_symlink(self._socket_link_path)
        self._socket_link_path = None
        if self._socket_server is not None:
            await self._socket_server.stop()
            self._socket_server = None

        # 7. Remove PID file.
        if self._pid_name:
            remove_pid(self._pid_name)

        logger.info("Bridge AgentDaemon stopped for %s", self.agent.nick)

    @staticmethod
    def _resolve_cursor_path(nick: str) -> str:
        """Resolve the MessageBuffer cursor persistence path
        (``~/.culture/bridge/cursors-<nick>.json``)."""
        from culture.clients._perm_broker import culture_home

        path = os.path.join(culture_home(), "bridge")
        try:
            os.makedirs(path, mode=0o700, exist_ok=True)
        except OSError:
            pass
        safe = nick.replace("/", "_").replace("..", "_")
        return os.path.join(path, f"cursors-{safe}.json")

    def _owned_task_channels(self) -> list[str]:
        """Return the subset of ``transport.channels`` that are owned
        ``#task-*`` channels for workers whose ``boss:`` field equals
        this bridge's nick.

        Used by ``stop()`` so CHANARCHIVE fires only for channels the
        bridge actually owns. A ``#task-*`` channel a worker happens to
        have invited the bridge into without manifest ownership is left
        alone — its real owner will archive it.
        """
        if self._transport is None:
            return []
        try:
            from culture.clients._perm_broker import culture_home
            from culture.config import load_config_or_default

            server_yaml = os.path.join(culture_home(), "server.yaml")
            cfg = load_config_or_default(server_yaml, fallback=server_yaml)
        except Exception:  # noqa: BLE001
            return []
        me = self.agent.nick
        owned_suffixes: set[str] = set()
        for ag in cfg.agents:
            if getattr(ag, "boss", "") != me:
                continue
            nick = getattr(ag, "nick", "")
            suffix = nick.split("-", 1)[1] if "-" in nick else nick
            owned_suffixes.add(suffix)
        out = []
        for channel in self._transport.channels:
            if not channel.startswith("#task-"):
                continue
            suffix = channel[len("#task-") :]
            if suffix in owned_suffixes:
                out.append(channel)
        return out

    async def _graceful_shutdown(self) -> None:
        """Trigger a graceful shutdown, signaling any waiting stop event."""
        logger.info("Graceful shutdown requested for %s", self.agent.nick)
        if self._stop_event is not None:
            self._stop_event.set()
        else:
            await self.stop()

    def set_stop_event(self, event: asyncio.Event) -> None:
        """Register an external stop event that ``_graceful_shutdown`` will signal."""
        self._stop_event = event

    # ------------------------------------------------------------------
    # Manifest invariant (Phase 2.6)
    # ------------------------------------------------------------------

    def _enforce_manifest_invariant(self) -> None:
        """Refuse to start when the manifest is in an unsafe shape.

        Rules:
        - More than one manifest entry tagged ``boss`` with this bridge's
          nick → two CC sessions are trying to claim the same identity.
          Refuse with ``ManifestInvariantError`` (the caller — typically
          ``culture agent start`` — translates this into ``sys.exit(2)``).
        - Zero manifest entries for this nick AND workers reference it
          via their ``boss:`` field → the manifest is corrupt (the boss
          entry was deleted but its workers point at it). Refuse.
        - Zero manifest entries AND no workers reference this nick →
          this is FIRST-RUN. Proceed silently (the bridge's start path
          writes its manifest entry on its own).

        Only enforced when this bridge is itself ``boss``-tagged. A
        non-boss bridge (e.g. a future ``role: dashboard-relay`` use
        case) sits this check out.
        """
        tags = list(getattr(self.agent, "tags", []) or [])
        if "boss" not in tags:
            return
        try:
            from culture.clients._perm_broker import culture_home
            from culture.config import load_config_or_default

            server_yaml = os.path.join(culture_home(), "server.yaml")
            cfg = load_config_or_default(server_yaml, fallback=server_yaml)
        except Exception:  # noqa: BLE001 — bad manifest must not silently pass
            logger.warning(
                "Manifest unreadable at startup; skipping invariant check",
                exc_info=True,
            )
            return
        me = self.agent.nick
        boss_entries_with_my_nick = [
            ag
            for ag in cfg.agents
            if getattr(ag, "nick", "") == me and "boss" in (getattr(ag, "tags", []) or [])
        ]
        workers_pointing_at_me = [
            ag
            for ag in cfg.agents
            if getattr(ag, "boss", "") == me and getattr(ag, "nick", "") != me
        ]
        if len(boss_entries_with_my_nick) > 1:
            logger.error(
                "Refusing to start: manifest has %d entries tagged 'boss' "
                "with nick %r — two CC sessions are trying to claim the same "
                "identity. Use distinct project-named nicks per session.",
                len(boss_entries_with_my_nick),
                me,
            )
            raise ManifestInvariantError(f"duplicate boss-tagged manifest entries for nick {me!r}")
        if len(boss_entries_with_my_nick) == 0 and workers_pointing_at_me:
            logger.error(
                "Refusing to start: %d workers reference boss %r but no "
                "manifest entry exists for that nick — manifest is corrupt.",
                len(workers_pointing_at_me),
                me,
            )
            raise ManifestInvariantError(f"workers reference missing boss nick {me!r}")
        # Zero entries + zero workers = first-run; proceed silently.

    # ------------------------------------------------------------------
    # IRC callback handlers — push inbound events into the spool + IPC
    # ------------------------------------------------------------------

    def _on_mention(self, target: str, sender: str, text: str) -> None:
        """Called by IRCTransport when this bridge is @mentioned or DM'd.

        Replaces the SDK-driven ``agent_runner.send_prompt`` path on the
        old boss daemon: the bridge has no LLM to run inference, so the
        event is (a) appended to the bridge spool placeholder for CC to
        drain on next ``cc_session_start``, and (b) pushed live via the
        socket whisper queue for any currently-connected CC session.

        DMs vs channel mentions are distinguished by ``target``: if
        ``target == self.agent.nick`` it's a DM, otherwise it's a
        channel @mention.
        """
        kind = "inbound_dm" if target == self.agent.nick else "inbound_mention"
        payload = {"target": target, "sender": sender, "text": text}
        # Persistent spool — Phase 3 will replace this with the real
        # server-side draft/chathistory drain.
        try:
            spool_inbound(self.agent.nick, kind, **payload)
        except Exception:  # noqa: BLE001 — spool errors must not block IRC
            logger.warning("Failed to spool %s payload", kind, exc_info=True)
        # Live push via the socket whisper queue. The CC plugin's IPC
        # client consumes whispers as push events.
        self._ipc_push(kind, payload)

    async def _on_irc_welcome(self) -> None:
        """Called after the bridge's IRC connection completes
        registration. Phase 3: issue CHATHISTORY for our own nick to
        drain any DMs the server spooled while we were offline.

        Best-effort: failures log a warning so a transient socket
        hiccup doesn't crash the welcome path."""
        if self._transport is None:
            return
        try:
            await self._transport.send_chathistory(self.agent.nick, limit=100)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to issue CHATHISTORY drain on welcome", exc_info=True)

    def _on_chathistory_entry(self, entry: dict) -> None:
        """Called by the transport for each PRIVMSG inside a CHATHISTORY
        batch. The entry is the bridge-side mirror of the IRCd's spool
        row: ``{msg_id, sender, recipient, text, tags, batch_id}``.

        Push to CC as an ``inbound_dm`` so the existing whisper queue
        delivers it the same way live DMs are delivered. CC acks via
        ``inbound_dm_ack(msg_id)``; bridge then marks delivered via
        ``CHATHISTORY DELETE`` (handled in ``_ipc_inbound_dm_ack``).

        Also re-spool to the file inbox so the persistent placeholder
        (Phase 2's interim spool) carries the same payload — CC's
        cc_session_start drain reads from there if it hasn't yet
        connected.
        """
        msg_id = entry.get("msg_id", "")
        if not msg_id:
            return
        self._pending_chathistory[msg_id] = entry
        payload = {
            "target": entry.get("recipient", self.agent.nick),
            "sender": entry.get("sender", ""),
            "text": entry.get("text", ""),
            "msg_id": msg_id,
            "source": "chathistory",
        }
        try:
            spool_inbound(self.agent.nick, "inbound_dm", **payload)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to spool chathistory entry", exc_info=True)
        self._ipc_push("inbound_dm", payload)

    def _on_chathistory_end(self, target: str) -> None:
        """Sentinel: server has emitted the entire batch for *target*.
        No further action; the pending acks finish out via
        ``_ipc_inbound_dm_ack`` as CC processes each entry."""
        logger.debug("CHATHISTORY drain complete for %s", target)

    def _on_roominvite(self, channel: str, meta_text: str) -> None:
        """Called by IRCTransport when a ROOMINVITE is received.

        Replaces the SDK-driven ROOMINVITE evaluation on the old boss
        daemon: the bridge spools the invite and pushes it to CC. The
        boss brain (CC) decides whether to JOIN via a follow-up
        ``irc_join`` IPC call.
        """
        payload = {"channel": channel, "meta": meta_text}
        try:
            spool_inbound(self.agent.nick, "inbound_roominvite", **payload)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to spool inbound_roominvite", exc_info=True)
        self._ipc_push("inbound_roominvite", payload)

    def _ipc_push(self, kind: str, payload: dict) -> None:
        """Push an inbound event to any connected CC session via the
        socket server's whisper queue. Fire-and-forget — if no CC is
        connected, the whisper sits in the queue until one connects."""
        if self._socket_server is None:
            return
        # ``make_whisper`` builds a structured IPC frame; the
        # ``SocketServer.send_whisper`` helper takes the message and
        # whisper_type as separate args and re-wraps. To avoid
        # re-wrapping a wrapped frame we pass the structured payload
        # as a single JSON string (decoders on the CC side parse it).
        import json as _json

        message = _json.dumps(payload)
        task = asyncio.create_task(self._socket_server.send_whisper(message, kind))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ------------------------------------------------------------------
    # Boss / worker watchdog — silent-death detection
    # ------------------------------------------------------------------

    async def _silent_death_watchdog(self) -> None:
        """Detect worker daemons that died without writing ``agent_exit``.

        Same logic as the original boss daemon (``daemon.py:670-744``):
        for each owned worker in the manifest, read its PID file, check
        process aliveness, and surface ``idle_warning`` to the bridge's
        own daemon-log when the process is dead but the worker's
        daemon-log has no clean exit marker. One-shot per worker per
        session.

        Pure filesystem operation — no SDK dependency, perfect home in
        the bridge (EL-11 of the rearchitecture plan).
        """
        from culture.config import load_config_or_default

        try:
            while True:
                await asyncio.sleep(WATCHDOG_POLL_SECONDS)
                try:
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
                    if not pid or is_process_alive(pid):
                        continue
                    if self._daemon_log_indicates_clean_exit(nick):
                        continue
                    self._silent_death_warned.add(nick)
                    await self._daemon_log.record(
                        "idle_warning",
                        reason="silent_death_after_done",
                        worker=nick,
                    )
                    # Push a DM-style notice to CC so the boss brain sees
                    # the worker's death without polling the daemon-log.
                    self._ipc_push(
                        "inbound_mention",
                        {
                            "target": self.agent.nick,
                            "sender": "bridge",
                            "text": (
                                f"[idle] worker {nick} died without writing "
                                f"agent_exit (silent death). Investigate or "
                                f"re-spawn."
                            ),
                        },
                    )
        except asyncio.CancelledError:
            return

    @staticmethod
    def _daemon_log_indicates_clean_exit(nick: str) -> bool:
        """True if *nick*'s daemon-log tail contains a clean exit marker."""
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
            return False
        return False

    async def _rejoin_owned_task_channels(self) -> None:
        """Rejoin ``#task-<suffix>`` channels for workers whose manifest-
        recorded boss is this bridge's nick (preserved from the claude
        daemon — RC-7 + EL-1 invariant). Best-effort; failures log a
        warning but do not block startup."""
        if self._transport is None:
            return
        try:
            from culture.clients._perm_broker import culture_home
            from culture.config import load_config_or_default

            server_yaml = os.path.join(culture_home(), "server.yaml")
            config = load_config_or_default(server_yaml, fallback=server_yaml)
        except Exception:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001
                logger.warning("Failed to rejoin %s", channel, exc_info=True)

    # ------------------------------------------------------------------
    # IPC dispatch
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

    def _log_action_bg(self, action: str, **detail) -> None:
        """Fire-and-forget a daemon-log record from a synchronous context."""
        task = asyncio.create_task(self._daemon_log.record(action, **detail))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ------------------------------------------------------------------
    # IPC handlers — preserved IRC/thread verbs (13)
    # ------------------------------------------------------------------

    def _check_mention_warnings(self, text: str) -> list[str]:
        """Return warnings for @mentioned nicks not seen in any buffer."""
        import re

        mentions = re.findall(r"@([\w-]+)", text)
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
        topic = msg.get("topic")
        await self._transport.send_topic(channel, topic)
        return make_response(req_id, ok=True)

    async def _ipc_irc_ask(self, req_id: str, msg: dict) -> dict:
        """Send a PRIVMSG and fire a question webhook."""
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
        return make_response(req_id, ok=True)

    # ------------------------------------------------------------------
    # IPC handlers — repurposed
    # ------------------------------------------------------------------

    async def _ipc_compact(self, req_id: str, msg: dict) -> dict:
        """Repurposed: no SDK to compact. Write a daemon-log entry on
        CC's behalf so the dashboard's Activity tab still records that
        a compact happened."""
        reason = (msg.get("reason") or "").strip()
        await self._daemon_log.record("compact", trigger="ipc", reason=reason)
        return make_response(req_id, ok=True)

    # ------------------------------------------------------------------
    # IPC handlers — preserved with reshaped response
    # ------------------------------------------------------------------

    async def _ipc_status(self, req_id: str, msg: dict) -> dict:
        """Reshape: drops ``circuit_open`` (no SDK to break), adds
        ``cc_connected`` (true once a CC session has issued
        ``cc_session_start``)."""
        channels = list(self._transport.channels) if self._transport is not None else []
        return make_response(
            req_id,
            ok=True,
            data={
                "running": True,
                "cc_connected": self._cc_connected,
                "runtime_model": self._runtime_model,
                "channels": channels,
                "activity": "connected" if self._cc_connected else "awaiting_cc",
            },
        )

    def _ipc_shutdown(self, req_id: str, msg: dict) -> dict:
        task = asyncio.create_task(self._graceful_shutdown())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return make_response(req_id, ok=True)

    # ------------------------------------------------------------------
    # IPC handlers — NET-NEW
    # ------------------------------------------------------------------

    async def _ipc_cc_session_start(self, req_id: str, msg: dict) -> dict:
        """CC announces it has connected (SessionStart hook fired)."""
        self._cc_connected = True
        nick = msg.get("nick") or self.agent.nick
        await self._daemon_log.record("cc_session_start", nick=nick)
        return make_response(req_id, ok=True)

    async def _ipc_cc_session_end(self, req_id: str, msg: dict) -> dict:
        """CC announces it is going offline."""
        self._cc_connected = False
        await self._daemon_log.record("cc_session_end")
        return make_response(req_id, ok=True)

    async def _ipc_set_runtime_model(self, req_id: str, msg: dict) -> dict:
        """CC reports the model resolved at first AssistantMessage.

        Replaces the old SDK-side ``model_resolved`` latch (v8.18.6) —
        the bridge no longer observes AssistantMessages directly, so CC
        pushes the resolved model name here on its first turn."""
        model = msg.get("model", "")
        if not isinstance(model, str) or not model:
            return make_response(req_id, ok=False, error="Missing 'model'")
        self._runtime_model = model
        await self._daemon_log.record("model_resolved", model=model)
        return make_response(req_id, ok=True)

    async def _ipc_sdk_event(self, req_id: str, msg: dict) -> dict:
        """CC pushes a structured SDK event for the bridge to audit-log.

        Replaces the old in-process ``self._audit.write(msg)`` path from
        ``_on_agent_message`` on the claude daemon. CC marshals the
        AssistantMessage / ResultMessage / etc. dict over IPC, the
        bridge appends it to ``~/.culture/audit/<nick>-YYYY-MM-DD.jsonl``
        byte-for-byte (v8.18.0 schema invariant — RC-6)."""
        event = msg.get("event")
        if not isinstance(event, dict):
            return make_response(req_id, ok=False, error="Missing 'event' object")
        await self._audit.write(event)
        return make_response(req_id, ok=True)

    async def _ipc_daemon_log_record(self, req_id: str, msg: dict) -> dict:
        """CC asks the bridge to record an action in the daemon-log."""
        action = msg.get("action", "")
        if not isinstance(action, str) or not action:
            return make_response(req_id, ok=False, error="Missing 'action'")
        detail = msg.get("detail") or {}
        if not isinstance(detail, dict):
            return make_response(req_id, ok=False, error="'detail' must be an object")
        await self._daemon_log.record(action, **detail)
        return make_response(req_id, ok=True)

    def _ipc_inbound_dm_ack(self, req_id: str, msg: dict) -> dict:
        """CC acks an inbound DM. When the ack carries a ``msg_id`` for
        a chathistory-drained entry, issue the server-side
        ``CHATHISTORY DELETE`` to mark the spool row delivered
        (two-phase drain — Phase 3.5). Idempotent under CC crash
        mid-drain: an entry the server has already marked delivered
        simply won't reappear on the next drain."""
        msg_id = msg.get("msg_id", "")
        if msg_id and msg_id in self._pending_chathistory:
            self._pending_chathistory.pop(msg_id, None)
            if self._transport is not None:
                task = asyncio.create_task(self._transport.send_chathistory_delete(msg_id))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        return make_response(req_id, ok=True)

    def _ipc_inbound_mention_ack(self, req_id: str, msg: dict) -> dict:
        """CC acks an inbound channel mention."""
        return make_response(req_id, ok=True)

    def _ipc_inbound_roominvite_ack(self, req_id: str, msg: dict) -> dict:
        """CC acks an inbound ROOMINVITE."""
        return make_response(req_id, ok=True)

    def _ipc_perm_decision_ack(self, req_id: str, msg: dict) -> dict:
        """CC acks a perm decision push (Phase 5 will wire the actual
        decision-file write)."""
        return make_response(req_id, ok=True)
