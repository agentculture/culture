"""Re-export shim — see ``cultureagent.clients.colleague.config``.

This module is kept so ``from culture_core.clients.colleague.config import
DaemonConfig`` calls in culture's CLI and tests keep working, symmetric with
the claude/codex shims. The implementation lives in cultureagent (the colleague
backend wraps ``colleague[culture]``'s ColleagueHarness); bug reports go
upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.colleague.config import *  # noqa: F401, F403
from cultureagent.clients.colleague.config import (  # noqa: F401
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    SupervisorConfig,
    TelemetryConfig,
    load_config,
    load_config_or_default,
    resolve_attention_config,
    sanitize_agent_name,
    save_config,
)
