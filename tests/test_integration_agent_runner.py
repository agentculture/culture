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

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.shared import telemetry as harness_tel


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
    from culture.clients.claude.daemon import AgentDaemon

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
        "culture.clients.claude.agent_runner.query",
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

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", _fake_query, raising=True)

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

    monkeypatch.setattr("culture.clients.claude.agent_runner.query", _failing_query, raising=True)

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
    daemon_mod = importlib.import_module(f"culture.clients.{backend}.daemon")
    config_mod = importlib.import_module(f"culture.clients.{backend}.config")
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
