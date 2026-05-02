"""Mesh and link configuration helpers for culture CLI."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from culture.config import load_config

from .constants import DEFAULT_CONFIG

logger = logging.getLogger("culture")


def parse_link(value: str):
    """Parse a link spec: name:host:port:password[:trust]

    Trust is extracted from the end if it matches a known value.
    This allows passwords containing colons.
    """
    from agentirc.config import LinkConfig

    trust = "full"
    if value.endswith(":full") or value.endswith(":restricted"):
        value, trust = value.rsplit(":", 1)

    parts = value.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"Link must be name:host:port:password[:trust], got: {value}"
        )
    name, host, port_str, password = parts
    try:
        port = int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid port: {port_str}")
    return LinkConfig(name=name, host=host, port=port, password=password, trust=trust)


def resolve_links_from_mesh(mesh_config_path: str) -> list:
    """Load link configs from mesh.yaml, looking up passwords from OS keyring."""
    from agentirc.config import LinkConfig

    from culture.credentials import lookup_credential
    from culture.mesh_config import load_mesh_config

    mesh = load_mesh_config(mesh_config_path)
    links = []
    for lc in mesh.server.links:
        password = lookup_credential(lc.name)
        if not password:
            logger.warning(
                "No credential found for peer '%s' — link will not be established. "
                "Run 'culture mesh setup' to store link passwords.",
                lc.name,
            )
            continue
        links.append(
            LinkConfig(
                name=lc.name,
                host=lc.host,
                port=lc.port,
                password=password,
                trust=lc.trust,
            )
        )
    return links


def generate_mesh_from_agents(mesh_config_path: str):
    """Fall back to generating mesh.yaml from agents.yaml when mesh.yaml is missing."""
    from culture.mesh_config import from_daemon_config, save_mesh_config

    if not os.path.isfile(DEFAULT_CONFIG):
        print(f"Mesh config not found: {mesh_config_path}", file=sys.stderr)
        print(f"Agent config not found either: {DEFAULT_CONFIG}", file=sys.stderr)
        return None

    daemon_config = load_config(DEFAULT_CONFIG)
    mesh = from_daemon_config(daemon_config)
    save_mesh_config(mesh, mesh_config_path)
    print(f"No mesh.yaml found — generated from {DEFAULT_CONFIG}")
    return mesh


def build_server_start_cmd(mesh, culture_bin: str, mesh_config_path: str) -> list[str]:
    """Build the server start command with --foreground and --mesh-config."""
    return [
        culture_bin,
        "server",
        "start",
        "--foreground",
        "--name",
        mesh.server.name,
        "--host",
        mesh.server.host,
        "--port",
        str(mesh.server.port),
        "--mesh-config",
        mesh_config_path,
    ]
