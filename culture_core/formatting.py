"""Shared formatting utilities used across culture modules."""

from __future__ import annotations

import time


def relative_time(timestamp: float) -> str:
    """Format a Unix timestamp as relative time (e.g., '2m ago', '1h ago')."""
    delta = int(time.time() - timestamp)
    if delta < 0:
        return "just now"
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"
