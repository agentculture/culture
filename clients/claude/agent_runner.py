from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


class AgentRunner:
    def __init__(self, command: list[str], directory: str,
                 on_exit: Callable[[int], Awaitable[None]] | None = None,
                 on_stdout: Callable[[str], Awaitable[None]] | None = None):
        self.command = command
        self.directory = directory
        self.on_exit = on_exit
        self.on_stdout = on_stdout
        self._process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.directory,
        )
        self._monitor_task = asyncio.create_task(self._monitor())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        if self.on_stdout:
            self._stdout_task = asyncio.create_task(self._read_stdout_loop())

    async def stop(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
        if self._stdout_task:
            self._stdout_task.cancel()
            try:
                await self._stdout_task
            except asyncio.CancelledError:
                pass

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def write_stdin(self, text: str) -> None:
        if self._process and self._process.stdin:
            self._process.stdin.write(text.encode())
            await self._process.stdin.drain()

    async def read_stdout_line(self) -> str:
        if self._process and self._process.stdout:
            line = await self._process.stdout.readline()
            return line.decode().rstrip("\n")
        return ""

    async def _monitor(self) -> None:
        if not self._process:
            return
        code = await self._process.wait()
        if self.on_exit:
            await self.on_exit(code)

    async def _drain_stderr(self) -> None:
        """Drain stderr to prevent pipe buffer deadlock."""
        try:
            while self._process and self._process.stderr:
                line = await self._process.stderr.readline()
                if not line:
                    break
                logger.debug("agent stderr: %s", line.decode().rstrip("\n"))
        except asyncio.CancelledError:
            return

    async def _read_stdout_loop(self) -> None:
        try:
            while self._process and self._process.stdout:
                line = await self._process.stdout.readline()
                if not line:
                    break
                if self.on_stdout:
                    await self.on_stdout(line.decode().rstrip("\n"))
        except asyncio.CancelledError:
            return
