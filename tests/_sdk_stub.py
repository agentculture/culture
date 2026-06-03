"""Shared claude_agent_sdk stub for tests that need it.

Calling :func:`install_claude_sdk_stub` is idempotent — it only installs the
stub if ``claude_agent_sdk`` is not already in ``sys.modules``. Test files
that need a deterministic stub should call this at module top *before*
importing any culture module that imports the SDK (notably
``culture.clients._perm_broker`` and ``culture.clients.claude.agent_runner``).

The stub mirrors the subset of the SDK surface that culture's harnesses
touch. New SDK surface that culture starts importing should be added here.
"""

from __future__ import annotations

import sys
import types
from typing import Any


class _StubAsyncIter:
    def __init__(self, messages: list[Any]) -> None:
        self._iter = iter(messages)

    def __aiter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __anext__(self):  # type: ignore[no-untyped-def]
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def install_claude_sdk_stub() -> None:
    """Install a minimal stub for claude_agent_sdk if not already loaded."""
    if "claude_agent_sdk" in sys.modules:
        return

    mod = types.ModuleType("claude_agent_sdk")

    class _Base:
        pass

    class AssistantMessage(_Base):
        def __init__(self, model: str = "stub-model", content: list[Any] | None = None) -> None:
            self.model = model
            self.content = content or []

    class ResultMessage(_Base):
        # Signature mirrors claude_agent_sdk 0.2.x ResultMessage — defaults
        # are stub-friendly so tests can construct via kwargs without
        # caring about the dataclass field-order changes between SDK
        # versions.
        def __init__(
            self,
            subtype: str = "success",
            duration_ms: int = 0,
            duration_api_ms: int = 0,
            is_error: bool = False,
            num_turns: int = 1,
            session_id: str = "sid-1",
            stop_reason: str | None = None,
            total_cost_usd: float | None = None,
            usage: Any = None,
            result: str | None = "",
            structured_output: Any = None,
        ) -> None:
            self.subtype = subtype
            self.duration_ms = duration_ms
            self.duration_api_ms = duration_api_ms
            self.is_error = is_error
            self.num_turns = num_turns
            self.session_id = session_id
            self.stop_reason = stop_reason
            self.total_cost_usd = total_cost_usd
            self.usage = usage
            self.result = result
            self.structured_output = structured_output

    class ClaudeAgentOptions(_Base):
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class TextBlock(_Base):
        pass

    class ThinkingBlock(_Base):
        pass

    class ToolUseBlock(_Base):
        pass

    class ToolResultBlock(_Base):
        pass

    class PermissionResultAllow(_Base):
        def __init__(
            self,
            behavior: str = "allow",
            updated_input: Any = None,
            updated_permissions: Any = None,
        ) -> None:
            self.behavior = behavior
            self.updated_input = updated_input
            self.updated_permissions = updated_permissions

    class PermissionResultDeny(_Base):
        def __init__(
            self,
            behavior: str = "deny",
            message: str = "",
            interrupt: bool = False,
        ) -> None:
            self.behavior = behavior
            self.message = message
            self.interrupt = interrupt

    class ToolPermissionContext(_Base):
        def __init__(self, signal: Any = None, suggestions: list[Any] | None = None) -> None:
            self.signal = signal
            self.suggestions = suggestions or []

    class HookMatcher(_Base):
        def __init__(
            self,
            matcher: Any = None,
            hooks: list[Any] | None = None,
            timeout: float | None = None,
        ) -> None:
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    def query(**kwargs: Any) -> _StubAsyncIter:  # noqa: ARG001
        return _StubAsyncIter([])

    mod.AssistantMessage = AssistantMessage
    mod.ResultMessage = ResultMessage
    mod.ClaudeAgentOptions = ClaudeAgentOptions
    mod.TextBlock = TextBlock
    mod.ThinkingBlock = ThinkingBlock
    mod.ToolUseBlock = ToolUseBlock
    mod.ToolResultBlock = ToolResultBlock
    mod.PermissionResultAllow = PermissionResultAllow
    mod.PermissionResultDeny = PermissionResultDeny
    mod.ToolPermissionContext = ToolPermissionContext
    mod.HookMatcher = HookMatcher
    mod.query = query

    sys.modules["claude_agent_sdk"] = mod
