"""Spike 0.7 — watchdog Observer + asyncio integration smoke test.

Validates that watchdog 6.0.0's threaded Observer can hand events back into
an asyncio loop via loop.call_soon_threadsafe — the pattern the
permission-broker FS observer (Phase 5.4 + 5.6) will rely on.
"""

import asyncio
import os
import shutil
import time
from pathlib import Path

from watchdog.events import PatternMatchingEventHandler
from watchdog.observers import Observer

WATCH_DIR = Path("/tmp/watchdog-spike")


class _Handler(PatternMatchingEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop, fut: asyncio.Future) -> None:
        super().__init__(patterns=["*.json"], ignore_directories=True)
        self._loop = loop
        self._fut = fut
        self._fired_at: float | None = None

    def on_created(self, event) -> None:
        self._fired_at = time.monotonic()
        # Cross-thread handoff: watchdog runs the callback on its own thread.
        self._loop.call_soon_threadsafe(self._safe_set, event.src_path, self._fired_at)

    def _safe_set(self, src_path: str, fired_at: float) -> None:
        if not self._fut.done():
            self._fut.set_result((src_path, fired_at))


async def main() -> None:
    if WATCH_DIR.exists():
        shutil.rmtree(WATCH_DIR)
    WATCH_DIR.mkdir(parents=True)

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    handler = _Handler(loop, fut)
    observer = Observer()
    observer.schedule(handler, str(WATCH_DIR), recursive=False)
    observer.start()
    try:
        # Give the FSEvents stream a brief moment to attach before writing.
        await asyncio.sleep(0.1)
        write_started = time.monotonic()
        target = WATCH_DIR / "test1.json"
        await asyncio.to_thread(target.write_text, '{"k":"v"}\n')
        src_path, fired_at = await asyncio.wait_for(fut, timeout=5.0)
        latency_ms = (fired_at - write_started) * 1000.0
        assert src_path.endswith("test1.json"), f"unexpected src_path: {src_path}"
        print(f"PASS: callback fired into asyncio (latency {latency_ms:.1f} ms)")
    finally:
        observer.stop()
        observer.join(timeout=2.0)
        shutil.rmtree(WATCH_DIR, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
