from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentirc.server.client import Client
    from agentirc.server.ircd import IRCd
    from agentirc.protocol.message import Message


class EventType(Enum):
    MESSAGE = "message"
    JOIN = "join"
    PART = "part"
    QUIT = "quit"
    TOPIC = "topic"
    ROOMMETA = "roommeta"
    TAGS = "tags"
    ROOMARCHIVE = "roomarchive"


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
        pass

    async def on_event(self, event: Event) -> None:
        pass

    async def on_command(self, client: Client, msg: Message) -> None:
        pass
