"""Tests for boss mission persistence (culture/clients/_mission.py).

Single source of truth for the file persistence behavior — used by
every backend's daemon via cite-don't-import + the all-backends rule.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from culture.clients import _mission
from culture.clients._perm_broker import mission_path_for


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


def _fake_agent(tags=()):
    return SimpleNamespace(nick="local-boss", tags=list(tags))


class TestIsBossAgent:
    def test_boss_tag_returns_true(self):
        assert _mission.is_boss_agent(_fake_agent(tags=["boss"])) is True

    def test_no_tag_returns_false(self):
        assert _mission.is_boss_agent(_fake_agent(tags=[])) is False

    def test_other_tag_returns_false(self):
        assert _mission.is_boss_agent(_fake_agent(tags=["worker", "qa"])) is False

    def test_missing_tags_attr_returns_false(self):
        assert _mission.is_boss_agent(SimpleNamespace()) is False

    def test_non_list_tags_returns_false(self):
        assert _mission.is_boss_agent(SimpleNamespace(tags="boss")) is False


class TestPersistAndLoad:
    def test_persist_writes_file(self, home):
        _mission.persist_mention("local-boss", "ori", "fix the auth bug")
        loaded = _mission.load_context("local-boss")
        assert "ori" in loaded
        assert "fix the auth bug" in loaded

    def test_persist_appends_multiple_mentions(self, home):
        _mission.persist_mention("local-boss", "ori", "first task")
        _mission.persist_mention("local-boss", "ori", "second task")
        loaded = _mission.load_context("local-boss")
        assert "first task" in loaded
        assert "second task" in loaded

    def test_load_missing_returns_empty(self, home):
        assert _mission.load_context("never-spawned") == ""

    def test_persist_creates_parent_dir(self, home):
        path = mission_path_for("local-boss")
        assert not os.path.exists(os.path.dirname(path))
        _mission.persist_mention("local-boss", "ori", "x")
        assert os.path.exists(os.path.dirname(path))

    def test_clear_removes_file(self, home):
        _mission.persist_mention("local-boss", "ori", "task")
        assert _mission.load_context("local-boss") != ""
        _mission.clear("local-boss")
        assert _mission.load_context("local-boss") == ""

    def test_clear_missing_is_idempotent(self, home):
        # Should not raise.
        _mission.clear("never-existed")


class TestRotation:
    def test_rotates_at_cap(self, home):
        # Write enough small entries to exceed the cap.
        big_text = "x" * 2048  # 2 KiB per mention
        for i in range(25):  # 25 * 2 KiB = 50 KiB > 32 KiB cap
            _mission.persist_mention("local-boss", "ori", big_text)
        loaded = _mission.load_context("local-boss")
        # After rotation, the file is bounded by the rotate floor.
        assert len(loaded.encode("utf-8")) <= _mission.MISSION_MAX_BYTES
        # Most recent entry survives — that's the rotation contract.
        # (The last persist had the big_text; oldest entries dropped.)
        assert big_text in loaded

    def test_rotation_marker_inserted(self, home):
        for i in range(25):
            _mission.persist_mention("local-boss", "ori", "x" * 2048)
        loaded = _mission.load_context("local-boss")
        assert "<!-- mission rotated:" in loaded


class TestBuildSystemPromptExtension:
    def test_empty_when_no_mission(self, home):
        assert _mission.build_system_prompt_extension("local-boss") == ""

    def test_includes_mission_when_present(self, home):
        _mission.persist_mention("local-boss", "ori", "drive the QA worker")
        ext = _mission.build_system_prompt_extension("local-boss")
        assert "Your current mission" in ext
        assert "drive the QA worker" in ext

    def test_stable_format(self, home):
        """The wrapper text is stable so the prompt cache hits across
        unchanged-mission turns."""
        _mission.persist_mention("local-boss", "ori", "task X")
        ext1 = _mission.build_system_prompt_extension("local-boss")
        ext2 = _mission.build_system_prompt_extension("local-boss")
        assert ext1 == ext2
