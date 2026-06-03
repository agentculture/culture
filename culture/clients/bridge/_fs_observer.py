"""Bridge FS observer — watchdog-library push channel for broker files.

Phase 5.4 of the mesh-rearchitecture plan. The bridge watches three
broker directories under ``~/.culture/``:

    perm-queue/             — worker writes a new request file here;
                              bridge pushes ``perm_request`` IPC to CC.

    perm-decisions/         — CC (via ``mesh approve``/``mesh deny``)
                              shells out to ``culture boss approve|deny``
                              which writes a decision file here; the
                              bridge sees the file land and forwards
                              ``perm_decision`` IPC to CC for the
                              dashboard's bookkeeping.

    perm-demote-notices/    — worker broker writes a notice when an
                              ``--always allow`` for a high-risk tool
                              gets demoted to ``scope=once`` because
                              the approval lacked ``input_regex``. The
                              bridge pushes ``inbound_mention`` IPC
                              with tag ``demote-notice`` so CC surfaces
                              the demote as a system reminder.

Implementation: a dedicated background thread runs a
``watchdog.observers.Observer`` with three
``PatternMatchingEventHandler`` instances (one per directory). The
handler is fired on the watchdog thread; we marshal each event back to
the bridge's asyncio loop via ``loop.call_soon_threadsafe`` so all
mutations of ``self._ipc_push`` happen on the loop thread.

Graceful degradation: if the ``watchdog`` import fails (or the user
has set the env override ``CULTURE_DISABLE_WATCHDOG=1``), the observer
falls back to a 250ms ``asyncio.sleep``-based directory poll. The
poll thread reads each directory's contents on each tick and diffs
against the last-seen set to surface new files. The fallback is
documented for operators in ``docs/superpowers/mesh-fs-observer.md``.

Thread-safety: the watchdog project documents that "a full thread
safety audit has not been completed" (per the rearchitecture
spec's iter-2 C-2 from agent 10). The stress test in
``tests/bridge/test_fs_observer_thread_safety.py`` creates 50
perm-queue files in rapid succession and asserts the bridge surfaces
50 IPC events with no drops over 60 seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


# Try to import watchdog at module load time. We expose ``_HAS_WATCHDOG``
# so callers (and tests) can branch on availability without doing their
# own import-try dance. ``CULTURE_DISABLE_WATCHDOG=1`` forces the
# polling fallback even when the library is present — useful for the
# fallback-path tests.
_HAS_WATCHDOG: bool
try:
    if os.environ.get("CULTURE_DISABLE_WATCHDOG", "") == "1":
        raise ImportError("CULTURE_DISABLE_WATCHDOG=1 — using polling fallback")
    from watchdog.events import (  # type: ignore[import-not-found]
        FileCreatedEvent,
        FileMovedEvent,
        PatternMatchingEventHandler,
    )
    from watchdog.observers import Observer  # type: ignore[import-not-found]

    _HAS_WATCHDOG = True
except ImportError:  # pragma: no cover — exercised in fallback tests
    _HAS_WATCHDOG = False
    Observer = None  # type: ignore[assignment,misc]
    PatternMatchingEventHandler = object  # type: ignore[assignment,misc]
    FileCreatedEvent = object  # type: ignore[assignment,misc]
    FileMovedEvent = object  # type: ignore[assignment,misc]


# Polling cadence for the fallback path. Matches the legacy broker's
# 250ms cadence so behaviour is indistinguishable from the user's
# perspective.
_POLL_FALLBACK_SECONDS = 0.25


# Event payload helpers. Each dispatch builds a small dict from the
# request/decision/demote file (best-effort: a transient missing-file
# race produces an empty ``payload`` rather than a crash).


def _read_json_best_effort(path: str) -> dict[str, Any]:
    """Read a JSON file; return ``{}`` on any error."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


# IPC verb names emitted by the observer. Kept as module constants so
# tests and the daemon agree without hard-coded strings drifting apart.
KIND_PERM_REQUEST = "perm_request"
KIND_PERM_DECISION = "perm_decision"
KIND_INBOUND_MENTION = "inbound_mention"
TAG_DEMOTE_NOTICE = "demote-notice"


# --------------------------------------------------------------------
# Polling fallback
# --------------------------------------------------------------------


class _PollingFallback:
    """Diff-based directory poller for environments without watchdog.

    Run on its own thread so the asyncio loop is never blocked by the
    listdir+stat cycle. Each tick lists each watched directory's
    contents and surfaces any new ``.json`` files via the dispatcher
    callback (which uses ``loop.call_soon_threadsafe`` to hop back to
    the loop thread).
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        dispatcher: Callable[[str, str], None],
        directories: dict[str, str],
        poll_interval: float = _POLL_FALLBACK_SECONDS,
    ) -> None:
        self._loop = loop
        self._dispatcher = dispatcher
        self._directories = dict(directories)
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen: dict[str, set[str]] = {label: set() for label in self._directories}
        # Seed each set with current contents so a freshly-started
        # observer doesn't replay every pre-existing file as if it
        # were just created. This mirrors watchdog's "events from the
        # start of the observer" semantics (no replay of pre-existing
        # files).
        for label, path in self._directories.items():
            self._seen[label] = self._snapshot(path)

    @staticmethod
    def _snapshot(directory: str) -> set[str]:
        try:
            return {n for n in os.listdir(directory) if n.endswith(".json")}
        except OSError:
            return set()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="culture-bridge-fs-poll",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            for label, directory in self._directories.items():
                current = self._snapshot(directory)
                new_names = current - self._seen.get(label, set())
                self._seen[label] = current
                for name in sorted(new_names):
                    path = os.path.join(directory, name)
                    try:
                        self._dispatcher(label, path)
                    except Exception:  # noqa: BLE001 — must not kill the poller
                        logger.warning(
                            "Polling dispatcher raised for %s",
                            path,
                            exc_info=True,
                        )
            self._stop_event.wait(self._poll_interval)


# --------------------------------------------------------------------
# watchdog-backed observer
# --------------------------------------------------------------------


class _BridgeEventHandler(PatternMatchingEventHandler):  # type: ignore[misc, valid-type]
    """Per-directory handler that forwards file creates to the dispatcher.

    ``label`` is the directory's logical name (e.g. ``perm-queue``)
    used by the dispatcher to pick the IPC kind. The handler is fired
    on the watchdog thread; the dispatcher is responsible for hopping
    back to the asyncio loop via ``call_soon_threadsafe``.
    """

    def __init__(self, label: str, dispatcher: Callable[[str, str], None]) -> None:
        super().__init__(  # type: ignore[no-untyped-call]
            patterns=["*.json"],
            ignore_patterns=[".tmp-*", "*.tmp"],
            ignore_directories=True,
            case_sensitive=True,
        )
        self._label = label
        self._dispatcher = dispatcher

    def on_created(self, event):  # type: ignore[no-untyped-def]
        if event.is_directory:
            return
        self._dispatcher(self._label, event.src_path)

    def on_moved(self, event):  # type: ignore[no-untyped-def]
        # Atomic writes via ``os.replace`` show up as a MOVED event with
        # the destination as ``dest_path``. We treat the move-to as a
        # creation for our purposes.
        if event.is_directory:
            return
        dest = getattr(event, "dest_path", "") or ""
        if dest.endswith(".json"):
            self._dispatcher(self._label, dest)


# --------------------------------------------------------------------
# BridgeFSObserver — public surface
# --------------------------------------------------------------------


class BridgeFSObserver:
    """Watches the three broker directories and pushes IPC events.

    Construct with the bridge's asyncio loop + an ``ipc_push`` callable
    matching the bridge's ``_ipc_push(kind, payload)`` signature. Call
    ``start()`` to begin watching, ``stop()`` to tear down.

    The callable is invoked on the asyncio loop thread (we marshal via
    ``loop.call_soon_threadsafe``) so the daemon can mutate its own
    state freely inside ``_ipc_push`` without worrying about thread
    affinity.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        ipc_push: Callable[[str, dict[str, Any]], None],
        queue_dir: str,
        decisions_dir: str,
        demote_dir: str,
        poll_interval: float = _POLL_FALLBACK_SECONDS,
    ) -> None:
        self._loop = loop
        self._ipc_push = ipc_push
        self._queue_dir = queue_dir
        self._decisions_dir = decisions_dir
        self._demote_dir = demote_dir
        self._poll_interval = poll_interval
        self._observer: Any = None
        self._fallback: _PollingFallback | None = None
        self._started: bool = False

    @property
    def using_watchdog(self) -> bool:
        """True iff the watchdog library was available + an observer started."""
        return self._observer is not None

    @property
    def using_fallback(self) -> bool:
        """True iff the polling fallback is in use."""
        return self._fallback is not None

    def _ensure_dirs(self) -> None:
        for path in (self._queue_dir, self._decisions_dir, self._demote_dir):
            try:
                os.makedirs(path, mode=0o700, exist_ok=True)
            except OSError:
                logger.warning("Failed to create observer dir %s", path, exc_info=True)

    def start(self) -> None:
        """Begin watching. Idempotent — repeated calls are no-ops."""
        if self._started:
            return
        self._ensure_dirs()
        directories = {
            "perm-queue": self._queue_dir,
            "perm-decisions": self._decisions_dir,
            "perm-demote-notices": self._demote_dir,
        }
        if _HAS_WATCHDOG:
            try:
                observer = Observer()
                for label, path in directories.items():
                    handler = _BridgeEventHandler(label, self._dispatch)
                    observer.schedule(handler, path=path, recursive=False)
                observer.daemon = True
                observer.start()
                self._observer = observer
                self._started = True
                logger.info("Bridge FS observer started (watchdog backend)")
                return
            except Exception:  # noqa: BLE001 — fall through to polling
                logger.warning(
                    "watchdog Observer.start() failed; falling back to polling",
                    exc_info=True,
                )
                self._observer = None
        # Polling fallback (no watchdog, or watchdog failed at start).
        self._fallback = _PollingFallback(
            loop=self._loop,
            dispatcher=self._dispatch,
            directories=directories,
            poll_interval=self._poll_interval,
        )
        self._fallback.start()
        self._started = True
        logger.info("Bridge FS observer started (polling fallback)")

    def stop(self) -> None:
        """Tear down. Safe to call multiple times."""
        if not self._started:
            return
        self._started = False
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:  # noqa: BLE001
                logger.warning("watchdog Observer.stop() raised", exc_info=True)
            self._observer = None
        if self._fallback is not None:
            self._fallback.stop()
            self._fallback = None

    # ------------------------------------------------------------------
    # Dispatcher — called on the watchdog/poll thread, hops to asyncio
    # ------------------------------------------------------------------

    def _dispatch(self, label: str, path: str) -> None:
        """Translate a directory + file into an IPC push.

        Runs on the watchdog or polling thread. The actual ``_ipc_push``
        call is scheduled via ``loop.call_soon_threadsafe`` so it runs
        on the asyncio loop thread — that's where the bridge's
        SocketServer + whisper queue live.
        """
        try:
            kind, payload = self._build_payload(label, path)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to build payload for %s", path, exc_info=True)
            return
        if kind is None:
            return
        # Hop back to the asyncio loop thread.
        try:
            self._loop.call_soon_threadsafe(self._ipc_push, kind, payload)
        except RuntimeError:
            # Loop is closed — observer outlived the bridge. Drop.
            logger.debug("Observer dispatch after loop close; dropping %s", path)

    def _build_payload(self, label: str, path: str) -> tuple[str | None, dict[str, Any]]:
        """Build the IPC kind + payload for a new file. Returns
        ``(None, {})`` for unknown labels."""
        # Skip atomic-write tempfiles. The atomic writer in
        # ``_perm_broker._atomic_write_json`` writes ``.tmp-…json``
        # then ``os.replace``s into the final name; we only care about
        # the final file. The watchdog handler's ``ignore_patterns``
        # already filters these, but the polling fallback may see them
        # land on the initial open() — belt-and-braces.
        basename = os.path.basename(path)
        if basename.startswith(".tmp-") or basename.endswith(".tmp"):
            return None, {}
        if label == "perm-queue":
            data = _read_json_best_effort(path)
            payload: dict[str, Any] = {
                "id": data.get("id", ""),
                "helper_nick": data.get("helper_nick", ""),
                "boss": data.get("boss", ""),
                "tool_name": data.get("tool_name", ""),
                "input": data.get("input", {}),
                "created_at": data.get("created_at", ""),
                "source": "fs_observer",
            }
            return KIND_PERM_REQUEST, payload
        if label == "perm-decisions":
            data = _read_json_best_effort(path)
            payload = {
                "id": data.get("id", ""),
                "verdict": data.get("verdict", ""),
                "scope": data.get("scope", ""),
                "reason": data.get("reason", ""),
                "pattern": data.get("pattern", ""),
                "input_regex": data.get("input_regex", ""),
                "decided_by": data.get("decided_by", ""),
                "decided_at": data.get("decided_at", ""),
                "source": "fs_observer",
            }
            return KIND_PERM_DECISION, payload
        if label == "perm-demote-notices":
            data = _read_json_best_effort(path)
            # Render as an ``inbound_mention`` so the existing CC plugin
            # path (Stop hook → drain queue → system reminder) carries
            # the notice without a new IPC kind.
            text = (
                f"Your `--always` approval for "
                f"{data.get('tool_name', '?')!r} was demoted to one-time "
                f"because no `input_regex` was supplied "
                f"(request {data.get('id', '?')})."
            )
            if data.get("reason"):
                text += f" Reason: {data['reason']}"
            payload = {
                "target": data.get("boss", ""),
                "sender": "bridge",
                "text": text,
                "tag": TAG_DEMOTE_NOTICE,
                "id": data.get("id", ""),
                "tool_name": data.get("tool_name", ""),
                "source": "fs_observer",
            }
            return KIND_INBOUND_MENTION, payload
        return None, {}
