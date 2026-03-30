"""Tests for room persistence (disk serialization)."""
import json
import os
import tempfile

import pytest


def test_room_store_save_and_load():
    """Rooms can be saved to disk and loaded back."""
    from agentirc.server.room_store import RoomStore
    from agentirc.server.channel import Channel

    with tempfile.TemporaryDirectory() as tmpdir:
        store = RoomStore(tmpdir)

        ch = Channel("#pyhelp")
        ch.room_id = "R7K2M9"
        ch.creator = "spark-ori"
        ch.owner = "spark-ori"
        ch.purpose = "Python help"
        ch.instructions = "Be helpful; share examples"
        ch.tags = ["python", "code-help"]
        ch.persistent = True
        ch.agent_limit = 8
        ch.extra_meta = {"language": "python"}
        ch.created_at = 1774852147.0

        store.save(ch)

        assert os.path.exists(os.path.join(tmpdir, "rooms", "R7K2M9.json"))

        loaded = store.load_all()
        assert len(loaded) == 1
        r = loaded[0]
        assert r["room_id"] == "R7K2M9"
        assert r["name"] == "#pyhelp"
        assert r["creator"] == "spark-ori"
        assert r["owner"] == "spark-ori"
        assert r["purpose"] == "Python help"
        assert r["instructions"] == "Be helpful; share examples"
        assert r["tags"] == ["python", "code-help"]
        assert r["persistent"] is True
        assert r["agent_limit"] == 8
        assert r["extra_meta"] == {"language": "python"}
        assert r["created_at"] == 1774852147.0


def test_room_store_delete():
    """Rooms can be deleted from store."""
    from agentirc.server.room_store import RoomStore
    from agentirc.server.channel import Channel

    with tempfile.TemporaryDirectory() as tmpdir:
        store = RoomStore(tmpdir)

        ch = Channel("#pyhelp")
        ch.room_id = "R7K2M9"
        ch.persistent = True
        ch.created_at = 1774852147.0
        store.save(ch)

        assert os.path.exists(os.path.join(tmpdir, "rooms", "R7K2M9.json"))

        store.delete("R7K2M9")
        assert not os.path.exists(os.path.join(tmpdir, "rooms", "R7K2M9.json"))


def test_room_store_load_empty_dir():
    """Loading from empty dir returns empty list."""
    from agentirc.server.room_store import RoomStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = RoomStore(tmpdir)
        assert store.load_all() == []
