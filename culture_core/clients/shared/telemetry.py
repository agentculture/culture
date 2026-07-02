"""Re-export shim — see ``cultureagent.clients.shared.telemetry``.

OpenTelemetry instrumentation for harness internals. The implementation
lives in cultureagent; bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.shared.telemetry import *  # noqa: F401, F403
from cultureagent.clients.shared.telemetry import (  # noqa: F401
    HarnessMetricsRegistry,
    init_harness_telemetry,
    record_llm_call,
    reset_for_tests,
)
