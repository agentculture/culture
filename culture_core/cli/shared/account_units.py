"""systemd hooks for ordered account activation (culture#506, d7).

:class:`SystemdUnitOps` implements :class:`culture_core.accounts.UnitOps`
for culture-owned agents: it wraps/unwraps the agent's
``culture-agent-<nick>`` user unit with the grant inject, restarts it, reads
the cultureagent version the unit's interpreter would import, and verifies
the agent reconnected (the unit stays ``active`` and the server's WHOIS sees
the nick).
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

from culture_core import persistence

_VERSION_PROBE = "import importlib.metadata as m; print(m.version('cultureagent'))"
_WILDCARD_HOSTS = ("", "0.0.0.0", "::")  # nosec B104 - compared, never bound


def _interpreter_of(program: str) -> str | None:
    """The Python a unit runs: *program* itself, or a console script's shebang
    interpreter (legacy units bake ``/path/to/culture`` instead of python)."""
    if Path(program).name.startswith("python"):
        return program
    try:
        with open(program, "rb") as fh:
            first = fh.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].decode("utf-8", "replace").split()
    if not parts:
        return None
    if Path(parts[0]).name == "env" and len(parts) > 1:
        return parts[1]
    return parts[0]


def unit_name(nick: str) -> str:
    return f"culture-agent-{nick}"


class SystemdUnitOps:
    """:class:`~culture_core.accounts.UnitOps` over ``systemctl --user`` units."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        server_name: str,
        verify_timeout: float = 30.0,
        grant_bin: str | None = None,
        allow_dev_interpreter: bool = False,
        poll_interval: float = 1.0,
    ):
        self.host = "127.0.0.1" if (host or "").strip() in _WILDCARD_HOSTS else host
        self.port = port
        self.server_name = server_name
        self.verify_timeout = verify_timeout
        self.grant_bin = grant_bin
        self.allow_dev_interpreter = allow_dev_interpreter
        self.poll_interval = poll_interval

    # -- UnitOps --------------------------------------------------------------

    def exists(self, nick: str) -> bool:
        return persistence.service_installed(unit_name(nick))

    def client_version(self, nick: str) -> str | None:
        argv = persistence.unit_exec_start(unit_name(nick))
        if not argv:
            return None
        if persistence.is_grant_wrapped(argv):
            argv = persistence.grant_unwrap(argv)
        interpreter = _interpreter_of(argv[0])
        if interpreter is None:
            return None
        try:
            out = subprocess.run(  # nosec B603 - the unit's own interpreter, no shell
                [interpreter, "-c", _VERSION_PROBE],
                capture_output=True,
                text=True,
                timeout=persistence.DEFAULT_CMD_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None

    def wrap(self, nick: str, name: str) -> bool | None:
        return persistence.wrap_installed_unit(
            unit_name(nick),
            name,
            grant_bin=self.grant_bin,
            allow_dev_interpreter=self.allow_dev_interpreter,
        )

    def unwrap(self, nick: str, name: str) -> bool | None:
        return persistence.unwrap_installed_unit(unit_name(nick), name)

    def restart(self, nick: str) -> bool:
        return persistence.restart_service(unit_name(nick))

    def verify(self, nick: str) -> str | None:
        """None once the unit is active and WHOIS sees *nick* twice in a row."""
        svc = unit_name(nick)
        deadline = time.monotonic() + self.verify_timeout
        streak = 0
        while True:
            state = persistence.service_active_state(svc)
            if state == "failed":
                return (
                    f"{svc} failed after the restart (a refused SASL login exits 78); "
                    f"see 'journalctl --user -u {svc}'"
                )
            if state == "active" and self._present(nick):
                streak += 1
                if streak >= 2:
                    return None
            else:
                streak = 0
            if time.monotonic() >= deadline:
                return (
                    f"{nick} did not reconnect to {self.host}:{self.port} within "
                    f"{self.verify_timeout:g}s (unit state: {state or 'unknown'})"
                )
            time.sleep(self.poll_interval)

    # -- helpers ----------------------------------------------------------------

    def _present(self, nick: str) -> bool:
        from culture_core.observer import IRCObserver

        observer = IRCObserver(self.host, self.port, self.server_name)
        try:
            return asyncio.run(observer.whois(nick))
        except OSError:  # covers ConnectionError and asyncio.TimeoutError (3.11+)
            return False
