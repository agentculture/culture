"""Regression: Qodo PR #50 #7 — Linux peercred signed-int overflow.

The prior ``struct.unpack("3i", ...)`` parsed pid AND uid AND gid as
SIGNED 32-bit integers. uid_t and gid_t are unsigned on Linux/glibc
(``unsigned int``), so a uid with the high bit set
(``>= 2_147_483_648``) wrapped negative — producing a false
``peer_uid != os.getuid()`` mismatch in the bridge IPC server that
denied a legitimate same-user connection.

The fix is the ``"iII"`` format string: ``i`` for the signed pid_t,
``II`` for the unsigned uid_t / gid_t. This file locks the parse
behavior by constructing raw bytes covering the boundary cases
(zero, root, normal user, just-below-2^31, exactly-2^31, large).
"""

from __future__ import annotations

import struct

import pytest

from culture.clients.bridge import _peercred


def _pack(pid: int, uid: int, gid: int) -> bytes:
    """Pack a ucred for the current platform's expected layout.

    Uses the module's actual ``_LINUX_UCRED_FMT`` so a future format
    bump would fail the test loudly rather than silently agreeing
    with itself.
    """
    return struct.pack(_peercred._LINUX_UCRED_FMT, pid, uid, gid)


class TestUcredFormatString:
    def test_format_is_pid_int_uid_uint_gid_uint(self) -> None:
        # Documenting the contract: pid signed (Linux pid_t is int),
        # uid and gid unsigned (Linux uid_t/gid_t are unsigned int).
        assert _peercred._LINUX_UCRED_FMT == "iII"

    def test_format_size_matches_struct_ucred(self) -> None:
        # struct ucred is 12 bytes (3 × 32-bit) on every 32- and 64-bit
        # Linux ABI we care about.
        assert _peercred._LINUX_UCRED_SIZE == 12


class TestUnpackBoundaryUids:
    """Round-trip pack/unpack tests across uid/gid boundary values.

    These cover the range where the old signed format produced wrong
    results — anywhere from 2^31 (-2_147_483_648 when interpreted as
    int) up to 2^32-1.
    """

    @pytest.mark.parametrize(
        "uid",
        [
            0,  # root
            1,  # daemon
            500,  # macOS default user
            1000,  # Linux default user
            65534,  # nobody
            65535,  # 16-bit boundary
            2_000_000_000,  # near INT32_MAX, still positive in signed too
            2_147_483_647,  # INT32_MAX exactly — last positive in signed
            2_147_483_648,  # FIRST value that would have gone negative
            3_000_000_000,  # well into the high-uid range
            4_294_967_294,  # UINT32_MAX - 1
            4_294_967_295,  # UINT32_MAX — kernel-special "invalid" sentinel
        ],
    )
    def test_uid_round_trips(self, uid: int) -> None:
        raw = _pack(pid=12345, uid=uid, gid=42)
        pid, parsed_uid, parsed_gid = struct.unpack(_peercred._LINUX_UCRED_FMT, raw)
        assert parsed_uid == uid, (
            f"high-bit uid {uid} mis-parsed as {parsed_uid} — the old "
            "signed-int format would do exactly this"
        )
        assert parsed_gid == 42
        assert pid == 12345

    @pytest.mark.parametrize(
        "gid",
        [0, 1000, 2_147_483_648, 3_000_000_000, 4_294_967_295],
    )
    def test_gid_round_trips(self, gid: int) -> None:
        raw = _pack(pid=1, uid=1000, gid=gid)
        _pid, _uid, parsed_gid = struct.unpack(_peercred._LINUX_UCRED_FMT, raw)
        assert parsed_gid == gid

    def test_old_signed_format_would_have_failed(self) -> None:
        """Belt + braces: confirm the OLD format actually misparses
        UINT32_MAX uid as negative. If this ever passes (because Python
        struct semantics change), we want to know."""
        raw = struct.pack("iII", 0, 4_294_967_295, 0)
        # Old buggy format = "3i" — pid + uid + gid all signed.
        _pid, old_uid, _gid = struct.unpack("3i", raw)
        assert old_uid == -1, (
            "expected the old signed-int format to wrap UINT32_MAX uid "
            f"to -1; got {old_uid} — this regression test may now be stale"
        )
