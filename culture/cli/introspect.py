"""Universal introspection verb dispatcher.

Registers three top-level verbs (``explain``, ``overview``, ``learn``)
on the culture CLI and dispatches each to a per-topic handler.

The module conforms to the extended culture CLI group protocol:
exports ``NAMES`` (frozenset) instead of the singular ``NAME``.
"""

from __future__ import annotations

import argparse
import logging
from typing import Callable

_log = logging.getLogger(__name__)

Handler = Callable[[str | None], tuple[str, int]]

NAMES = frozenset({"explain", "overview", "learn"})

_explain: dict[str, Handler] = {}
_overview: dict[str, Handler] = {}
_learn: dict[str, Handler] = {}

_REGISTRIES: dict[str, dict[str, Handler]] = {
    "explain": _explain,
    "overview": _overview,
    "learn": _learn,
}


def register_topic(
    topic: str,
    *,
    explain: Handler | None = None,
    overview: Handler | None = None,
    learn: Handler | None = None,
) -> None:
    """Register handlers for a topic. Any verb may be omitted.

    Re-registration is last-write-wins; a warning is logged when an existing
    handler is overwritten so accidental double-registration is visible at
    culture's default INFO log level.
    """
    for verb, handler, registry in (
        ("explain", explain, _explain),
        ("overview", overview, _overview),
        ("learn", learn, _learn),
    ):
        if handler is None:
            continue
        if topic in registry:
            _log.warning("overriding %s handler for topic %r", verb, topic)
        registry[topic] = handler


def _clear_registry() -> None:
    """Test-only: wipe all registries, then re-register the root handlers."""
    _explain.clear()
    _overview.clear()
    _learn.clear()
    _register_root()


def _resolve(verb: str, topic: str | None) -> tuple[str, int]:
    registry = _REGISTRIES[verb]
    effective = topic if topic is not None else "culture"
    handler = registry.get(effective)
    if handler is None:
        # Known-but-unregistered namespaces are advertised via _NAMESPACES
        # in `culture explain` output with "(coming soon)". Surface that
        # same framing on a direct `culture <verb> <ns>` call instead of
        # failing as if the topic were unknown. Exit 0 because this is a
        # known future state, not a user error.
        if effective in _NAMESPACES:
            return (
                f"`culture {effective}` — coming soon. "
                f"Not yet implemented. Run `culture explain` to see the "
                f"current registry of namespaces.\n",
                0,
            )
        available = sorted(registry.keys())
        msg = (
            f"unknown topic '{effective}' for {verb};"
            f" available: {', '.join(available) or '(none)'}"
        )
        return msg, 1
    return handler(topic)


def explain(topic: str | None) -> tuple[str, int]:
    return _resolve("explain", topic)


def overview(topic: str | None) -> tuple[str, int]:
    return _resolve("overview", topic)


def learn(topic: str | None) -> tuple[str, int]:
    return _resolve("learn", topic)


_NAMESPACES = (
    "agent",
    "server",
    "mesh",
    "channel",
    "console",
    "bot",
    "skills",
    "devex",
    "afi",
)


def _culture_explain(_topic: str | None) -> tuple[str, int]:
    lines = [
        "# Culture",
        "",
        "Culture is the framework of agreements that makes agent behavior",
        "portable, inspectable, and effective. It hosts a mesh of IRC servers",
        "where AI agents collaborate, share knowledge, and coordinate work.",
        "",
        "## Namespaces",
        "",
    ]
    for ns in _NAMESPACES:
        registered = any(ns in registry for registry in _REGISTRIES.values())
        marker = "" if registered else "  (coming soon)"
        lines.append(f"- `culture {ns}`{marker}")
    lines.append("")
    lines.append("## Universal verbs")
    lines.append("")
    lines.append("- `culture explain [topic]` — deep description")
    lines.append("- `culture overview [topic]` — shallow map")
    lines.append("- `culture learn [topic]` — agent onboarding prompt")
    return "\n".join(lines) + "\n", 0


def _culture_overview(_topic: str | None) -> tuple[str, int]:
    return (
        "Culture: agent IRC mesh. Namespaces: "
        + ", ".join(_NAMESPACES)
        + ". Universal verbs: explain, overview, learn.\n",
        0,
    )


def _culture_learn(_topic: str | None) -> tuple[str, int]:
    from culture.learn_prompt import generate_learn_prompt

    return generate_learn_prompt(), 0


def _agent_explain(_topic: str | None) -> tuple[str, int]:
    return (
        "# culture agent\n\n"
        "Manage AI agents on the mesh. Each agent runs as a daemon "
        "with its own IRC connection and Claude Agent SDK harness.\n\n"
        "## Verbs\n\n"
        "- `create` / `join` — scaffold a new agent or register an existing "
        "agent directory (claude / codex / copilot / acp)\n"
        "- `register` / `unregister` — add or remove from `~/.culture/server.yaml`\n"
        "- `start` / `stop` / `restart` / `status` — agent daemon lifecycle\n"
        "- `sleep` / `wake` — pause and resume without unregistering\n"
        "- `message` / `read` — DM other agents (read is currently a stub; use "
        "`culture channel read` for shared history)\n"
        "- `learn` — print the onboarding prompt the agent reads on first run\n"
        "- `rename` / `assign` / `archive` / `unarchive` / `delete` — admin\n",
        0,
    )


def _server_explain(_topic: str | None) -> tuple[str, int]:
    return (
        "# culture server\n\n"
        "Manage the IRC server (lifecycle + agentirc passthrough). Servers "
        "host channels and federate to peers via `link`.\n\n"
        "## Culture-owned verbs\n\n"
        "- `start` / `stop` / `status` — daemon lifecycle (writes "
        "`~/.culture/pids/server-<name>.pid`)\n"
        "- `default` — set the default server name resolved by other "
        "commands\n"
        "- `rename` / `archive` / `unarchive` — admin on `~/.culture/server.yaml`\n\n"
        "## Forwarded verbs\n\n"
        "These pass straight through to the bundled `agentirc` CLI:\n\n"
        "- `restart` / `link` / `logs` / `version` / `serve`\n",
        0,
    )


def _mesh_explain(_topic: str | None) -> tuple[str, int]:
    return (
        "# culture mesh\n\n"
        "Inspect and configure the federated mesh of culture servers. The "
        "mesh is a graph of servers linked over IRC server-to-server "
        "connections, with trust managed via `~/.culture/mesh.yaml`.\n\n"
        "## Verbs\n\n"
        "- `overview` — rich status: rooms, online agents, federation links, "
        "recent activity\n"
        "- `setup` — interactive mesh-link configuration (writes "
        "`mesh.yaml` and stores the password in the OS keyring)\n"
        "- `update` — refresh trust + peer config from `mesh.yaml`\n"
        "- `console` — DEPRECATED, use `culture console` instead\n",
        0,
    )


def _channel_explain(_topic: str | None) -> tuple[str, int]:
    return (
        "# culture channel\n\n"
        "Read, write, and inspect channels on the mesh. Most verbs route "
        "through a running agent daemon (set `CULTURE_NICK`) or fall back "
        "to an ephemeral peek client when no daemon is reachable.\n\n"
        "## Verbs\n\n"
        "- `list` — channels with members on the local server\n"
        "- `read` — recent channel history (e.g. `culture channel read "
        "'#general' --limit 50`)\n"
        "- `message` — send a message to a channel\n"
        "- `who` — list members of a channel\n"
        "- `join` / `part` — join or leave a channel\n"
        "- `ask` — send a question and wait for a reply\n"
        "- `topic` — get or set the channel topic\n"
        "- `compact` / `clear` — operate on the calling agent's context "
        "window (despite the `channel` namespace)\n",
        0,
    )


def _bot_explain(_topic: str | None) -> tuple[str, int]:
    return (
        "# culture bot\n\n"
        "Manage event-driven bots on a culture server. Bots react to "
        "channel events (joins, messages, mentions) by running templates "
        "or shell hooks defined in `bot.yaml`.\n\n"
        "## Verbs\n\n"
        "- `create` — scaffold a new bot directory with a `bot.yaml`\n"
        "- `start` / `stop` / `list` — lifecycle and inventory\n"
        "- `inspect` — show the bot's config, recent events, and last "
        "render output\n"
        "- `archive` / `unarchive` — soft-remove without losing config\n",
        0,
    )


def _skills_explain(_topic: str | None) -> tuple[str, int]:
    return (
        "# culture skills\n\n"
        "Install culture's bundled skills into the per-backend skills "
        "directory so agents can use them without hunting for SKILL.md "
        "by hand.\n\n"
        "## Verbs\n\n"
        "- `install <target>` — copy the bundled skills into the target's "
        "harness dir. Three skills are installed per target:\n"
        "  - `irc` / `culture-irc` — agent-facing IRC channel guide\n"
        "  - `culture` (admin) — administrator-facing culture operations\n"
        "  - `communicate` (with `scripts/`) — cross-repo + mesh "
        "communication helpers\n"
        "- Targets: `claude`, `codex`, `copilot`, `acp` (with `opencode` "
        "as an alias for `acp`), or `all` to install for every backend\n",
        0,
    )


_NAMESPACE_EXPLAINERS: dict[str, Handler] = {
    "agent": _agent_explain,
    "server": _server_explain,
    "mesh": _mesh_explain,
    "channel": _channel_explain,
    "bot": _bot_explain,
    "skills": _skills_explain,
}


def _register_root() -> None:
    register_topic(
        "culture",
        explain=_culture_explain,
        overview=_culture_overview,
        learn=_culture_learn,
    )
    for ns, handler in _NAMESPACE_EXPLAINERS.items():
        register_topic(ns, explain=handler)


_register_root()


# --- CLI group protocol ---------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    for verb in ("explain", "overview", "learn"):
        p = subparsers.add_parser(verb, help=f"{verb.capitalize()} a topic (culture by default)")
        p.add_argument("topic", nargs="?", default=None, help="Topic to inspect")


def dispatch(args: argparse.Namespace) -> None:
    import sys

    verb = args.command
    topic = getattr(args, "topic", None)
    stdout, code = _resolve(verb, topic)
    if stdout:
        stream = sys.stdout if code == 0 else sys.stderr
        end = "" if stdout.endswith("\n") else "\n"
        print(stdout, end=end, file=stream)
    if code != 0:
        sys.exit(code)
