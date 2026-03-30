"""Data model for mesh overview state."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    """A single channel message."""
    nick: str
    text: str
    timestamp: float
    channel: str


@dataclass
class Agent:
    """An agent on the mesh (local or remote)."""
    nick: str
    status: str  # "active", "idle", "paused", "remote"
    activity: str
    channels: list[str]
    server: str
    # IPC-enriched fields (local agents only):
    backend: str | None = None
    model: str | None = None
    directory: str | None = None
    turns: int | None = None
    uptime: str | None = None

    @property
    def is_local(self) -> bool:
        return self.status != "remote"


@dataclass
class Room:
    """An IRC channel with members and messages."""
    name: str
    topic: str
    members: list[Agent]
    operators: list[str]
    federation_servers: list[str]
    messages: list[Message]


@dataclass
class MeshState:
    """Complete snapshot of the mesh."""
    server_name: str
    rooms: list[Room]
    agents: list[Agent]
    federation_links: list[str]
