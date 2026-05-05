"""Verify the vendored `communicate` skill signs as `- culture (Claude)`.

The steward source signs as `- steward (Claude)`. When vendored into
culture, the signature literal MUST be rewritten to `- culture (Claude)`
per agentculture/culture#324 §2 — otherwise cross-repo issues opened
from a culture-owned harness would be misattributed to steward.
"""

from __future__ import annotations

from pathlib import Path

import culture

SKILL_DIR = Path(culture.__file__).parent / "skills" / "communicate"


def test_post_issue_signs_as_culture() -> None:
    script = (SKILL_DIR / "scripts" / "post-issue.sh").read_text(encoding="utf-8")
    # Exact literal — `printf '\n\n- culture (Claude)\n'`.
    assert "- culture (Claude)" in script, "post-issue.sh missing culture signature"


def test_post_issue_does_not_sign_as_steward() -> None:
    """A leftover steward literal would silently misattribute issues."""
    script = (SKILL_DIR / "scripts" / "post-issue.sh").read_text(encoding="utf-8")
    assert "- steward (Claude)" not in script, "post-issue.sh still has steward signature"


def test_skill_md_documents_culture_signature() -> None:
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "- culture (Claude)" in skill_md
    # The per-channel rules table mentions auto-append for github issues.
    assert "auto-append" in skill_md.lower() or "auto-signs" in skill_md.lower()


def test_mesh_message_appends_no_signature() -> None:
    """`mesh-message.sh` must stay unsigned — IRC nick is the speaker."""
    script = (SKILL_DIR / "scripts" / "mesh-message.sh").read_text(encoding="utf-8")
    assert "- culture (Claude)" not in script
    assert "- steward (Claude)" not in script
