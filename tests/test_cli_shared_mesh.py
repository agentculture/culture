"""Tests for `culture.cli.shared.mesh` — link/mesh helpers for CLI.

The module is a flat utility surface — four functions, no dispatch, no
IPC, no process management. Tests are unit-level with `tmp_path` for any
filesystem fixtures and `monkeypatch` for `culture.credentials.lookup_credential`.

Functions under test:

- `parse_link(value)` — argparse type for `name:host:port:password[:trust]` specs.
- `resolve_links_from_mesh(mesh_config_path)` — reads mesh.yaml + keyring.
- `generate_mesh_from_agents(mesh_config_path)` — fallback when mesh.yaml missing.
- `build_server_start_cmd(mesh, culture_bin, mesh_config_path)` — pure argv builder.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import pytest

from culture.cli.shared import mesh as mesh_mod

# ---------------------------------------------------------------------------
# parse_link
# ---------------------------------------------------------------------------


class TestParseLink:
    def test_basic_spec_defaults_to_full_trust(self):
        link = mesh_mod.parse_link("thor:thor.example.com:6667:secret")
        assert link.name == "thor"
        assert link.host == "thor.example.com"
        assert link.port == 6667
        assert link.password == "secret"
        assert link.trust == "full"

    def test_explicit_full_trust_suffix(self):
        link = mesh_mod.parse_link("thor:thor.example.com:6667:secret:full")
        assert link.trust == "full"
        assert link.password == "secret"

    def test_explicit_restricted_trust_suffix(self):
        link = mesh_mod.parse_link("thor:thor.example.com:6667:secret:restricted")
        assert link.trust == "restricted"
        assert link.password == "secret"

    def test_password_can_contain_colons(self):
        """Password is parsed with maxsplit=3 so embedded colons survive."""
        link = mesh_mod.parse_link("thor:thor.example.com:6667:pass:with:colons")
        assert link.host == "thor.example.com"
        assert link.port == 6667
        assert link.password == "pass:with:colons"
        assert link.trust == "full"

    def test_rejects_missing_fields(self):
        with pytest.raises(argparse.ArgumentTypeError, match="Link must be"):
            mesh_mod.parse_link("only:two")

    def test_rejects_non_integer_port(self):
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid port"):
            mesh_mod.parse_link("thor:thor.example.com:not-a-port:secret")


# ---------------------------------------------------------------------------
# resolve_links_from_mesh
# ---------------------------------------------------------------------------


@dataclass
class _StubLink:
    """Minimal duck of agentirc.config.LinkConfig for mesh_config.links."""

    name: str
    host: str
    port: int
    trust: str = "full"


@dataclass
class _StubServer:
    name: str = "spark"
    host: str = "spark.example.com"
    port: int = 6667
    links: list = field(default_factory=list)


@dataclass
class _StubMesh:
    server: _StubServer


class TestResolveLinksFromMesh:
    def test_returns_empty_when_no_peers(self, monkeypatch):
        monkeypatch.setattr(
            "culture.mesh_config.load_mesh_config",
            lambda _p: _StubMesh(server=_StubServer(links=[])),
        )
        monkeypatch.setattr("culture.credentials.lookup_credential", lambda _n: "unused")
        assert mesh_mod.resolve_links_from_mesh("/tmp/mesh.yaml") == []

    def test_builds_one_link_per_peer_with_keyring_password(self, monkeypatch):
        peers = [
            _StubLink(name="thor", host="thor.example.com", port=6667),
            _StubLink(name="zeus", host="zeus.example.com", port=6668, trust="restricted"),
        ]
        monkeypatch.setattr(
            "culture.mesh_config.load_mesh_config",
            lambda _p: _StubMesh(server=_StubServer(links=peers)),
        )
        passwords = {"thor": "thor-pw", "zeus": "zeus-pw"}
        monkeypatch.setattr(
            "culture.credentials.lookup_credential",
            lambda name: passwords.get(name),
        )

        links = mesh_mod.resolve_links_from_mesh("/tmp/mesh.yaml")

        assert [(l.name, l.host, l.port, l.password, l.trust) for l in links] == [
            ("thor", "thor.example.com", 6667, "thor-pw", "full"),
            ("zeus", "zeus.example.com", 6668, "zeus-pw", "restricted"),
        ]

    def test_skips_peers_without_credentials(self, monkeypatch, caplog):
        peers = [
            _StubLink(name="thor", host="thor.example.com", port=6667),
            _StubLink(name="ghost", host="ghost.example.com", port=6669),
        ]
        monkeypatch.setattr(
            "culture.mesh_config.load_mesh_config",
            lambda _p: _StubMesh(server=_StubServer(links=peers)),
        )
        # ghost has no credential
        monkeypatch.setattr(
            "culture.credentials.lookup_credential",
            lambda name: "thor-pw" if name == "thor" else None,
        )

        with caplog.at_level("WARNING", logger="culture"):
            links = mesh_mod.resolve_links_from_mesh("/tmp/mesh.yaml")

        assert [l.name for l in links] == ["thor"]
        assert any("ghost" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# generate_mesh_from_agents
# ---------------------------------------------------------------------------


class TestGenerateMeshFromAgents:
    def test_returns_none_when_default_config_missing(self, monkeypatch, capsys, tmp_path):
        # Point DEFAULT_CONFIG at a path that doesn't exist.
        nonexistent = str(tmp_path / "agents.yaml")
        monkeypatch.setattr(mesh_mod, "DEFAULT_CONFIG", nonexistent)

        result = mesh_mod.generate_mesh_from_agents("/tmp/mesh.yaml")

        assert result is None
        err = capsys.readouterr().err
        assert "Mesh config not found" in err
        assert "Agent config not found" in err

    def test_generates_and_saves_mesh_when_default_config_present(
        self, monkeypatch, capsys, tmp_path
    ):
        agents_path = tmp_path / "agents.yaml"
        agents_path.write_text("placeholder\n")  # contents don't matter — load_config is mocked
        mesh_path = tmp_path / "mesh.yaml"
        monkeypatch.setattr(mesh_mod, "DEFAULT_CONFIG", str(agents_path))

        captured_saves: list[tuple[object, str]] = []
        fake_mesh = _StubMesh(server=_StubServer(name="spark"))

        # `culture.config.load_config` is bound at import time into mesh_mod —
        # patch it there. `from_daemon_config` and `save_mesh_config` are
        # imported lazily inside `generate_mesh_from_agents`, so patching
        # `culture.mesh_config.*` works for them.
        monkeypatch.setattr(mesh_mod, "load_config", lambda _p: "daemon-config-obj")
        monkeypatch.setattr(
            "culture.mesh_config.from_daemon_config",
            lambda dc: fake_mesh if dc == "daemon-config-obj" else None,
        )
        monkeypatch.setattr(
            "culture.mesh_config.save_mesh_config",
            lambda mesh, path: captured_saves.append((mesh, path)),
        )

        result = mesh_mod.generate_mesh_from_agents(str(mesh_path))

        assert result is fake_mesh
        assert captured_saves == [(fake_mesh, str(mesh_path))]
        out = capsys.readouterr().out
        assert "generated from" in out


# ---------------------------------------------------------------------------
# build_server_start_cmd
# ---------------------------------------------------------------------------


class TestBuildServerStartCmd:
    def _mesh(self, name="spark", host="0.0.0.0", port=6667):
        return _StubMesh(server=_StubServer(name=name, host=host, port=port))

    def test_includes_foreground_and_mesh_config(self):
        cmd = mesh_mod.build_server_start_cmd(self._mesh(), "/usr/bin/culture", "/tmp/mesh.yaml")
        assert cmd[0] == "/usr/bin/culture"
        assert cmd[1:3] == ["server", "start"]
        assert "--foreground" in cmd
        assert "--mesh-config" in cmd
        assert cmd[cmd.index("--mesh-config") + 1] == "/tmp/mesh.yaml"

    def test_includes_server_name_host_and_port(self):
        cmd = mesh_mod.build_server_start_cmd(
            self._mesh(name="thor", host="10.0.0.5", port=6700),
            "/usr/bin/culture",
            "/tmp/mesh.yaml",
        )
        assert cmd[cmd.index("--name") + 1] == "thor"
        assert cmd[cmd.index("--host") + 1] == "10.0.0.5"
        assert cmd[cmd.index("--port") + 1] == "6700"

    def test_port_is_stringified(self):
        cmd = mesh_mod.build_server_start_cmd(
            self._mesh(port=12345), "/usr/bin/culture", "/tmp/mesh.yaml"
        )
        port_arg = cmd[cmd.index("--port") + 1]
        assert port_arg == "12345"
        assert isinstance(port_arg, str)
