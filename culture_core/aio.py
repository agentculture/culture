"""Async utilities for culture."""

from __future__ import annotations

import asyncio


async def maybe_await(result):
    """Await the result only if it's a coroutine, otherwise return directly."""
    if asyncio.iscoroutine(result):
        return await result
    return result
