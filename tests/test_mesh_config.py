# tests/test_mesh_config.py
"""Tests for mesh.yaml configuration module."""

import pytest

from culture_core.clients.claude.config import AgentConfig, DaemonConfig, ServerConnConfig
from culture_core.mesh_config import (
    MeshAgentConfig,
    MeshConfig,
    MeshLinkConfig,
    MeshServerConfig,
    from_daemon_config,
    load_mesh_config,
    merge_links,
    save_mesh_config,
)


def test_mesh_config_round_trip(tmp_path):
    """Save and reload a mesh config — all fields preserved."""
    config = MeshConfig(
        server=MeshServerConfig(
            name="spark",
            host="0.0.0.0",
            port=6667,
            links=[
                MeshLinkConfig(name="thor", host="192.168.1.12", port=6667, trust="full"),
            ],
        ),
        agents=[
            MeshAgentConfig(
                nick="claude", type="claude", workdir="~/projects/myproject", channels=["#general"]
            ),
            MeshAgentConfig(
                nick="codex",
                type="codex",
                workdir="~/projects/other",
                channels=["#general", "#dev"],
            ),
        ],
    )

    path = tmp_path / "mesh.yaml"
    save_mesh_config(config, path)
    loaded = load_mesh_config(path)

    assert loaded.server.name == "spark"
    assert loaded.server.port == 6667
    assert len(loaded.server.links) == 1
    assert loaded.server.links[0].name == "thor"
    assert loaded.server.links[0].trust == "full"
    assert len(loaded.agents) == 2
    assert loaded.agents[0].nick == "claude"
    assert loaded.agents[0].type == "claude"
    assert loaded.agents[0].workdir == "~/projects/myproject"
    assert loaded.agents[1].nick == "codex"
    assert loaded.agents[1].channels == ["#general", "#dev"]


def test_mesh_config_defaults(tmp_path):
    """Minimal config uses sensible defaults."""
    config = MeshConfig(
        server=MeshServerConfig(name="test"),
    )
    path = tmp_path / "mesh.yaml"
    save_mesh_config(config, path)
    loaded = load_mesh_config(path)

    assert loaded.server.name == "test"
    assert loaded.server.host == "0.0.0.0"
    assert loaded.server.port == 6667
    assert loaded.server.links == []
    assert loaded.agents == []


def test_mesh_config_no_password_field(tmp_path):
    """MeshLinkConfig does not store passwords — they live in OS keyring."""
    config = MeshConfig(
        server=MeshServerConfig(
            name="spark",
            links=[MeshLinkConfig(name="thor", host="1.2.3.4", port=6667)],
        ),
    )
    path = tmp_path / "mesh.yaml"
    save_mesh_config(config, path)

    # Verify no password in the YAML file
    content = path.read_text()
    assert "password" not in content.lower() or "# password" in content.lower()

    loaded = load_mesh_config(path)
    assert loaded.server.links[0].name == "thor"
    assert not hasattr(loaded.server.links[0], "password") or not getattr(
        loaded.server.links[0], "password", None
    )


def test_mesh_config_file_not_found():
    """Loading a missing file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_mesh_config("/nonexistent/mesh.yaml")


def test_from_daemon_config_strips_prefix():
    """from_daemon_config strips server prefix from agent nicks."""
    daemon = DaemonConfig(
        server=ServerConnConfig(name="spark", host="localhost", port=6667),
        agents=[
            AgentConfig(
                nick="spark-claude",
                agent="claude",
                directory="/home/user/proj",
                channels=["#general", "#dev"],
            ),
            AgentConfig(
                nick="spark-codex",
                agent="codex",
                directory="/home/user/other",
                channels=["#general"],
            ),
        ],
    )
    mesh = from_daemon_config(daemon)

    assert mesh.server.name == "spark"
    assert mesh.server.host == "0.0.0.0"  # listen address, not connection target
    assert mesh.server.port == 6667
    assert mesh.server.links == []
    assert len(mesh.agents) == 2
    assert mesh.agents[0].nick == "claude"
    assert mesh.agents[0].type == "claude"
    assert mesh.agents[0].workdir == "/home/user/proj"
    assert mesh.agents[0].channels == ["#general", "#dev"]
    assert mesh.agents[1].nick == "codex"
    assert mesh.agents[1].type == "codex"


def test_from_daemon_config_unprefixed_nick():
    """from_daemon_config handles nicks without server prefix."""
    daemon = DaemonConfig(
        server=ServerConnConfig(name="spark"),
        agents=[AgentConfig(nick="standalone", agent="claude", directory=".")],
    )
    mesh = from_daemon_config(daemon)
    assert mesh.agents[0].nick == "standalone"


def test_from_daemon_config_empty_agents():
    """from_daemon_config works with no agents."""
    daemon = DaemonConfig(
        server=ServerConnConfig(name="test", port=7000),
    )
    mesh = from_daemon_config(daemon)
    assert mesh.server.name == "test"
    assert mesh.server.port == 7000
    assert mesh.agents == []


def test_merge_links_appends_missing():
    """merge_links adds links not already present."""
    target = MeshConfig(
        server=MeshServerConfig(
            name="spark",
            links=[MeshLinkConfig(name="thor", host="1.2.3.4", port=6667)],
        ),
    )
    source = [
        MeshLinkConfig(name="orin", host="5.6.7.8", port=6668),
    ]
    merge_links(target, source)
    assert len(target.server.links) == 2
    assert target.server.links[1].name == "orin"


def test_merge_links_skips_duplicates():
    """merge_links does not duplicate links already present."""
    target = MeshConfig(
        server=MeshServerConfig(
            name="spark",
            links=[MeshLinkConfig(name="thor", host="1.2.3.4", port=6667)],
        ),
    )
    source = [
        MeshLinkConfig(name="thor", host="9.9.9.9", port=9999),
        MeshLinkConfig(name="orin", host="5.6.7.8", port=6668),
    ]
    merge_links(target, source)
    assert len(target.server.links) == 2
    names = [l.name for l in target.server.links]
    assert names == ["thor", "orin"]
    # Original thor link is unchanged
    assert target.server.links[0].host == "1.2.3.4"
