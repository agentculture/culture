"""End-to-end seed integration in the dashboard (v8.19.20).

Closes a gap from v8.19.18: the per-channel ``seed_preview`` and the
task-group title fallback through TOPIC/seed were never tested against
the real ``list_tasks`` output. These tests assert the full flow:
persist a seed → call list_tasks → channel dict carries seed_preview →
task title uses the seed first line.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with (
        patch("culture.clients._perm_broker.culture_home", return_value=str(home)),
        patch("culture.clients._daemon_log.culture_home", return_value=str(home)),
        patch("culture.clients._audit.culture_home", return_value=str(home)),
        patch("culture.clients._usage.culture_home", return_value=str(home)),
    ):
        yield home


def _build_manifest(home, boss_suffix: str, worker_suffixes: list[str], server_name: str = "local"):
    """Build the per-directory culture.yaml layout the manifest loader expects.

    Each agent lives in its own directory with a ``culture.yaml``. The
    server.yaml maps ``agents: {suffix: dir}``. The boss-worker link is
    declared by writing ``boss: <boss-nick>`` into each worker's
    culture.yaml (the ``boss`` property reads ``extras["boss"]``).
    Returns the server.yaml path.
    """
    import yaml

    bdir = home / boss_suffix
    bdir.mkdir(parents=True, exist_ok=True)
    with open(bdir / "culture.yaml", "w") as fh:
        yaml.safe_dump(
            {"suffix": boss_suffix, "backend": "claude", "tags": ["boss"], "channels": ["#boss"]},
            fh,
        )

    agents_manifest = {boss_suffix: str(bdir)}
    boss_nick = f"{server_name}-{boss_suffix}"
    for wsuf in worker_suffixes:
        wdir = home / wsuf
        wdir.mkdir(parents=True, exist_ok=True)
        with open(wdir / "culture.yaml", "w") as fh:
            yaml.safe_dump(
                {"suffix": wsuf, "backend": "claude", "boss": boss_nick},
                fh,
            )
        agents_manifest[wsuf] = str(wdir)

    config_path = str(home / "server.yaml")
    with open(config_path, "w") as fh:
        yaml.safe_dump(
            {
                "server": {"name": server_name, "host": "127.0.0.1", "port": 6667},
                "agents": agents_manifest,
            },
            fh,
        )
    return config_path


def test_list_tasks_stamps_seed_preview_on_channel(isolated_home):
    from culture.clients._seed import persist_seed
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "boss", ["w1"])  # nicks: local-boss, local-w1
    persist_seed("#task-w1", "First headline of the seed brief.\nDetail body here.")
    # nick is `<server>-<suffix>` so the worker channel name follows the
    # same convention as the production builder: #task-w1.

    tasks = list_tasks(config_path)
    assert len(tasks) == 1
    task = tasks[0]
    # Find the #task-w1 channel in the task's channel list.
    task_chan = next(c for c in task["channels"] if c["channel"] == "#task-w1")
    assert task_chan["seed_preview"] == "First headline of the seed brief.", task_chan


def test_list_tasks_title_falls_back_to_seed_preview(isolated_home):
    """When a worker channel has a seed and no mission.md exists, the task
    TITLE is the seed's first line (not the default `<nick>'s work`)."""
    from culture.clients._seed import persist_seed
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "boss", ["w1"])  # nicks: local-boss, local-w1
    persist_seed("#task-w1", "Build the v2 dashboard.")
    tasks = list_tasks(config_path)
    assert tasks[0]["title"] == "Build the v2 dashboard."


def test_list_tasks_title_falls_back_to_nick_when_no_seed(isolated_home):
    """Without a seed AND without mission.md, title is `<nick>'s work`."""
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "lonelyboss", ["w1"])
    tasks = list_tasks(config_path)
    assert tasks[0]["title"] == "local-lonelyboss's work"


def test_list_tasks_seed_preview_truncates_at_80_chars(isolated_home):
    """A first line longer than the 80-char limit must be truncated with an ellipsis."""
    from culture.clients._seed import persist_seed
    from culture.dashboard.server import list_tasks

    long_line = "A" * 120
    config_path = _build_manifest(isolated_home, "boss", ["w1"])  # nicks: local-boss, local-w1
    persist_seed("#task-w1", long_line)
    tasks = list_tasks(config_path)
    task_chan = next(c for c in tasks[0]["channels"] if c["channel"] == "#task-w1")
    # 79 chars + ellipsis = 80 visible chars total.
    assert task_chan["seed_preview"].endswith("…")
    assert len(task_chan["seed_preview"]) <= 80


def test_list_tasks_seed_preview_handles_leading_blank_lines(isolated_home):
    """First non-empty line wins — leading blank lines are skipped."""
    from culture.clients._seed import persist_seed
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "boss", ["w1"])  # nicks: local-boss, local-w1
    persist_seed("#task-w1", "\n\n   \nActual first line.\nMore body.")
    tasks = list_tasks(config_path)
    task_chan = next(c for c in tasks[0]["channels"] if c["channel"] == "#task-w1")
    assert task_chan["seed_preview"] == "Actual first line."


def test_list_tasks_empty_seed_file_means_empty_preview(isolated_home):
    """A non-existent seed file means seed_preview is "" (frontend skips the panel)."""
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "boss", ["w1"])  # nicks: local-boss, local-w1
    tasks = list_tasks(config_path)
    task_chan = next(c for c in tasks[0]["channels"] if c["channel"] == "#task-w1")
    assert task_chan["seed_preview"] == ""


def test_list_tasks_uses_first_worker_seed_for_title_when_multiple(isolated_home):
    """With multiple workers, the FIRST seeded worker channel's title wins."""
    from culture.clients._seed import persist_seed
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "boss", ["w1", "w2"])
    # Only w2 has a seed — title should still find it.
    persist_seed("#task-w2", "Seeded second worker.")
    tasks = list_tasks(config_path)
    assert tasks[0]["title"] == "Seeded second worker."


# --- v8.19.21 token-counter integration ----------------------------------


def test_list_tasks_stamps_member_tokens_used(isolated_home):
    """Each member dict carries `tokens_used` = sum of its usage records."""
    from culture.clients._usage import record_turn_usage_sync
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "boss", ["w1"])
    record_turn_usage_sync("local-boss", tokens_input=10_000, tokens_output=2_000)
    record_turn_usage_sync("local-w1", tokens_input=30_000, tokens_output=5_000)
    tasks = list_tasks(config_path)
    members = {m["nick"]: m for c in tasks[0]["channels"] for m in c["members"]}
    assert members["local-boss"]["tokens_used"] == 12_000
    assert members["local-w1"]["tokens_used"] == 35_000


def test_list_tasks_channel_tokens_total_sums_members(isolated_home):
    """`tokens_total` on each channel = sum of every member's tokens_used."""
    from culture.clients._usage import record_turn_usage_sync
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "boss", ["w1"])
    record_turn_usage_sync("local-boss", tokens_input=100, tokens_output=10)
    record_turn_usage_sync("local-w1", tokens_input=200, tokens_output=20)
    tasks = list_tasks(config_path)
    task_chan = next(c for c in tasks[0]["channels"] if c["channel"] == "#task-w1")
    # 100 + 10 (boss) + 200 + 20 (worker) = 330
    assert task_chan["tokens_total"] == 330


def test_list_tasks_zero_tokens_when_no_records(isolated_home):
    """Missing usage files → tokens_used = 0, tokens_total = 0."""
    from culture.dashboard.server import list_tasks

    config_path = _build_manifest(isolated_home, "boss", ["w1"])
    tasks = list_tasks(config_path)
    for c in tasks[0]["channels"]:
        assert c["tokens_total"] == 0
        for m in c["members"]:
            assert m["tokens_used"] == 0


def test_list_tasks_per_member_token_cache_within_one_call(isolated_home, monkeypatch):
    """list_tasks memoizes sum_tokens per nick within a single call.

    A boss appears in every channel of its task; without the cache,
    sum_tokens would be called N times for the same file. With the cache,
    once.
    """
    from culture.clients._usage import record_turn_usage_sync
    from culture.dashboard import server as dash_server

    config_path = _build_manifest(isolated_home, "boss", ["w1", "w2"])
    record_turn_usage_sync("local-boss", tokens_input=1)

    real_sum_tokens = dash_server.sum_tokens if hasattr(dash_server, "sum_tokens") else None
    calls: dict[str, int] = {}

    from culture.clients import _usage as usage_mod

    original = usage_mod.sum_tokens

    def counting_sum(nick):
        calls[nick] = calls.get(nick, 0) + 1
        return original(nick)

    monkeypatch.setattr(usage_mod, "sum_tokens", counting_sum)
    dash_server.list_tasks(config_path)
    # local-boss is in every channel of the task (#boss, #task-w1, #task-w2)
    # but should be summed only ONCE because of the per-call cache.
    assert calls.get("local-boss", 0) == 1, calls
