"""Peer-credential lookup for AF_UNIX sockets.

Bridge IPC needs to know who is on the other end of an accepted Unix-domain
socket. Linux exposes this through ``SO_PEERCRED`` (a ``struct ucred`` of
``pid, uid, gid``); Darwin does not implement ``SO_PEERCRED`` at all and
instead provides ``getpeereid(3)`` in libc. Windows has no equivalent for
AF_UNIX sockets.

This module hides those differences behind a single ``peercred(sock_fd)``
function returning ``(uid, gid)``.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import socket
import struct
import sys

__all__ = ["peercred"]


# struct ucred { pid_t pid; uid_t uid; gid_t gid; } on Linux.
# Linux/glibc declare pid_t as ``int`` (signed 32-bit) and uid_t/gid_t as
# ``unsigned int`` (32-bit). Qodo PR #50 #7: the previous ``"3i"`` format
# unpacked all three as SIGNED 32-bit integers, so a UID or GID with the
# high bit set (>= 2_147_483_648 — possible with explicit setuid or with
# user-namespace mappings) was mis-parsed as negative, producing a false
# ``uid != os.getuid()`` mismatch and refusing IPC from the legitimate
# peer.
#
# Layout below: "i" for pid (signed), "II" for uid/gid (unsigned). Native
# alignment matches glibc — pid is 4 bytes, no trailing padding because
# all members are 32-bit aligned.
_LINUX_UCRED_FMT = "iII"
_LINUX_UCRED_SIZE = struct.calcsize(_LINUX_UCRED_FMT)


def _peercred_linux(sock_fd: int) -> tuple[int, int]:
    # socket.fromfd() dup()s the descriptor, so closing the wrapper does not
    # affect the fd the caller handed us.
    sock = socket.fromfd(sock_fd, socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _LINUX_UCRED_SIZE)
    finally:
        sock.close()
    _pid, uid, gid = struct.unpack(_LINUX_UCRED_FMT, raw)
    return uid, gid


def _peercred_darwin(sock_fd: int) -> tuple[int, int]:
    libc_path = ctypes.util.find_library("c")
    if libc_path is None:  # pragma: no cover — libc is always present on Darwin
        raise OSError("libc not found; cannot resolve getpeereid")
    libc = ctypes.CDLL(libc_path, use_errno=True)

    # int getpeereid(int s, uid_t *euid, gid_t *egid);
    libc.getpeereid.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    libc.getpeereid.restype = ctypes.c_int

    uid = ctypes.c_uint32()
    gid = ctypes.c_uint32()
    rc = libc.getpeereid(sock_fd, ctypes.byref(uid), ctypes.byref(gid))
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"getpeereid(fd={sock_fd}) failed: {err}")
    return int(uid.value), int(gid.value)


def peercred(sock_fd: int) -> tuple[int, int]:
    """Return ``(uid, gid)`` of the peer connected to *sock_fd*.

    *sock_fd* must be the integer file descriptor of an accepted AF_UNIX
    stream socket. The caller retains ownership of the descriptor; this
    function does not close it.

    Raises:
        NotImplementedError: On platforms other than Linux and Darwin.
        OSError: If the underlying syscall fails.
    """
    if sys.platform.startswith("linux"):
        return _peercred_linux(sock_fd)
    if sys.platform == "darwin":
        return _peercred_darwin(sock_fd)
    raise NotImplementedError(
        f"peercred() is not supported on platform {sys.platform!r}; "
        "only Linux (SO_PEERCRED) and Darwin (getpeereid) are implemented."
    )
