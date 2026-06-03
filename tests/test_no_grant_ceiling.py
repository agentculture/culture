"""NT-13 — assert the legacy boss grant-ceiling stack is fully gone.

The 2026-06-03 mesh re-architecture replaced the boss-policy "ceiling"
(``DEFAULT_BOSS_CEILING`` / ``load_boss_ceiling`` / ``is_above_ceiling``
/ ``write_default_boss_ceiling`` / ``boss_policy_path_for`` /
``_boss_policy_dir``) with the per-session ``CLAUDE_PERMITTED_TOOLS``
heuristic enforced by ``cc_plugin.tools.mesh_grant``. None of the old
symbols should still exist as attributes on ``_perm_broker``, no code
in ``culture/`` or ``tests/`` should reference them, and the old
``tests/test_boss_grant_ceiling.py`` should be deleted.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

from culture.clients import _perm_broker as broker  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestNoCeilingAttributes:
    """The broker module must not expose any of the old ceiling helpers."""

    def test_no_default_boss_ceiling(self) -> None:
        assert not hasattr(broker, "DEFAULT_BOSS_CEILING")

    def test_no_is_above_ceiling(self) -> None:
        assert not hasattr(broker, "is_above_ceiling")

    def test_no_load_boss_ceiling(self) -> None:
        assert not hasattr(broker, "load_boss_ceiling")

    def test_no_write_default_boss_ceiling(self) -> None:
        assert not hasattr(broker, "write_default_boss_ceiling")

    def test_no_boss_policy_path_for(self) -> None:
        assert not hasattr(broker, "boss_policy_path_for")

    def test_no_boss_policy_dir(self) -> None:
        assert not hasattr(broker, "_boss_policy_dir")


class TestNoCeilingReferencesInTree:
    """A repository-wide grep must return zero hits for the ceiling stack."""

    def test_no_ceiling_references_in_culture_or_tests(self) -> None:
        result = subprocess.run(
            [
                "git",
                "grep",
                "-E",
                "BOSS_CEILING|boss_policy|is_above_ceiling|"
                "write_default_boss_ceiling|load_boss_ceiling",
                "culture/",
                "tests/",
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        # ``git grep`` exits 1 when there are no matches — that's success.
        hits = [line for line in result.stdout.splitlines() if line.strip()]
        assert hits == [], "found ceiling-stack references that should be gone: " + "\n".join(hits)


class TestLegacyTestFileRemoved:
    """The old ceiling-test file must not exist."""

    def test_test_boss_grant_ceiling_is_gone(self) -> None:
        legacy = _REPO_ROOT / "tests" / "test_boss_grant_ceiling.py"
        assert not os.path.exists(legacy), (
            f"{legacy} should have been deleted as part of the ceiling-stack removal"
        )
