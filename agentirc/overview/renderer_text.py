"""Render MeshState as markdown text."""

from __future__ import annotations

import time

from .model import Agent, BotInfo, MeshState, Message, Room


def _relative_time(timestamp: float) -> str:
    """Format a timestamp as relative time (e.g., '2m ago', '1h ago')."""
    delta = int(time.time() - timestamp)
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _escape_cell(text: str) -> str:
    """Escape pipe and newline characters for markdown table cells."""
    return text.replace("|", "\\|").replace("\n", " ")


def _agent_table(members: list[Agent]) -> str:
    """Render a markdown table of agents."""
    lines = [
        "| Agent | Status | Activity |",
        "|-------|--------|----------|",
    ]
    for a in members:
        activity = _escape_cell(a.activity) if a.is_local else ""
        lines.append(f"| {_escape_cell(a.nick)} | {a.status} | {activity} |")
    return "\n".join(lines)


def _message_list(messages: list[Message], limit: int) -> str:
    """Render recent messages as a markdown bullet list."""
    if not messages:
        return "No recent messages."
    shown = messages[:limit]
    lines = []
    for m in shown:
        lines.append(f"- {m.nick} ({_relative_time(m.timestamp)}): {m.text}")
    return "\n".join(lines)


def _render_room(room: Room, message_limit: int) -> str:
    """Render a single room section."""
    parts = [f"## {room.name}"]
    parts.append(f"Topic: {room.topic}" if room.topic else "Topic: (none)")
    if room.room_id:
        parts.append(f"Purpose: {room.purpose or ''}")
        parts.append(f"Tags: {', '.join(room.tags) if room.tags else 'none'}")
        meta_parts = []
        if room.owner:
            meta_parts.append(f"Owner: {room.owner}")
        if room.persistent:
            meta_parts.append("Persistent")
        if meta_parts:
            parts.append(" | ".join(meta_parts))
    parts.append("")
    parts.append(_agent_table(room.members))
    parts.append("")
    parts.append("### Recent messages")
    parts.append("")
    parts.append(_message_list(room.messages, message_limit))
    return "\n".join(parts)


def render_text(
    mesh: MeshState,
    *,
    room_filter: str | None = None,
    agent_filter: str | None = None,
    message_limit: int = 4,
) -> str:
    """Render a full mesh overview as markdown."""
    if agent_filter:
        return _render_agent_detail(mesh, agent_filter, message_limit)
    if room_filter:
        return _render_room_detail(mesh, room_filter, message_limit)
    return _render_default(mesh, message_limit)


def _render_default(mesh: MeshState, message_limit: int) -> str:
    """Render the full mesh overview."""
    fed_count = len(mesh.federation_links)
    fed_str = f"{fed_count} federation link{'s' if fed_count != 1 else ''}"
    if mesh.federation_links:
        fed_str += f" ({', '.join(mesh.federation_links)})"

    parts = [f"# {mesh.server_name} mesh"]
    parts.append("")
    parts.append(
        f"{len(mesh.rooms)} room{'s' if len(mesh.rooms) != 1 else ''} | "
        f"{len(mesh.agents)} agent{'s' if len(mesh.agents) != 1 else ''} | "
        f"{fed_str}"
    )

    for room in mesh.rooms:
        parts.append("")
        parts.append(_render_room(room, message_limit))

    # Bots section
    if mesh.bots:
        parts.append("")
        parts.append("## Bots")
        parts.append("")
        parts.append("| Bot | Trigger | Channels | Owner |")
        parts.append("|-----|---------|----------|-------|")
        for bot in mesh.bots:
            channels = ", ".join(bot.channels) if bot.channels else "-"
            parts.append(f"| {bot.name} | {bot.trigger_type} | {channels} | {bot.owner} |")

    return "\n".join(parts) + "\n"


def _render_room_detail(mesh: MeshState, room_name: str, message_limit: int) -> str:
    """Render a single room drill-down."""
    room = None
    for r in mesh.rooms:
        if r.name == room_name:
            room = r
            break
    if room is None:
        return f"Room {room_name} not found.\n"

    fed_str = ", ".join(room.federation_servers) if room.federation_servers else "none"
    ops_str = ", ".join(room.operators) if room.operators else "none"

    parts = [f"# {room.name}"]
    parts.append("")
    parts.append(f"Topic: {room.topic}" if room.topic else "Topic: (none)")
    parts.append(f"Members: {len(room.members)} | Operators: {ops_str} | Federation: {fed_str}")
    parts.append("")
    parts.append(_agent_table(room.members))
    parts.append("")
    parts.append(f"## Recent messages (last {message_limit})")
    parts.append("")
    parts.append(_message_list(room.messages, message_limit))
    return "\n".join(parts) + "\n"


def _render_agent_detail(mesh: MeshState, nick: str, message_limit: int) -> str:
    """Render a single agent drill-down."""
    agent = None
    for a in mesh.agents:
        if a.nick == nick:
            agent = a
            break
    if agent is None:
        return f"Agent {nick} not found.\n"

    parts = [f"# {agent.nick}"]
    parts.append("")

    # Metadata table
    rows = [
        ("Status", agent.status),
    ]
    if agent.backend:
        rows.append(("Backend", agent.backend))
    if agent.model:
        rows.append(("Model", agent.model))
    if agent.directory:
        rows.append(("Directory", agent.directory))
    rows.append(("Activity", agent.activity or "none"))
    if agent.turns is not None:
        rows.append(("Turns", str(agent.turns)))
    if agent.uptime:
        rows.append(("Uptime", agent.uptime))
    if agent.tags:
        rows.append(("Tags", ", ".join(agent.tags)))

    parts.append("| Field | Value |")
    parts.append("|-------|-------|")
    for field_name, value in rows:
        parts.append(f"| {field_name} | {_escape_cell(value)} |")

    # Channels table
    parts.append("")
    parts.append(f"## Channels ({len(agent.channels)})")
    parts.append("")
    parts.append("| Channel | Role | Last spoke |")
    parts.append("|---------|------|------------|")
    for ch_name in agent.channels:
        role = (
            "operator"
            if any(r.name == ch_name and agent.nick in r.operators for r in mesh.rooms)
            else "member"
        )
        last_spoke = "never"
        for room in mesh.rooms:
            if room.name == ch_name:
                for msg in room.messages:
                    if msg.nick == agent.nick:
                        last_spoke = _relative_time(msg.timestamp)
                        break
                break
        parts.append(f"| {ch_name} | {role} | {last_spoke} |")

    # Cross-channel recent activity
    all_msgs = []
    for room in mesh.rooms:
        for msg in room.messages:
            if msg.nick == agent.nick:
                all_msgs.append(msg)
    all_msgs.sort(key=lambda m: m.timestamp, reverse=True)

    parts.append("")
    parts.append(f"## Recent activity across channels (last {message_limit})")
    parts.append("")
    if all_msgs:
        for msg in all_msgs[:message_limit]:
            parts.append(f"- {msg.channel} ({_relative_time(msg.timestamp)}): {msg.text}")
    else:
        parts.append("No recent activity.")

    # Owned bots
    owned_bots = [b for b in mesh.bots if b.owner == nick]
    if owned_bots:
        parts.append("")
        parts.append(f"## Bots ({len(owned_bots)})")
        parts.append("")
        for bot in owned_bots:
            channels = ", ".join(bot.channels) if bot.channels else "-"
            parts.append(f"- {bot.name} ({bot.trigger_type}, {channels}, {bot.status})")

    return "\n".join(parts) + "\n"
