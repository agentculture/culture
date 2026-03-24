"""Codex agent runner — wraps codex app-server over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import subprocess
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class CodexAgentRunner:
    """Manages a Codex app-server session for the agentirc daemon."""

    def __init__(
        self,
        model: str,
        directory: str,
        system_prompt: str = "",
        on_exit: Callable[[int], Awaitable[None]] | None = None,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.model = model
        self.directory = directory
        self.system_prompt = system_prompt
        self.on_exit = on_exit
        self.on_message = on_message

        self._process: asyncio.subprocess.Process | None = None
        self._thread_id: str | None = None
        self._running = False
        self._busy = False
        self._prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._stopping = False
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._accumulated_text = ""

    def is_running(self) -> bool:
        return self._running

    @property
    def session_id(self) -> str | None:
        return self._thread_id

    async def start(self, initial_prompt: str = "") -> None:
        """Start codex app-server as a subprocess and initialize a thread."""
        self._stopping = False

        # Spawn codex app-server in stdio mode
        self._process = await asyncio.create_subprocess_exec(
            "codex", "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Start reading responses
        self._reader_task = asyncio.create_task(self._read_loop())

        # Initialize
        resp = await self._send_request("initialize", {
            "clientInfo": {"name": "agentirc-codex", "version": "0.1.0"},
        })
        logger.info("Codex initialized: %s", resp)

        # Start a thread
        resp = await self._send_request("thread/start", {
            "cwd": self.directory,
            "model": self.model,
            "approvalPolicy": {"mode": "auto-edit"},
            "baseInstructions": self.system_prompt or None,
        })

        thread = resp.get("result", {}).get("thread", {})
        self._thread_id = thread.get("id")
        self._running = True
        logger.info("Codex thread started: %s", self._thread_id)

        # Start the prompt processing loop
        self._task = asyncio.create_task(self._prompt_loop())

        if initial_prompt:
            await self.send_prompt(initial_prompt)

    async def stop(self) -> None:
        """Stop the codex app-server."""
        self._stopping = True
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass

        # Fail any pending requests
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("Runner stopped"))
        self._pending.clear()

        if self.on_exit and not self._stopping:
            await self.on_exit(0)

    async def send_prompt(self, text: str) -> None:
        """Queue a prompt for the agent."""
        await self._prompt_queue.put(text)

    # ------------------------------------------------------------------
    # Internal: JSON-RPC over stdio
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        if not self._process or not self._process.stdin:
            raise ConnectionError("App server not running")

        req_id = self._next_id()
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending[req_id] = future

        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        return await asyncio.wait_for(future, timeout=30)

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        """Read JSON-RPC messages from stdout."""
        if not self._process or not self._process.stdout:
            return
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue

                # Response to a request
                if "id" in msg and ("result" in msg or "error" in msg):
                    req_id = msg["id"]
                    future = self._pending.pop(req_id, None)
                    if future and not future.done():
                        future.set_result(msg)

                # Notification from server
                elif "method" in msg:
                    await self._handle_notification(msg)

        except (asyncio.CancelledError, ConnectionError):
            pass
        except Exception:
            logger.exception("Codex read loop error")
            if not self._stopping and self.on_exit:
                await self.on_exit(1)

    async def _handle_notification(self, msg: dict) -> None:
        """Handle server notifications."""
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "turn/started":
            self._busy = True
            self._accumulated_text = ""

        elif method == "item/agentMessage/delta":
            # Accumulate streaming text
            delta = params.get("delta", "")
            self._accumulated_text += delta

        elif method == "turn/completed":
            self._busy = False
            # Fire on_message with accumulated text
            if self.on_message and self._accumulated_text:
                msg_dict = {
                    "type": "assistant",
                    "model": self.model,
                    "content": [{"type": "text", "text": self._accumulated_text}],
                }
                await self.on_message(msg_dict)
            self._accumulated_text = ""

        elif method == "exec_approval_request":
            # Auto-approve command execution
            req_id = msg.get("id")
            if req_id is not None:
                resp = {"jsonrpc": "2.0", "id": req_id, "result": {"approved": True}}
                if self._process and self._process.stdin:
                    line = json.dumps(resp) + "\n"
                    self._process.stdin.write(line.encode())
                    await self._process.stdin.drain()

        elif method == "file_change_approval_request":
            # Auto-approve file changes
            req_id = msg.get("id")
            if req_id is not None:
                resp = {"jsonrpc": "2.0", "id": req_id, "result": {"approved": True}}
                if self._process and self._process.stdin:
                    line = json.dumps(resp) + "\n"
                    self._process.stdin.write(line.encode())
                    await self._process.stdin.drain()

        elif method == "patch_apply_approval_request":
            # Auto-approve patches
            req_id = msg.get("id")
            if req_id is not None:
                resp = {"jsonrpc": "2.0", "id": req_id, "result": {"approved": True}}
                if self._process and self._process.stdin:
                    line = json.dumps(resp) + "\n"
                    self._process.stdin.write(line.encode())
                    await self._process.stdin.drain()

        elif method == "error":
            logger.error("Codex error: %s", params)

    async def _prompt_loop(self) -> None:
        """Process queued prompts one at a time."""
        try:
            while self._running:
                text = await self._prompt_queue.get()
                if not self._running or not self._thread_id:
                    break

                try:
                    await self._send_request("turn/start", {
                        "threadId": self._thread_id,
                        "input": [{"type": "text", "text": text}],
                    })

                    # Wait for turn to complete
                    while self._busy:
                        await asyncio.sleep(0.1)

                except Exception:
                    logger.exception("Codex turn error")

        except asyncio.CancelledError:
            pass
