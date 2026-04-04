from __future__ import annotations

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from agentirc.server.client import Client
    from agentirc.server.remote_client import RemoteClient

    Member = Union[Client, RemoteClient]


class Channel:
    """Represents an IRC channel with members and topic."""

    def __init__(self, name: str):
        self.name = name
        self.topic: str | None = None
        self.members: set[Client] = set()
        self.operators: set[Client] = set()
        self.voiced: set[Client] = set()
        self.restricted = False  # +R mode — never federate
        self.shared_with: set[str] = set()  # +S servers — share with these servers

        # Room metadata (populated by ROOMCREATE, None for plain channels)
        self.room_id: str | None = None
        self.creator: str | None = None
        self.owner: str | None = None
        self.purpose: str | None = None
        self.instructions: str | None = None
        self.tags: list[str] = []
        self.persistent: bool = False
        self.agent_limit: int | None = None
        self.extra_meta: dict[str, str] = {}
        self.archived: bool = False
        self.created_at: float | None = None

    @property
    def is_managed(self) -> bool:
        """True if this channel was created via ROOMCREATE."""
        return self.room_id is not None

    def _local_members(self) -> set[Client]:
        """Return only local (non-remote, non-virtual) members."""
        from agentirc.bots.virtual_client import VirtualClient
        from agentirc.server.remote_client import RemoteClient

        return {m for m in self.members if not isinstance(m, (RemoteClient, VirtualClient))}

    def add(self, client: Client) -> None:
        # Only grant op to the first LOCAL joiner
        if not self._local_members():
            from agentirc.server.remote_client import RemoteClient

            if not isinstance(client, RemoteClient):
                self.operators.add(client)
        self.members.add(client)

    def remove(self, client: Client) -> None:
        self.members.discard(client)
        was_op = client in self.operators
        self.operators.discard(client)
        self.voiced.discard(client)
        if was_op and not self.operators:
            # Auto-promote only among local members
            local = self._local_members()
            if local:
                self.operators.add(min(local, key=lambda m: m.nick))

    def is_operator(self, client: Client) -> bool:
        return client in self.operators

    def is_voiced(self, client: Client) -> bool:
        return client in self.voiced

    def get_prefix(self, client: Client) -> str:
        if client in self.operators:
            return "@"
        if client in self.voiced:
            return "+"
        return ""
