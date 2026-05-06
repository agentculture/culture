"""Regression tests for issue #260: `fires_event` at the top level of bot.yaml.

The canonical location for the block is under `output:`, but prior to the
fix a top-level `fires_event:` block was silently ignored, producing a bot
whose `fires_event` attribute was ``None`` — so chained events never fired.
"""

from __future__ import annotations

import yaml

from culture.bots.config import load_bot_config, save_bot_config


def _write_yaml(tmp_path, data: dict):
    path = tmp_path / "bot.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_load_top_level_fires_event(tmp_path):
    path = _write_yaml(
        tmp_path,
        {
            "bot": {"name": "spark-greeter", "owner": "spark"},
            "trigger": {"type": "event", "filter": "type == 'user.join'"},
            "output": {"channels": ["#general"], "template": "hi {event.nick}"},
            "fires_event": {
                "type": "custom.greeted",
                "data": {"nick": "{{ event.nick }}"},
            },
        },
    )

    cfg = load_bot_config(path)
    assert cfg.fires_event is not None
    assert cfg.fires_event.type == "custom.greeted"
    assert cfg.fires_event.data == {"nick": "{{ event.nick }}"}


def test_load_canonical_output_fires_event_still_works(tmp_path):
    path = _write_yaml(
        tmp_path,
        {
            "bot": {"name": "spark-greeter", "owner": "spark"},
            "trigger": {"type": "event", "filter": "type == 'user.join'"},
            "output": {
                "channels": ["#general"],
                "template": "hi {event.nick}",
                "fires_event": {
                    "type": "custom.greeted",
                    "data": {"nick": "{{ event.nick }}"},
                },
            },
        },
    )

    cfg = load_bot_config(path)
    assert cfg.fires_event is not None
    assert cfg.fires_event.type == "custom.greeted"


def test_output_location_wins_over_top_level(tmp_path):
    """If both are set, `output.fires_event` is canonical and wins."""
    path = _write_yaml(
        tmp_path,
        {
            "bot": {"name": "spark-greeter", "owner": "spark"},
            "trigger": {"type": "event", "filter": "type == 'user.join'"},
            "output": {
                "channels": ["#general"],
                "fires_event": {"type": "custom.canonical", "data": {}},
            },
            "fires_event": {"type": "custom.toplevel", "data": {}},
        },
    )

    cfg = load_bot_config(path)
    assert cfg.fires_event is not None
    assert cfg.fires_event.type == "custom.canonical"


def test_save_round_trip_writes_canonical_location(tmp_path):
    """save_bot_config always emits the canonical output.fires_event form."""
    src = _write_yaml(
        tmp_path,
        {
            "bot": {"name": "spark-greeter", "owner": "spark"},
            "trigger": {"type": "event", "filter": "type == 'user.join'"},
            "output": {"channels": ["#general"], "template": "hi"},
            "fires_event": {"type": "custom.greeted", "data": {"n": "x"}},
        },
    )

    cfg = load_bot_config(src)
    out_path = tmp_path / "saved.yaml"
    save_bot_config(out_path, cfg)
    data = yaml.safe_load(out_path.read_text())
    assert "fires_event" not in data, "top-level fires_event must not be re-emitted"
    assert data["output"]["fires_event"]["type"] == "custom.greeted"


def test_missing_fires_event_still_loads(tmp_path):
    path = _write_yaml(
        tmp_path,
        {
            "bot": {"name": "spark-plain", "owner": "spark"},
            "trigger": {"type": "webhook"},
            "output": {"channels": ["#general"], "template": "hi"},
        },
    )

    cfg = load_bot_config(path)
    assert cfg.fires_event is None


def test_top_level_deprecation_notice_logged_once_per_process(tmp_path, caplog):
    """Loading the same bot twice should not double-log the deprecation INFO."""
    import logging

    from culture.bots.config import reset_fires_event_warning_state

    path = _write_yaml(
        tmp_path,
        {
            "bot": {"name": "spark-greeter", "owner": "spark"},
            "trigger": {"type": "event", "filter": "type == 'user.join'"},
            "output": {"channels": ["#general"], "template": "hi"},
            "fires_event": {"type": "custom.greeted", "data": {}},
        },
    )

    reset_fires_event_warning_state()
    with caplog.at_level(logging.INFO, logger="culture.bots.config"):
        load_bot_config(path)
        first = len(caplog.records)
        load_bot_config(path)
        second = len(caplog.records)

    assert first == 1
    assert second == 1, "loading the same bot twice should log once"


def test_top_level_deprecation_dedup_keyed_by_path_not_name(tmp_path, caplog):
    """Two configs sharing `bot.name` each get their own deprecation notice."""
    import logging

    from culture.bots.config import reset_fires_event_warning_state

    a_dir = tmp_path / "a"
    a_dir.mkdir()
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    path_a = _write_yaml(
        a_dir,
        {
            "bot": {"name": "spark-greeter", "owner": "spark"},
            "trigger": {"type": "event", "filter": "type == 'user.join'"},
            "output": {"channels": ["#general"], "template": "hi"},
            "fires_event": {"type": "custom.greeted", "data": {}},
        },
    )
    path_b = _write_yaml(
        b_dir,
        {
            "bot": {"name": "spark-greeter", "owner": "spark"},
            "trigger": {"type": "event", "filter": "type == 'user.join'"},
            "output": {"channels": ["#general"], "template": "hi"},
            "fires_event": {"type": "custom.greeted", "data": {}},
        },
    )

    reset_fires_event_warning_state()
    with caplog.at_level(logging.INFO, logger="culture.bots.config"):
        load_bot_config(path_a)
        load_bot_config(path_b)

    assert len(caplog.records) == 2, "distinct config paths should each warn once"


def test_top_level_deprecation_handles_unhashable_name(tmp_path, caplog):
    """A malformed (non-string) `bot.name` must not crash the dedup path."""
    import logging

    from culture.bots.config import reset_fires_event_warning_state

    path = tmp_path / "bot.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "bot": {"name": ["not", "a", "string"], "owner": "spark"},
                "trigger": {"type": "event", "filter": "type == 'user.join'"},
                "output": {"channels": ["#general"], "template": "hi"},
                "fires_event": {"type": "custom.greeted", "data": {}},
            }
        )
    )

    reset_fires_event_warning_state()
    with caplog.at_level(logging.INFO, logger="culture.bots.config"):
        cfg = load_bot_config(path)

    assert cfg.fires_event is not None
    assert any("top-level 'fires_event'" in r.getMessage() for r in caplog.records)
