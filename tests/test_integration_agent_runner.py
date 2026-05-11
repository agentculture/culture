"""End-to-end agent_runner timeout behavior — claude backend.

Drives the full daemon → AgentRunner → ``_run_loop`` → ``_process_turn``
chain with a hanging SDK and a short per-turn timeout, then asserts the
``culture.harness.llm.calls`` counter records ``outcome=timeout``.
Replaces the integration-shaped portion of
``tests/harness/test_agent_runner_claude.py``'s timeout test (the
harness unit test moves to cultureagent in Phase 1).

**Other backends (codex, copilot, acp):** the audit's "parameterize
over 4 backends" ask is acknowledged but narrowed for this PR. Each
backend has a different SDK injection point (subprocess, sessions, ACP
SDK), so adding all four in one PR would mix scope and risk per-backend
flakiness. The harness unit tests in
``tests/harness/test_agent_runner_{codex,copilot,acp}.py`` already
cover the timeout path at unit shape and ship to cultureagent in
Phase 1; the cross-backend integration coverage is tracked as a
follow-up.
"""

import asyncio
import importlib.util
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub out the copilot SDK if not installed so CI can import
# cultureagent.clients.copilot.agent_runner.start() (which lazy-imports
# `from copilot import CopilotClient, PermissionHandler, SubprocessConfig`).
# Mirrors the per-file stub pattern at tests/harness/test_agent_runner_copilot.py:32-69
# but uses importlib.util.find_spec to gate on actual SDK availability — so a
# real copilot install (dev env) isn't masked.
# ---------------------------------------------------------------------------


def _stub_copilot_sdk():
    # If copilot is already in sys.modules (real install or a sibling test's
    # stub), don't override — a sibling stub may lack `__spec__`, which would
    # make find_spec() raise ValueError, so check sys.modules first.
    if "copilot" in sys.modules:
        return
    # Check distribution availability without importing. ValueError can fire
    # if find_spec encounters a malformed entry; treat as "install our stub".
    try:
        if importlib.util.find_spec("copilot") is not None:
            return
    except (ValueError, ImportError):
        pass
    mod = types.ModuleType("copilot")
    # Set __spec__ so consumers calling find_spec("copilot") later don't
    # raise ValueError on this stub.
    mod.__spec__ = importlib.util.spec_from_loader("copilot", loader=None)

    class CopilotClient:
        def __init__(self, config=None):  # noqa: ARG002
            pass

        async def start(self):
            await asyncio.sleep(0)

        async def stop(self):
            await asyncio.sleep(0)

        async def create_session(self, **_kwargs):
            await asyncio.sleep(0)
            # Production teardown does `await self._session.destroy()`; provide
            # an AsyncMock so the destroy path runs cleanly instead of raising
            # TypeError that the runner's except-block swallows.
            session = MagicMock()
            session.destroy = AsyncMock()
            return session

    class PermissionHandler:
        approve_all = staticmethod(lambda _req: True)

    class SubprocessConfig:
        def __init__(self, cwd=None, env=None):
            self.cwd = cwd
            self.env = env

    mod.CopilotClient = CopilotClient
    mod.PermissionHandler = PermissionHandler
    mod.SubprocessConfig = SubprocessConfig
    sys.modules["copilot"] = mod


_stub_copilot_sdk()


from cultureagent.clients.shared import telemetry as harness_tel  # noqa: E402

from culture.clients.claude.config import (  # noqa: E402
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)


def _redirect_pidfile(monkeypatch, tmp_path):
    """Redirect ``culture.pidfile.PID_DIR`` so daemons don't write into the
    real ``~/.culture/pids`` from a unit test."""
    monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "pids"))


def _invalidate_harness_telemetry_cache():
    """Clear the harness telemetry module cache so ``init_harness_telemetry``
    re-resolves the OTel globals (which the conftest fixtures own).
    Same helper rationale as Task 6 — see
    ``tests/test_integration_telemetry.py`` for the longer note on why
    this is the narrow safe poke vs ``harness_tel.reset_for_tests()``.
    """
    harness_tel._initialized_for = None
    harness_tel._tracer = None
    harness_tel._registry = None


def _iter_data_points(metrics_reader, metric_name):
    """Yield each data point on ``metric_name`` across all resource/scope
    metric trees the reader has captured. Flattening this once here keeps
    the polling helpers readable."""
    data = metrics_reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == metric_name:
                    yield from metric.data.data_points


def _find_data_point(metrics_reader, metric_name, match_attrs):
    """Return the first data point on ``metric_name`` whose attributes
    are a superset of ``match_attrs``, else ``None``."""
    for dp in _iter_data_points(metrics_reader, metric_name):
        if all(dp.attributes.get(k) == v for k, v in match_attrs.items()):
            return dp
    return None


async def _wait_for_outcome_metric(metrics_reader, outcome, timeout=10.0):
    """Bounded poll until ``culture.harness.llm.calls`` has a data point
    with the given ``outcome`` attribute. Returns the data point.
    Replaces fixed sleeps; ``record_llm_call`` fires after each turn but
    ordering vs. the test's next line isn't guaranteed."""
    async with asyncio.timeout(timeout):
        while True:
            dp = _find_data_point(metrics_reader, "culture.harness.llm.calls", {"outcome": outcome})
            if dp is not None:
                return dp
            await asyncio.sleep(0.1)


def _build_daemon(server, agent_dir, sock_dir, nick="testserv-bot", turn_timeout=0.2):
    """Helper: build a claude AgentDaemon configured for these tests.
    Returns the (unstarted) daemon. ``turn_timeout`` controls
    ``agent.turn_timeout_seconds``."""
    from cultureagent.clients.claude.daemon import AgentDaemon

    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(
        nick=nick,
        directory=str(agent_dir),
        channels=["#general"],
    )
    # AgentConfig is non-frozen — daemon reads turn_timeout_seconds via
    # getattr() with DEFAULT_TURN_TIMEOUT_SECONDS fallback.
    agent.turn_timeout_seconds = turn_timeout
    return AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=False)


def _build_codex_daemon(server, agent_dir, sock_dir, nick="testserv-bot", turn_timeout=0.2):
    """Helper: build a codex CodexDaemon configured for these tests."""
    from cultureagent.clients.codex.daemon import CodexDaemon

    from culture.clients.codex.config import AgentConfig as CodexAgentConfig
    from culture.clients.codex.config import DaemonConfig as CodexDaemonConfig
    from culture.clients.codex.config import ServerConnConfig as CodexServerConnConfig
    from culture.clients.codex.config import WebhookConfig as CodexWebhookConfig

    config = CodexDaemonConfig(
        server=CodexServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=CodexWebhookConfig(url=None),
    )
    agent = CodexAgentConfig(nick=nick, directory=str(agent_dir), channels=["#general"])
    agent.turn_timeout_seconds = turn_timeout
    return CodexDaemon(config, agent, socket_dir=str(sock_dir), skip_codex=False)


def _build_copilot_daemon(server, agent_dir, sock_dir, nick="testserv-bot", turn_timeout=0.2):
    """Helper: build a copilot CopilotDaemon configured for these tests."""
    from cultureagent.clients.copilot.daemon import CopilotDaemon

    from culture.clients.copilot.config import AgentConfig as CopilotAgentConfig
    from culture.clients.copilot.config import DaemonConfig as CopilotDaemonConfig
    from culture.clients.copilot.config import ServerConnConfig as CopilotServerConnConfig
    from culture.clients.copilot.config import WebhookConfig as CopilotWebhookConfig

    config = CopilotDaemonConfig(
        server=CopilotServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=CopilotWebhookConfig(url=None),
    )
    agent = CopilotAgentConfig(nick=nick, directory=str(agent_dir), channels=["#general"])
    agent.turn_timeout_seconds = turn_timeout
    return CopilotDaemon(config, agent, socket_dir=str(sock_dir), skip_copilot=False)


def _build_acp_daemon(server, agent_dir, sock_dir, nick="testserv-bot", turn_timeout=0.2):
    """Helper: build an acp ACPDaemon configured for these tests.
    Note: ACP uses ``skip_agent`` (not ``skip_acp``)."""
    from cultureagent.clients.acp.daemon import ACPDaemon

    from culture.clients.acp.config import AgentConfig as ACPAgentConfig
    from culture.clients.acp.config import DaemonConfig as ACPDaemonConfig
    from culture.clients.acp.config import ServerConnConfig as ACPServerConnConfig
    from culture.clients.acp.config import WebhookConfig as ACPWebhookConfig

    config = ACPDaemonConfig(
        server=ACPServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=ACPWebhookConfig(url=None),
    )
    agent = ACPAgentConfig(nick=nick, directory=str(agent_dir), channels=["#general"])
    agent.turn_timeout_seconds = turn_timeout
    return ACPDaemon(config, agent, socket_dir=str(sock_dir), skip_agent=False)


@pytest.mark.asyncio
async def test_claude_agent_runner_records_timeout_outcome(
    server, metrics_reader, tracing_exporter, tmp_path, monkeypatch
):
    """A wedged claude SDK call exceeds ``turn_timeout_seconds``;
    AgentRunner's timeout path records ``outcome=timeout`` on the
    ``culture.harness.llm.calls`` counter."""
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    # Hanging async generator — never yields, never returns. Mirrors the
    # production failure mode that motivated issue #349 (the per-turn
    # timeout that this whole code path defends against). Use Event().wait()
    # rather than `await asyncio.Future()` so cancellation propagates
    # cleanly — a bare Future left pending on cancel emits "Future was
    # destroyed but it is pending" warnings.
    async def _hanging_query(**_kwargs):
        await asyncio.Event().wait()
        yield  # pragma: no cover  -- unreachable, marks this an async generator

    monkeypatch.setattr(
        "cultureagent.clients.claude.agent_runner.query",
        _hanging_query,
        raising=True,
    )

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    daemon = _build_daemon(server, agent_dir, sock_dir, turn_timeout=0.2)

    await daemon.start()
    try:
        assert daemon._agent_runner is not None
        await daemon._agent_runner.send_prompt("hello")

        timeout_dp = await _wait_for_outcome_metric(metrics_reader, "timeout")
        assert timeout_dp.attributes.get("backend") == "claude"
        assert timeout_dp.value >= 1
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_claude_agent_runner_records_success_outcome(
    server, metrics_reader, tracing_exporter, tmp_path, monkeypatch
):
    """A normal SDK turn that yields ``ResultMessage`` records
    ``outcome=success`` on ``culture.harness.llm.calls`` plus token
    counters on ``culture.harness.llm.tokens.input`` /
    ``tokens.output`` (the ``record_llm_call`` happy path —
    ``agent_runner.py:200-235``)."""
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    # conftest installs a claude_agent_sdk stub at collection time (see
    # tests/conftest.py). Its ResultMessage takes only kwargs; pylint
    # however statically resolves the *real* SDK's ResultMessage which
    # has required positional args (subtype, duration_ms, ...). The
    # disable below applies only to the constructor call.
    from claude_agent_sdk import ResultMessage

    fake_result = ResultMessage(  # pylint: disable=no-value-for-parameter
        session_id="sid-1",
        is_error=False,
        usage={"input_tokens": 100, "output_tokens": 200},
    )

    async def _fake_query(**_kwargs):
        yield fake_result

    monkeypatch.setattr("cultureagent.clients.claude.agent_runner.query", _fake_query, raising=True)

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    # Plenty of timeout for the fake query to complete; no wedging here.
    daemon = _build_daemon(server, agent_dir, sock_dir, turn_timeout=5.0)

    await daemon.start()
    try:
        assert daemon._agent_runner is not None
        await daemon._agent_runner.send_prompt("hello")

        success_dp = await _wait_for_outcome_metric(metrics_reader, "success")
        assert success_dp.attributes.get("backend") == "claude"
        assert success_dp.value >= 1

        tokens_in = _find_data_point(
            metrics_reader,
            "culture.harness.llm.tokens.input",
            {"backend": "claude", "harness.nick": "testserv-bot"},
        )
        tokens_out = _find_data_point(
            metrics_reader,
            "culture.harness.llm.tokens.output",
            {"backend": "claude", "harness.nick": "testserv-bot"},
        )
        assert tokens_in is not None and tokens_in.value == 100
        assert tokens_out is not None and tokens_out.value == 200
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_claude_agent_runner_records_error_outcome(
    server, metrics_reader, tracing_exporter, tmp_path, monkeypatch
):
    """An SDK exception other than ``TimeoutError`` records
    ``outcome=error`` on ``culture.harness.llm.calls`` and triggers
    ``on_exit(1)`` (``agent_runner.py:216-221``)."""
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    async def _failing_query(**_kwargs):
        # `if False: yield` keeps this a valid async generator function
        # without dead-code-after-raise (which pylint flags as unreachable).
        if False:  # pragma: no cover
            yield
        raise RuntimeError("SDK exploded")

    monkeypatch.setattr(
        "cultureagent.clients.claude.agent_runner.query", _failing_query, raising=True
    )

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    # Plenty of timeout — error fires before the timeout window closes.
    daemon = _build_daemon(server, agent_dir, sock_dir, turn_timeout=5.0)

    await daemon.start()
    try:
        assert daemon._agent_runner is not None
        await daemon._agent_runner.send_prompt("hello")

        error_dp = await _wait_for_outcome_metric(metrics_reader, "error")
        assert error_dp.attributes.get("backend") == "claude"
        assert error_dp.value >= 1
    finally:
        await daemon.stop()


_DAEMON_BACKENDS = [
    ("claude", "AgentDaemon", "skip_claude"),
    ("codex", "CodexDaemon", "skip_codex"),
    ("copilot", "CopilotDaemon", "skip_copilot"),
    ("acp", "ACPDaemon", "skip_agent"),  # ACP uses skip_agent, not skip_acp
]


@pytest.mark.parametrize("backend, daemon_cls_name, skip_flag", _DAEMON_BACKENDS)
@pytest.mark.asyncio
async def test_daemon_stop_handles_shutdown_from_within_tracked_task(
    server, tmp_path, monkeypatch, backend, daemon_cls_name, skip_flag
):
    """Regression (all four backends): ``_ipc_shutdown`` (sync) schedules
    ``_graceful_shutdown`` onto ``_background_tasks``, and that task awaits
    ``self.stop()`` when no external ``_stop_event`` is registered.
    ``stop()``'s cancel loop must exclude ``asyncio.current_task()`` —
    otherwise the running shutdown task cancels itself, aborting teardown
    before transport/socket cleanup. Surfaced by Qodo on PR #373.

    Parametrized over all backends because the cancel-loop block was added
    identically to all four daemons (cite-don't-import twin code) and the
    self-cancellation hazard is structural to every backend.
    """
    import importlib

    _redirect_pidfile(monkeypatch, tmp_path)
    daemon_mod = importlib.import_module(f"cultureagent.clients.{backend}.daemon")
    config_mod = importlib.import_module(f"cultureagent.clients.{backend}.config")
    daemon_cls = getattr(daemon_mod, daemon_cls_name)
    daemon_config_cls = config_mod.DaemonConfig
    agent_config_cls = config_mod.AgentConfig
    server_conn_cls = config_mod.ServerConnConfig
    webhook_cls = config_mod.WebhookConfig

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    config = daemon_config_cls(
        server=server_conn_cls(host="127.0.0.1", port=server.config.port),
        webhooks=webhook_cls(url=None),
    )
    agent = agent_config_cls(nick="testserv-bot", directory=str(agent_dir), channels=["#general"])
    daemon = daemon_cls(config, agent, socket_dir=str(sock_dir), **{skip_flag: True})

    await daemon.start()
    assert daemon._transport is not None
    assert daemon._socket_server is not None

    # Schedule a non-current background task so stop()'s cancel loop has
    # something to actually cancel and gather. Without this the test only
    # exercises the snapshot/filter; the cancel + gather lines don't run.
    async def _sleeper():
        await asyncio.sleep(60)

    sleeper_task = asyncio.create_task(_sleeper())
    daemon._background_tasks.add(sleeper_task)
    sleeper_task.add_done_callback(daemon._background_tasks.discard)

    # Trigger the IPC shutdown path: synchronous method that creates a
    # _graceful_shutdown task and adds it to _background_tasks. With no
    # _stop_event registered, _graceful_shutdown awaits self.stop().
    daemon._ipc_shutdown("req-1", {})

    # If self-cancellation regresses, stop() raises CancelledError before
    # transport.disconnect() / socket_server.stop() runs, so _transport stays
    # non-None. Bounded poll for clean teardown.
    async with asyncio.timeout(5.0):
        while daemon._transport is not None:
            await asyncio.sleep(0.05)

    assert daemon._transport is None
    assert daemon._socket_server is None


# ---------------------------------------------------------------------------
# Codex + copilot timeout integration tests (Phase 0a Task 8 narrowing follow-up).
#
# Mirror the claude timeout test for codex and copilot. The harness unit tests
# in tests/harness/test_agent_runner_{codex,copilot}.py move to cultureagent in
# Phase 1; without these integration replacements the timeout path loses
# coverage on cutover.
# ---------------------------------------------------------------------------


class _FakeCodexProcess:
    """Stand-in for the codex app-server subprocess. Provides just enough
    surface for ``CodexAgentRunner.start()``/``stop()``/``_send_request()``
    plumbing to succeed when ``_send_request`` and ``_read_loop`` are also
    monkeypatched. Does not back any real stdio."""

    def __init__(self):
        self.returncode = None

        class _Stdin:
            def write(self_inner, data):
                pass

            async def drain(self_inner):
                return None

        self.stdin = _Stdin()
        # _read_loop is patched to hang in our test, so .stdout.readline is
        # never actually awaited. Provide an attribute so the truthiness
        # check in `_read_loop` (`if not self._process.stdout: return`)
        # doesn't bail early.
        self.stdout = object()

    def terminate(self):
        self.returncode = -15

    async def wait(self):
        return self.returncode if self.returncode is not None else 0

    def kill(self):
        self.returncode = -9


@pytest.mark.asyncio
async def test_codex_agent_runner_records_timeout_outcome(
    server, metrics_reader, tracing_exporter, tmp_path, monkeypatch
):
    """Wedge the codex JSON-RPC turn: ``_send_request`` returns a fake
    response but the ``turn/completed`` notification never arrives (the
    read-loop is patched to hang), so ``_turn_done.wait()`` hangs and
    ``_execute_single_turn``'s outer ``asyncio.timeout`` fires. Assert
    ``outcome=timeout`` lands on ``culture.harness.llm.calls`` with
    ``backend=codex`` (``agent_runner.py:402-447``).

    Replaces the integration-shaped portion of
    ``tests/harness/test_agent_runner_codex.py``'s timeout test (the
    harness unit test moves to cultureagent in Phase 1).
    """
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    # Fake out the subprocess spawn so `runner.start()` doesn't require
    # the real `codex` binary on PATH. Only the runner's interactions with
    # `self._process` need the fake; we patch _send_request and _read_loop
    # to bypass actual JSON-RPC plumbing.
    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeCodexProcess()

    monkeypatch.setattr(
        "cultureagent.clients.codex.agent_runner.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    # Hang the read-loop so no `turn/completed` notification ever fires —
    # this is what causes `_turn_done.wait()` to never resolve, which is
    # the wedge state the outer timeout defends against in production.
    async def _hanging_read_loop(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "cultureagent.clients.codex.agent_runner.CodexAgentRunner._read_loop",
        _hanging_read_loop,
        raising=True,
    )

    # Replace _send_request with a fake JSON-RPC responder so
    # `runner.start()` (which calls initialize + thread/start) succeeds and
    # later turn/start calls also return immediately. The wedge is in the
    # absence of `turn/completed`, not in _send_request itself.
    async def _fake_send_request(self, method, params=None):
        if method == "thread/start":
            return {"jsonrpc": "2.0", "id": "x", "result": {"thread": {"id": "t-1"}}}
        return {"jsonrpc": "2.0", "id": "x", "result": {}}

    monkeypatch.setattr(
        "cultureagent.clients.codex.agent_runner.CodexAgentRunner._send_request",
        _fake_send_request,
        raising=True,
    )

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    daemon = _build_codex_daemon(server, agent_dir, sock_dir, turn_timeout=0.2)

    await daemon.start()
    try:
        assert daemon._agent_runner is not None
        await daemon._agent_runner.send_prompt("hello")
        timeout_dp = await _wait_for_outcome_metric(metrics_reader, "timeout")
        assert timeout_dp.attributes.get("backend") == "codex"
        assert timeout_dp.value >= 1
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_copilot_agent_runner_records_timeout_outcome(
    server, metrics_reader, tracing_exporter, tmp_path, monkeypatch
):
    """Wedge the copilot session: replace ``_session.send_and_wait`` with
    a never-resolving coroutine after ``daemon.start()`` so
    ``asyncio.wait_for`` fires its outer timeout. Assert
    ``outcome=timeout`` lands on ``culture.harness.llm.calls`` with
    ``backend=copilot`` (``agent_runner.py:204-239``).

    Replaces the integration-shaped portion of
    ``tests/harness/test_agent_runner_copilot.py``'s timeout test (the
    harness unit test moves to cultureagent in Phase 1). The copilot SDK
    stub installed at the top of this module mirrors the harness file's
    pattern so CI works without ``copilot`` installed.
    """
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    daemon = _build_copilot_daemon(server, agent_dir, sock_dir, turn_timeout=0.2)

    await daemon.start()
    try:
        assert daemon._agent_runner is not None

        # _session is created during runner.start() via _client.create_session
        # (which our SDK stub returns a MagicMock for). Mutate its
        # send_and_wait attribute to a never-resolving coroutine.
        async def _hanging_send_and_wait(_text, timeout=None):  # noqa: ARG001
            await asyncio.Event().wait()

        daemon._agent_runner._session.send_and_wait = _hanging_send_and_wait

        await daemon._agent_runner.send_prompt("hello")
        timeout_dp = await _wait_for_outcome_metric(metrics_reader, "timeout")
        assert timeout_dp.attributes.get("backend") == "copilot"
        assert timeout_dp.value >= 1
    finally:
        await daemon.stop()


class _FakeACPProcess:
    """Stand-in for the ``opencode acp`` subprocess. Mirrors
    ``_FakeCodexProcess`` shape; ACP also reads stderr (``_stderr_loop``)
    so we add an opaque ``stderr`` attribute alongside ``stdout``."""

    def __init__(self):
        self.returncode = None

        class _Stdin:
            def write(self_inner, data):
                pass

            async def drain(self_inner):
                return None

        self.stdin = _Stdin()
        # _read_loop and _stderr_loop are patched to hang in our test, so
        # the corresponding stream attributes are never actually read.
        # Provide them as opaque truthy objects.
        self.stdout = object()
        self.stderr = object()

    def terminate(self):
        self.returncode = -15

    async def wait(self):
        return self.returncode if self.returncode is not None else 0

    def kill(self):
        self.returncode = -9


@pytest.mark.asyncio
async def test_acp_agent_runner_records_timeout_outcome(
    server, metrics_reader, tracing_exporter, tmp_path, monkeypatch
):
    """Wedge the ACP busy-poll: ``_send_prompt_with_retry`` returns an
    empty dict, then ``_handle_prompt_result`` hangs on the
    ``while self._busy`` poll because the read-loop is faked and never
    delivers a ``stopReason`` notification. The outer
    ``asyncio.timeout(self._turn_timeout)`` (the safety net for issue
    #349) fires; ``outcome=timeout`` lands on ``culture.harness.llm.calls``
    with ``backend=acp`` (``agent_runner.py:466-507``).

    Replaces the integration-shaped portion of
    ``tests/harness/test_agent_runner_acp.py``'s timeout test (the
    harness unit test moves to cultureagent in Phase 1). Pattern matches
    that file's lines 237-286 — patch the pair (``_send_prompt_with_retry``
    + ``_handle_prompt_result``) so the wedge is the busy-poll itself,
    not the inner request, mirroring the production failure mode.
    """
    _redirect_pidfile(monkeypatch, tmp_path)
    _invalidate_harness_telemetry_cache()

    # Fake the subprocess spawn so runner.start() doesn't require the
    # `opencode acp` binary on PATH.
    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeACPProcess()

    monkeypatch.setattr(
        "cultureagent.clients.acp.agent_runner.asyncio.create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    # Hang the read/stderr loops so no `session/update` notifications
    # ever arrive — this is the wedge state the busy-poll defends against.
    async def _hang(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "cultureagent.clients.acp.agent_runner.ACPAgentRunner._read_loop",
        _hang,
        raising=True,
    )
    monkeypatch.setattr(
        "cultureagent.clients.acp.agent_runner.ACPAgentRunner._stderr_loop",
        _hang,
        raising=True,
    )

    # Fake `initialize` + `session/new` so runner.start() succeeds. The
    # _initialize_acp_session helper requires session/new to return a
    # truthy `sessionId`, else it raises RuntimeError.
    async def _fake_send_request(self, method, params=None, timeout=None):  # noqa: ARG001
        if method == "session/new":
            return {"jsonrpc": "2.0", "id": "x", "result": {"sessionId": "sess-1"}}
        return {"jsonrpc": "2.0", "id": "x", "result": {}}

    monkeypatch.setattr(
        "cultureagent.clients.acp.agent_runner.ACPAgentRunner._send_request",
        _fake_send_request,
        raising=True,
    )

    # Wedge the busy-poll: _send_prompt_with_retry returns immediately,
    # _handle_prompt_result hangs forever — the outer asyncio.timeout
    # then fires (mirroring tests/harness/test_agent_runner_acp.py:237-286).
    async def _fake_prompt(self, text):  # noqa: ARG001
        return {"result": {}}

    async def _hang_handle(self, resp):  # noqa: ARG001
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "cultureagent.clients.acp.agent_runner.ACPAgentRunner._send_prompt_with_retry",
        _fake_prompt,
        raising=True,
    )
    monkeypatch.setattr(
        "cultureagent.clients.acp.agent_runner.ACPAgentRunner._handle_prompt_result",
        _hang_handle,
        raising=True,
    )

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    daemon = _build_acp_daemon(server, agent_dir, sock_dir, turn_timeout=0.2)

    await daemon.start()
    try:
        assert daemon._agent_runner is not None
        await daemon._agent_runner.send_prompt("hello")
        timeout_dp = await _wait_for_outcome_metric(metrics_reader, "timeout")
        assert timeout_dp.attributes.get("backend") == "acp"
        assert timeout_dp.value >= 1
    finally:
        await daemon.stop()
