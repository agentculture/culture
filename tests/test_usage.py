"""Tests for ``culture.clients._usage`` (v8.19.21).

The sidecar usage log behind the dashboard's per-agent + per-task
token badges. Failures here are silent on the runner side (advisory,
must never block the agent loop) but verifiable here as ground truth.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from culture.clients import _usage


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # `from X import Y` binds Y at module load → must patch the symbol in
    # EVERY module that consumed it. _usage imported culture_home directly.
    with (
        patch("culture.clients._perm_broker.culture_home", return_value=str(home)),
        patch("culture.clients._usage.culture_home", return_value=str(home)),
    ):
        yield home


# --- record + sum roundtrip ------------------------------------------------


def test_record_and_sum_single_turn(isolated_home):
    _usage.record_turn_usage_sync(
        "local-w", tokens_input=1000, tokens_output=200, model="claude-opus-4-7"
    )
    totals = _usage.sum_tokens("local-w")
    assert totals == {"in": 1000, "out": 200, "total": 1200, "turns": 1}


def test_record_appends_across_calls(isolated_home):
    _usage.record_turn_usage_sync("w", tokens_input=10, tokens_output=2)
    _usage.record_turn_usage_sync("w", tokens_input=30, tokens_output=4)
    _usage.record_turn_usage_sync("w", tokens_input=15, tokens_output=3)
    totals = _usage.sum_tokens("w")
    assert totals["in"] == 55
    assert totals["out"] == 9
    assert totals["total"] == 64
    assert totals["turns"] == 3


def test_record_with_both_none_is_noop(isolated_home):
    """A backend that doesn't expose tokens calls with None/None → no write."""
    _usage.record_turn_usage_sync("w", tokens_input=None, tokens_output=None)
    assert _usage.sum_tokens("w") == {"in": 0, "out": 0, "total": 0, "turns": 0}


def test_record_with_only_input(isolated_home):
    """Input-only is valid (output may be missing in some SDKs)."""
    _usage.record_turn_usage_sync("w", tokens_input=500)
    totals = _usage.sum_tokens("w")
    assert totals["in"] == 500
    assert totals["out"] == 0
    assert totals["turns"] == 1


def test_record_with_only_output(isolated_home):
    _usage.record_turn_usage_sync("w", tokens_input=None, tokens_output=42)
    totals = _usage.sum_tokens("w")
    assert totals == {"in": 0, "out": 42, "total": 42, "turns": 1}


def test_record_negative_value_skipped(isolated_home):
    """Negative tokens are nonsensical — skip the value, no write."""
    _usage.record_turn_usage_sync("w", tokens_input=-5, tokens_output=None)
    totals = _usage.sum_tokens("w")
    # tokens_input=-5 rejected by the >=0 guard → both None → no write.
    assert totals == {"in": 0, "out": 0, "total": 0, "turns": 0}


def test_sum_missing_file_returns_zeros(isolated_home):
    assert _usage.sum_tokens("never-existed") == {
        "in": 0,
        "out": 0,
        "total": 0,
        "turns": 0,
    }


def test_sum_skips_malformed_lines(isolated_home):
    """Half a record + a valid record → only the valid one counts."""
    _usage.record_turn_usage_sync("w", tokens_input=100, tokens_output=20)
    path = _usage.usage_path_for("w")
    with open(path, "a") as fh:
        fh.write("garbage not json\n")
        fh.write('{"in": "not a number"}\n')
        fh.write(json.dumps({"in": 50, "out": 10}) + "\n")
    totals = _usage.sum_tokens("w")
    # The "not a number" string-typed in is rejected; turns counts records
    # where AT LEAST ONE numeric value was found.
    assert totals["in"] == 150  # 100 + 50
    assert totals["out"] == 30  # 20 + 10
    assert totals["turns"] == 2


def test_clear_removes_file(isolated_home):
    _usage.record_turn_usage_sync("w", tokens_input=1)
    assert _usage.clear_usage("w") is True
    assert _usage.sum_tokens("w") == {"in": 0, "out": 0, "total": 0, "turns": 0}
    # Idempotent — second call returns False.
    assert _usage.clear_usage("w") is False


def test_file_lives_under_culture_home(isolated_home):
    _usage.record_turn_usage_sync("local-w", tokens_input=1)
    path = isolated_home / "usage" / "local-w.jsonl"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    rec = json.loads(content.strip())
    assert rec["in"] == 1


# --- async wrapper ---------------------------------------------------------


@pytest.mark.asyncio
async def test_async_record_turn_usage_works(isolated_home):
    """The async wrapper just hands off to sync via to_thread; verify it commits."""
    await _usage.record_turn_usage("w", tokens_input=2000, tokens_output=400)
    totals = _usage.sum_tokens("w")
    assert totals["total"] == 2400
    assert totals["turns"] == 1


# --- write error tolerance -------------------------------------------------


def test_record_swallows_oserror_silently(isolated_home, monkeypatch):
    """If disk write fails, the agent loop must NOT see the exception."""
    import culture.clients._usage as usage_mod

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(usage_mod.os, "open", boom)
    # Must not raise.
    _usage.record_turn_usage_sync("w", tokens_input=1)
