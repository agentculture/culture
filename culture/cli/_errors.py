"""CultureError + exit-code policy for the agent-first CLI surface.

Ported from agtag (``agtag/cli/_errors.py``). Every CLI failure that
reaches the user (in particular, anything emitted under ``--json``) is
raised as :class:`CultureError`; :func:`culture.cli._output.emit_error`
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
