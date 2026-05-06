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
    # Mesh nicks can have multiple hyphens in the agent part (e.g.
    # "spark-culture-greeter"). The agent suffix is everything after the
    # exact ``<server>-`` prefix, so "culture-greeter" stays intact.
    nick = _nick("spark-culture-greeter")
    assert re.fullmatch(r"spark-culture-greeter__peek[0-9a-f]{4}", nick), nick


def test_hyphenated_server_name_resolves_correctly():
    """Server names that themselves contain hyphens must still attribute.

    Regression: an earlier implementation used ``parent_nick.partition('-')``
    which would split ``my-server-claude`` into prefix ``my`` (wrong) and
    drop attribution entirely. The current implementation uses an exact
    ``<server>-`` prefix match, so the parent's agent suffix is everything
    *after* the full server name.
    """
    obs = IRCObserver(
        host="127.0.0.1", port=6667, server_name="my-server", parent_nick="my-server-claude"
    )
    nick = obs._temp_nick()
    assert re.fullmatch(r"my-server-claude__peek[0-9a-f]{4}", nick), nick


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


def test_temp_nick_uses_4_hex_chars_from_secrets_token_hex(monkeypatch):
    """Deterministic check that the suffix is exactly 4 hex chars from
    ``secrets.token_hex(2)`` — replaces an earlier probabilistic
    collision-rate test that could rarely flake under random luck.
    """
    import culture.observer as observer

    calls: list[int] = []

    def _fake_token_hex(n: int) -> str:
        calls.append(n)
        return "abcd"

    monkeypatch.setattr(observer.secrets, "token_hex", _fake_token_hex)

    nick = _nick("spark-claude")
    assert calls == [2], "_temp_nick should call secrets.token_hex(2) exactly once"
    assert nick == "spark-claude__peekabcd"


def test_control_characters_in_parent_nick_are_stripped():
    """CR/LF in CULTURE_NICK must not let an attacker inject IRC commands.

    Regression for the security review on #329: a malformed env value like
    ``spark-claude\\r\\nJOIN #danger`` would previously close the
    ``USER`` realname line and inject a second IRC command. The
    ``IRCObserver`` constructor now strips C0 controls (and DEL) so the
    field is safe to interpolate verbatim into the protocol stream.
    """
    obs = IRCObserver(
        host="127.0.0.1",
        port=6667,
        server_name="spark",
        parent_nick="spark-claude\r\nJOIN #danger",
    )
    # \r\n was stripped, so the parent_nick is clean and the nick still
    # attributes correctly.
    assert obs.parent_nick == "spark-claudeJOIN #danger"
    # The injected JOIN payload remains visible to operators (so they
    # can investigate the malformed value), but it's now part of the
    # nick string and cannot start a new IRC line.
    assert "\r" not in obs.parent_nick
    assert "\n" not in obs.parent_nick


def test_peek_marker_is_present_in_every_form():
    """Bots filter on ``'_peek' in nick`` — this must hold for both shapes."""
    assert "_peek" in _nick(None)
    assert "_peek" in _nick("spark-claude")
    assert "_peek" in _nick("thor-claude")  # opaque fallback still has the marker
