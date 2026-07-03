"""Tests for expanduser handling in mesh-config path consumers.

Regression for the 2026-07-03 outage: a systemd unit whose ExecStart passed a
*literal* ``~/.culture/mesh.yaml`` crashed the server with ENOENT because the
loader opened the tilde path raw instead of expanding it. Every consumer of a
mesh-config path must resolve a leading ``~`` before opening or writing.

These tests use the *real* ``os.path.expanduser`` and simply point ``$HOME`` at
a temp dir (POSIX expanduser reads ``$HOME``), so they exercise the production
expansion rather than a stand-in. They also guard against the exact pollution
the outage RED-phase produced: a literal ``~`` directory under the cwd.
"""

from __future__ import annotations

import pytest

from culture_core.cli.shared.mesh import load_mesh_or_generate
from culture_core.mesh_config import (
    MeshConfig,
    MeshServerConfig,
    load_mesh_config,
    save_mesh_config,
)

TILDE_PATH = "~/.culture/mesh.yaml"


@pytest.fixture()
def fake_home(monkeypatch, tmp_path):
    """Point $HOME at a temp dir so real expanduser resolves '~' there."""
    home = tmp_path / "home"
    (home / ".culture").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    # os.path.expanduser also consults these on some platforms; keep them aligned.
    monkeypatch.delenv("USERPROFILE", raising=False)
    return home


class TestLoadSaveExpanduser:
    """load_mesh_config / save_mesh_config must expand a literal-tilde path."""

    def test_load_expands_tilde(self, fake_home):
        (fake_home / ".culture" / "mesh.yaml").write_text("server:\n  name: loaded\n")

        config = load_mesh_config(TILDE_PATH)

        assert config.server.name == "loaded"

    def test_save_expands_tilde_and_does_not_pollute_cwd(self, fake_home, monkeypatch):
        # Run from a scratch cwd so a stray literal '~' would be obvious there.
        scratch = fake_home.parent / "scratch"
        scratch.mkdir()
        monkeypatch.chdir(scratch)

        save_mesh_config(MeshConfig(server=MeshServerConfig(name="saved")), TILDE_PATH)

        # Written to the expanded location...
        assert (fake_home / ".culture" / "mesh.yaml").exists()
        # ...and NOT to a literal '~' directory under the cwd (the outage bug).
        assert not (scratch / "~").exists()

    def test_round_trip_through_tilde(self, fake_home):
        save_mesh_config(MeshConfig(server=MeshServerConfig(name="rt")), TILDE_PATH)

        assert load_mesh_config(TILDE_PATH).server.name == "rt"


class TestExpansionHappensBeforeOpen:
    """Audit: the path is expanded before open(), for every consumer.

    A missing file surfaces as FileNotFoundError whose ``filename`` is exactly
    what was handed to ``open()``. Asserting that filename is the *expanded*
    path (no leading '~') proves expansion happened before the open — the
    property the outage violated — without reaching into implementation.
    """

    def test_load_expands_before_open(self, fake_home):
        with pytest.raises(FileNotFoundError) as exc:
            load_mesh_config("~/.culture/does-not-exist.yaml")

        filename = str(exc.value.filename)
        assert not filename.startswith("~")
        assert str(fake_home) in filename


class TestCliResolutionArgPath:
    """The server-provisioning arg path resolves a literal-tilde --mesh-config.

    ``load_mesh_or_generate`` is the single resolution every unit-provisioning
    verb uses (mesh setup, server install/uninstall). Driving it with a literal
    tilde is the ExecStart scenario from the outage.
    """

    def test_load_mesh_or_generate_expands_tilde(self, fake_home):
        (fake_home / ".culture" / "mesh.yaml").write_text("server:\n  name: execstart\n")

        mesh = load_mesh_or_generate(TILDE_PATH)

        assert mesh is not None
        assert mesh.server.name == "execstart"
