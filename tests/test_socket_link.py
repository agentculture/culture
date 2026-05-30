"""Tests for culture.clients._socket_link — symlink management for IPC sockets."""

from __future__ import annotations

import os
import tempfile

import pytest

from culture.clients._socket_link import (
    _cli_runtime_dir,
    ensure_socket_symlink,
    remove_socket_symlink,
)


@pytest.fixture
def tmp_dirs(tmp_path, monkeypatch):
    """Create separate socket and CLI-runtime dirs; unset XDG_RUNTIME_DIR."""
    sock_dir = tmp_path / "sockets"
    sock_dir.mkdir()
    cli_dir = tmp_path / "cli-run"
    cli_dir.mkdir()
    # Force _cli_runtime_dir to return our test dir
    monkeypatch.setattr(
        "culture.clients._socket_link._cli_runtime_dir",
        lambda: str(cli_dir),
    )
    return sock_dir, cli_dir


class TestEnsureSocketSymlink:
    """ensure_socket_symlink creates an atomic symlink."""

    def test_creates_symlink(self, tmp_dirs):
        sock_dir, cli_dir = tmp_dirs
        sock_path = str(sock_dir / "culture-test-agent.sock")
        # Create a fake socket file
        with open(sock_path, "w") as f:
            f.write("")

        link = ensure_socket_symlink(sock_path, "test-agent")

        assert link is not None
        assert os.path.islink(link)
        assert os.readlink(link) == sock_path
        assert link == str(cli_dir / "culture-test-agent.sock")

    def test_returns_none_when_same_dir(self, tmp_path, monkeypatch):
        """No symlink needed when socket already lives in the CLI dir."""
        monkeypatch.setattr(
            "culture.clients._socket_link._cli_runtime_dir",
            lambda: str(tmp_path),
        )
        sock_path = str(tmp_path / "culture-local-agent.sock")
        with open(sock_path, "w") as f:
            f.write("")

        link = ensure_socket_symlink(sock_path, "local-agent")
        assert link is None

    def test_replaces_stale_symlink(self, tmp_dirs):
        """An existing stale symlink is atomically replaced."""
        sock_dir, cli_dir = tmp_dirs
        old_target = str(sock_dir / "old.sock")
        new_target = str(sock_dir / "culture-local-worker.sock")
        link_path = str(cli_dir / "culture-local-worker.sock")

        # Create a stale symlink
        os.symlink(old_target, link_path)
        assert os.readlink(link_path) == old_target

        # Now create real socket and ensure symlink
        with open(new_target, "w") as f:
            f.write("")

        result = ensure_socket_symlink(new_target, "local-worker")
        assert result == link_path
        assert os.readlink(link_path) == new_target

    def test_replaces_regular_file(self, tmp_dirs):
        """A regular file at the link path is replaced."""
        sock_dir, cli_dir = tmp_dirs
        sock_path = str(sock_dir / "culture-local-agent.sock")
        link_path = str(cli_dir / "culture-local-agent.sock")

        with open(sock_path, "w") as f:
            f.write("")
        # Place a regular file where the symlink should go
        with open(link_path, "w") as f:
            f.write("stale")

        result = ensure_socket_symlink(sock_path, "local-agent")
        assert result == link_path
        assert os.path.islink(link_path)
        assert os.readlink(link_path) == sock_path


class TestRemoveSocketSymlink:
    """remove_socket_symlink cleans up the symlink."""

    def test_removes_symlink(self, tmp_path):
        link_path = str(tmp_path / "culture-agent.sock")
        os.symlink("/tmp/nonexistent", link_path)
        assert os.path.islink(link_path)

        remove_socket_symlink(link_path)
        assert not os.path.exists(link_path)

    def test_noop_on_none(self):
        """Passing None does nothing."""
        remove_socket_symlink(None)  # should not raise

    def test_noop_on_missing(self, tmp_path):
        """Passing a path that does not exist does nothing."""
        remove_socket_symlink(str(tmp_path / "nonexistent.sock"))

    def test_does_not_remove_regular_file(self, tmp_path):
        """Only removes symlinks, not regular files."""
        path = str(tmp_path / "culture-agent.sock")
        with open(path, "w") as f:
            f.write("real socket")

        remove_socket_symlink(path)
        assert os.path.exists(path)  # regular file left intact


class TestCliRuntimeDir:
    """_cli_runtime_dir mirrors the CLI's resolution logic."""

    def test_uses_xdg_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        assert _cli_runtime_dir() == str(tmp_path)

    def test_falls_back_to_culture_run(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        home = str(tmp_path / "fakehome")
        os.makedirs(home)
        monkeypatch.setenv("HOME", home)
        result = _cli_runtime_dir()
        expected = os.path.join(home, ".culture", "run")
        assert result == expected
        assert os.path.isdir(expected)
        # Check permissions
        mode = os.stat(expected).st_mode & 0o777
        assert mode == 0o700
