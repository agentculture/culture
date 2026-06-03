"""MCP tool registrations exposed by the culture-bridge plugin (Phase 4.4).

These tools are what the CC assistant calls to drive the mesh — every
verb routes through ``_bridge_client.request`` to the bridge's IPC
socket. The thin shim here gives each tool a clean Python signature +
docstring + return shape so the MCP server can advertise them, while
keeping the actual bridge contract centralized in ``_bridge_client``.

Known limitation (recorded as a Phase 4 gap):

    ``mesh grant`` enforces the "boss can only grant what boss has"
    rule (Rule 8, AD-1) using a best-effort heuristic — it reads the
    ``CLAUDE_PERMITTED_TOOLS`` environment variable if set. Claude
    Code does not currently expose the session-permitted-tools list
    to plugin tools via any documented API, so a missing env var
    falls through to "allow" and the bridge enforces against the
    worker's own policy file at the next ``PreToolUse`` gate. See
    ``README.md`` and Phase 5 of the plan for the follow-up path.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from culture.clients.claude.cc_plugin import _bridge_client
from culture.clients.claude.cc_plugin._nick_resolver import resolve_project_nick

logger = logging.getLogger(__name__)


# High-risk tools that require an input regex when granted with
# scope=always. Mirrors the bridge's ``_append_sticky_rule`` guard
# (Phase 5.1b) so we refuse early — before the bridge does.
_HIGH_RISK_TOOLS = ("Bash", "Edit", "Write")


def _own_nick() -> str:
    """Resolve the boss-nick for the CURRENT CC session.

    The session-start hook (``hooks/session_start.py``) writes the
    resolved nick into ``CULTURE_NICK`` so child subprocesses inherit
    it. When the env var isn't set yet (very first tool call before
    SessionStart fires), we re-resolve from cwd.
    """
    env_nick = os.environ.get("CULTURE_NICK", "").strip()
    if env_nick:
        return env_nick
    return resolve_project_nick(os.getcwd())


def _bridge(verb: str, **payload: Any) -> dict[str, Any]:
    """Run a bridge request as the current session's boss nick."""
    return _bridge_client.request(_own_nick(), verb, **payload)


# ---------------------------------------------------------------------------
# Channel + DM verbs
# ---------------------------------------------------------------------------


def mesh_send(channel: str, text: str) -> dict[str, Any]:
    """Send a PRIVMSG to ``channel`` on the mesh. ``channel`` must start
    with ``#``."""
    if not channel.startswith("#"):
        return {"ok": False, "error": "channel must start with '#'"}
    return _bridge("irc_send", channel=channel, message=text)


def mesh_dm(nick: str, text: str) -> dict[str, Any]:
    """Send a DM (PRIVMSG to a user nick) on the mesh. The bridge handles
    spool-on-offline so an offline recipient still gets the message."""
    return _bridge("irc_send", channel=nick, message=text)


def mesh_inbox() -> dict[str, Any]:
    """Drain the pending inbound queue (DMs + mentions + ROOMINVITEs) and
    return a formatted summary. Used by the assistant to catch up on
    anything that arrived while it was mid-tool."""
    return _bridge("inbox_drain")


def mesh_who(channel: str = "") -> dict[str, Any]:
    """List who's on a channel (or globally if no channel given)."""
    return _bridge("irc_who", target=channel or "*")


def mesh_status() -> dict[str, Any]:
    """Return bridge status: ``cc_connected``, channels, queued events,
    runtime model. Useful for ``culture boss status``-equivalent
    in-conversation queries."""
    return _bridge("status")


def mesh_agents() -> dict[str, Any]:
    """List the workers this boss owns (per the manifest)."""
    return _bridge("list_owned_agents")


def mesh_pending() -> dict[str, Any]:
    """List perm-queue entries pending decision."""
    return _bridge("list_perm_queue")


def mesh_approve(
    request_id: str,
    input_regex: str = "",
    scope: str = "once",
) -> dict[str, Any]:
    """Approve a pending perm request.

    ``scope=once`` writes a one-shot decision; ``scope=always`` appends
    a sticky rule to the worker's policy. Sticky rules for high-risk
    tools (Bash/Edit/Write) MUST carry a non-empty ``input_regex`` —
    the bridge will reject otherwise (Phase 5.1b).
    """
    payload: dict[str, Any] = {"id": request_id, "scope": scope}
    if input_regex:
        payload["input_regex"] = input_regex
    return _bridge("perm_approve", **payload)


def mesh_deny(request_id: str, reason: str = "") -> dict[str, Any]:
    """Deny a pending perm request with an optional reason shown to the
    worker."""
    return _bridge("perm_deny", id=request_id, reason=reason)


def mesh_invite(worker: str, channel: str) -> dict[str, Any]:
    """Invite a worker into an extra channel (e.g. ``#joint-fixes`` or
    ``#team-<project>``). Workers default to ``#task-<own>`` only."""
    if not channel.startswith("#"):
        return {"ok": False, "error": "channel must start with '#'"}
    return _bridge("invite_worker", worker=worker, channel=channel)


def mesh_team_channel_create(topic: str = "") -> dict[str, Any]:
    """Create ``#team-<own-project>`` for sibling worker awareness.
    Optional ``topic`` sets the IRC TOPIC at creation time."""
    return _bridge("team_channel_create", topic=topic)


# ---------------------------------------------------------------------------
# Grant
# ---------------------------------------------------------------------------


def _permitted_tools_from_env() -> set[str] | None:
    """Best-effort read of the boss's own permitted tools.

    See module docstring's "known limitation" note. Returns ``None``
    when no signal is available — caller treats that as "allow" and
    relies on the worker's own policy file for enforcement.
    """
    raw = os.environ.get("CLAUDE_PERMITTED_TOOLS", "").strip()
    if not raw:
        return None
    return {item.strip() for item in raw.split(",") if item.strip()}


def mesh_grant(
    worker: str,
    tool: str,
    input_regex: str = "",
    scope: str = "once",
) -> dict[str, Any]:
    """Grant ``worker`` permission to use ``tool`` (Rule 8 / AD-1).

    Refusal logic (best-effort): if ``CLAUDE_PERMITTED_TOOLS`` is set
    in the environment AND it does not include ``tool``, we refuse
    early ("boss can only grant what boss has"). When the env var is
    unset, we forward to the bridge and let the worker's policy file
    govern at the next ``PreToolUse`` gate.

    Sticky grants (``scope=always``) for high-risk tools require a
    non-empty ``input_regex`` — same guard as ``mesh_approve``.
    """
    permitted = _permitted_tools_from_env()
    if permitted is not None and tool not in permitted:
        return {
            "ok": False,
            "error": (
                f"refused: this CC session does not have {tool!r} in its "
                f"permitted tools, so it cannot grant it to {worker!r}"
            ),
        }
    if scope == "always" and tool in _HIGH_RISK_TOOLS and not input_regex:
        return {
            "ok": False,
            "error": (
                f"refused: high-risk tool {tool!r} cannot be granted with "
                "scope=always without a non-empty input_regex (Phase 5.1b)."
            ),
        }
    payload: dict[str, Any] = {
        "worker": worker,
        "tool": tool,
        "scope": scope,
    }
    if input_regex:
        payload["input_regex"] = input_regex
    return _bridge("grant_worker_tool", **payload)


# Public registration table. The MCP server iterates this dict to
# advertise the tools. Keys are the user-visible verb names; values are
# the callable + a one-line description.
TOOLS: dict[str, tuple[Any, str]] = {
    "mesh send": (mesh_send, "Send a PRIVMSG to a channel."),
    "mesh dm": (mesh_dm, "Send a direct message to a user nick."),
    "mesh inbox": (mesh_inbox, "Drain pending inbound DMs/mentions/invites."),
    "mesh who": (mesh_who, "List occupants of a channel (or globally)."),
    "mesh status": (mesh_status, "Report bridge status (connected, channels, queued)."),
    "mesh agents": (mesh_agents, "List the workers this boss owns."),
    "mesh pending": (mesh_pending, "List pending perm requests."),
    "mesh approve": (mesh_approve, "Approve a pending perm request."),
    "mesh deny": (mesh_deny, "Deny a pending perm request."),
    "mesh invite": (mesh_invite, "Invite a worker into a channel."),
    "mesh team-channel-create": (
        mesh_team_channel_create,
        "Create #team-<project> for sibling awareness.",
    ),
    "mesh grant": (mesh_grant, "Grant a worker a tool (subject to Rule 8)."),
}
