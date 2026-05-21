"""Universal introspection verb dispatcher.

Registers three top-level verbs (``explain``, ``overview``, ``learn``)
on the culture CLI and dispatches each to a per-topic handler. All three
verbs accept ``--json`` for the AgentCulture sibling JSON contract (see
``docs/reference/cli/learn-explain-json.md``); without ``--json`` the
existing markdown/text behavior is unchanged.

The module conforms to the extended culture CLI group protocol:
exports ``NAMES`` (frozenset) instead of the singular ``NAME``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Callable

from culture.cli._errors import EXIT_USER_ERROR, CultureError
from culture.cli._output import emit_error, emit_result

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
    "agents",
    "server",
    "mesh",
    "channel",
    "console",
    "bot",
    "skills",
    "devex",
    "afi",
)

# Nouns that delegate their real reference to a sibling binary. Listed in
# ``learn --json`` under ``passthroughs`` (not ``nouns``) so katvan's
# reference-sync skips them — it'll pull each sibling's own ``learn --json``
# / ``explain --json`` directly from its registry entry.
_PASSTHROUGHS: dict[str, str] = {
    "devex": "agex",
    "afi": "afi",
    "console": "irc-lens",
}

_NATIVE_NOUNS: tuple[str, ...] = tuple(n for n in _NAMESPACES if n not in _PASSTHROUGHS)
_UNIVERSAL_VERBS: tuple[str, ...] = ("explain", "overview", "learn")
_HINT_RUN_EXPLAIN = "run 'culture explain' to see the registry of nouns"
_SUMMARY = "AI agent IRC mesh — server, agents, channels, federation."
_PURPOSE = (
    "Culture is the framework of agreements that makes agent behavior "
    "portable, inspectable, and effective. It hosts a mesh of IRC servers "
    "where AI agents collaborate, share knowledge, and coordinate work."
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


def _agents_explain(_topic: str | None) -> tuple[str, int]:
    return (
        "# culture agents\n\n"
        "The unified agent namespace. Manage the lifecycle of agents on the "
        "mesh — each agent runs as a daemon with its own IRC connection and "
        "harness.\n\n"
        "## Lifecycle verbs\n\n"
        "- `create` / `join` — scaffold or register an agent directory "
        "(claude / codex / copilot / acp)\n"
        "- `register` / `unregister` — add or remove from `~/.culture/server.yaml`\n"
        "- `start` / `stop` / `status` — agent daemon lifecycle\n"
        "- `sleep` / `wake` — pause and resume without unregistering\n"
        "- `install` / `uninstall` — manage the per-agent systemd/launchd unit\n"
        "- `message` / `read` — DM other agents\n"
        "- `learn` — print the onboarding prompt the agent reads on first run\n"
        "- `rename` / `assign` / `archive` / `unarchive` / `delete` / `migrate` — admin\n"
        "\n## Alignment verbs (forwarded to steward)\n\n"
        "- `doctor` — diagnose this repo or the whole sibling corpus\n"
        "- `show <target>` — one agent's full configuration in one view\n"
        "- `overview` — ecosystem inventory + relationship graph\n\n"
        "Three inspection lenses: `status` (runtime liveness), `show` (static "
        "config), `overview` (cross-repo graph).\n",
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
        "as an alias for `acp`), or `all` to install for every backend\n"
        "- `announce-update` — broadcast a vendored-skill migration brief "
        "(forwarded to `steward announce-skill-update`)\n",
        0,
    )


_NAMESPACE_EXPLAINERS: dict[str, Handler] = {
    "agents": _agents_explain,
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


# --- JSON contract helpers ------------------------------------------------


def _split_path(topic: str | None) -> list[str]:
    """Normalise a ``topic`` arg into a noun/verb path list.

    ``None`` and ``""`` → ``[]``; ``"agents"`` → ``["agents"]``;
    ``"agents/start"`` → ``["agents", "start"]``. Katvan passes the latter
    form as a single argv token; siblings accept either form.
    """
    if not topic:
        return []
    return [seg for seg in topic.split("/") if seg]


def _collect_verbs(noun: str) -> list[str]:
    """Return the sorted list of verbs (subcommands) registered under
    ``culture <noun>``.

    Reaches into argparse private attrs ``_actions`` /
    ``_SubParsersAction`` — stable since Python 3.2 and the only way to
    enumerate subparsers without duplicating each group's registration
    metadata. Returns ``[]`` for passthrough nouns (their parser uses
    ``REMAINDER`` and exposes no inner subparsers).
    """
    from culture.cli import _build_parser

    parser = _build_parser()
    top_sub = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if top_sub is None or noun not in top_sub.choices:
        return []
    noun_parser = top_sub.choices[noun]
    inner = next(
        (a for a in noun_parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    return sorted(inner.choices.keys()) if inner else []


def _format_verb_help(noun: str, verb: str) -> str:
    """Return ``culture <noun> <verb> --help`` formatted help text.

    Used as the ``markdown`` body of ``culture explain noun/verb --json``;
    keeps the leaf-level reference in lockstep with argparse — no
    hand-authored doc per verb needed.
    """
    from culture.cli import _build_parser

    parser = _build_parser()
    top_sub = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if top_sub is None or noun not in top_sub.choices:
        raise CultureError(
            EXIT_USER_ERROR,
            f"unknown noun '{noun}'",
            _HINT_RUN_EXPLAIN,
        )
    noun_parser = top_sub.choices[noun]
    inner = next(
        (a for a in noun_parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if inner is None or verb not in inner.choices:
        raise CultureError(
            EXIT_USER_ERROR,
            f"unknown verb '{verb}' for noun '{noun}'",
            f"run 'culture explain {noun}' to see the verbs of that noun",
        )
    return inner.choices[verb].format_help()


def _learn_root_payload() -> dict[str, Any]:
    from culture import __version__

    return {
        "tool": "culture",
        "version": __version__,
        "summary": _SUMMARY,
        "purpose": _PURPOSE,
        "nouns": list(_NATIVE_NOUNS),
        "passthroughs": [
            {"noun": noun, "binary": binary} for noun, binary in _PASSTHROUGHS.items()
        ],
        "verbs": list(_UNIVERSAL_VERBS),
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "culture explain <path>",
    }


def _explain_payload(path: list[str]) -> dict[str, Any]:
    if not path or path == ["culture"]:
        markdown, _ = _culture_explain(None)
        return {
            "path": [],
            "nouns": list(_NATIVE_NOUNS),
            "passthroughs": [
                {"noun": noun, "binary": binary} for noun, binary in _PASSTHROUGHS.items()
            ],
            "markdown": markdown,
        }
    if len(path) == 1:
        noun = path[0]
        if noun in _PASSTHROUGHS:
            return {
                "path": [noun],
                "passthrough_to": _PASSTHROUGHS[noun],
                "markdown": (
                    f"`culture {noun}` is a passthrough to "
                    f"`{_PASSTHROUGHS[noun]}`. Pull its reference from "
                    f"that sibling's `learn --json` / `explain --json` "
                    f"output directly.\n"
                ),
            }
        explainer = _NAMESPACE_EXPLAINERS.get(noun)
        if explainer is None:
            raise CultureError(
                EXIT_USER_ERROR,
                f"unknown noun '{noun}' for explain",
                _HINT_RUN_EXPLAIN,
            )
        markdown, _ = explainer(None)
        return {
            "path": [noun],
            "verbs": _collect_verbs(noun),
            "markdown": markdown,
        }
    if len(path) == 2:
        noun, verb = path
        markdown = _format_verb_help(noun, verb)
        return {"path": [noun, verb], "markdown": markdown}
    raise CultureError(
        EXIT_USER_ERROR,
        f"path too deep: {'/'.join(path)} (max depth is noun/verb)",
        "use 'culture explain <noun>' or 'culture explain <noun>/<verb>'",
    )


def _overview_payload(path: list[str]) -> dict[str, Any]:
    """Thin sibling of explain — symmetric shape, no verbs list."""
    if not path or path == ["culture"]:
        markdown, _ = _culture_overview(None)
        return {
            "path": [],
            "nouns": list(_NATIVE_NOUNS),
            "passthroughs": [
                {"noun": noun, "binary": binary} for noun, binary in _PASSTHROUGHS.items()
            ],
            "markdown": markdown,
        }
    # Fall back to the explain shape minus 'verbs' for any other path —
    # keeps overview useful as a thin wrapper even though katvan does
    # not consume it.
    payload = _explain_payload(path)
    payload.pop("verbs", None)
    return payload


def _payload_for(verb: str, path: list[str]) -> dict[str, Any]:
    if verb == "learn":
        # ``culture learn --json`` always emits the root payload regardless
        # of topic; katvan only ever calls the no-topic form, and a
        # topic-scoped JSON learn has no defined consumer.
        return _learn_root_payload()
    if verb == "explain":
        return _explain_payload(path)
    if verb == "overview":
        return _overview_payload(path)
    raise CultureError(
        EXIT_USER_ERROR,
        f"unsupported verb '{verb}' for --json",
        "use one of: explain, overview, learn",
    )


# --- CLI group protocol ---------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    for verb in _UNIVERSAL_VERBS:
        p = subparsers.add_parser(verb, help=f"{verb.capitalize()} a topic (culture by default)")
        p.add_argument("topic", nargs="?", default=None, help="Topic to inspect")
        p.add_argument(
            "--json",
            action="store_true",
            dest="json",
            help="Emit structured JSON (stdout) per the AgentCulture sibling contract.",
        )


def dispatch(args: argparse.Namespace) -> None:
    verb = args.command
    topic = getattr(args, "topic", None)
    json_mode = bool(getattr(args, "json", False))
    try:
        if json_mode:
            payload = _payload_for(verb, _split_path(topic))
            emit_result(payload, json_mode=True)
            return
        stdout, code = _resolve(verb, topic)
        if code != 0:
            raise CultureError(
                code,
                stdout.rstrip("\n").split(";")[0] or f"unknown topic for {verb}",
                _HINT_RUN_EXPLAIN,
            )
        if stdout:
            end = "" if stdout.endswith("\n") else "\n"
            print(stdout, end=end, file=sys.stdout)
    except CultureError as err:
        emit_error(err, json_mode=json_mode)
        sys.exit(err.code)
