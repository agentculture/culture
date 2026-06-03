"""Tests for the bridge peercred shim.

Linux uses ``SO_PEERCRED``, Darwin uses ``getpeereid(3)``, Windows is
unsupported. We exercise the live syscall path on the dev platform via a
``socket.socketpair()`` and skip the rest.
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

from culture.clients.bridge._peercred import peercred


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux-only path (SO_PEERCRED)",
)
def test_peercred_returns_self_uid_on_linux() -> None:
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        uid, gid = peercred(a.fileno())
        assert uid == os.getuid()
        assert gid == os.getgid()
    finally:
        a.close()
        b.close()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Darwin-only path (getpeereid)",
)
def test_peercred_returns_self_uid_on_darwin() -> None:
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        uid, gid = peercred(a.fileno())
        assert uid == os.getuid()
        assert gid == os.getgid()
    finally:
        a.close()
        b.close()


def test_peercred_raises_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(NotImplementedError) as excinfo:
        peercred(0)
    assert "win32" in str(excinfo.value)


def test_peercred_raises_on_unknown_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "haiku")
    with pytest.raises(NotImplementedError):
        peercred(0)
