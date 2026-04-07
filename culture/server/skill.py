from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from culture.protocol.message import Message
    from culture.server.client import Client
    from culture.server.ircd import IRCd


class EventType(Enum):
    MESSAGE = "message"
    JOIN = "join"
    PART = "part"
    QUIT = "quit"
    TOPIC = "topic"
    ROOMMETA = "roommeta"
    TAGS = "tags"
    ROOMARCHIVE = "roomarchive"
    THREAD_CREATE = "thread_create"
    THREAD_MESSAGE = "thread_message"
    THREAD_CLOSE = "thread_close"


@dataclass
class Event:
    type: EventType
    channel: str | None
    nick: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class Skill:
    name: str = ""
    commands: set[str] = set()

    async def start(self, server: IRCd) -> None:
        self.server = server

    async def stop(self) -> None:
        """Stop the skill. Subclasses override to release resources."""

    async def on_event(self, event: Event) -> None:
        """Handle an IRC event. Subclasses override to react to events."""

    async def on_command(self, client: Client, msg: Message) -> None:
        """Handle an IRC command. Subclasses override to process commands."""
