"""Declarative mesh configuration (mesh.yaml)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from culture.clients.claude.config import DaemonConfig


@dataclass
class MeshLinkConfig:
    """A peer server to link to.

    Passwords are NOT stored here — they live in the OS credential store.
    Use culture.credentials.lookup_credential(name) to retrieve them.
    """

    name: str
    host: str
    port: int = 6667
    trust: str = "full"


@dataclass
class MeshServerConfig:
    """Local server settings."""

    name: str
    host: str = "0.0.0.0"
    port: int = 6667
    links: list[MeshLinkConfig] = field(default_factory=list)


@dataclass
class MeshAgentConfig:
    """An agent to run on this machine."""

    nick: str = ""
    type: str = "claude"
    workdir: str = "."
    channels: list[str] = field(default_factory=lambda: ["#general"])
    archived: bool = False


@dataclass
class MeshConfig:
    """Top-level mesh configuration for one machine."""

    server: MeshServerConfig = field(default_factory=lambda: MeshServerConfig(name="culture"))
    agents: list[MeshAgentConfig] = field(default_factory=list)


DEFAULT_MESH_PATH = os.path.expanduser("~/.culture/mesh.yaml")


def load_mesh_config(path: str | Path = DEFAULT_MESH_PATH) -> MeshConfig:
    """Load mesh config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server_raw = raw.get("server", {}) or {}
    links_raw = server_raw.get("links", []) or []
    links = [MeshLinkConfig(**lc) for lc in links_raw]
    server_kwargs = {k: v for k, v in server_raw.items() if k != "links"}
    if "name" not in server_kwargs:
        server_kwargs["name"] = "culture"
    server = MeshServerConfig(**server_kwargs, links=links)

    agents = [MeshAgentConfig(**a) for a in raw.get("agents", [])]

    return MeshConfig(server=server, agents=agents)


def from_daemon_config(daemon_config: DaemonConfig) -> MeshConfig:
    """Derive a MeshConfig from an existing DaemonConfig (agents.yaml).

    Useful when mesh.yaml doesn't exist but the user has a running mesh
    started manually via ``culture server start`` + ``culture start``.

    Note: DaemonConfig.server.host is the *connection* target (often localhost),
    while MeshServerConfig.host is the *listen* address. We use the default
    0.0.0.0 for the listen address to preserve external accessibility.
    """
    server = MeshServerConfig(
        name=daemon_config.server.name,
        port=daemon_config.server.port,
    )
    server_prefix = f"{daemon_config.server.name}-"
    agents = []
    for a in daemon_config.agents:
        nick = a.nick
        if nick.startswith(server_prefix):
            nick = nick[len(server_prefix) :]
        agents.append(
            MeshAgentConfig(
                nick=nick,
                type=a.agent,
                workdir=a.directory,
                channels=list(a.channels),
            )
        )
    return MeshConfig(server=server, agents=agents)


def merge_links(target: MeshConfig, source_links: list[MeshLinkConfig]) -> None:
    """Copy link configs from *source_links* into *target* if not already present."""
    existing = {link.name for link in target.server.links}
    for link in source_links:
        if link.name not in existing:
            target.server.links.append(link)
            existing.add(link.name)


def save_mesh_config(config: MeshConfig, path: str | Path = DEFAULT_MESH_PATH) -> None:
    """Serialize mesh config to YAML and write atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = asdict(config)
    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False)

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(yaml_str)
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
