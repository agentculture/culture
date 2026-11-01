"""Data model for culture doctor diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Finding:
    """A single drift finding from the doctor scan."""

    drift_class: int  # 1, 2, 3, or 4
    severity: str  # 'error', 'warning' or 'info' (class 5: never fails the report)
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
    class5: list[Finding] = field(default_factory=list)  # agent accounts (#506)

    @property
    def ok(self) -> bool:
        # Class-5 info/warnings (e.g. "registered agent has no account":
        # accounts are opt-in) never make the report not-ok (culture#506).
        class5 = [f for f in self.class5 if f.severity == "error"]
        return not (self.class1 or self.class2 or self.class3 or self.class4 or class5)

    @property
    def exit_code(self) -> int:
        # Class-4 warnings (restart-looping) don't fail the run; parked
        # (failed) units are errors and do — same policy as class 2 vs 1/3.
        class4_errors = any(f.severity == "error" for f in self.class4)
        class5_errors = any(f.severity == "error" for f in self.class5)
        return 1 if (self.class1 or self.class3 or class4_errors or class5_errors) else 0
