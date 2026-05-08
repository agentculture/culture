"""ACP agent runner — manages any ACP-compatible agent via JSON-RPC over stdio.

Supports any agent that implements the Agent Client Protocol (ACP), such as
Cline (cline --acp), OpenCode (opencode acp), and others.
"""

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
from culture.clients.acp import constants as _C
from culture.clients.acp.telemetry import _HARNESS_TRACER_NAME, record_llm_call

if TYPE_CHECKING:
    from culture.clients.acp.telemetry import HarnessMetricsRegistry

logger = logging.getLogger(__name__)


class ACPAgentRunner:
    """Manages an ACP session for the culture daemon.

    Works with any ACP-compatible agent by configuring the spawn command
    via the ``acp_command`` parameter (e.g. ``["cline", "--acp"]`` or
    ``["opencode", "acp"]``).
    """

    def __init__(
        self,
        model: str,
        directory: str,
        acp_command: list[str] | None = None,
        system_prompt: str = "",
        on_exit: Callable[[int], Awaitable[None]] | None = None,
        on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_turn_error: Callable[[], Awaitable[None] | None] | None = None,
        metrics: HarnessMetricsRegistry | None = None,
        nick: str = "",
        turn_timeout_seconds: float = _C.DEFAULT_TURN_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model
        self.directory = directory
        self.acp_command = acp_command or ["opencode", "acp"]
        self.system_prompt = system_prompt
        self.on_exit = on_exit
        self.on_message = on_message
        self.on_turn_error = on_turn_error
        self._metrics = metrics
        self._nick = nick
        # Outer safety net for the whole prompt round-trip (send +
        # busy-poll). The inner _C.INNER_REQUEST_TIMEOUT_SECONDS on
        # _send_request stays — it bounds individual JSON-RPC
        # requests; this fires if the busy-flag never clears (the
        # failure mode that motivated issue #349).
        self._turn_timeout = turn_timeout_seconds

        self._isolated_home: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._session_id: str | None = None
        self._running = False
        self._busy = False
        self._prompt_queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stopping = False
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._accumulated_text = ""

    def is_running(self) -> bool:
        return self._running

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def _initialize_acp_session(self, cmd_label: str) -> None:
        """Run ACP protocol negotiation: initialize, log auth methods, create session."""
        # Initialize with ACP protocol
        resp = await self._send_request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
                "clientInfo": {"name": "culture-acp", "version": "0.1.0"},
            },
        )
        logger.info("ACP initialized (%s): %s", cmd_label, resp)

        # Log available auth methods as a hint
        init_result = resp.get("result", {})
        auth_methods = init_result.get("authMethods", [])
        descriptions: list[str] = []
        if auth_methods:
            descriptions = [m.get("description", m.get("id", "unknown")) for m in auth_methods]
            logger.warning(
                "ACP agent (%s) reports auth methods: %s. "
                "If prompts fail, configure auth tokens.",
                cmd_label,
                ", ".join(descriptions),
            )

        # Create a session with model selection
        session_params: dict = {
            "cwd": self.directory,
            "mcpServers": [],
        }
        if self.model:
            session_params["model"] = self.model
        resp = await self._send_request("session/new", session_params)

        logger.info("ACP session/new raw response: %s", json.dumps(resp)[:500])
        result = resp.get("result", {})
        self._session_id = result.get("sessionId")
        if not self._session_id:
            # Session creation failed — likely auth or model issue
            error = resp.get("error", {})
            error_msg = error.get("message", "unknown error")
            if descriptions:
                error_msg += f". Auth may be required: {', '.join(descriptions)}"
            raise RuntimeError(f"ACP agent ({cmd_label}) session creation failed: {error_msg}")
        self._running = True
        logger.info("ACP session started (%s): %s", cmd_label, self._session_id)

    async def start(self, initial_prompt: str = "") -> None:
        """Start the ACP agent as a subprocess and initialize a session."""
        self._stopping = False

        # Isolate data/state dirs to prevent session interference, but
        # preserve HOME and XDG_CONFIG_HOME so the agent finds auth tokens.
        self._isolated_home = tempfile.mkdtemp(prefix="culture-acp-")
        isolated_env = dict(os.environ)
        isolated_env["XDG_DATA_HOME"] = os.path.join(self._isolated_home, ".local", "share")
        isolated_env["XDG_STATE_HOME"] = os.path.join(self._isolated_home, ".local", "state")

        cmd_label = " ".join(self.acp_command)
        try:
            # Spawn ACP agent in stdio mode
            # Use a large stdout buffer — ACP messages (especially session/new with
            # all available models) can exceed asyncio's default 64KB line limit.
            self._process = await asyncio.create_subprocess_exec(
                *self.acp_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1024 * 1024,  # 1MB line buffer
                env=isolated_env,
            )

            # Start reading responses and stderr
            self._reader_task = asyncio.create_task(self._read_loop())
            self._stderr_task = asyncio.create_task(self._stderr_loop())

            await self._initialize_acp_session(cmd_label)

            # Start the prompt processing loop
            self._task = asyncio.create_task(self._prompt_loop())

            # Queue system prompt as the first turn so all subsequent turns
            # are conditioned on it (ACP has no dedicated system instructions field).
            # Queued rather than awaited to avoid blocking start() on LLM completion.
            if self.system_prompt:
                await self.send_prompt(self.system_prompt)

            if initial_prompt:
                await self.send_prompt(initial_prompt)
        except Exception:
            await self.stop()
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
            async with asyncio.timeout(_C.PROCESS_TERMINATE_GRACE_SECONDS):
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
        """Stop the ACP agent process."""
        self._stopping = True
        self._running = False

        await self._cancel_task(self._task)
        await self._cancel_task(self._reader_task)
        await self._cancel_task(self._stderr_task)
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

    async def _send_request(self, method: str, params: dict, timeout: float = 30) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        if not self._process or not self._process.stdin:
            raise ConnectionError("ACP server not running")

        req_id = self._next_id()
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending[req_id] = future

        line = json.dumps(msg) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        try:
            async with asyncio.timeout(timeout):
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
            async with asyncio.timeout(_C.PROCESS_TERMINATE_GRACE_SECONDS):
                return await self._process.wait()
        except asyncio.TimeoutError:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(_C.PROCESS_KILL_GRACE_SECONDS):
                    return await self._process.wait()
            except asyncio.TimeoutError:
                return -1

    async def _cleanup_process(self) -> None:
        """Wait for process exit, fail pending futures, cancel companion tasks, fire on_exit."""
        returncode = await self._await_process_exit()
        self._fail_pending_requests("Process exited")
        self._running = False

        # Cancel companion tasks so they don't outlive the process
        for task in (self._task, self._stderr_task):
            if task and not task.done():
                task.cancel()

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
            logger.exception("ACP read loop error")
        finally:
            await self._cleanup_process()

    async def _stderr_loop(self) -> None:
        """Log stderr output from the ACP agent process."""
        if not self._process or not self._process.stderr:
            return
        cmd_name = self.acp_command[0]
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.warning("acp[%s] stderr: %s", cmd_name, text)
        except asyncio.CancelledError:
            raise
        except ConnectionError:
            pass

    async def _handle_notification(self, msg: dict) -> None:
        """Handle ACP server notifications."""
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "session/update":
            await self._handle_session_update(params)

        elif method == "session/request_permission":
            await self._auto_approve(msg)

        elif method == "error":
            logger.error("ACP error (%s): %s", self.acp_command[0], params)

    async def _handle_session_update(self, params: dict) -> None:
        """Process a session/update notification."""
        update = params.get("update", params)
        update_type = update.get("sessionUpdate", "")

        if update_type in ("agent_message_chunk", "agent_thought_chunk"):
            self._busy = True
            content = update.get("content", {})
            if update_type == "agent_message_chunk" and content.get("type") == "text":
                self._accumulated_text += content.get("text", "")

        if "stopReason" in update:
            self._busy = False
            await self._flush_accumulated_text()

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
        """Auto-approve a permission request from the ACP process."""
        req_id = msg.get("id")
        if req_id is not None:
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"approved": True}}
            if self._process and self._process.stdin:
                line = json.dumps(resp) + "\n"
                self._process.stdin.write(line.encode())
                await self._process.stdin.drain()

    async def _send_prompt_with_retry(self, text: str) -> dict:
        """Send a session/prompt request, retrying once on TimeoutError."""
        prompt_params = {
            "sessionId": self._session_id,
            "prompt": [{"type": "text", "text": text}],
        }
        try:
            return await self._send_request(
                "session/prompt",
                prompt_params,
                timeout=_C.INNER_REQUEST_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "ACP prompt timed out, retrying once: %s",
                text[:80],
            )
            return await self._send_request(
                "session/prompt",
                prompt_params,
                timeout=_C.INNER_REQUEST_TIMEOUT_SECONDS,
            )

    async def _handle_prompt_result(self, resp: dict) -> None:
        """Process the prompt response and wait for the busy flag to clear."""
        result = resp.get("result", {})
        if "stopReason" in result:
            await self._flush_accumulated_text()
            self._busy = False

        while self._busy and self._running:
            await asyncio.sleep(0.1)

    async def _execute_single_prompt(self, text: str) -> None:
        """Send one prompt and handle its result, managing the busy flag."""
        start_perf = time.perf_counter()
        outcome = "success"
        tracer = _otel_trace.get_tracer(_HARNESS_TRACER_NAME)
        with tracer.start_as_current_span(
            "harness.llm.call",
            attributes={
                "harness.backend": "acp",
                "harness.model": self.model,
                "harness.nick": self._nick,
            },
        ):
            try:
                self._busy = True
                if self._turn_timeout > 0:
                    async with asyncio.timeout(self._turn_timeout):
                        resp = await self._send_prompt_with_retry(text)
                        await self._handle_prompt_result(resp)
                else:
                    resp = await self._send_prompt_with_retry(text)
                    await self._handle_prompt_result(resp)
            except TimeoutError:
                # Both _send_prompt_with_retry's inner-request retry-then-fail
                # and the outer asyncio.timeout above raise TimeoutError
                # (asyncio.TimeoutError is a TimeoutError alias in 3.11+).
                outcome = "timeout"
                logger.exception(
                    "ACP turn timeout (turn_timeout_seconds=%ss); "
                    "terminating subprocess so cleanup → on_exit fires "
                    "for crash recovery",
                    self._turn_timeout,
                )
                if self.on_turn_error:
                    await maybe_await(self.on_turn_error())
                # Terminate the wedged subprocess so the read-loop EOF
                # triggers _cleanup_process → on_exit(returncode) →
                # daemon._on_agent_exit → _delayed_restart. Without
                # this, ACP timeouts never reach crash recovery.
                if self._process is not None and self._process.returncode is None:
                    try:
                        self._process.terminate()
                    except ProcessLookupError:
                        pass
            except Exception:
                outcome = "error"
                logger.exception("ACP turn error")
                if self.on_turn_error:
                    await maybe_await(self.on_turn_error())
            finally:
                self._busy = False
        duration_ms = (time.perf_counter() - start_perf) * 1000.0
        if self._metrics is not None:
            # ACP token usage MAY arrive in session/update stopReason payload;
            # current implementation does not extract — usage=None for v1.
            # When we add extraction (per backing agent), thread through here.
            record_llm_call(
                self._metrics,
                backend="acp",
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
                if not self._running or not self._session_id:
                    break
                await self._execute_single_prompt(text)
        except asyncio.CancelledError:
            raise
