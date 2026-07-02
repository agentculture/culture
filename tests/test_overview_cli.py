"""Tests for overview CLI subcommand argument parsing and error handling."""

import subprocess
import sys
from unittest.mock import patch

import pytest


def test_overview_help():
    """The overview subcommand is registered and has help."""
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "mesh", "overview", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--room" in result.stdout
    assert "--agent" in result.stdout
    assert "--messages" in result.stdout
    assert "--serve" in result.stdout
    assert "--refresh" in result.stdout


def test_overview_default_args():
    """Default args parse correctly."""
    from culture_core.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["mesh", "overview"])
    assert args.command == "mesh"
    assert args.mesh_command == "overview"
    assert args.room is None
    assert args.agent is None
    assert args.messages == 4
    assert args.serve is False
    assert args.refresh == 5


def test_overview_with_flags():
    from culture_core.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["mesh", "overview", "--room", "#general", "--messages", "10"])
    assert args.room == "#general"
    assert args.messages == 10


def test_overview_connection_refused(capsys):
    """ConnectionRefusedError produces a helpful message."""
    from culture_core.cli import _build_parser
    from culture_core.cli._errors import CultureError
    from culture_core.cli.mesh import _cmd_overview

    parser = _build_parser()
    args = parser.parse_args(["mesh", "overview"])

    with patch(
        "culture_core.overview.collector.collect_mesh_state",
        side_effect=ConnectionRefusedError(111, "Connection refused"),
    ):
        with pytest.raises(CultureError) as exc_info:
            _cmd_overview(args)

    assert exc_info.value.code == 1
    assert "is the server running?" in exc_info.value.message
    assert "culture server start" in exc_info.value.remediation


def test_overview_timeout(capsys):
    """TimeoutError produces a helpful message."""
    from culture_core.cli import _build_parser
    from culture_core.cli._errors import CultureError
    from culture_core.cli.mesh import _cmd_overview

    parser = _build_parser()
    args = parser.parse_args(["mesh", "overview"])

    with patch(
        "culture_core.overview.collector.collect_mesh_state",
        side_effect=TimeoutError("Registration timed out"),
    ):
        with pytest.raises(CultureError) as exc_info:
            _cmd_overview(args)

    assert exc_info.value.code == 1
    assert "not responding" in exc_info.value.message
    assert "still be starting up" in exc_info.value.message


def test_overview_os_error(capsys):
    """OSError shows the original error details."""
    from culture_core.cli import _build_parser
    from culture_core.cli._errors import CultureError
    from culture_core.cli.mesh import _cmd_overview

    parser = _build_parser()
    args = parser.parse_args(["mesh", "overview"])

    with patch(
        "culture_core.overview.collector.collect_mesh_state",
        side_effect=OSError("Name or service not known"),
    ):
        with pytest.raises(CultureError) as exc_info:
            _cmd_overview(args)

    assert exc_info.value.code == 1
    assert "Name or service not known" in exc_info.value.message


@pytest.mark.asyncio
async def test_connect_timeout_has_message():
    """_connect raises TimeoutError with a non-empty message on registration timeout."""
    import asyncio

    from culture_core.overview.collector import _connect

    # TCP server that accepts but never sends IRC 001 (silent handshake)
    stop = asyncio.Event()

    async def hold_open(reader, writer):
        await stop.wait()
        writer.close()

    server = await asyncio.start_server(hold_open, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    try:
        with pytest.raises(TimeoutError, match="Registration timed out"):
            import culture_core.overview.collector as col

            original = col.REGISTER_TIMEOUT
            col.REGISTER_TIMEOUT = 0.5
            try:
                await _connect("127.0.0.1", port, "test")
            finally:
                col.REGISTER_TIMEOUT = original
    finally:
        stop.set()
        server.close()
        await server.wait_closed()
