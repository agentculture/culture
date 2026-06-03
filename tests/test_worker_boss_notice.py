"""Tests for the worker daemon's boss permission-notice DM (boss-agent layer)."""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import json  # noqa: E402
import os  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

import culture.clients.claude.daemon as daemon_mod  # noqa: E402
from culture.clients.claude.config import AgentConfig, DaemonConfig  # noqa: E402
from culture.clients.claude.daemon import AgentDaemon  # noqa: E402


def _daemon(boss: str) -> AgentDaemon:
    config = DaemonConfig()
    agent = AgentConfig(
        nick="local-worker", directory="/tmp", channels=["#team", "#task-worker"], boss=boss
    )
    return AgentDaemon(config, agent, socket_dir="/tmp", skip_claude=True)


class TestStopFullyDrainsTasks:
    """v8.18.2-D: AgentDaemon.stop() must cancel every spawned task so the
    Python process actually exits. Observed live during v8.18.1
    verification: secscan's daemon-log recorded ``agent_stop`` but the
    process stayed alive 5+ minutes, with the watchdog inside the zombie
    firing ``stalled_post_engagement`` after the official stop."""

    def _daemon(self) -> AgentDaemon:
        config = DaemonConfig()
        agent = AgentConfig(nick="local-worker", directory="/tmp", channels=["#team"], boss="")
        return AgentDaemon(config, agent, socket_dir="/tmp", skip_claude=True)

    @pytest.mark.asyncio
    async def test_stop_cancels_remaining_background_tasks(self):
        # A fire-and-forget background task in _background_tasks must be
        # cancelled by stop() — otherwise the asyncio loop stays pinned and
        # the process zombies.
        import asyncio as _asyncio

        d = self._daemon()
        ran_to_completion = []

        async def _long_running() -> None:
            try:
                await _asyncio.sleep(300)
                ran_to_completion.append(True)
            except _asyncio.CancelledError:
                raise

        task = _asyncio.create_task(_long_running())
        d._background_tasks.add(task)
        task.add_done_callback(d._background_tasks.discard)
        await d.stop()
        assert task.cancelled() or task.done()
        assert not ran_to_completion  # never finished

    @pytest.mark.asyncio
    async def test_stop_waits_for_supervisor_evals(self):
        # An in-flight supervisor evaluation must be drained by stop, not
        # left running on the loop.
        from unittest.mock import AsyncMock as _AsyncMock

        d = self._daemon()
        d._supervisor = _AsyncMock()
        d._supervisor.wait_for_evals = _AsyncMock(return_value=None)
        await d.stop()
        d._supervisor.wait_for_evals.assert_awaited_once()


class TestRejoinOwnedTaskChannels:
    """v8.18.2-C: on start, a boss daemon must rejoin #task-<suffix> channels
    for its owned workers. Without this, `culture boss brief <worker>` fails
    the channel-membership pre-check after a boss restart until the
    operator manually rejoins via IPC."""

    def _boss_daemon(self) -> AgentDaemon:
        config = DaemonConfig()
        agent = AgentConfig(
            nick="local-boss",
            directory="/tmp",
            channels=["#team", "#boss"],
            tags=["boss"],
        )
        return AgentDaemon(config, agent, socket_dir="/tmp", skip_claude=True)

    @staticmethod
    def _write_manifest(home, agents: list[tuple[str, str, str]]) -> None:
        """agents: list of (suffix, directory, boss_nick)."""
        import os

        import yaml

        for suffix, directory, boss in agents:
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, "culture.yaml"), "w", encoding="utf-8") as f:
                yaml.safe_dump({"suffix": suffix, "backend": "claude", "boss": boss}, f)
        server_yaml = os.path.join(str(home), "server.yaml")
        with open(server_yaml, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "server": {"name": "local", "host": "127.0.0.1", "port": 6667},
                    "agents": {suffix: directory for suffix, directory, _ in agents},
                },
                f,
            )

    @pytest.mark.asyncio
    async def test_rejoins_owned_workers_task_channels(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = self._boss_daemon()
        self._write_manifest(
            tmp_path,
            [
                ("w1", str(tmp_path / "w1"), "local-boss"),
                ("w2", str(tmp_path / "w2"), "local-boss"),
                ("foreign", str(tmp_path / "foreign"), "local-otherboss"),
            ],
        )
        d._transport = AsyncMock()
        await d._rejoin_owned_task_channels()
        joined = [c.args[0] for c in d._transport.join_channel.await_args_list]
        assert "#task-w1" in joined
        assert "#task-w2" in joined
        assert "#task-foreign" not in joined  # owned by another boss

    @pytest.mark.asyncio
    async def test_skips_channels_already_in_agent_config(self, tmp_path, monkeypatch):
        # If the boss daemon already lists a task channel in its own
        # culture.yaml `channels:`, transport.connect() already joined it;
        # the rejoin must not duplicate.
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = self._boss_daemon()
        d.agent = AgentConfig(
            nick="local-boss",
            directory="/tmp",
            channels=["#team", "#boss", "#task-w1"],  # already joined
            tags=["boss"],
        )
        self._write_manifest(
            tmp_path,
            [
                ("w1", str(tmp_path / "w1"), "local-boss"),
                ("w2", str(tmp_path / "w2"), "local-boss"),
            ],
        )
        d._transport = AsyncMock()
        await d._rejoin_owned_task_channels()
        joined = [c.args[0] for c in d._transport.join_channel.await_args_list]
        assert "#task-w1" not in joined  # skipped
        assert "#task-w2" in joined  # joined

    @pytest.mark.asyncio
    async def test_no_owned_workers_no_joins(self, tmp_path, monkeypatch):
        # A daemon that owns no workers (per manifest) does nothing.
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = self._boss_daemon()
        self._write_manifest(
            tmp_path,
            [("foreign", str(tmp_path / "foreign"), "local-otherboss")],
        )
        d._transport = AsyncMock()
        await d._rejoin_owned_task_channels()
        d._transport.join_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_transport_is_noop(self, tmp_path, monkeypatch):
        # Defensive: must not raise if _transport is None (start() ordering).
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = self._boss_daemon()
        d._transport = None
        await d._rejoin_owned_task_channels()


class TestIdleWatchdog:
    """The watchdog catches three classes of silent worker:

    * never_briefed — no mention/poll/invite ever landed within grace
    * stalled_pre_engagement — brief landed, no AssistantMessage in STALL grace
    * stalled_post_engagement — engaged, then no new AssistantMessage in grace

    Tests drive ``_watchdog_tick`` directly (the loop body) so the watchdog can
    be exercised deterministically without sleeping for ``WATCHDOG_POLL_SECONDS``.
    """

    @staticmethod
    def _wd_state() -> dict:
        return {"warned_state": None}

    @pytest.mark.asyncio
    async def test_dms_boss_when_never_engaged(self, tmp_path, monkeypatch):
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = False
        await d._watchdog_tick(_time.time() - 1, self._wd_state())
        d._transport.send_privmsg.assert_awaited_once()
        target, text = d._transport.send_privmsg.await_args.args
        assert target == "local-boss"
        assert "idle" in text.lower() and "local-worker" in text

    @pytest.mark.asyncio
    async def test_no_dm_when_engaged_and_recently_active(self, tmp_path, monkeypatch):
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        monkeypatch.setattr(daemon_mod, "STALL_GRACE_SECONDS", 300)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = True
        d._last_assistant_message_at = _time.time()  # just produced a turn
        await d._watchdog_tick(_time.time() - 1, self._wd_state())
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_dm_when_activated_within_stall_grace(self, tmp_path, monkeypatch):
        # A worker that received its brief recently (within STALL grace) but
        # hasn't finished its first turn yet (slow model, extended thinking,
        # long first tool call) is busy, not stalled.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        monkeypatch.setattr(daemon_mod, "STALL_GRACE_SECONDS", 300)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = False
        d._last_activation = _time.time() - 5  # briefed 5s ago, mid-first-turn
        await d._watchdog_tick(_time.time() - 1, self._wd_state())
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dms_boss_when_stalled_pre_engagement(self, tmp_path, monkeypatch):
        # Brief landed but no AssistantMessage ever produced — SDK hang.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        monkeypatch.setattr(daemon_mod, "STALL_GRACE_SECONDS", 1)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = False
        d._last_activation = _time.time() - 60  # briefed 60s ago, no output
        await d._watchdog_tick(_time.time() - 1, self._wd_state())
        d._transport.send_privmsg.assert_awaited_once()
        target, text = d._transport.send_privmsg.await_args.args
        assert target == "local-boss"
        assert "stall" in text.lower() and "received" in text.lower()

    @pytest.mark.asyncio
    async def test_dms_boss_when_stalled_post_engagement(self, tmp_path, monkeypatch):
        # Worker engaged then went silent — the engaged-then-silent class the
        # old one-shot watchdog could not see.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        monkeypatch.setattr(daemon_mod, "STALL_GRACE_SECONDS", 1)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = True
        d._last_assistant_message_at = _time.time() - 60  # last turn 60s ago
        await d._watchdog_tick(_time.time() - 1, self._wd_state())
        d._transport.send_privmsg.assert_awaited_once()
        target, text = d._transport.send_privmsg.await_args.args
        assert target == "local-boss"
        assert "stall" in text.lower() and "engaged" in text.lower()

    @pytest.mark.asyncio
    async def test_dms_boss_when_stalled_in_retry_loop(self, tmp_path, monkeypatch):
        # NEW v8.18.4 class — worker is engaged and producing AssistantMessages
        # (tool calls) but no turn has completed in STALL_GRACE_SECONDS.
        # Catches the SDK CLI "Stream closed" retry loop where every Write
        # attempt is a fresh AssistantMessage refreshing
        # _last_assistant_message_at, while no ResultMessage ever lands.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        monkeypatch.setattr(daemon_mod, "STALL_GRACE_SECONDS", 1)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = True
        now = _time.time()
        # Recent AssistantMessage but stale turn completion → looping.
        d._last_assistant_message_at = now - 0.1
        d._last_turn_completed_at = now - 60
        await d._watchdog_tick(now - 1, self._wd_state())
        d._transport.send_privmsg.assert_awaited_once()
        target, text = d._transport.send_privmsg.await_args.args
        assert target == "local-boss"
        assert (
            "retry loop" in text.lower()
            or "no progress" in text.lower()
            or "completed a turn" in text.lower()
        )

    @pytest.mark.asyncio
    async def test_no_dm_when_engaged_and_completing_turns(self, tmp_path, monkeypatch):
        # The complement: a healthy worker that's both producing
        # AssistantMessages AND completing turns recently must NOT trigger
        # stalled_in_retry_loop.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        monkeypatch.setattr(daemon_mod, "STALL_GRACE_SECONDS", 1)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = True
        now = _time.time()
        d._last_assistant_message_at = now - 0.1
        d._last_turn_completed_at = now - 0.2  # recent — making progress
        await d._watchdog_tick(now - 1, self._wd_state())
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dms_boss_when_stalled_in_failed_retry(self, tmp_path, monkeypatch):
        # v8.18.5 — intermittent-success retry loop. Failure counter
        # exceeds threshold (5) even though clean turns happened recently.
        # Catches the SDK-CLI-Stream-closed + Bash-workaround pattern from
        # the context-watch dogfood that v8.18.4's stalled_in_retry_loop
        # missed.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        monkeypatch.setattr(daemon_mod, "STALL_GRACE_SECONDS", 300)
        monkeypatch.setattr(daemon_mod, "CONSECUTIVE_FAILED_TURN_THRESHOLD", 3)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = True
        now = _time.time()
        d._last_assistant_message_at = now - 1
        d._last_turn_completed_at = now - 1  # recent — not the v8.18.4 case
        d._consecutive_failed_turns = 3  # threshold reached
        await d._watchdog_tick(now - 1, self._wd_state())
        d._transport.send_privmsg.assert_awaited_once()
        text = d._transport.send_privmsg.await_args.args[1]
        assert "failed" in text.lower() or "thrashing" in text.lower()

    @pytest.mark.asyncio
    async def test_failed_turn_increments_counter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = _daemon(boss="local-boss")
        assert d._consecutive_failed_turns == 0
        await d._on_turn_failed()
        await d._on_turn_failed()
        assert d._consecutive_failed_turns == 2

    @pytest.mark.asyncio
    async def test_completed_turn_resets_failure_counter(self, tmp_path, monkeypatch):
        # A clean turn must zero the counter — otherwise intermittent
        # success would still eventually trip the watchdog, defeating
        # the v8.18.5 semantics ("alternating fail/succeed is only a
        # stall if the SUSTAINED rate is bad").
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = _daemon(boss="local-boss")
        d._consecutive_failed_turns = 4
        await d._on_turn_complete()
        assert d._consecutive_failed_turns == 0

    @pytest.mark.asyncio
    async def test_on_turn_complete_updates_timestamp(self, tmp_path, monkeypatch):
        # The runner's _on_turn_complete callback (fired after a clean
        # async-for query() loop) must update _last_turn_completed_at.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = _daemon(boss="local-boss")
        assert d._last_turn_completed_at is None
        t0 = _time.time()
        await d._on_turn_complete()
        assert d._last_turn_completed_at is not None
        assert d._last_turn_completed_at >= t0

    @pytest.mark.asyncio
    async def test_warns_once_per_state(self, tmp_path, monkeypatch):
        # Calling tick twice in the same state must DM the boss only once.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = False
        state = self._wd_state()
        await d._watchdog_tick(_time.time() - 1, state)
        await d._watchdog_tick(_time.time() - 1, state)
        assert d._transport.send_privmsg.await_count == 1

    @pytest.mark.asyncio
    async def test_warns_again_on_state_change(self, tmp_path, monkeypatch):
        # never_briefed → activation arrives → eventually stalled_pre_engagement.
        # Two distinct DMs (one per state).
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        monkeypatch.setattr(daemon_mod, "STALL_GRACE_SECONDS", 1)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = False
        state = self._wd_state()
        await d._watchdog_tick(_time.time() - 1, state)  # never_briefed
        d._last_activation = _time.time() - 60  # now stalled-pre-engagement
        await d._watchdog_tick(_time.time() - 1, state)
        assert d._transport.send_privmsg.await_count == 2
        first = d._transport.send_privmsg.await_args_list[0].args[1].lower()
        second = d._transport.send_privmsg.await_args_list[1].args[1].lower()
        assert "idle" in first and "stall" in second

    @pytest.mark.asyncio
    async def test_no_dm_when_paused(self, tmp_path, monkeypatch):
        # Paused workers don't fire; returns True to stop the loop.
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        d._agent_runner = AsyncMock()
        d._engaged = False
        d._paused = True
        stop = await d._watchdog_tick(_time.time() - 1, self._wd_state())
        assert stop is True
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_dm_when_runner_dead(self, tmp_path, monkeypatch):
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        monkeypatch.setattr(daemon_mod, "IDLE_GRACE_SECONDS", 0)
        d = _daemon(boss="local-boss")
        d._transport = AsyncMock()
        runner = AsyncMock()
        # is_running is a *sync* method in production; override the
        # AsyncMock-generated coroutine attribute with a MagicMock that
        # returns the actual bool.
        runner.is_running = MagicMock(return_value=False)
        d._agent_runner = runner
        d._engaged = False
        stop = await d._watchdog_tick(_time.time() - 1, self._wd_state())
        assert stop is True
        d._transport.send_privmsg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_engaged_message_resets_stall_timer(self, tmp_path, monkeypatch):
        # The unified watchdog reads _last_assistant_message_at; verify
        # _on_agent_message sets it (so the post-engagement tracker drives correctly).
        import time as _time

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = _daemon(boss="local-boss")
        d._supervisor = None
        assert d._last_assistant_message_at is None
        t0 = _time.time()
        await d._on_agent_message({"type": "assistant", "text": "hi", "tool_uses": []})
        assert d._last_assistant_message_at is not None
        assert d._last_assistant_message_at >= t0

    @pytest.mark.asyncio
    async def test_engagement_flag_and_engaged_record_on_first_turn(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = _daemon(boss="local-boss")
        d._supervisor = None
        assert d._engaged is False
        await d._on_agent_message({"type": "assistant", "text": "hi", "tool_uses": []})
        await d._on_agent_message({"type": "assistant", "text": "more", "tool_uses": []})
        assert d._engaged is True
        # `engaged` is recorded exactly once (first turn), so the dashboard idle
        # signal clears authoritatively without depending on audit size.
        log_path = os.path.join(str(tmp_path), "daemon-log", "local-worker.jsonl")
        with open(log_path, encoding="utf-8") as f:
            actions = [json.loads(line)["action"] for line in f if line.strip()]
        assert actions.count("engaged") == 1

    @pytest.mark.asyncio
    async def test_poll_dispatch_counts_as_activation(self, tmp_path, monkeypatch):
        # A worker driven by the channel poll (boss posts task context WITHOUT an
        # @mention) must count as activated, so it isn't falsely flagged idle.
        from culture.clients.claude.message_buffer import MessageBuffer

        monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
        d = _daemon(boss="local-boss")
        d._agent_runner = AsyncMock()
        d._buffer = MessageBuffer()
        d._buffer.add("#task-worker", "local-boss", "here is the task context (no mention)")
        assert d._last_activation is None
        d._send_channel_poll("#task-worker")
        assert d._last_activation is not None


# NB: ``TestPermInputPreview`` was removed in Phase 5.7 along with
# ``AgentDaemon._on_perm_request`` and ``AgentDaemon._perm_input_preview``.
# The boss-side DM cascade is replaced by the bridge's FS observer on the
# perm-queue directory; no worker-daemon helper formats the preview anymore.
