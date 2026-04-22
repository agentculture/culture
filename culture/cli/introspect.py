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


_NAMESPACES = ("agex", "server", "agent", "mesh", "bot", "channel", "skills")


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


def _register_root() -> None:
    register_topic(
        "culture",
        explain=_culture_explain,
        overview=_culture_overview,
        learn=_culture_learn,
    )


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
