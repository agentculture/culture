"""NT-2 — system prompts must not instruct agents to poll IRC.

The 2026-06-03 mesh re-architecture replaces the daemon's IRC-polling
loop with a push-style transport: the daemon delivers inbound traffic
via callbacks, not by having the LLM repeatedly call ``irc_read``.
The default ``_build_system_prompt`` for each of the four backends
(claude, codex, copilot, acp) MUST NOT mention "periodically", "check
IRC", or "irc_read" — otherwise the model continues to think it needs
to poll and burns context (the v8.x-era footgun this PR closes).

Backend coverage:

- **claude** is exercised via the live constructor + method call (the
  full path the daemon takes at runtime).
- **codex / copilot / acp** are validated via static source inspection
  of ``_build_system_prompt`` (``inspect.getsource``). Static inspection
  is the right tool for these three because they share a forked-copy
  pattern (CLAUDE.md "cite, don't import"): a missing helper import in
  any one of them prevents the live constructor path from being
  testable in isolation, but the user-visible default-prompt string
  literally lives in the function body. The polling-substring contract
  (the assertion this test enforces) is a pure-text property of the
  function source — running the function adds no signal beyond what the
  source itself shows.
"""

from __future__ import annotations

import inspect
import tempfile

import pytest

from tests._sdk_stub import install_claude_sdk_stub

install_claude_sdk_stub()


# The substrings that — if present in a default system prompt — indicate
# the model is being told to poll IRC. Match is case-insensitive so a
# stray "Periodically" can't sneak through.
_FORBIDDEN = ("periodically", "check irc", "irc_read")


def _assert_no_polling(text: str, backend: str) -> None:
    lower = text.lower()
    for needle in _FORBIDDEN:
        assert needle not in lower, (
            f"{backend} default system prompt contains forbidden polling instruction "
            f"{needle!r} — push transport replaces polling; remove from _build_system_prompt"
        )


@pytest.fixture
def sock_dir():
    return tempfile.mkdtemp()


# ---------------------------------------------------------------------------
# Claude — live function-call coverage
# ---------------------------------------------------------------------------


def test_claude_default_system_prompt_has_no_polling(sock_dir) -> None:
    from culture.clients.claude.config import AgentConfig, DaemonConfig
    from culture.clients.claude.daemon import AgentDaemon

    config = DaemonConfig()
    agent = AgentConfig(nick="testserv-claude", directory="/tmp", channels=["#general"])
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    prompt = daemon._build_system_prompt()
    _assert_no_polling(prompt, "claude")


# ---------------------------------------------------------------------------
# Codex / Copilot / ACP — static source inspection.
# The default-prompt literal lives in the function body; inspecting the
# source is sufficient to enforce the no-polling contract without
# depending on auxiliary helper modules.
# ---------------------------------------------------------------------------


def test_codex_default_system_prompt_has_no_polling() -> None:
    from culture.clients.codex.daemon import CodexDaemon

    src = inspect.getsource(CodexDaemon._build_system_prompt)
    _assert_no_polling(src, "codex")


def test_copilot_default_system_prompt_has_no_polling() -> None:
    from culture.clients.copilot.daemon import CopilotDaemon

    src = inspect.getsource(CopilotDaemon._build_system_prompt)
    _assert_no_polling(src, "copilot")


def test_acp_default_system_prompt_has_no_polling() -> None:
    from culture.clients.acp.daemon import ACPDaemon

    src = inspect.getsource(ACPDaemon._build_system_prompt)
    _assert_no_polling(src, "acp")
