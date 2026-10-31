"""External-resident helpers for culture CLI.

An *external* resident is an agent whose ``backend`` is the free-form string
``external``: a process outside culture holds its IRC connection and owns its
lifecycle (e.g. a systemd user unit such as ``gwen.service``), so no harness
backend runs behind its nick and ``culture agents start``/``stop``/``install``
leave it alone (culture#506). The optional ``owner`` field on AgentConfig
names that process or unit; when it is unset, the owner message falls back to
a generic phrasing.
"""

from __future__ import annotations

from culture_core.config import AgentConfig


def is_external(agent: AgentConfig) -> bool:
    """True if the agent is driven by an external process."""
    return agent.backend == "external"


def owner_message(agent: AgentConfig) -> str:
    """Human-readable ownership line for an external resident."""
    if agent.owner:
        return f"owned by {agent.owner}"
    return "owned by an external process"
