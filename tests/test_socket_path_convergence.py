"""Regression test for issue #302: all socket-path resolvers must agree.

Before the fix, `culture/cli/shared/constants.py::culture_runtime_dir()`
fell back to `~/.culture/run/` when `XDG_RUNTIME_DIR` was unset, while
the 4 backend daemons and 4 skill irc_clients fell back to `/tmp/`. On
macOS (where XDG is unset by default), the CLI looked in one place and
the daemon listened in another, so `culture channel message` silently
fell through to the anonymous-peek observer.

This test asserts that every site that constructs the daemon socket path
agrees on it, both with `XDG_RUNTIME_DIR` set and unset. If a future PR
re-introduces a raw `os.environ.get("XDG_RUNTIME_DIR", "/tmp")` anywhere,
the parametric test below catches it before merge.
"""

from __future__ import annotations

import pytest

from culture.cli.shared.constants import culture_runtime_dir
from culture.cli.shared.ipc import agent_socket_path
from culture.clients.acp.config import AgentConfig as ACPAgentConfig
from culture.clients.acp.config import DaemonConfig as ACPDaemonConfig
from culture.clients.acp.daemon import ACPDaemon
from culture.clients.acp.skill.irc_client import _sock_path_from_env as acp_sock
from culture.clients.claude.config import AgentConfig as ClaudeAgentConfig
from culture.clients.claude.config import DaemonConfig as ClaudeDaemonConfig
from culture.clients.claude.daemon import AgentDaemon as ClaudeDaemon
from culture.clients.claude.skill.irc_client import _sock_path_from_env as claude_sock
from culture.clients.codex.config import AgentConfig as CodexAgentConfig
from culture.clients.codex.config import DaemonConfig as CodexDaemonConfig
from culture.clients.codex.daemon import CodexDaemon
from culture.clients.codex.skill.irc_client import _sock_path_from_env as codex_sock
from culture.clients.copilot.config import AgentConfig as CopilotAgentConfig
from culture.clients.copilot.config import DaemonConfig as CopilotDaemonConfig
from culture.clients.copilot.daemon import CopilotDaemon
from culture.clients.copilot.skill.irc_client import _sock_path_from_env as copilot_sock

NICK = "testserver-testagent"

DAEMON_FACTORIES = [
    (
        "claude",
        lambda: ClaudeDaemon(
            ClaudeDaemonConfig(),
            ClaudeAgentConfig(nick=NICK, directory="/tmp", channels=[]),
            socket_dir=None,
            skip_claude=True,
        ),
    ),
    (
        "codex",
        lambda: CodexDaemon(
            CodexDaemonConfig(),
            CodexAgentConfig(nick=NICK, directory="/tmp", channels=[]),
            socket_dir=None,
            skip_codex=True,
        ),
    ),
    (
        "copilot",
        lambda: CopilotDaemon(
            CopilotDaemonConfig(),
            CopilotAgentConfig(nick=NICK, directory="/tmp", channels=[]),
            socket_dir=None,
            skip_copilot=True,
        ),
    ),
    (
        "acp",
        lambda: ACPDaemon(
            ACPDaemonConfig(),
            ACPAgentConfig(nick=NICK, directory="/tmp", channels=[]),
            socket_dir=None,
            skip_agent=True,
        ),
    ),
]

SKILL_RESOLVERS = [
    ("claude", claude_sock),
    ("codex", codex_sock),
    ("copilot", copilot_sock),
    ("acp", acp_sock),
]


@pytest.mark.parametrize("xdg_set", [True, False], ids=["xdg-set", "xdg-unset-macos"])
def test_all_resolvers_agree_with_cli(monkeypatch, tmp_path, xdg_set):
    """Daemon, skill, and CLI must all build the same socket path for a nick.

    Both XDG modes are exercised:
      - xdg-set: Linux/systemd default — XDG_RUNTIME_DIR points at /run/user/<uid>
      - xdg-unset-macos: macOS default — env var is missing, so the resolver
        must fall back to ~/.culture/run/ (the CLI's choice). Issue #302
        regression: any site that falls back to /tmp/ instead breaks here.
    """
    if xdg_set:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    else:
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    cli_path = agent_socket_path(NICK)

    # Each daemon, with socket_dir=None, must compute the same path the CLI
    # would dial. socket_dir override is preserved for tests; only the
    # fallback behavior is under test.
    for name, factory in DAEMON_FACTORIES:
        daemon = factory()
        assert daemon._socket_path == cli_path, (
            f"{name} daemon socket_path={daemon._socket_path!r} "
            f"diverges from CLI agent_socket_path={cli_path!r}"
        )

    # Each skill irc_client (the in-agent CLI used by SKILL.md examples)
    # must resolve to the same path so the agent's own tools can reach its
    # daemon.
    monkeypatch.setenv("CULTURE_NICK", NICK)
    for name, sock_fn in SKILL_RESOLVERS:
        assert sock_fn() == cli_path, (
            f"{name} skill _sock_path_from_env()={sock_fn()!r} "
            f"diverges from CLI agent_socket_path={cli_path!r}"
        )


def test_culture_runtime_dir_used_by_resolvers(monkeypatch, tmp_path):
    """Sanity check: agent_socket_path is built on culture_runtime_dir().

    If a refactor breaks this delegation (e.g. inlining the env lookup),
    the convergence test above can still pass for a moment while drifting,
    so pin the relationship explicitly.
    """
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    expected = str(tmp_path) + f"/culture-{NICK}.sock"
    assert agent_socket_path(NICK) == expected
    assert culture_runtime_dir() == str(tmp_path)
