"""Utility functions for managed rooms."""
from __future__ import annotations

import time
import threading

_counter = 0
_counter_lock = threading.Lock()

_BASE36_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def generate_room_id() -> str:
    """Generate a unique room ID: R + base-36 encoded timestamp + counter."""
    global _counter
    with _counter_lock:
        _counter += 1
        counter_val = _counter

    ts_ms = int(time.time() * 1000)
    combined = ts_ms * 1000 + (counter_val % 1000)

    result = []
    while combined:
        result.append(_BASE36_CHARS[combined % 36])
        combined //= 36
    return "R" + "".join(reversed(result))


def parse_room_meta(text: str) -> dict[str, str]:
    """Parse 'key=value;key=value;instructions=...' metadata format.

    The ``instructions`` field must be last — everything after
    ``instructions=`` is captured verbatim (it may contain semicolons).
    """
    if not text:
        return {}

    result: dict[str, str] = {}

    if "instructions=" in text:
        before, instructions = text.split("instructions=", 1)
        result["instructions"] = instructions
        text = before.rstrip(";")

    if not text:
        return result

    for pair in text.split(";"):
        pair = pair.strip()
        if "=" in pair:
            key, value = pair.split("=", 1)
            result[key.strip()] = value.strip()

    return result
