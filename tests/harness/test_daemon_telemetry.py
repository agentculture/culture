"""Structural tests for packages/agent-harness/daemon.py OTEL wiring (Plan 5 Task 3).

The reference daemon is a template with BACKEND stub imports and cannot be
instantiated directly. These tests verify the correct wiring by reading the
source file as text and asserting the expected patterns are present.
"""

from __future__ import annotations

from pathlib import Path

# Resolve path relative to this test file — robust regardless of cwd.
_DAEMON_PY = Path(__file__).parent.parent.parent / "packages" / "agent-harness" / "daemon.py"


def _daemon_source() -> str:
    """Return the full source text of the reference daemon.py."""
    return _DAEMON_PY.read_text(encoding="utf-8")


def test_daemon_template_imports_init_harness_telemetry():
    """daemon.py must import init_harness_telemetry from culture.clients.shared.telemetry."""
    source = _daemon_source()
    assert "from culture.clients.shared.telemetry import init_harness_telemetry" in source, (
        "Expected shared import 'from culture.clients.shared.telemetry import "
        "init_harness_telemetry' not found in daemon.py"
    )


def test_daemon_template_initializes_tracer_metrics_attrs():
    """__init__ must declare self._tracer = None and self._metrics = None."""
    source = _daemon_source()
    assert (
        "self._tracer = None" in source
    ), "Expected 'self._tracer = None' attribute initialisation not found in daemon.py"
    assert (
        "self._metrics = None" in source
    ), "Expected 'self._metrics = None' attribute initialisation not found in daemon.py"


def test_daemon_template_calls_init_harness_telemetry_in_start():
    """start() must call init_harness_telemetry(self.config) and unpack into _tracer/_metrics."""
    source = _daemon_source()
    assert "self._tracer, self._metrics = init_harness_telemetry(self.config)" in source, (
        "Expected 'self._tracer, self._metrics = init_harness_telemetry(self.config)' "
        "call not found in daemon.py start()"
    )


def test_daemon_template_passes_kwargs_to_irc_transport():
    """IRCTransport construction in start() must pass tracer, metrics, and backend kwargs."""
    source = _daemon_source()
    assert (
        "tracer=self._tracer," in source
    ), "Expected 'tracer=self._tracer,' kwarg not found in IRCTransport construction"
    assert (
        "metrics=self._metrics," in source
    ), "Expected 'metrics=self._metrics,' kwarg not found in IRCTransport construction"
    assert (
        'backend="BACKEND",' in source
    ), "Expected 'backend=\"BACKEND\",' kwarg not found in IRCTransport construction"


def test_daemon_template_documents_runner_metrics_contract():
    """_start_agent_runner's NotImplementedError must mention the metrics kwarg contract."""
    source = _daemon_source()
    # "telemetry.record_llm_call" appears only in the NotImplementedError message,
    # so this assertion fails correctly if the documentation block is removed.
    assert "telemetry.record_llm_call" in source, (
        "Expected 'telemetry.record_llm_call' contract text not found in "
        "_start_agent_runner's NotImplementedError message"
    )
