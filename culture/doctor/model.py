"""Data model for culture doctor diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    """A single drift finding from the doctor scan."""

    drift_class: int  # 1, 2, or 3
    severity: str  # 'error' or 'warning'
    subject: str  # agent nick or suffix, or repo dir name
    path: str
    message: str
    fix_hint: str  # suggested shell command or ''


@dataclass
class DoctorReport:
    """Aggregated doctor report grouped by drift class."""

    class1: list[Finding] = field(default_factory=list)
    class2: list[Finding] = field(default_factory=list)
    class3: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.class1 or self.class2 or self.class3)

    @property
    def exit_code(self) -> int:
        return 1 if (self.class1 or self.class3) else 0
