"""Lightweight daemon IPC status queries for the console."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def discover_agent_sockets() -> list[tuple[str, Path]]:
    """List culture daemon sockets in the culture runtime directory.

    Returns a list of ``(nick, socket_path)`` tuples.
    """
    from culture.cli.shared.constants import culture_runtime_dir

    runtime_dir = Path(culture_runtime_dir())
    results: list[tuple[str, Path]] = []
    if not runtime_dir.is_dir():
        return results
    for entry in runtime_dir.iterdir():
        if entry.name.startswith("culture-") and entry.name.endswith(".sock") and entry.is_socket():
            nick = entry.name[len("culture-") : -len(".sock")]
            results.append((nick, entry))
    return results


async def query_agent_status(socket_path: Path) -> dict:
    """Query a single daemon socket for status (no LLM query).

    Returns a dict with ``activity``, ``paused``, ``circuit_open``,
    ``running`` keys, or an empty dict on failure.
    """
    from culture.cli.shared.ipc import ipc_request

    resp = await ipc_request(str(socket_path), "status")
    if resp is None or not resp.get("ok"):
        return {}
    return resp.get("data", {})


def _derive_activity(data: dict) -> str:
    """Derive a single activity string from daemon status fields."""
    if data.get("circuit_open"):
        return "circuit-open"
    if data.get("paused"):
        return "paused"
    if not data.get("running"):
        return "idle"
    return data.get("activity", "idle")


async def query_all_agents() -> dict[str, str]:
    """Query all local daemon sockets and return nick -> activity mapping."""
    import asyncio

    sockets = discover_agent_sockets()
    if not sockets:
        return {}

    results: dict[str, str] = {}

    async def _query_one(nick: str, path: Path) -> None:
        try:
            data = await asyncio.wait_for(query_agent_status(path), timeout=3.0)
            if data:
                results[nick] = _derive_activity(data)
        except (TimeoutError, Exception):
            logger.debug("Failed to query status for %s", nick)

    await asyncio.gather(*[_query_one(nick, path) for nick, path in sockets])
    return results
