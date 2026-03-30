"""Disk persistence for managed rooms."""
from __future__ import annotations

import json
import re
from pathlib import Path


class RoomStore:
    """Save and load managed room metadata to/from disk as JSON files."""

    def __init__(self, data_dir: str | Path):
        self._rooms_dir = Path(data_dir) / "rooms"
        self._rooms_dir.mkdir(parents=True, exist_ok=True)

    def save(self, channel) -> None:
        """Persist a managed room's metadata to disk."""
        if not channel.room_id:
            return
        # Sanitize room_id to prevent path traversal
        safe_id = re.sub(r'[^A-Z0-9]', '', channel.room_id)
        if not safe_id:
            return
        data = {
            "room_id": channel.room_id,
            "name": channel.name,
            "creator": channel.creator,
            "owner": channel.owner,
            "purpose": channel.purpose,
            "instructions": channel.instructions,
            "tags": channel.tags,
            "persistent": channel.persistent,
            "agent_limit": channel.agent_limit,
            "extra_meta": channel.extra_meta,
            "archived": channel.archived,
            "created_at": channel.created_at,
            "topic": channel.topic,
        }
        path = self._rooms_dir / f"{safe_id}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.rename(path)

    def delete(self, room_id: str) -> None:
        """Remove a room's persisted data."""
        # Sanitize room_id to prevent path traversal
        safe_id = re.sub(r'[^A-Z0-9]', '', room_id)
        if not safe_id:
            return
        path = self._rooms_dir / f"{safe_id}.json"
        if path.exists():
            path.unlink()

    def load_all(self) -> list[dict]:
        """Load all persisted rooms from disk."""
        rooms = []
        if not self._rooms_dir.exists():
            return rooms
        for path in sorted(self._rooms_dir.glob("*.json")):
            with open(path) as f:
                rooms.append(json.load(f))
        return rooms
