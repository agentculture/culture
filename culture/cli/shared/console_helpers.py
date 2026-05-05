"""Shared helpers for resolving culture servers and console nicks.

Extracted from ``culture.cli.mesh`` so both the new ``culture console``
group and the legacy ``culture mesh console`` deprecation alias can
reuse them without circular imports.
"""

from __future__ import annotations

import os
import re
import subprocess

from culture.pidfile import list_servers, read_default_server, read_port


def resolve_server(server_name: str | None) -> tuple[str, int] | None:
    """Resolve a culture server name (or default) to ``(name, port)``.

    Returns ``None`` when no culture servers are running.
    """
    if server_name:
        p = read_port(server_name)
        port = p if p else 6667
        return server_name, port

    servers = list_servers()
    if not servers:
        return None

    if len(servers) == 1:
        return servers[0]["name"], servers[0]["port"]

    default = read_default_server()
    if default:
        match = [s for s in servers if s["name"] == default]
        if match:
            return match[0]["name"], match[0]["port"]

    return servers[0]["name"], servers[0]["port"]


def resolve_console_nick() -> str:
    """Resolve the human nick: git user.name -> OS USER -> 'human'."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            name = result.stdout.strip().lower()
            name = re.sub(r"[^a-z0-9-]", "", name.replace(" ", "-"))
            if name:
                return name
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return os.environ.get("USER", "human")
