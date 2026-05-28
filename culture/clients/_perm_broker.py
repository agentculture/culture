"""File-backed permission broker for boss-supervised helper agents.

The broker bridges the Claude Agent SDK's ``can_use_tool`` callback to a
file-backed request/decision queue under ``~/.culture/``. A regular Claude
Code session ("boss") acts as the human-in-the-loop authority for tool calls
made by helper agent daemons it has spawned.

Design spec: docs/superpowers/specs/2026-05-28-helper-boss-permission-broker.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml
from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

logger = logging.getLogger(__name__)

# Poll cadence while awaiting a decision file.
_POLL_INTERVAL_SECONDS = 0.25

# Default permission policy seeded by spawn-helper.sh when a helper has no
# existing perm-policy/<nick>.yaml.  Mirrors the spec's "pre-seeded safe-read
# defaults".  Note: ``require_approval`` is informational; anything that does
# not match ``auto_allow`` or ``auto_deny`` falls through to the boss anyway.
_BASH_SAFE_READ_REGEX = (
    r"^(ls|cat|head|tail|wc|file|stat|pwd|which|rg|grep|find|tree|"
    r"git (status|log|diff|blame|show)|gh (.* )?(list|view))(\s|$)"
)
DEFAULT_POLICY: dict[str, Any] = {
    "auto_allow": [
        {"tool": "Read"},
        {"tool": "Glob"},
        {"tool": "Grep"},
        {"tool": "Bash", "input_regex": _BASH_SAFE_READ_REGEX},
    ],
    "auto_deny": [],
    "require_approval": [
        {"tool": "Edit"},
        {"tool": "Write"},
        {"tool": "mcp__.*"},
        {"tool": "Bash"},
    ],
}


def culture_home() -> str:
    """Resolve the broker's root directory.

    Honors ``CULTURE_HOME`` for test isolation; falls back to ``~/.culture``.
    """
    return os.environ.get("CULTURE_HOME", os.path.expanduser("~/.culture"))


def _queue_dir() -> str:
    return os.path.join(culture_home(), "perm-queue")


def _decisions_dir() -> str:
    return os.path.join(culture_home(), "perm-decisions")


def _policy_dir() -> str:
    return os.path.join(culture_home(), "perm-policy")


def policy_path_for(nick: str) -> str:
    """Return the policy file path for a given agent nick."""
    return os.path.join(_policy_dir(), f"{nick}.yaml")


def has_policy_file(nick: str) -> bool:
    """True iff the helper has a policy file (i.e. is boss-supervised)."""
    return bool(nick) and os.path.exists(policy_path_for(nick))


def _mkdir_secure(path: str) -> None:
    """Create a directory with 0700 perms if missing."""
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        logger.debug("Could not chmod %s; continuing", path, exc_info=True)


def _atomic_write_json(dest: str, payload: dict[str, Any]) -> None:
    """Write JSON to ``dest`` atomically with 0600 perms.

    Writes via ``tempfile`` in the same directory, fsyncs, then ``os.replace``.
    """
    _mkdir_secure(os.path.dirname(dest))
    fd, tmp = tempfile.mkstemp(
        prefix=".tmp-",
        suffix=".json",
        dir=os.path.dirname(dest),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except BaseException:
        # Clean up the temp file on any failure (incl. cancellation).
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_yaml(dest: str, payload: dict[str, Any]) -> None:
    """Write YAML to ``dest`` atomically with 0600 perms."""
    _mkdir_secure(os.path.dirname(dest))
    fd, tmp = tempfile.mkstemp(
        prefix=".tmp-",
        suffix=".yaml",
        dir=os.path.dirname(dest),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False, default_flow_style=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_default_policy(nick: str) -> str:
    """Seed the default policy file for a helper if missing.

    Returns the path. Idempotent — if the file exists, it is not overwritten.
    """
    dest = policy_path_for(nick)
    if os.path.exists(dest):
        return dest
    _atomic_write_yaml(dest, DEFAULT_POLICY)
    return dest


def handoff_path_for(nick: str) -> str:
    """Return the context-handoff file path for a given agent nick."""
    return os.path.join(culture_home(), "handoff", f"{nick}.md")


def _handoff_auto_allow_rule(nick: str) -> dict[str, Any]:
    """Auto-allow rule letting a helper write its own context-handoff file.

    A context-crisis handoff must never stall on boss approval, so the helper's
    Write to handoff/<nick>.md is pre-approved. All other Writes still route to
    the boss.
    """
    return {
        "tool": "Write",
        "input_regex": rf"/handoff/{re.escape(nick)}\.md$",
    }


def seed_helper_policy(nick: str) -> str:
    """Seed a boss-supervised helper's policy file.

    Writes the default safe-read policy (if missing) and ensures the
    context-handoff write auto-allow rule is present. Idempotent.
    """
    dest = write_default_policy(nick)
    rule = _handoff_auto_allow_rule(nick)
    try:
        with open(dest, encoding="utf-8") as handle:
            policy = yaml.safe_load(handle) or {}
    except OSError:
        policy = {}
    if not isinstance(policy, dict):
        policy = {}
    auto_allow = policy.setdefault("auto_allow", []) or []
    if not isinstance(auto_allow, list):
        auto_allow = []
    if rule not in auto_allow:
        auto_allow.append(rule)
        policy["auto_allow"] = auto_allow
        _atomic_write_yaml(dest, policy)
    return dest


# ---------------------------------------------------------------------------
# Policy matcher
# ---------------------------------------------------------------------------

_REGEX_METACHARS = re.compile(r"[.*+?\[\]^$|()\\]")


def _tool_matches(tool_name: str, pattern: str) -> bool:
    if _REGEX_METACHARS.search(pattern):
        try:
            return re.fullmatch(pattern, tool_name) is not None
        except re.error:
            logger.warning("Invalid tool pattern %r in policy", pattern)
            return False
    return tool_name == pattern


def _project_input(tool_name: str, input_dict: dict[str, Any]) -> str | None:
    """Project a tool's input to a single string for regex matching."""
    if tool_name == "Bash":
        value = input_dict.get("command")
        return value if isinstance(value, str) else None
    if tool_name in ("Edit", "Write"):
        value = input_dict.get("file_path")
        return value if isinstance(value, str) else None
    if tool_name.startswith("mcp__"):
        try:
            return json.dumps(input_dict, sort_keys=True)
        except TypeError:
            return repr(input_dict)
    return None


def _rule_matches(tool_name: str, input_dict: dict[str, Any], rule: dict[str, Any]) -> bool:
    pattern = rule.get("tool")
    if not isinstance(pattern, str) or not _tool_matches(tool_name, pattern):
        return False
    input_regex = rule.get("input_regex")
    if input_regex is None:
        return True
    projected = _project_input(tool_name, input_dict) if isinstance(input_regex, str) else None
    if projected is None:
        return False
    try:
        return re.search(input_regex, projected) is not None
    except re.error:
        logger.warning("Invalid input_regex %r in policy", input_regex)
        return False


def match_policy(
    tool_name: str,
    input_dict: dict[str, Any],
    policy: dict[str, Any],
) -> str | None:
    """Apply policy rules to a tool invocation.

    Returns ``"allow"``, ``"deny"``, or ``None`` (route to boss). ``auto_deny``
    is checked before ``auto_allow``; first match wins within each section.
    """
    for section, verdict in (("auto_deny", "deny"), ("auto_allow", "allow")):
        for rule in policy.get(section, []) or []:
            if isinstance(rule, dict) and _rule_matches(tool_name, input_dict, rule):
                return verdict
    return None


# ---------------------------------------------------------------------------
# Boss grant ceiling (human-over-boss gate)
# ---------------------------------------------------------------------------

# Tools a boss agent MAY NOT auto-grant to a worker; these escalate to the human.
# Structurally a denylist, matched by reusing ``match_policy`` (as auto_deny).
DEFAULT_BOSS_CEILING: list[dict[str, Any]] = [
    {"tool": "mcp__.*"},  # any MCP server — external side effects
    {
        "tool": "Bash",
        "input_regex": (
            r"(^|\s|;|&&|\|\|)(rm\s+-rf|git\s+push|gh\s+(pr|release)\s+(create|merge)|"
            r"kubectl|terraform|drop\s+table|truncate)"
        ),
    },
]


def _boss_policy_dir() -> str:
    return os.path.join(culture_home(), "boss-policy")


def boss_policy_path_for(nick: str) -> str:
    """Path to a boss agent's grant-ceiling file."""
    return os.path.join(_boss_policy_dir(), f"{nick}.yaml")


def write_default_boss_ceiling(nick: str) -> str:
    """Seed a boss agent's grant-ceiling file if missing. Idempotent."""
    dest = boss_policy_path_for(nick)
    if os.path.exists(dest):
        return dest
    _atomic_write_yaml(dest, {"grant_ceiling": DEFAULT_BOSS_CEILING})
    return dest


def load_boss_ceiling(nick: str) -> list[dict[str, Any]]:
    """Load a boss agent's ceiling rules (empty list if no file)."""
    path = boss_policy_path_for(nick)
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(data, dict):
        return []
    rules = data.get("grant_ceiling", []) or []
    return [r for r in rules if isinstance(r, dict)]


def is_above_ceiling(tool_name: str, input_dict: dict[str, Any], boss_nick: str) -> bool:
    """True iff a tool call is above the boss's grant ceiling (→ escalate to human).

    Reuses ``match_policy`` by treating the ceiling as an ``auto_deny`` denylist.
    """
    ceiling = load_boss_ceiling(boss_nick)
    if not ceiling:
        return False
    return match_policy(tool_name, input_dict, {"auto_deny": ceiling}) == "deny"


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------


def _new_request_id() -> str:
    """Mint a sortable, unique request ID."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f")
    return f"req-{ts}-{secrets.token_hex(3)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Request IDs are minted as ``req-<iso-with-dashes>-<hex>`` — letters, digits,
# hyphens only. Approvers (the boss CLI, the dashboard POST body) pass ids from
# untrusted input, so every id that becomes a file path must match this before
# it reaches ``os.path.join`` — otherwise ``../`` / ``/`` escapes the queue dir.
_REQUEST_ID_RE = re.compile(r"^req-[A-Za-z0-9_-]+$")


def valid_request_id(request_id: str) -> bool:
    return bool(request_id) and _REQUEST_ID_RE.fullmatch(request_id) is not None


# ---------------------------------------------------------------------------
# Approver-side helpers (shared by the boss CLI and the human approve scripts)
# ---------------------------------------------------------------------------


def list_pending() -> list[dict[str, Any]]:
    """Return permission requests still awaiting a decision, oldest first.

    A request whose ``perm-decisions/<id>.json`` already exists is *decided* —
    it is only waiting for its worker to consume the verdict — so it is excluded.
    Otherwise an approver (boss CLI or dashboard) would see already-decided
    requests and re-act on them (hitting :class:`DecisionExistsError`), which is
    visible whenever a worker is slow or gone.
    """
    queue_dir = _queue_dir()
    decisions_dir = _decisions_dir()
    out: list[dict[str, Any]] = []
    try:
        names = sorted(n for n in os.listdir(queue_dir) if n.endswith(".json"))
    except OSError:
        return out
    for name in names:
        if os.path.exists(os.path.join(decisions_dir, name)):
            continue  # already decided, awaiting worker consumption
        try:
            with open(os.path.join(queue_dir, name), encoding="utf-8") as handle:
                out.append(json.load(handle))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def read_request(request_id: str) -> dict[str, Any] | None:
    """Read a single pending request by id, or None if absent/unreadable/invalid."""
    if not valid_request_id(request_id):
        return None
    path = os.path.join(_queue_dir(), f"{request_id}.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


class DecisionExistsError(RuntimeError):
    """Raised when a decision already exists for a request (first-writer-wins)."""


class InvalidRequestIdError(ValueError):
    """Raised when a request id is not a valid, path-safe broker id."""


def write_decision(
    request_id: str,
    *,
    verdict: str,
    scope: str = "once",
    reason: str = "",
    pattern: str = "",
    decided_by: str = "boss",
) -> str:
    """Write a decision file (first-writer-wins via O_CREAT|O_EXCL + atomic rename).

    Raises :class:`InvalidRequestIdError` if ``request_id`` is not path-safe
    (approvers pass it from untrusted input), :class:`DecisionExistsError` if a
    decision already exists. Returns the decision path.
    """
    if not valid_request_id(request_id):
        raise InvalidRequestIdError(request_id)
    dest = os.path.join(_decisions_dir(), f"{request_id}.json")
    _mkdir_secure(_decisions_dir())
    # First-writer-wins guard on the destination path.
    try:
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise DecisionExistsError(request_id) from exc
    os.close(fd)
    payload: dict[str, Any] = {
        "id": request_id,
        "verdict": verdict,
        "scope": scope,
        "decided_by": decided_by,
        "decided_at": _now_iso(),
    }
    if reason:
        payload["reason"] = reason
    if pattern:
        payload["pattern"] = pattern
    try:
        _atomic_write_json(dest, payload)
    except BaseException:
        # On any failure after we created the O_EXCL placeholder, remove it.
        # Otherwise a zero-byte sentinel lingers at dest: the waiting worker
        # parses it forever (silent deadlock) and a retry is permanently
        # blocked by DecisionExistsError.
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise
    return dest


@dataclass
class _PolicyCache:
    path: str
    mtime: float
    policy: dict[str, Any]


class PermissionBroker:
    """Per-helper permission broker.

    One instance is created per ``AgentRunner`` and reused across SDK turns.
    The broker's ``gate`` method is the ``can_use_tool`` callback.
    """

    def __init__(
        self,
        nick: str,
        on_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        if not nick:
            raise ValueError("PermissionBroker requires a non-empty nick")
        self._nick = nick
        self._cache: _PolicyCache | None = None
        # Optional best-effort notification fired when a request is routed to the
        # boss (e.g. the worker daemon posts an IRC notice). Never blocks gating.
        self._on_request = on_request

    @property
    def nick(self) -> str:
        return self._nick

    def _load_policy(self) -> dict[str, Any]:
        """Load (or refresh) the policy file for this helper."""
        path = policy_path_for(self._nick)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            # No file → no auto-rules. Everything falls through to boss.
            return {"auto_allow": [], "auto_deny": []}
        if self._cache and self._cache.path == path and self._cache.mtime == mtime:
            return self._cache.policy
        try:
            with open(path, encoding="utf-8") as handle:
                policy = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError):
            logger.warning("Failed to read policy %s; treating as empty", path, exc_info=True)
            policy = {}
        if not isinstance(policy, dict):
            logger.warning("Policy %s is not a mapping; treating as empty", path)
            policy = {}
        self._cache = _PolicyCache(path=path, mtime=mtime, policy=policy)
        return policy

    async def gate(
        self,
        tool_name: str,
        input_dict: dict[str, Any],
        context: ToolPermissionContext,  # noqa: ARG002 — SDK API requires this slot
    ) -> PermissionResultAllow | PermissionResultDeny:
        """SDK ``can_use_tool`` callback.

        Fast path: policy match returns immediately. Slow path: write request
        to ``perm-queue/<id>.json``, await ``perm-decisions/<id>.json``,
        return SDK verdict.
        """
        policy = self._load_policy()
        verdict = match_policy(tool_name, input_dict, policy)
        if verdict == "allow":
            return PermissionResultAllow(updated_input=None)
        if verdict == "deny":
            return PermissionResultDeny(
                message=f"Policy auto-deny for {tool_name}",
                interrupt=False,
            )
        return await self._request_from_boss(tool_name, input_dict)

    async def _request_from_boss(
        self,
        tool_name: str,
        input_dict: dict[str, Any],
    ) -> PermissionResultAllow | PermissionResultDeny:
        request_id = _new_request_id()
        queue_path = os.path.join(_queue_dir(), f"{request_id}.json")
        decision_path = os.path.join(_decisions_dir(), f"{request_id}.json")
        _mkdir_secure(_queue_dir())
        _mkdir_secure(_decisions_dir())

        payload = {
            "id": request_id,
            "helper_nick": self._nick,
            "tool_name": tool_name,
            "input": _safe_jsonable(input_dict),
            "created_at": _now_iso(),
        }
        _atomic_write_json(queue_path, payload)

        # Best-effort notify the boss (e.g. an IRC post). A failure here must
        # never block or fail the gate — the file queue is the source of truth
        # and the boss can still find the request by polling.
        if self._on_request is not None:
            try:
                await self._on_request(dict(payload))
            except asyncio.CancelledError:
                self._best_effort_unlink(queue_path)
                raise
            except Exception:  # noqa: BLE001 — notification is advisory
                logger.warning(
                    "on_request notification failed for %s; boss must poll",
                    request_id,
                    exc_info=True,
                )

        try:
            decision = await self._await_decision(decision_path)
        except asyncio.CancelledError:
            # Helper task cancelled mid-wait; clean up our request file so it
            # does not linger in the queue.  Re-raise so the SDK sees the
            # cancellation.  Mirrors the discipline from commit d0902f9
            # ("fix: re-raise CancelledError and save create_task results").
            self._best_effort_unlink(queue_path)
            raise

        # Clean up both files; they are single-use.
        self._best_effort_unlink(queue_path)
        self._best_effort_unlink(decision_path)

        scope = decision.get("scope", "once")
        verdict = decision.get("verdict")
        reason = decision.get("reason", "")

        if scope == "always" and verdict in ("allow", "deny"):
            self._append_sticky_rule(verdict, tool_name, decision)

        # Drop the policy cache so the freshly-appended rule is visible to
        # the next call.
        self._cache = None

        if verdict == "allow":
            return PermissionResultAllow(updated_input=None)
        if verdict == "deny":
            return PermissionResultDeny(
                message=reason or f"Boss denied {tool_name}",
                interrupt=False,
            )
        # Unknown verdict — fail closed.
        return PermissionResultDeny(
            message=f"Broker received unknown verdict {verdict!r}",
            interrupt=False,
        )

    async def _await_decision(self, decision_path: str) -> dict[str, Any]:
        """Poll until the decision file exists and parses, then return it.

        Reads are best-effort each tick: a transient ``OSError`` (the file was
        removed between the existence check and the open) or ``JSONDecodeError``
        (a non-atomic writer mid-write) is swallowed and the loop retries on the
        next tick. The boss scripts write atomically via ``os.replace`` so a
        complete, valid file is the steady state; this loop simply never lets a
        read error escape and orphan the in-flight request.
        """
        while True:
            decision = self._try_read_decision(decision_path)
            if decision is not None:
                return decision
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    @staticmethod
    def _try_read_decision(decision_path: str) -> dict[str, Any] | None:
        """Read+parse a decision file, or None if not yet readable/valid."""
        try:
            with open(decision_path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def _append_sticky_rule(
        self,
        verdict: str,
        tool_name: str,
        decision: dict[str, Any],
    ) -> None:
        """Append a sticky rule to this helper's policy file."""
        policy_path = policy_path_for(self._nick)
        try:
            with open(policy_path, encoding="utf-8") as handle:
                policy = yaml.safe_load(handle) or {}
        except OSError:
            policy = {}
        if not isinstance(policy, dict):
            policy = {}

        section = "auto_allow" if verdict == "allow" else "auto_deny"
        rules = policy.setdefault(section, []) or []
        if not isinstance(rules, list):
            rules = []
        # Decision may carry an override pattern in ``decision["pattern"]``;
        # otherwise the rule is an exact-tool-name match.
        rule: dict[str, Any] = {"tool": decision.get("pattern") or tool_name}
        # Avoid duplicating an identical rule.
        if rule not in rules:
            rules.append(rule)
        policy[section] = rules
        _atomic_write_yaml(policy_path, policy)

    @staticmethod
    def _best_effort_unlink(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass


def _safe_jsonable(value: Any) -> Any:
    """Convert arbitrary tool input into a JSON-serialisable shape."""
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _safe_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_safe_jsonable(v) for v in value]
        return repr(value)
