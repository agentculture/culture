"""Tests for `culture.cli.shared.process` — agent/server shutdown.

The module orchestrates graceful shutdown for both agents (IPC-first
with PID/signal fallback) and servers (PID/signal only). The tests
cover every branch of the four public/private functions:

- `stop_agent(nick)` — dispatcher: IPC then PID.
- `_try_ipc_shutdown(nick, socket_path)` — IPC + drain-poll.
- `_try_pid_shutdown(nick)` — PID file → SIGTERM → 5s poll → SIGKILL/SIGTERM.
- `server_stop_by_name(name)` — PID file → SIGTERM → 5s poll → SIGKILL/SIGTERM.

All OS-touching primitives (`os.kill`, `os.path.exists`, `time.sleep`,
`asyncio.run(ipc_shutdown)`, the `culture.pidfile.*` helpers) are
monkeypatched so the suite is hermetic and the poll loops do not block.

Both the POSIX (SIGKILL escalation) and win32 (SIGTERM-only second
attempt) branches of the implementation are exercised by pinning
`sys.platform` via monkeypatch — so the tests are portable across
hosts. The Phase 1 `exclude_lines = ["if sys\\.platform == .win32."]`
rule only suppresses *reporting* of the win32 if/body in coverage,
not its execution at runtime.
"""

from __future__ import annotations

import signal

import pytest

from culture.cli.shared import process as proc_mod

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_os(monkeypatch):
    """Capture os.kill calls and never raise unless asked to."""
    calls: list[tuple[int, int]] = []
    raises: dict[int, type[BaseException]] = {}

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if (exc := raises.get(len(calls))) is not None:
            raise exc()

    monkeypatch.setattr(proc_mod.os, "kill", fake_kill)

    class State:
        def __init__(self) -> None:
            self.calls = calls
            self.raises = raises

        def raise_on_call(self, n: int, exc: type[BaseException]) -> None:
            self.raises[n] = exc

    return State()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Make the poll loops instant."""
    monkeypatch.setattr(proc_mod.time, "sleep", lambda _s: None)


@pytest.fixture
def posix_platform(monkeypatch):
    """Pin sys.platform to a POSIX value so SIGKILL-escalation paths run."""
    monkeypatch.setattr(proc_mod.sys, "platform", "linux")


@pytest.fixture
def win32_platform(monkeypatch):
    """Pin sys.platform to win32 so the SIGTERM-only fallback runs."""
    monkeypatch.setattr(proc_mod.sys, "platform", "win32")


@pytest.fixture
def pid_state(monkeypatch):
    """Bookkeeping for the pidfile helpers + remove_pid calls."""
    removed: list[str] = []
    pids: dict[str, int] = {}
    alive: dict[int, list[bool]] = {}  # pid -> queue of is_process_alive answers
    culture: dict[int, list[bool]] = {}  # pid -> queue of is_culture_process answers

    def _pop(table: dict[int, list[bool]], pid: int, default: bool) -> bool:
        seq = table.get(pid)
        if not seq:
            return default
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(proc_mod, "read_pid", lambda name: pids.get(name))
    monkeypatch.setattr(proc_mod, "is_process_alive", lambda pid: _pop(alive, pid, True))
    monkeypatch.setattr(proc_mod, "is_culture_process", lambda pid: _pop(culture, pid, True))
    monkeypatch.setattr(proc_mod, "remove_pid", lambda name: removed.append(name))

    class State:
        def __init__(self) -> None:
            self.removed = removed
            self.pids = pids
            self.alive = alive
            self.culture = culture

        def set_pid(self, name: str, pid: int) -> None:
            self.pids[name] = pid

        def alive_sequence(self, pid: int, *answers: bool) -> None:
            """is_process_alive(pid) returns these answers in order."""
            self.alive[pid] = list(answers)

        def culture_sequence(self, pid: int, *answers: bool) -> None:
            self.culture[pid] = list(answers)

    return State()


# ---------------------------------------------------------------------------
# stop_agent — orchestration
# ---------------------------------------------------------------------------


def test_stop_agent_uses_ipc_when_socket_exists(monkeypatch, pid_state, capsys):
    monkeypatch.setattr(proc_mod, "agent_socket_path", lambda nick: f"/tmp/{nick}.sock")
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ipc_ok(_path):
        return True

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ipc_ok)

    proc_mod.stop_agent("ada")

    out = capsys.readouterr().out
    assert "shutdown requested via IPC" in out
    assert "stopped" in out


def test_stop_agent_falls_back_to_pid_when_socket_missing(monkeypatch, pid_state, capsys):
    monkeypatch.setattr(proc_mod, "agent_socket_path", lambda nick: f"/tmp/{nick}.sock")
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: False)

    # No pidfile registered → _try_pid_shutdown returns immediately with a notice.
    proc_mod.stop_agent("ada")

    assert "No PID file for agent 'ada'" in capsys.readouterr().out


def test_stop_agent_falls_back_to_pid_when_ipc_returns_false(monkeypatch, pid_state, capsys):
    monkeypatch.setattr(proc_mod, "agent_socket_path", lambda nick: f"/tmp/{nick}.sock")
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ipc_no(_path):
        return False

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ipc_no)

    proc_mod.stop_agent("ada")

    # IPC said no → pid path runs and complains about no pidfile.
    assert "No PID file for agent 'ada'" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _try_ipc_shutdown
# ---------------------------------------------------------------------------


def test_ipc_shutdown_returns_false_when_socket_missing(monkeypatch):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: False)
    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is False


def test_ipc_shutdown_returns_false_when_ipc_raises(monkeypatch):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _boom(_path):
        raise RuntimeError("broken pipe")

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _boom)
    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is False


def test_ipc_shutdown_returns_false_when_ipc_returns_false(monkeypatch):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _no(_path):
        return False

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _no)
    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is False


def test_ipc_shutdown_returns_true_when_pid_unknown(monkeypatch, pid_state, capsys):
    """If there's no pidfile to poll, IPC ack is enough to declare success."""
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ok(_path):
        return True

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ok)
    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is True
    assert "stopped" in capsys.readouterr().out


def test_ipc_shutdown_polls_until_process_exits(monkeypatch, pid_state, capsys):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ok(_path):
        return True

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ok)

    pid_state.set_pid("agent-ada", 4242)
    # Three "still alive" answers then dead.
    pid_state.alive_sequence(4242, True, True, True, False)

    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is True
    assert "agent-ada" in pid_state.removed
    assert "stopped" in capsys.readouterr().out


def test_ipc_shutdown_returns_false_when_polls_exhausted(monkeypatch, pid_state):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ok(_path):
        return True

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ok)
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)  # always alive

    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is False
    assert "agent-ada" not in pid_state.removed


# ---------------------------------------------------------------------------
# _try_pid_shutdown
# ---------------------------------------------------------------------------


def test_pid_shutdown_returns_when_no_pidfile(pid_state, capsys, fake_os):
    proc_mod._try_pid_shutdown("ada")
    assert "No PID file for agent 'ada'" in capsys.readouterr().out
    assert fake_os.calls == []


def test_pid_shutdown_removes_corrupt_pidfile(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 0)

    proc_mod._try_pid_shutdown("ada")

    out = capsys.readouterr().out
    assert "Invalid PID 0" in out
    assert "agent-ada" in pid_state.removed
    assert fake_os.calls == []


def test_pid_shutdown_removes_stale_pidfile_when_process_not_alive(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, False)

    proc_mod._try_pid_shutdown("ada")

    assert "stale PID 4242" in capsys.readouterr().out
    assert "agent-ada" in pid_state.removed
    assert fake_os.calls == []


def test_pid_shutdown_removes_non_culture_pidfile(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, False)

    proc_mod._try_pid_shutdown("ada")

    assert "not a culture process" in capsys.readouterr().out
    assert "agent-ada" in pid_state.removed
    assert fake_os.calls == []


def test_pid_shutdown_handles_sigterm_process_lookup_error(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True)
    fake_os.raise_on_call(1, ProcessLookupError)

    proc_mod._try_pid_shutdown("ada")

    # The SIGTERM call happened — and was the only kill attempted.
    assert fake_os.calls == [(4242, signal.SIGTERM)]
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_sigterm_success(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True, True, False)
    pid_state.culture_sequence(4242, True)

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == [(4242, signal.SIGTERM)]
    out = capsys.readouterr().out
    assert "Stopping agent 'ada'" in out
    assert "stopped" in out
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_aborts_kill_when_pid_no_longer_culture(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    # alive throughout the 50 polls; then post-poll check says "not culture"
    pid_state.alive_sequence(4242, True)
    # First call (pre-SIGTERM gate) culture=True; second call (post-poll) culture=False
    pid_state.culture_sequence(4242, True, False)

    proc_mod._try_pid_shutdown("ada")

    # Only SIGTERM was sent — SIGKILL was aborted.
    assert fake_os.calls == [(4242, signal.SIGTERM)]
    assert "no longer a culture process" in capsys.readouterr().out
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_sigkill_escalation_posix(posix_platform, pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)  # never dies during polls
    pid_state.culture_sequence(4242, True, True)  # still culture post-poll

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    out = capsys.readouterr().out
    assert "sending SIGKILL" in out
    assert "killed" in out
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_swallows_sigkill_process_lookup_error_posix(
    posix_platform, pid_state, capsys, fake_os
):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True, True)
    fake_os.raise_on_call(2, ProcessLookupError)  # SIGKILL throws

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_win32_escalates_with_sigterm_not_sigkill(
    win32_platform, pid_state, capsys, fake_os
):
    """On Windows, the second-attempt signal is SIGTERM, not SIGKILL."""
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True, True)

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == [(4242, signal.SIGTERM), (4242, signal.SIGTERM)]
    out = capsys.readouterr().out
    assert "terminating" in out
    assert "sending SIGKILL" not in out
    assert "killed" in out
    assert "agent-ada" in pid_state.removed


# ---------------------------------------------------------------------------
# server_stop_by_name
# ---------------------------------------------------------------------------


def test_server_stop_no_pidfile(pid_state, fake_os):
    proc_mod.server_stop_by_name("spark")
    assert fake_os.calls == []
    assert pid_state.removed == []


def test_server_stop_removes_pidfile_when_process_not_alive(pid_state, fake_os):
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, False)

    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == []
    assert "server-spark" in pid_state.removed


def test_server_stop_removes_pidfile_when_not_culture_process(pid_state, fake_os):
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, False)

    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == []
    assert "server-spark" in pid_state.removed


def test_server_stop_sigterm_success(pid_state, fake_os):
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, True, True, False)
    pid_state.culture_sequence(4242, True)

    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == [(4242, signal.SIGTERM)]
    assert "server-spark" in pid_state.removed


def test_server_stop_sigkill_escalation_posix(posix_platform, pid_state, fake_os):
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True)

    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert "server-spark" in pid_state.removed


def test_server_stop_win32_escalates_with_sigterm(win32_platform, pid_state, fake_os):
    """On Windows, server_stop_by_name re-sends SIGTERM instead of SIGKILL."""
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True)

    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == [(4242, signal.SIGTERM), (4242, signal.SIGTERM)]
    assert "server-spark" in pid_state.removed


def test_server_stop_sigkill_swallows_process_lookup_error_posix(
    posix_platform, pid_state, fake_os
):
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True)
    fake_os.raise_on_call(2, ProcessLookupError)  # SIGKILL throws

    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert "server-spark" in pid_state.removed
