"""External-resident helpers for culture CLI.

An *external* resident is an agent whose ``backend`` is the free-form string
``external`` — its turns are driven by a process outside culture (e.g. a
colleague dispatch rig) rather than one of the in-tree SDK backends. The
optional ``owner`` field on AgentConfig names that external process; when it
is unset, the owner message falls back to a generic phrasing.
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
