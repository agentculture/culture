"""Re-export shim — see ``cultureagent.clients.copilot.config``.

This module is kept so existing
``from culture.clients.copilot.config import AgentConfig`` calls in
culture's CLI and tests keep working. The implementation lives in
cultureagent; bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.copilot.config import *  # noqa: F401, F403
from cultureagent.clients.copilot.config import (  # noqa: F401
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
