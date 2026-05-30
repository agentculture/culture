"""Symlink management for daemon IPC sockets.

The daemon binds its Unix socket in the runtime directory ($XDG_RUNTIME_DIR
or /tmp), but the culture channel CLI resolves sockets via ~/.culture/run/.
When those two directories differ, the CLI cannot reach the daemon.

This module creates an atomic symlink from the CLI-visible path to the real
socket immediately after bind, and removes it on stop.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile

logger = logging.getLogger(__name__)


def _cli_runtime_dir() -> str:
    """Return the directory where the CLI looks for sockets.

    Mirrors ``culture.cli.shared.constants.culture_runtime_dir`` without
    importing CLI code so the daemon does not depend on the CLI package.
    If you change the resolution order here, update ``constants.py`` too.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return xdg
    fallback = os.path.join(os.path.expanduser("~"), ".culture", "run")
    os.makedirs(fallback, mode=0o700, exist_ok=True)
    os.chmod(fallback, stat.S_IRWXU)
    return fallback


def ensure_socket_symlink(socket_path: str, nick: str) -> str | None:
    """Create a symlink so the CLI can find the socket at *socket_path*.

    Returns the symlink path on success, or None if socket_path already
    lives in the CLI directory (no link needed).
    """
    link_path = None
    tmp_path = None
    try:
        cli_dir = _cli_runtime_dir()
        sock_name = f"culture-{nick}.sock"
        link_path = os.path.join(cli_dir, sock_name)

        if os.path.abspath(socket_path) == os.path.abspath(link_path):
            return None
        fd, tmp_path = tempfile.mkstemp(dir=cli_dir, prefix=f".{sock_name}.")
        os.close(fd)
        os.unlink(tmp_path)
        os.symlink(socket_path, tmp_path)
        os.replace(tmp_path, link_path)
        logger.debug("Symlinked %s -> %s", link_path, socket_path)
        return link_path
    except OSError:
        logger.warning(
            "Failed to create socket symlink %s -> %s",
            link_path or "<unknown>",
            socket_path,
            exc_info=True,
        )
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return None


def remove_socket_symlink(link_path: str | None) -> None:
    """Remove a symlink previously created by ensure_socket_symlink.

    Safe to call with None (no-op).
    """
    if link_path is None:
        return
    try:
        if os.path.islink(link_path):
            os.unlink(link_path)
            logger.debug("Removed socket symlink %s", link_path)
    except OSError:
        logger.warning("Failed to remove socket symlink %s", link_path, exc_info=True)
