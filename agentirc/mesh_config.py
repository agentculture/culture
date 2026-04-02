"""Declarative mesh configuration (mesh.yaml)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass
class MeshLinkConfig:
    """A peer server to link to.

    Passwords are NOT stored here — they live in the OS credential store.
    Use agentirc.credentials.lookup_credential(name) to retrieve them.
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


@dataclass
class MeshConfig:
    """Top-level mesh configuration for one machine."""

    server: MeshServerConfig = field(default_factory=lambda: MeshServerConfig(name="agentirc"))
    agents: list[MeshAgentConfig] = field(default_factory=list)


DEFAULT_MESH_PATH = os.path.expanduser("~/.agentirc/mesh.yaml")


def load_mesh_config(path: str | Path = DEFAULT_MESH_PATH) -> MeshConfig:
    """Load mesh config from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    server_raw = raw.get("server", {}) or {}
    links_raw = server_raw.get("links", []) or []
    links = [MeshLinkConfig(**lc) for lc in links_raw]
    server_kwargs = {k: v for k, v in server_raw.items() if k != "links"}
    if "name" not in server_kwargs:
        server_kwargs["name"] = "agentirc"
    server = MeshServerConfig(**server_kwargs, links=links)

    agents = [MeshAgentConfig(**a) for a in raw.get("agents", [])]

    return MeshConfig(server=server, agents=agents)


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
