"""CultureError + exit-code policy for the agent-first CLI surface.

Ported from agtag (``agtag/cli/_errors.py``). Every CLI failure that
reaches the user (in particular, anything emitted under ``--json``) is
raised as :class:`CultureError`; :func:`culture_core.cli._output.emit_error`
serialises it. Guarantees:

* no Python traceback leaks to stderr in JSON mode;
* every JSON-mode error has shape ``{code, message, remediation}``;
* the exit-code policy is centralised here.
"""

from __future__ import annotations

from dataclasses import dataclass

EXIT_SUCCESS = 0
EXIT_USER_ERROR = 1
EXIT_ENV_ERROR = 2

# Daemon-child exit contract (#15). Service managers restart transient
# crashes (systemd's ``Restart=on-failure``) but must park permanent ones
# instead of restart-looping every ``RestartSec`` forever — generated units
# carry ``RestartPreventExitStatus=EXIT_DAEMON_PERMANENT`` and the Windows
# .bat retry loop stops on the same code.
EXIT_DAEMON_TRANSIENT = 1
EXIT_DAEMON_PERMANENT = 78  # sysexits.h EX_CONFIG

# Exception types that mark a daemon crash as permanent (not restartable):
# malformed config, bad credentials, and missing files surface as these
# during config construction. PermissionError and FileNotFoundError are
# OSError subclasses, so they must be matched here BEFORE the "plain
# OSError is transient" default below ever sees them.
_PERMANENT_DAEMON_EXC = (
    PermissionError,
    FileNotFoundError,
    ValueError,
    KeyError,
    TypeError,
)


def classify_daemon_exit(exc: BaseException) -> int:
    """Map a daemon-child crash to its contract exit code.

    Permanent config/user errors exit :data:`EXIT_DAEMON_PERMANENT` so the
    service manager parks the unit in a clear failed state. Plain OSError
    (port briefly taken, peer down) and anything unknown stay
    :data:`EXIT_DAEMON_TRANSIENT` — it is safer to keep restarting crashes
    we can't positively classify.
    """
    if isinstance(exc, _PERMANENT_DAEMON_EXC):
        return EXIT_DAEMON_PERMANENT
    return EXIT_DAEMON_TRANSIENT


@dataclass
class CultureError(Exception):
    """Structured error with a remediation hint for agents."""

    code: int
    message: str
    remediation: str = ""

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }
