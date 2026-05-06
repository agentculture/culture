"""Tests for the typo-channel guard on `culture channel message` (#331).

Before the fix, `culture channel message '#nonexistent' "x"` silently
auto-created the channel via the peek client, then confidently printed
``Sent to #nonexistent`` while the message landed in an orphan room
nobody else ever joined.

The guard refuses to send to a channel not in ``culture channel list``;
operators who genuinely want to bootstrap a channel can pass
``--create`` to opt back into the legacy behavior.
"""

from __future__ import annotations

import argparse
import asyncio

import pytest

from culture.cli import channel


def _args(target: str, text: str = "hello", create: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        target=target,
        text=text,
        config="~/.culture/server.yaml",
        create=create,
    )


class _FakeObserver:
    """Stand-in for IRCObserver. ``list_channels`` returns the canned set."""

    def __init__(self, channels: list[str]):
        self._channels = channels
        self.send_calls: list[tuple[str, str]] = []

    async def list_channels(self) -> list[str]:
        # `async def` is required because the real list_channels is
        # awaited via `asyncio.run(...)`. The trivial `await
        # asyncio.sleep(0)` keeps the body honest about being async
        # (and silences SonarCloud's python:S7503).
        await asyncio.sleep(0)
        return list(self._channels)

    async def send_message(self, target: str, text: str) -> None:
        await asyncio.sleep(0)
        self.send_calls.append((target, text))


@pytest.fixture
def isolate_ipc(monkeypatch):
    """Force the IPC path to miss so the guard goes through the observer fallback."""
    monkeypatch.delenv("CULTURE_NICK", raising=False)


def test_typo_channel_is_refused(isolate_ipc, monkeypatch, capsys):
    fake = _FakeObserver(channels=["#general", "#code-review"])
    monkeypatch.setattr(channel, "get_observer", lambda _config: fake)

    with pytest.raises(SystemExit) as ei:
        channel._cmd_message(_args("#nonexistent"))

    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "#nonexistent" in err
    assert "does not exist" in err
    assert "--create" in err  # hint to bootstrap if intentional
    assert fake.send_calls == [], "must not send to a non-existent channel"


def test_existing_channel_passes_through(isolate_ipc, monkeypatch, capsys):
    fake = _FakeObserver(channels=["#general"])
    monkeypatch.setattr(channel, "get_observer", lambda _config: fake)

    channel._cmd_message(_args("#general", text="hi"))

    out = capsys.readouterr().out
    assert "Sent to #general" in out
    assert fake.send_calls == [("#general", "hi")]


def test_create_flag_bypasses_guard(isolate_ipc, monkeypatch, capsys):
    """``--create`` is the explicit opt-in for bootstrap workflows."""
    fake = _FakeObserver(channels=["#general"])
    monkeypatch.setattr(channel, "get_observer", lambda _config: fake)

    channel._cmd_message(_args("#new-room", text="bootstrap", create=True))

    out = capsys.readouterr().out
    assert "Sent to #new-room" in out
    assert fake.send_calls == [("#new-room", "bootstrap")]


def test_target_without_hash_prefix_is_normalized(isolate_ipc, monkeypatch, capsys):
    """Using ``general`` (no hash) should still hit the guard for ``#general``."""
    fake = _FakeObserver(channels=["#general"])
    monkeypatch.setattr(channel, "get_observer", lambda _config: fake)

    channel._cmd_message(_args("general", text="hi"))

    out = capsys.readouterr().out
    assert "Sent to #general" in out
    assert fake.send_calls == [("#general", "hi")]
