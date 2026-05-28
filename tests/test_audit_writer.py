"""Tests for the per-helper JSONL audit log."""

from __future__ import annotations

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()

import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402

from culture.clients._audit import AuditWriter, audit_path_for  # noqa: E402


@pytest.fixture
def culture_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CULTURE_HOME", str(tmp_path))
    return tmp_path


class TestAuditWriter:
    def test_path_under_culture_home(self, culture_root):
        assert audit_path_for("local-foo").startswith(str(culture_root))

    @pytest.mark.asyncio
    async def test_write_assistant_message_appends_line(self, culture_root):
        writer = AuditWriter(nick="local-foo")
        msg = {
            "type": "assistant",
            "model": "claude-opus-4-7",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ],
        }
        await writer.write(msg)

        with open(writer.path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "assistant"
        assert record["nick"] == "local-foo"
        assert record["model"] == "claude-opus-4-7"
        assert record["text"] == "Hello"
        assert record["tool_uses"][0]["name"] == "Bash"
        assert record["tool_uses"][0]["input_digest"].startswith("sha256:")
        assert record["ts"].endswith("Z")  # ISO8601 UTC

    @pytest.mark.asyncio
    async def test_non_assistant_messages_are_skipped(self, culture_root):
        writer = AuditWriter(nick="local-foo")
        await writer.write({"type": "user", "content": [{"type": "text", "text": "hi"}]})
        await writer.write({"type": "result", "session_id": "x"})
        assert not os.path.exists(writer.path)

    @pytest.mark.asyncio
    async def test_multiple_writes_preserve_order(self, culture_root):
        writer = AuditWriter(nick="local-foo")
        for i in range(5):
            await writer.write(
                {
                    "type": "assistant",
                    "model": "m",
                    "content": [{"type": "text", "text": f"line-{i}"}],
                }
            )
        with open(writer.path) as f:
            lines = [json.loads(line) for line in f.readlines()]
        assert [record["text"] for record in lines] == [f"line-{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_concurrent_writes_serialize(self, culture_root):
        writer = AuditWriter(nick="local-foo")

        async def write_one(i: int) -> None:
            await writer.write(
                {
                    "type": "assistant",
                    "model": "m",
                    "content": [{"type": "text", "text": f"line-{i:02d}"}],
                }
            )

        await asyncio.gather(*(write_one(i) for i in range(20)))

        with open(writer.path) as f:
            lines = f.readlines()
        # All 20 lines must be present and individually valid JSON.
        assert len(lines) == 20
        for line in lines:
            json.loads(line)

    @pytest.mark.asyncio
    async def test_tool_result_preview_truncated(self, culture_root):
        writer = AuditWriter(nick="local-foo")
        big = "x" * 1000
        await writer.write(
            {
                "type": "assistant",
                "model": "m",
                "content": [
                    {"type": "tool_result", "name": "Bash", "content": big},
                ],
            }
        )
        with open(writer.path) as f:
            record = json.loads(f.readlines()[0])
        preview = record["tool_results"][0]["preview"]
        assert len(preview) <= 200
        assert preview.startswith("x")

    @pytest.mark.asyncio
    async def test_empty_nick_rejected(self, culture_root):
        with pytest.raises(ValueError):
            AuditWriter(nick="")
