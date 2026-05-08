"""Tests for AttentionConfig parsing, validation, and per-agent merge."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from attention import Band  # type: ignore[import-not-found]
from config import (  # type: ignore[import-not-found]
    AgentConfig,
    DaemonConfig,
    load_config,
    resolve_attention_config,
)


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "server.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_no_attention_block_uses_defaults_with_legacy_poll_interval(tmp_path):
    cfg_path = _write_yaml(tmp_path, {"poll_interval": 90, "agents": []})
    cfg = load_config(cfg_path)
    assert cfg.attention.enabled is True
    # Legacy poll_interval migrates into idle.interval_s
    assert cfg.attention.bands[Band.IDLE].interval_s == 90
    # Other bands keep defaults
    assert cfg.attention.bands[Band.HOT].interval_s == 30


def test_attention_disabled_falls_back_to_legacy_poll_interval(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {"poll_interval": 45, "attention": {"enabled": False}, "agents": []},
    )
    cfg = load_config(cfg_path)
    assert cfg.attention.enabled is False
    assert cfg.poll_interval == 45


def test_explicit_attention_overrides_defaults(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {
                "enabled": True,
                "tick_s": 3,
                "thread_window_s": 600,
                "bands": {
                    "hot": {"interval_s": 15, "hold_s": 60},
                    "warm": {"interval_s": 60, "hold_s": 180},
                    "cool": {"interval_s": 180, "hold_s": 360},
                    "idle": {"interval_s": 900},
                },
            },
            "agents": [],
        },
    )
    cfg = load_config(cfg_path)
    assert cfg.attention.tick_s == 3
    assert cfg.attention.thread_window_s == 600
    assert cfg.attention.bands[Band.HOT].interval_s == 15
    assert cfg.attention.bands[Band.IDLE].hold_s is None


def test_partial_band_override_inherits_defaults(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {"bands": {"hot": {"interval_s": 10, "hold_s": 30}}},
            "agents": [],
        },
    )
    cfg = load_config(cfg_path)
    assert cfg.attention.bands[Band.HOT].interval_s == 10
    # Unspecified bands keep defaults
    assert cfg.attention.bands[Band.WARM].interval_s == 120
    assert cfg.attention.bands[Band.COOL].interval_s == 300


def test_non_monotonic_bands_rejected(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {
                "bands": {
                    "hot": {"interval_s": 100, "hold_s": 60},
                    "warm": {"interval_s": 50, "hold_s": 60},
                }
            },
            "agents": [],
        },
    )
    with pytest.raises(ValueError, match="monotonic"):
        load_config(cfg_path)


def test_zero_interval_rejected(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {"attention": {"bands": {"hot": {"interval_s": 0, "hold_s": 60}}}, "agents": []},
    )
    with pytest.raises(ValueError, match="interval_s"):
        load_config(cfg_path)


def test_zero_hold_rejected_for_non_idle(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {"attention": {"bands": {"warm": {"interval_s": 60, "hold_s": 0}}}, "agents": []},
    )
    with pytest.raises(ValueError, match="hold_s"):
        load_config(cfg_path)


def test_tick_s_must_not_exceed_min_interval(tmp_path):
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {
                "tick_s": 60,
                "bands": {"hot": {"interval_s": 30, "hold_s": 60}},
            },
            "agents": [],
        },
    )
    with pytest.raises(ValueError, match="tick_s"):
        load_config(cfg_path)


def test_per_agent_override_shallow_merges(tmp_path):
    """resolve_attention_config(daemon, agent) merges per-agent over daemon."""
    cfg_path = _write_yaml(
        tmp_path,
        {
            "attention": {"bands": {"hot": {"interval_s": 30, "hold_s": 120}}},
            "agents": [
                {
                    "nick": "spark-bot",
                    "channels": ["#dev"],
                    "attention": {"bands": {"hot": {"interval_s": 15, "hold_s": 60}}},
                }
            ],
        },
    )
    cfg = load_config(cfg_path)
    agent = cfg.get_agent("spark-bot")
    assert agent is not None
    resolved = resolve_attention_config(cfg, agent)
    # Per-agent override applied
    assert resolved.bands[Band.HOT].interval_s == 15
    assert resolved.bands[Band.HOT].hold_s == 60
    # Other bands inherit from daemon defaults
    assert resolved.bands[Band.WARM].interval_s == 120
