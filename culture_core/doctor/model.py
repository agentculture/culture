"""Data model for culture doctor diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    """A single drift finding from the doctor scan."""

    drift_class: int  # 1, 2, 3, or 4
    severity: str  # 'error' or 'warning'
    subject: str  # agent nick or suffix, repo dir name, or service name
    path: str
    message: str
    fix_hint: str  # suggested shell command or ''


@dataclass
class DoctorReport:
    """Aggregated doctor report grouped by drift class."""

    class1: list[Finding] = field(default_factory=list)
    class2: list[Finding] = field(default_factory=list)
    class3: list[Finding] = field(default_factory=list)
    class4: list[Finding] = field(default_factory=list)  # service health (#15)

    @property
    def ok(self) -> bool:
        return not (self.class1 or self.class2 or self.class3 or self.class4)

    @property
    def exit_code(self) -> int:
        # Class-4 warnings (restart-looping) don't fail the run; parked
        # (failed) units are errors and do — same policy as class 2 vs 1/3.
        class4_errors = any(f.severity == "error" for f in self.class4)
        return 1 if (self.class1 or self.class3 or class4_errors) else 0
