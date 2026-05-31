"""Watcher service main loop + config loader (v8.19.19).

Periodic poll over the per-agent log files; dispatches into the
pattern detectors; runs cooldown-gated alerts through the router.
Designed to live in its own process — launched by
``culture watcher start``.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

import yaml

from culture.clients._audit import audit_path_for
from culture.clients._daemon_log import daemon_log_path_for
from culture.clients._perm_broker import culture_home, list_pending
from culture.watcher.alerts import AlertRouter
from culture.watcher.patterns import PatternEvent, detect_patterns
from culture.watcher.state import WatcherState

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 30.0
DEFAULT_COOLDOWN_SECONDS = 600.0
DEFAULT_PATTERNS = (
    "silent_death",
    "crash_burst",
    "token_spike",
    "perm_escalation_above_ceiling",
    "mission_stuck",
)


@dataclass
class WatcherConfig:
    poll_interval_seconds: float = DEFAULT_POLL_SECONDS
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
    enabled_patterns: tuple[str, ...] = DEFAULT_PATTERNS
    boss_ceiling: dict[str, list[str]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WatcherConfig":
        patterns_in = data.get("patterns")
        if isinstance(patterns_in, list):
            enabled = tuple(
                p["name"] if isinstance(p, dict) else str(p)
                for p in patterns_in
                if (isinstance(p, dict) and p.get("name")) or isinstance(p, str)
            )
        else:
            enabled = DEFAULT_PATTERNS
        return cls(
            poll_interval_seconds=float(
                data.get("poll_interval_seconds", DEFAULT_POLL_SECONDS) or DEFAULT_POLL_SECONDS
            ),
            cooldown_seconds=float(
                data.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS) or DEFAULT_COOLDOWN_SECONDS
            ),
            enabled_patterns=enabled,
            boss_ceiling={str(k): list(v) for k, v in (data.get("boss_ceiling") or {}).items()},
            raw=data,
        )


def default_config_path() -> str:
    return os.path.join(culture_home(), "watcher.yaml")


def default_state_path() -> str:
    return os.path.join(culture_home(), "watcher-state.json")


def load_config(path: str | None = None) -> tuple[WatcherConfig, dict]:
    """Load (config_object, raw_dict). Missing file → DEFAULTS, no error."""
    resolved = path or default_config_path()
    try:
        with open(resolved, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError:
        data = {}
    if not isinstance(data, dict):
        logger.warning("watcher config %s is not a mapping; using defaults", resolved)
        data = {}
    return WatcherConfig.from_dict(data), data


# --- The service -----------------------------------------------------------


@dataclass
class _AgentInputs:
    nick: str
    daemon_log: list[dict]
    audit: list[dict]


class WatcherService:
    """Polls + dispatches detectors + sends alerts.

    Inject a ``send_irc`` coroutine (binds the IRC observer to the
    router) and the router itself. The service owns the polling loop;
    ``run_forever`` is the entry point.
    """

    def __init__(
        self,
        config: WatcherConfig,
        state: WatcherState,
        router: AlertRouter,
        send_irc=None,  # async def send_irc(target, text) — optional in tests
        pidfile_dir: str | None = None,
        sources_glob_daemon_log: str | None = None,
        sources_glob_audit: str | None = None,
    ):
        self.config = config
        self.state = state
        self.router = router
        self.send_irc = send_irc
        self.pidfile_dir = pidfile_dir or os.path.join(culture_home(), "runtime")
        # Globs default to the canonical paths via culture_home; tests
        # can override for isolation.
        self.glob_daemon_log = sources_glob_daemon_log or os.path.join(
            culture_home(), "daemon-log", "*.jsonl"
        )
        self.glob_audit = sources_glob_audit or os.path.join(culture_home(), "audit", "*.jsonl")

    # --- One-pass detection ------------------------------------------------

    def discover_agents(self) -> list[str]:
        """Union of nicks visible in the daemon-log + audit globs."""
        seen: set[str] = set()
        for pattern in (self.glob_daemon_log, self.glob_audit):
            for path in glob.glob(pattern):
                name = os.path.basename(path)
                if name.endswith(".jsonl"):
                    seen.add(name[: -len(".jsonl")])
        return sorted(seen)

    def collect_inputs(self, nick: str) -> _AgentInputs:
        from culture.watcher.patterns import _read_jsonl_tail

        dl_path = daemon_log_path_for(nick)
        audit_path = audit_path_for(nick)
        return _AgentInputs(
            nick=nick,
            daemon_log=_read_jsonl_tail(dl_path, max_lines=2048),
            audit=_read_jsonl_tail(audit_path, max_lines=2048),
        )

    def detect_for_agent(
        self,
        inputs: _AgentInputs,
        *,
        pending_requests: list[dict],
        now: float | None = None,
    ) -> list[PatternEvent]:
        return detect_patterns(
            enabled=self.config.enabled_patterns,
            nick=inputs.nick,
            daemon_log=inputs.daemon_log,
            audit=inputs.audit,
            pidfile_dir=self.pidfile_dir,
            pending_requests=pending_requests,
            boss_ceiling=self.config.boss_ceiling,
            now=now,
        )

    def cooldown_filter(self, events: Iterable[PatternEvent]) -> list[PatternEvent]:
        survivors: list[PatternEvent] = []
        for ev in events:
            if self.state.in_cooldown(ev.key, self.config.cooldown_seconds):
                continue
            survivors.append(ev)
        return survivors

    async def dispatch(self, events: Iterable[PatternEvent]) -> int:
        """Route an iterable of events through every configured sink.

        Returns count of events actually shipped (post-cooldown).
        """
        fresh = self.cooldown_filter(events)
        count = 0
        loop = asyncio.get_running_loop()
        for ev in fresh:
            # IRC
            recipients = self.router.irc_recipients()
            if self.send_irc and recipients:
                line = self.router.format_irc_line(ev)
                for r in recipients:
                    try:
                        await self.send_irc(r, line)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("watcher IRC alert to %s failed: %s", r, exc)
            # Email + webhook (blocking sockets → run in executor)
            if self.router.sinks.email.enabled:
                try:
                    await loop.run_in_executor(None, self.router.send_email, ev)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("watcher email alert failed: %s", exc)
            if self.router.sinks.webhook.enabled:
                try:
                    await loop.run_in_executor(None, self.router.send_webhook, ev)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("watcher webhook alert failed: %s", exc)
            self.state.record_firing(ev.key)
            count += 1
        if count:
            self.state.save()
        return count

    async def run_once(self) -> int:
        """One pass: discover agents, detect, dispatch. Returns events shipped."""
        agents = self.discover_agents()
        try:
            pending = list_pending()
        except Exception as exc:  # noqa: BLE001
            logger.warning("watcher: list_pending failed: %s", exc)
            pending = []
        all_events: list[PatternEvent] = []
        for nick in agents:
            inputs = self.collect_inputs(nick)
            events = self.detect_for_agent(inputs, pending_requests=pending)
            all_events.extend(events)
        return await self.dispatch(all_events)

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Main poll loop. Awaits ``stop_event`` (if provided) between passes."""
        while True:
            try:
                shipped = await self.run_once()
                if shipped:
                    logger.info("watcher: shipped %d alert(s)", shipped)
            except Exception as exc:  # noqa: BLE001 — never let one pass kill the loop
                logger.exception("watcher pass failed: %s", exc)
            if stop_event is not None:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.config.poll_interval_seconds
                    )
                    if stop_event.is_set():
                        return
                except asyncio.TimeoutError:
                    continue
            else:
                await asyncio.sleep(self.config.poll_interval_seconds)
