"""Guard against re-citing a shared module into a single backend without
following the documented fork-back procedure.

If a backend genuinely needs to diverge on one of these modules, the
fork-back procedure (see docs/architecture/shared-vs-cited.md) is to:

    1. cp culture/clients/shared/X.py culture/clients/<backend>/X.py
    2. update that backend's imports to point at the local file
    3. leave the other three backends pointing at shared/
    4. re-add X.py to the parity matrix for the diverging backends
    5. update docs/architecture/shared-vs-cited.md and CLAUDE.md

This test catches the case where someone skips steps 4 and 5 and silently
re-cites a shared module.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_CLIENTS = _REPO_ROOT / "culture" / "clients"

BACKENDS = ["claude", "codex", "copilot", "acp"]
SHARED_MODULES = {
    "attention.py",
    "message_buffer.py",
    "ipc.py",
    "telemetry.py",
    "irc_transport.py",
    "socket_server.py",
    "webhook.py",
}


def test_no_per_backend_copy_of_shared_modules():
    leaked: dict[str, set[str]] = {}
    for backend in BACKENDS:
        backend_dir = _CLIENTS / backend
        local_files = {p.name for p in backend_dir.iterdir() if p.is_file()}
        intersection = SHARED_MODULES & local_files
        if intersection:
            leaked[backend] = intersection

    assert not leaked, (
        f"Shared modules leaked back into per-backend directories: {leaked}. "
        f"If a backend genuinely needs to diverge, follow the fork-back "
        f"procedure in docs/architecture/shared-vs-cited.md."
    )
