"""Tests for `culture_core.cli.shared.process` — agent/server shutdown.

The module orchestrates graceful shutdown for both agents (IPC-first
with PID/signal fallback) and servers (PID/signal only). The tests
cover every branch of the four public/private functions:

- `stop_agent(nick)` — dispatcher: IPC then PID.
- `_try_ipc_shutdown(nick, socket_path)` — IPC + drain-poll.
- `_try_pid_shutdown(nick)` — PID file → SIGTERM → 5s poll → SIGKILL/SIGTERM.
- `server_stop_by_name(name)` — PID file → SIGTERM → 5s poll → SIGKILL/SIGTERM.

All OS-touching primitives (`os.kill`, `os.path.exists`, `time.sleep`,
`asyncio.run(ipc_shutdown)`, the `culture_core.pidfile.*` helpers) are
monkeypatched so the suite is hermetic and the poll loops do not block.

Both the POSIX (SIGKILL escalation) and win32 (SIGTERM-only second
attempt) branches of the implementation are exercised by pinning
`sys.platform` via monkeypatch — so the tests are portable across
hosts. The Phase 1 `exclude_lines = ["if sys\\.platform == .win32."]`
rule only suppresses *reporting* of the win32 if/body in coverage,
not its execution at runtime.
"""

from __future__ import annotations

import subprocess

import pytest

from culture_core.cli.shared import process as proc_mod
from culture_core.pidfile import remove_pid, write_pid

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
    termination = {"result": "stopped"}

    def _pop(table: dict[int, list[bool]], pid: int, default: bool) -> bool:
        seq = table.get(pid)
        if not seq:
            return default
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(proc_mod, "read_pid", lambda name: pids.get(name))
    monkeypatch.setattr(proc_mod, "is_process_alive", lambda pid: _pop(alive, pid, True))
    monkeypatch.setattr(
        proc_mod,
        "classify_pidfile",
        lambda name, pid=None: (
            "stale"
            if pid is None
            else "current" if _pop(alive, pid, True) and _pop(culture, pid, True) else "stale"
        ),
    )
    monkeypatch.setattr(proc_mod, "remove_pid", lambda name, _pid=None: removed.append(name))
    monkeypatch.setattr(proc_mod, "terminate_verified", lambda _name, _pid: termination["result"])
    monkeypatch.setattr(
        proc_mod,
        "terminate_captured",
        lambda _pid, _identity: termination["result"],
    )
    monkeypatch.setattr(
        proc_mod,
        "capture_process_identity",
        lambda pid: (str(pid), "boot") if pid else None,
    )

    class State:
        def __init__(self) -> None:
            self.removed = removed
            self.pids = pids
            self.alive = alive
            self.culture = culture
            self.termination = termination

        def set_pid(self, name: str, pid: int) -> None:
            self.pids[name] = pid

        def alive_sequence(self, pid: int, *answers: bool) -> None:
            """is_process_alive(pid) returns these answers in order."""
            self.alive[pid] = list(answers)

        def culture_sequence(self, pid: int, *answers: bool) -> None:
            self.culture[pid] = list(answers)

        def termination_result(self, result: str) -> None:
            self.termination["result"] = result

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

    assert proc_mod.stop_agent("ada") is True
    assert "No PID file for agent 'ada'" in capsys.readouterr().out


def test_stop_agent_falls_back_to_pid_when_ipc_returns_false(monkeypatch, pid_state, capsys):
    monkeypatch.setattr(proc_mod, "agent_socket_path", lambda nick: f"/tmp/{nick}.sock")
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ipc_no(_path):
        return False

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ipc_no)

    assert proc_mod.stop_agent("ada") is True
    assert "No PID file for agent 'ada'" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _try_ipc_shutdown
# ---------------------------------------------------------------------------


def test_ipc_shutdown_returns_false_when_socket_missing(monkeypatch):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: False)
    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is None


def test_ipc_shutdown_returns_false_when_ipc_raises(monkeypatch):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _boom(_path):
        raise RuntimeError("broken pipe")

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _boom)
    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is None


def test_ipc_shutdown_returns_false_when_ipc_returns_false(monkeypatch):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _no(_path):
        return False

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _no)
    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is None


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


def test_ipc_shutdown_escalates_when_polls_exhausted(monkeypatch, pid_state):
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ok(_path):
        return True

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ok)
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)  # always alive

    assert proc_mod._try_ipc_shutdown("ada", "/tmp/x.sock") is True
    assert "agent-ada" in pid_state.removed


@pytest.mark.skipif(not proc_mod.os.path.isdir("/proc"), reason="needs /proc")
def test_ipc_ack_pidfile_removal_still_terminates_real_child(monkeypatch):
    child = subprocess.Popen([proc_mod.sys.executable, "-c", "import time; time.sleep(30)"])
    write_pid("agent-ada", child.pid)
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ack_and_remove(_path):
        remove_pid("agent-ada", child.pid)
        return True

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ack_and_remove)
    try:
        assert proc_mod.stop_agent("ada") is True
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_ipc_ack_never_signals_a_reused_pid(monkeypatch):
    """An accepted IPC shutdown must not escalate onto a PID the agent no longer owns."""
    child = subprocess.Popen([proc_mod.sys.executable, "-c", "import time; time.sleep(30)"])
    path = write_pid("agent-ada", child.pid)
    path.with_suffix(".identity").write_text(f"{child.pid} 1 not-this-boot\n")
    monkeypatch.setattr(proc_mod.os.path, "exists", lambda _p: True)

    async def _ack(_path):
        return True

    monkeypatch.setattr(proc_mod, "ipc_shutdown", _ack)
    monkeypatch.setattr(proc_mod.time, "sleep", lambda _s: None)
    try:
        assert proc_mod.stop_agent("ada") is True
        assert child.poll() is None, "a reused PID must never be signalled"
    finally:
        child.kill()
        child.wait()


# ---------------------------------------------------------------------------
# _try_pid_shutdown
# ---------------------------------------------------------------------------


def test_pid_shutdown_returns_when_no_pidfile(pid_state, capsys, fake_os):
    assert proc_mod._try_pid_shutdown("ada") is True
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

    assert "stale PID" in capsys.readouterr().out
    assert "agent-ada" in pid_state.removed
    assert fake_os.calls == []


def test_pid_shutdown_unknown_never_signals(pid_state, monkeypatch, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    monkeypatch.setattr(proc_mod, "classify_pidfile", lambda _n, _p=None: "unknown")
    proc_mod._try_pid_shutdown("ada")
    assert fake_os.calls == []
    assert pid_state.removed == []
    assert "Cannot verify" in capsys.readouterr().out


def test_pid_shutdown_handles_sigterm_process_lookup_error(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True)
    pid_state.termination_result("stopped")

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == []
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_sigterm_success(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True, True, False)
    pid_state.culture_sequence(4242, True)

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == []
    out = capsys.readouterr().out
    assert "Stopping agent 'ada'" in out
    assert "stopped" in out
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_aborts_kill_when_pid_no_longer_culture(pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.termination_result("refused")

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == []
    assert "identity changed" in capsys.readouterr().out
    assert "agent-ada" not in pid_state.removed


def test_pid_shutdown_sigkill_escalation_posix(posix_platform, pid_state, capsys, fake_os):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)  # never dies during polls
    pid_state.termination_result("killed")

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == []
    out = capsys.readouterr().out
    assert "sending SIGKILL" in out
    assert "killed" in out
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_swallows_sigkill_process_lookup_error_posix(
    posix_platform, pid_state, capsys, fake_os
):
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.termination_result("stopped")

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == []
    assert "agent-ada" in pid_state.removed


def test_pid_shutdown_win32_escalates_with_sigterm_not_sigkill(
    win32_platform, pid_state, capsys, fake_os
):
    """On Windows, the second-attempt signal is SIGTERM, not SIGKILL."""
    pid_state.set_pid("agent-ada", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.termination_result("killed")

    proc_mod._try_pid_shutdown("ada")

    assert fake_os.calls == []
    out = capsys.readouterr().out
    assert "terminating" in out
    assert "sending SIGKILL" not in out
    assert "killed" in out
    assert "agent-ada" in pid_state.removed


# ---------------------------------------------------------------------------
# server_stop_by_name
# ---------------------------------------------------------------------------


def test_server_stop_no_pidfile(pid_state, fake_os):
    assert proc_mod.server_stop_by_name("spark") is True
    assert fake_os.calls == []
    assert pid_state.removed == []


def test_server_stop_unknown_reports_failure(pid_state, fake_os, monkeypatch):
    pid_state.set_pid("server-spark", 4242)
    monkeypatch.setattr(proc_mod, "classify_pidfile", lambda *_a: "unknown")
    assert proc_mod.server_stop_by_name("spark") is False
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

    assert fake_os.calls == []
    assert "server-spark" in pid_state.removed


def test_server_stop_sigkill_escalation_posix(posix_platform, pid_state, fake_os):
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True)

    pid_state.termination_result("killed")
    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == []
    assert "server-spark" in pid_state.removed


def test_server_stop_win32_escalates_with_sigterm(win32_platform, pid_state, fake_os):
    """On Windows, server_stop_by_name re-sends SIGTERM instead of SIGKILL."""
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True)

    pid_state.termination_result("killed")
    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == []
    assert "server-spark" in pid_state.removed


def test_server_stop_sigkill_swallows_process_lookup_error_posix(
    posix_platform, pid_state, fake_os
):
    pid_state.set_pid("server-spark", 4242)
    pid_state.alive_sequence(4242, True)
    pid_state.culture_sequence(4242, True)
    pid_state.termination_result("stopped")

    proc_mod.server_stop_by_name("spark")

    assert fake_os.calls == []
    assert "server-spark" in pid_state.removed
