"""Pin the `culture server` deprecation alias after Phase A3.

`culture server <verb>` keeps working through 9.x as an alias for
`culture chat <verb>`. It must:

1. Print a stable, greppable warning to stderr at dispatch time so
   automation can detect the rename and migrate.
2. Route the verb through to the same handlers as `culture chat`.

Removed in 10.0.0 (per ``culture/cli/server.py`` module docstring).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

CULTURE = [sys.executable, "-m", "culture"]

DEPRECATION_NEEDLE = "'culture server' is renamed to 'culture chat'"


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout, proc.stderr


def test_server_help_lists_same_verbs_as_chat() -> None:
    rc_chat, out_chat, _ = _run([*CULTURE, "chat", "--help"])
    rc_srv, out_srv, _ = _run([*CULTURE, "server", "--help"])
    assert rc_chat == rc_srv == 0
    # Both nouns must list the full verb set. The wording around the
    # parser name will differ ("chat" vs "(deprecated alias for ...)"
    # so we only assert the verbs themselves appear.
    for verb in (
        "start",
        "stop",
        "status",
        "default",
        "rename",
        "archive",
        "unarchive",
        "restart",
        "link",
        "logs",
        "version",
        "serve",
    ):
        assert verb in out_chat
        assert verb in out_srv


def test_server_version_emits_deprecation_warning() -> None:
    """`culture server version` should warn and forward."""
    rc, out, err = _run([*CULTURE, "server", "version"])
    assert rc == 0
    assert DEPRECATION_NEEDLE in err, f"deprecation warning missing from stderr: {err!r}"
    # The forwarded output (agentirc's version) lands on stdout.
    assert "agentirc" in out.lower()


@pytest.mark.parametrize("verb", ["status", "version"])
def test_server_alias_routes_to_same_handler_as_chat(verb: str) -> None:
    """Output of `culture server <verb>` matches `culture chat <verb>`
    apart from the stderr deprecation warning."""
    chat_rc, chat_out, _ = _run([*CULTURE, "chat", verb])
    srv_rc, srv_out, srv_err = _run([*CULTURE, "server", verb])
    assert srv_rc == chat_rc
    assert srv_out == chat_out
    assert DEPRECATION_NEEDLE in srv_err
