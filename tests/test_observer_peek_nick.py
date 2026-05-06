"""Unit tests for parent-aware peek nick generation (#329).

The peek client used by `culture channel message`, `culture channel
read`, and `culture agent message` previously registered as
``<server>-_peek<hex>`` — opaque to other agents reading the channel.
Peek nicks now embed the calling agent when ``parent_nick`` is supplied
and shares the observer's server prefix:

* ``IRCObserver(server_name="spark", parent_nick="spark-claude")`` →
  ``spark-claude__peek7aef``
* ``IRCObserver(server_name="spark", parent_nick="thor-claude")`` →
  ``spark-_peek7aef`` (cross-server peek; no attribution)
* ``IRCObserver(server_name="spark")`` → ``spark-_peek7aef`` (legacy)

The double-underscore before ``peek`` is the protocol signal that bots
filter on to avoid greeting transient peek joins (#334).
"""

from __future__ import annotations

import re

import pytest

from culture.observer import IRCObserver


def _nick(parent: str | None) -> str:
    obs = IRCObserver(host="127.0.0.1", port=6667, server_name="spark", parent_nick=parent)
    return obs._temp_nick()


def test_no_parent_uses_legacy_opaque_form():
    nick = _nick(None)
    assert re.fullmatch(r"spark-_peek[0-9a-f]{4}", nick), nick


def test_parent_on_same_server_attributes_in_nick():
    nick = _nick("spark-claude")
    assert re.fullmatch(r"spark-claude__peek[0-9a-f]{4}", nick), nick
    assert "_peek" in nick, "still satisfies the bot filter convention"


def test_parent_with_long_agent_name_is_kept_intact():
    # Mesh nicks can be long (e.g. "spark-culture-greeter"). The agent
    # part is everything after the first "-".
    nick = _nick("spark-culture-greeter")
    assert re.fullmatch(r"spark-culture-greeter__peek[0-9a-f]{4}", nick), nick


def test_cross_server_parent_falls_back_to_opaque():
    # Parent is from a different server than the observer is connecting
    # to. Embedding the parent would produce a misleading
    # spark-thor-claude__peek...; fall back instead.
    nick = _nick("thor-claude")
    assert re.fullmatch(r"spark-_peek[0-9a-f]{4}", nick), nick


@pytest.mark.parametrize(
    "bad_parent",
    [
        "spark",  # no server-agent split
        "-claude",  # empty server prefix
        "spark-",  # empty agent part
    ],
)
def test_malformed_parent_falls_back_to_opaque(bad_parent: str):
    nick = _nick(bad_parent)
    assert re.fullmatch(r"spark-_peek[0-9a-f]{4}", nick), nick


def test_temp_nick_includes_4_hex_suffix_to_avoid_collisions():
    seen = {_nick("spark-claude") for _ in range(20)}
    assert len(seen) >= 18, "_peek suffix should rarely collide across 20 calls"


def test_peek_marker_is_present_in_every_form():
    """Bots filter on ``'_peek' in nick`` — this must hold for both shapes."""
    assert "_peek" in _nick(None)
    assert "_peek" in _nick("spark-claude")
    assert "_peek" in _nick("thor-claude")  # opaque fallback still has the marker
