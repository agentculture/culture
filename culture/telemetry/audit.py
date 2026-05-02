"""Audit JSONL sink for Culture.

`init_audit(config, metrics)` returns an AuditSink. The sink runs a
dedicated async writer task draining a bounded `asyncio.Queue`. Each
record is JSON-serialized and appended to a daily-rotated file under
`config.telemetry.audit_dir`. On queue overflow records are dropped
(and `culture.audit.writes{outcome=error}` increments) — dropping is
preferable to blocking the event loop.

Lifecycle is owned by IRCd: __init__ calls init_audit(); start() awaits
sink.start(); stop() awaits sink.shutdown(). When audit_enabled=False
sink.submit() is a no-op and start/shutdown do nothing.

Audit is independent of telemetry.enabled — even with OTEL fully off,
audit_enabled=True still writes to JSONL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agentirc.config import ServerConfig

if TYPE_CHECKING:
    from culture.agentirc.skill import Event
    from culture.telemetry.metrics import MetricsRegistry

logger = logging.getLogger(__name__)

_initialized_for: dict | None = None
_sink: "AuditSink | None" = None


def _write_all(fd: int, buf: bytes) -> int:
    """Write ``buf`` fully to ``fd``, looping over short writes.

    Returns the total bytes written. Raises OSError on hard failure.
    """
    written = 0
    view = memoryview(buf)
    while written < len(buf):
        n = os.write(fd, view[written:])
        if n == 0:
            # POSIX write should never return 0 unless len(buf) is 0.
            raise OSError("os.write returned 0 — refusing to spin")
        written += n
    return written


@dataclass
class AuditSink:
    """Async-safe JSONL audit sink.

    See culture/protocol/extensions/audit.md for the on-disk record
    schema and rotation rules.

    Lifecycle:
        sink = init_audit(config, metrics)        # construct
        await sink.start()                         # spawn writer task
        sink.submit({...})                          # non-blocking enqueue
        await sink.shutdown(drain_timeout=5.0)     # drain + cancel

    When `enabled=False`, all four methods become no-ops; submit drops
    records silently.
    """

    server_name: str
    audit_dir: Path
    max_file_bytes: int
    rotate_utc_midnight: bool
    queue_depth: int
    enabled: bool
    metrics: "MetricsRegistry"

    queue: asyncio.Queue | None = field(default=None, init=False)
    _writer_task: asyncio.Task | None = field(default=None, init=False)
    _current_path: Path | None = field(default=None, init=False)
    _current_size: int = field(default=0, init=False)
    _current_date: str | None = field(default=None, init=False)  # YYYY-MM-DD
    _current_fd: int = field(default=-1, init=False)
    _current_suffix: int = field(default=0, init=False)

    def submit(self, record: dict) -> None:
        """Non-blocking enqueue. Drop on overflow or when disabled."""
        if not self.enabled:
            return
        if self.queue is None:
            # start() not yet called — record cannot be enqueued. Count and
            # log so this state is observable rather than a silent vanish.
            self.metrics.audit_writes.add(1, {"outcome": "error"})
            logger.warning("audit submit called before start(); dropped 1 record")
            return
        try:
            self.queue.put_nowait(record)
            self.metrics.audit_queue_depth.add(1)
        except asyncio.QueueFull:
            self.metrics.audit_writes.add(1, {"outcome": "error"})
            logger.warning("audit queue full (depth=%d); dropped 1 record", self.queue_depth)

    async def start(self) -> None:
        """Create the audit directory if missing, then spawn the writer task.

        Idempotent: calling start() twice is harmless (returns immediately
        on the second call).
        """
        if not self.enabled:
            return
        if self._writer_task is not None:
            return
        await asyncio.to_thread(self.audit_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
        self.queue = asyncio.Queue(maxsize=self.queue_depth)
        self._writer_task = asyncio.create_task(self._writer_loop(), name="audit-writer")

    async def shutdown(self, *, drain_timeout: float = 5.0) -> None:
        """Drain the queue (bounded by drain_timeout) then cancel writer."""
        if not self.enabled or self._writer_task is None:
            return
        if self.queue is not None:
            try:
                await asyncio.wait_for(self.queue.join(), timeout=drain_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "audit drain timed out after %.1fs; %d records may be lost",
                    drain_timeout,
                    self.queue.qsize() if self.queue is not None else 0,
                )
        self._writer_task.cancel()
        try:
            await self._writer_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._writer_task = None
        if self._current_fd != -1:
            try:
                os.close(self._current_fd)
            except OSError:
                pass
            self._current_fd = -1

    async def _writer_loop(self) -> None:
        """Drain the queue forever, JSON-encoding and appending each record."""
        if self.queue is None:
            # Should never happen — start() always assigns the queue before
            # spawning this task — but guard against `python -O` stripping
            # the assert, which would mask a real bug.
            logger.error("audit writer task started without a queue; exiting")
            return
        while True:
            record = await self.queue.get()
            try:
                line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
                line_bytes = line.encode("utf-8")
                self._maybe_rotate(len(line_bytes))
                if self._current_fd != -1:
                    _write_all(self._current_fd, line_bytes)
                    self._current_size += len(line_bytes)
                    self.metrics.audit_writes.add(1, {"outcome": "ok"})
                else:
                    self.metrics.audit_writes.add(1, {"outcome": "error"})
            except OSError as exc:
                self.metrics.audit_writes.add(1, {"outcome": "error"})
                logger.warning("audit write failed: %s", exc)
            except Exception:  # noqa: BLE001 - audit must never crash the loop
                self.metrics.audit_writes.add(1, {"outcome": "error"})
                logger.exception("audit record serialization failed")
            finally:
                self.metrics.audit_queue_depth.add(-1)
                self.queue.task_done()

    def _pick_rotation_path(self, today: str) -> tuple[Path, int, int]:
        """Find the next audit file path for *today* with available capacity.

        Returns (path, suffix, existing_size). The suffix is written back to
        ``self._current_suffix`` by the caller so subsequent writes continue
        appending to the same slot.
        """
        suffix_part = f".{self._current_suffix}" if self._current_suffix else ""
        path = self.audit_dir / f"{self.server_name}-{today}{suffix_part}.jsonl"

        # Respect existing file size so a mid-day restart honours the cap.
        try:
            existing_size = path.stat().st_size if path.exists() else 0
        except OSError:
            existing_size = 0

        # Bump suffix until we find a slot below the cap (bounded to 1000).
        while existing_size >= self.max_file_bytes and self._current_suffix < 1000:
            self._current_suffix += 1
            suffix_part = f".{self._current_suffix}"
            path = self.audit_dir / f"{self.server_name}-{today}{suffix_part}.jsonl"
            try:
                existing_size = path.stat().st_size if path.exists() else 0
            except OSError:
                existing_size = 0

        return path, self._current_suffix, existing_size

    def _open_audit_file(self, path: Path) -> int:
        """Open *path* for append-write and enforce 0600 permissions.

        Returns the new file descriptor. Raises ``OSError`` on failure.
        Using ``os.fchmod`` on the open fd avoids a race window between
        open and a separate ``path.chmod`` call.
        """
        new_fd = os.open(
            str(path),
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            0o600,
        )
        # Enforce 0600 even if the file pre-existed with broader perms.
        os.fchmod(new_fd, 0o600)
        return new_fd

    def _maybe_rotate(self, next_record_bytes: int) -> None:
        """Open a new file if the date rolled or the size cap would be hit.

        Called synchronously from the writer loop. Closes the previous fd
        if one was open; opens the new one with O_WRONLY|O_APPEND|O_CREAT
        and mode 0o600 (enforced via fchmod on the open fd).
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_changed = self.rotate_utc_midnight and self._current_date != today
        size_exceeded = (
            self._current_fd != -1 and self._current_size + next_record_bytes > self.max_file_bytes
        )
        first_open = self._current_fd == -1

        if not (date_changed or size_exceeded or first_open):
            return  # current file is fine

        if date_changed:
            self._current_suffix = 0  # new day, reset
        elif size_exceeded:
            self._current_suffix += 1  # same day, next slot

        self._current_date = today
        path, _, existing_size = self._pick_rotation_path(today)

        old_fd = self._current_fd
        try:
            new_fd = self._open_audit_file(path)
        except OSError as exc:
            # New open failed — keep the old fd usable so the next record may
            # still write to the previous file. If old_fd was -1 we degrade to
            # silent-drop (counter increments via writer-loop OSError branch on
            # next write attempt — which will retry rotation).
            logger.error("audit open failed for %s: %s — keeping previous fd open", path, exc)
            return

        # New fd opened successfully — now close the old one if there was one.
        if old_fd != -1:
            try:
                os.close(old_fd)
            except OSError:
                pass
        self._current_fd = new_fd
        self._current_path = path
        self._current_size = existing_size


def utc_iso_timestamp(epoch_seconds: float) -> str:
    """ISO 8601 UTC with microsecond precision, trailing Z."""
    dt = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}Z"


# Back-compat alias kept for any code written against the private name.
_utc_iso_timestamp = utc_iso_timestamp


def _target_for(event: "Event") -> dict[str, str]:
    if event.channel:
        return {"kind": "channel", "name": event.channel}
    target_name = event.data.get("target", "") if isinstance(event.data, dict) else ""
    if target_name:
        return {"kind": "nick", "name": target_name}
    return {"kind": "", "name": ""}


def build_audit_record(
    server_name: str,
    event: "Event",
    origin_tag: str | None,
    trace_id: str,
    span_id: str,
    *,
    actor_kind: str = "human",
    actor_remote_addr: str = "",
    extra_tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a schema-compliant audit record dict.

    See culture/protocol/extensions/audit.md for the field set.
    """
    payload = {k: v for k, v in (event.data or {}).items() if not k.startswith("_")}
    if event.nick:
        payload.setdefault("nick", event.nick)
    if event.channel:
        payload.setdefault("channel", event.channel)

    event_type_str = event.type.value if hasattr(event.type, "value") else str(event.type)

    return {
        "ts": utc_iso_timestamp(event.timestamp),
        "server": server_name,
        "event_type": event_type_str,
        "origin": "federated" if origin_tag else "local",
        "peer": origin_tag or "",
        "trace_id": trace_id,
        "span_id": span_id,
        "actor": {
            "nick": event.nick or "",
            "kind": actor_kind,
            "remote_addr": actor_remote_addr,
        },
        "target": _target_for(event),
        "payload": payload,
        "tags": dict(extra_tags) if extra_tags else {},
    }


def init_audit(config: ServerConfig, metrics: "MetricsRegistry") -> AuditSink:
    """Construct an AuditSink from config. Idempotent.

    NOT gated by `config.telemetry.enabled` — audit fires whenever
    `audit_enabled=True`, even with OTEL fully off.
    """
    global _initialized_for, _sink

    tcfg = config.telemetry
    snapshot = {"telemetry": asdict(tcfg), "instance": config.name}
    if _initialized_for == snapshot and _sink is not None:
        return _sink

    # Reinit: warn the caller that the previous sink is being replaced
    # without an explicit shutdown. Production callers should call
    # `await sink.shutdown()` themselves before reinit; this branch
    # primarily protects test isolation.
    if _sink is not None and _sink._writer_task is not None:
        logger.warning(
            "init_audit called with mutated config but previous sink is still "
            "running; the old writer task will be orphaned. Caller should "
            "await sink.shutdown() before reinit."
        )

    audit_dir = Path(tcfg.audit_dir).expanduser()

    sink = AuditSink(
        server_name=config.name,
        audit_dir=audit_dir,
        max_file_bytes=tcfg.audit_max_file_bytes,
        rotate_utc_midnight=tcfg.audit_rotate_utc_midnight,
        queue_depth=tcfg.audit_queue_depth,
        enabled=tcfg.audit_enabled,
        metrics=metrics,
    )

    _sink = sink
    _initialized_for = snapshot
    return sink


def reset_for_tests() -> None:
    """Test-only: clear module state. Caller is responsible for shutting
    down any active sink before calling this."""
    global _initialized_for, _sink
    if _sink is not None and _sink._writer_task is not None:
        logger.warning(
            "reset_for_tests called while sink writer task still running; "
            "test should `await sink.shutdown()` before reset_for_tests."
        )
    _initialized_for = None
    _sink = None
