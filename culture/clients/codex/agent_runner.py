"""Codex agent runner — manages codex app-server via JSON-RPC over stdio."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from opentelemetry import trace as _otel_trace

from culture.aio import maybe_await
from culture.clients.codex.telemetry import _HARNESS_TRACER_NAME, record_llm_call

if TYPE_CHECKING:
    from culture.clients.codex.telemetry import HarnessMetricsRegistry

logger = logging.getLogger(__name__)


class CodexAgentRunner:
    """Manages a Codex app-server session for the culture daemon."""

    def __init__(
        self,
        model: str,
        directory: str,
        system_prompt: str = "",
        on_exit: Callable[[int], Awaitable[None]] | None = None,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_turn_error: Callable[[], Awaitable[None] | None] | None = None,
        metrics: HarnessMetricsRegistry | None = None,
        nick: str = "",
        turn_timeout_seconds: float = 600.0,
    ) -> None:
        self.model = model
        self.directory = directory
        self.system_prompt = system_prompt
        self.on_exit = on_exit
        self.on_message = on_message
        self.on_turn_error = on_turn_error
        self._metrics = metrics
        self._nick = nick
        # Outer safety net for the whole turn (request + completion
        # event). Replaces the previous hardcoded 300s on the event
        # wait alone. Non-positive disables the wrap.
        self._turn_timeout = turn_timeout_seconds

        self._isolated_home: str | None = None
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
        self._turn_done: asyncio.Event = asyncio.Event()
        self._turn_done.set()  # Initially not busy

    def is_running(self) -> bool:
        return self._running

    @property
    def session_id(self) -> str | None:
        return self._thread_id

    async def start(self, initial_prompt: str = "") -> None:
        """Start codex app-server as a subprocess and initialize a thread."""
        self._stopping = False

        # Isolate data/state dirs to prevent session interference, but
        # preserve HOME and XDG_CONFIG_HOME so codex finds auth tokens.
        self._isolated_home = tempfile.mkdtemp(prefix="culture-codex-")
        isolated_env = dict(os.environ)
        isolated_env["XDG_DATA_HOME"] = os.path.join(self._isolated_home, ".local", "share")
        isolated_env["XDG_STATE_HOME"] = os.path.join(self._isolated_home, ".local", "state")

        try:
            # Spawn codex app-server in stdio mode
            self._process = await asyncio.create_subprocess_exec(
                "codex",
                "app-server",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=isolated_env,
            )

            # Start reading responses
            self._reader_task = asyncio.create_task(self._read_loop())

            # Initialize
            resp = await self._send_request(
                "initialize",
                {
                    "clientInfo": {"name": "culture-codex", "version": "0.1.0"},
                },
            )
            logger.info("Codex initialized: %s", resp)

            # Start a thread
            resp = await self._send_request(
                "thread/start",
                {
                    "cwd": self.directory,
                    "model": self.model,
                    "approvalPolicy": "never",
                    "baseInstructions": self.system_prompt or None,
                },
            )

            logger.info("Codex thread/start raw response: %s", json.dumps(resp)[:500])
            thread = resp.get("result", {}).get("thread", {})
            self._thread_id = thread.get("id")
            self._running = True
            logger.info("Codex thread started: %s", self._thread_id)

            # Start the prompt processing loop
            self._task = asyncio.create_task(self._prompt_loop())

            if initial_prompt:
                await self.send_prompt(initial_prompt)
        except Exception:
            shutil.rmtree(self._isolated_home, ignore_errors=True)
            self._isolated_home = None
            raise

    async def _cancel_task(self, task: asyncio.Task | None) -> None:
        """Cancel a task and await its completion."""
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _terminate_process(self) -> None:
        """Terminate the subprocess, escalating to kill on timeout."""
        if not self._process:
            return
        try:
            self._process.terminate()
            async with asyncio.timeout(5):
                await self._process.wait()
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                self._process.kill()
            except ProcessLookupError:
                pass

    def _fail_pending_requests(self, reason: str) -> None:
        """Fail all pending futures with a ConnectionError and clear them."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(reason))
        self._pending.clear()

    async def stop(self) -> None:
        """Stop the codex app-server."""
        self._stopping = True
        self._running = False

        await self._cancel_task(self._task)
        await self._cancel_task(self._reader_task)
        await self._terminate_process()
        self._fail_pending_requests("Runner stopped")

        if self._isolated_home:
            shutil.rmtree(self._isolated_home, ignore_errors=True)
            self._isolated_home = None

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

        try:
            async with asyncio.timeout(30):
                return await future
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._pending.pop(req_id, None)
            if not future.done():
                future.cancel()
            raise

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

    def _dispatch_jsonrpc_message(self, msg: dict) -> bool:
        """Route a JSON-RPC response to its pending future.

        Returns True if the message was a response (handled here),
        False if it should be treated as a notification.
        """
        if "id" in msg and ("result" in msg or "error" in msg):
            req_id = msg["id"]
            future = self._pending.pop(req_id, None)
            if future and not future.done():
                future.set_result(msg)
            return True
        return False

    async def _await_process_exit(self) -> int:
        """Wait for the process to exit, killing if it takes too long."""
        if not self._process:
            return -1
        try:
            async with asyncio.timeout(5):
                return await self._process.wait()
        except asyncio.TimeoutError:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(1):
                    return await self._process.wait()
            except asyncio.TimeoutError:
                return -1

    async def _cleanup_codex_process(self) -> None:
        """Wait for process exit, fail pending futures, fire on_exit."""
        returncode = await self._await_process_exit()
        self._fail_pending_requests("Process exited")
        self._running = False

        if not self._stopping and self.on_exit:
            await self.on_exit(returncode)

    async def _process_stdout_lines(self) -> None:
        """Read and dispatch JSON-RPC messages from stdout until EOF."""
        assert self._process and self._process.stdout
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode())
            except json.JSONDecodeError:
                continue

            if not self._dispatch_jsonrpc_message(msg) and "method" in msg:
                await self._handle_notification(msg)

    async def _read_loop(self) -> None:
        """Read JSON-RPC messages from stdout."""
        if not self._process or not self._process.stdout:
            return
        try:
            await self._process_stdout_lines()
        except asyncio.CancelledError:
            raise
        except ConnectionError:
            pass
        except Exception:
            logger.exception("Codex read loop error")
        finally:
            await self._cleanup_codex_process()

    _APPROVAL_METHODS = frozenset(
        {
            "exec_approval_request",
            "file_change_approval_request",
            "patch_apply_approval_request",
        }
    )

    async def _handle_notification(self, msg: dict) -> None:
        """Handle server notifications."""
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "turn/started":
            self._busy = True
            self._accumulated_text = ""

        elif method == "item/agentMessage/delta":
            delta = params.get("delta", "")
            self._accumulated_text += delta

        elif method == "turn/completed":
            self._busy = False
            await self._flush_accumulated_text()
            self._turn_done.set()

        elif method in self._APPROVAL_METHODS:
            await self._auto_approve(msg)

        elif method == "error":
            logger.error("Codex error: %s", params)
            self._busy = False
            self._turn_done.set()
            if self.on_turn_error:
                await maybe_await(self.on_turn_error())

    async def _flush_accumulated_text(self) -> None:
        """Fire on_message with any accumulated text and reset the buffer."""
        if self.on_message and self._accumulated_text:
            msg_dict = {
                "type": "assistant",
                "model": self.model,
                "content": [{"type": "text", "text": self._accumulated_text}],
            }
            await self.on_message(msg_dict)
        self._accumulated_text = ""

    async def _auto_approve(self, msg: dict) -> None:
        """Auto-approve a permission request from the Codex process."""
        req_id = msg.get("id")
        if req_id is not None:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"approved": True}}
            if self._process and self._process.stdin:
                line = json.dumps(resp) + "\n"
                self._process.stdin.write(line.encode())
                await self._process.stdin.drain()

    async def _execute_single_turn(self, text: str) -> None:
        """Send one turn request and wait for completion."""
        start_perf = time.perf_counter()
        outcome = "success"
        tracer = _otel_trace.get_tracer(_HARNESS_TRACER_NAME)
        with tracer.start_as_current_span(
            "harness.llm.call",
            attributes={
                "harness.backend": "codex",
                "harness.model": self.model,
                "harness.nick": self._nick,
            },
        ):
            self._turn_done.clear()
            try:
                # Outer wrap covers both _send_request and the
                # turn/completed event wait, so a wedged request also
                # times out (previously only the event wait was bounded).
                if self._turn_timeout > 0:
                    async with asyncio.timeout(self._turn_timeout):
                        await self._send_request(
                            "turn/start",
                            {
                                "threadId": self._thread_id,
                                "input": [{"type": "text", "text": text}],
                            },
                        )
                        await self._turn_done.wait()
                else:
                    await self._send_request(
                        "turn/start",
                        {
                            "threadId": self._thread_id,
                            "input": [{"type": "text", "text": text}],
                        },
                    )
                    await self._turn_done.wait()
            except asyncio.TimeoutError:
                outcome = "timeout"
                # Either the inner _send_request 30 s or the outer
                # turn_timeout fired — log both budgets so the cause is
                # diagnosable from the journal alone.
                logger.warning(
                    "Codex turn timed out (inner request budget 30s, outer "
                    "turn_timeout_seconds=%ss); terminating subprocess so "
                    "cleanup → on_exit fires for crash recovery",
                    self._turn_timeout,
                )
                self._turn_done.set()
                if self.on_turn_error:
                    await maybe_await(self.on_turn_error())
                # Terminate the wedged subprocess. The read-loop sees
                # EOF, _cleanup_codex_process runs, and the daemon's
                # _on_agent_exit schedules _delayed_restart. Without
                # this, on_exit is never called and the agent stays
                # stuck.
                if self._process is not None and self._process.returncode is None:
                    try:
                        self._process.terminate()
                    except ProcessLookupError:
                        pass
            except Exception:
                outcome = "error"
                logger.exception("Codex turn error")
                self._turn_done.set()
                if self.on_turn_error:
                    await maybe_await(self.on_turn_error())
        duration_ms = (time.perf_counter() - start_perf) * 1000.0
        if self._metrics is not None:
            # Codex token usage tracking — issue #298 (currently usage=None;
            # token counts are not exposed by the codex app-server turn/completed
            # notification in the current SDK version).
            record_llm_call(
                self._metrics,
                backend="codex",
                model=self.model,
                nick=self._nick,
                usage=None,
                duration_ms=duration_ms,
                outcome=outcome,
            )

    async def _prompt_loop(self) -> None:
        """Process queued prompts one at a time."""
        try:
            while self._running:
                text = await self._prompt_queue.get()
                if not self._running or not self._thread_id:
                    break
                await self._execute_single_turn(text)
        except asyncio.CancelledError:
            raise
