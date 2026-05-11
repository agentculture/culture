"""Verify the vendored `communicate` skill signs as `- culture (Claude)`.

In culture 11.1.0 the skill was rebased on `agtag` (steward 0.11.0): the
GitHub verbs (`post-issue.sh`, `post-comment.sh`, `fetch-issues.sh`) are
thin wrappers around `agtag issue post|reply|fetch`. agtag resolves the
signing nick from the local `culture.yaml`'s first agent `suffix`
(falling back to the repo basename), so there is no hard-coded signature
literal in the scripts anymore.

What this test pins:

1. The GitHub verb scripts invoke `agtag issue …` (the contract that
   keeps signature resolution + JSON output + exit-code semantics
   consistent).
2. No script hard-codes `- steward (Claude)` (would silently misattribute
   posts to the upstream).
3. No script hard-codes `- culture (Claude)` either — this was true in
   culture 10.x's pre-agtag wrapper but the agtag-backed rewrite
   deliberately removes the literal so vendors can override via
   `culture.yaml` or `--as NICK` without forking scripts.
4. The repo-root `culture.yaml` still names `suffix: culture` so agtag
   resolves the runtime signature to `- culture (Claude)` for posts
   coming from this repo.
5. SKILL.md documents the `- culture (Claude)` outcome (resolution path,
   not literal injection).
6. `mesh-message.sh` stays unsigned — IRC nick is the speaker.

See [agentculture/culture#379](https://github.com/agentculture/culture/issues/379)
for the resync that motivated this rewrite.
"""

from __future__ import annotations

from pathlib import Path

import culture

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = Path(culture.__file__).parent / "skills" / "communicate"


def test_post_issue_wraps_agtag() -> None:
    script = (SKILL_DIR / "scripts" / "post-issue.sh").read_text(encoding="utf-8")
    assert "agtag issue post" in script, "post-issue.sh must wrap `agtag issue post`"


def test_post_comment_wraps_agtag() -> None:
    script = (SKILL_DIR / "scripts" / "post-comment.sh").read_text(encoding="utf-8")
    assert "agtag issue reply" in script, "post-comment.sh must wrap `agtag issue reply`"


def test_fetch_issues_wraps_agtag() -> None:
    script = (SKILL_DIR / "scripts" / "fetch-issues.sh").read_text(encoding="utf-8")
    assert "agtag issue fetch" in script, "fetch-issues.sh must wrap `agtag issue fetch`"


def test_no_scripts_hardcode_steward_signature() -> None:
    """A leftover steward literal would silently misattribute issues."""
    for script_name in ("post-issue.sh", "post-comment.sh", "fetch-issues.sh", "mesh-message.sh"):
        script = (SKILL_DIR / "scripts" / script_name).read_text(encoding="utf-8")
        assert "- steward (Claude)" not in script, f"{script_name} has stale steward signature"


def test_no_scripts_hardcode_culture_signature_literal() -> None:
    """The agtag-backed wrappers must let `culture.yaml` drive the nick.

    The pre-11.1.0 wrapper hard-coded `printf '\\n\\n- culture (Claude)\\n'`;
    that literal is intentionally gone now. agtag reads
    `culture.yaml:suffix` (or repo basename) and appends the signature
    itself. Vendors who need a different nick set `--as NICK` at the call
    site instead of editing the script.
    """
    for script_name in ("post-issue.sh", "post-comment.sh", "fetch-issues.sh", "mesh-message.sh"):
        script = (SKILL_DIR / "scripts" / script_name).read_text(encoding="utf-8")
        assert "- culture (Claude)" not in script, (
            f"{script_name} re-introduced a hard-coded culture signature literal — "
            "agtag should be resolving the nick from culture.yaml instead"
        )


def test_repo_culture_yaml_resolves_to_culture_nick() -> None:
    """agtag resolves `<suffix>` from `<repo-root>/culture.yaml`.

    With `suffix: culture` here, agtag will sign posts as
    `- culture (Claude)` at runtime. If the suffix changes, this test
    fails — that catches accidental rename of the culture-side nick.
    """
    yaml_text = (REPO_ROOT / "culture.yaml").read_text(encoding="utf-8")
    assert "suffix: culture" in yaml_text, (
        "repo-root culture.yaml must declare `suffix: culture` so agtag "
        "signs GitHub posts as `- culture (Claude)` at runtime"
    )


def test_skill_md_documents_culture_signature() -> None:
    skill_md = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "- culture (Claude)" in skill_md, "SKILL.md must name the resolved culture signature"
    text_lower = skill_md.lower()
    assert "agtag" in text_lower, "SKILL.md must mention agtag as the signature resolver"


def test_mesh_message_appends_no_signature() -> None:
    """`mesh-message.sh` must stay unsigned — IRC nick is the speaker."""
    script = (SKILL_DIR / "scripts" / "mesh-message.sh").read_text(encoding="utf-8")
    assert "- culture (Claude)" not in script
    assert "- steward (Claude)" not in script
