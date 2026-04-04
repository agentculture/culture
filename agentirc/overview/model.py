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
    tags: list[str] = field(default_factory=list)

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
    room_id: str | None = None
    owner: str | None = None
    purpose: str | None = None
    tags: list[str] = field(default_factory=list)
    persistent: bool = False


@dataclass
class BotInfo:
    """A bot on the mesh."""

    name: str
    owner: str
    trigger_type: str
    channels: list[str]
    status: str  # "active", "stopped"
    description: str = ""
    webhook_url: str | None = None
    mention: str | None = None


@dataclass
class MeshState:
    """Complete snapshot of the mesh."""

    server_name: str
    rooms: list[Room]
    agents: list[Agent]
    federation_links: list[str]
    bots: list[BotInfo] = field(default_factory=list)
