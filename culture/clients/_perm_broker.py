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
import time
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

# Maximum time the broker will block waiting for a boss decision before
# returning a synthetic deny ("timeout"). Generous so a boss has time to
# read the request, ask the human if needed, and decide; bounded so a dead
# or unresponsive boss does not hang the worker's SDK call forever.
_PERM_DECISION_TIMEOUT_SECONDS = 600


# Phase 5.6: probe whether ``watchdog`` is importable so ``_await_decision``
# can branch between the push (Event-driven Future) and pull (250ms poll)
# paths. Tests may override this via monkeypatch to exercise the fallback.
_HAS_WATCHDOG: bool
try:
    if os.environ.get("CULTURE_DISABLE_WATCHDOG", "") == "1":
        raise ImportError("CULTURE_DISABLE_WATCHDOG=1 — disabled by env")
    import watchdog.observers  # noqa: F401 — probe-only

    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False

# Default permission policy seeded by ``culture boss spawn`` (seed_helper_policy)
# when a helper has no existing perm-policy/<nick>.yaml.  Mirrors the spec's
# "pre-seeded safe-read defaults".  Note: ``require_approval`` is informational;
# anything that does not match ``auto_allow`` or ``auto_deny`` falls through to
# the boss anyway.
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


def _demote_notices_dir() -> str:
    """Where the broker drops a notice when a sticky --always allow for
    a high-risk tool is demoted to scope=once (Task 5.1c)."""
    return os.path.join(culture_home(), "perm-demote-notices")


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


def mission_path_for(nick: str) -> str:
    """Return the mission-persistence file path for a boss agent.

    The boss daemon writes its accumulated brief here on every @mention
    so a restart can re-load it into the SDK system prompt — the original
    brief in the IRC channel may be too far back in history to recover
    cheaply (v8.19.x Pattern B mission persistence).

    The path is under ``~/.culture/mission/<nick>.md`` so it's
    self-contained alongside ``handoff/`` and other per-nick state.
    """
    return os.path.join(culture_home(), "mission", f"{nick}.md")


def seed_path_for(channel: str) -> str:
    """Return the seed-text path for a task channel (v8.19.18).

    The seed is the initial brief text the boss sent when opening a
    task channel — surfaced in the dashboard as a collapsible "Seed
    brief" header so the operator can see the original mission without
    scrolling all the way back. Written once on first brief (or via
    ``culture boss spawn --topic``) and never overwritten thereafter.

    ``channel`` may carry a leading ``#``; it's stripped. The remaining
    string is validated as a safe filename token (alnum / underscore /
    hyphen only) to keep the path inside ``~/.culture/seeds/``.
    """
    name = channel.lstrip("#")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(f"invalid channel name for seed path: {channel!r}")
    return os.path.join(culture_home(), "seeds", f"{name}.md")


def _handoff_auto_allow_rule(nick: str) -> dict[str, Any]:
    """Auto-allow rule letting a helper write its own context-handoff file.

    A context-crisis handoff must never stall on boss approval, so the helper's
    Write to ``~/.culture/handoff/<nick>.md`` is pre-approved. All other
    Writes still route to the boss.

    SECURITY: the regex is anchored to the EXACT absolute path (re.fullmatch
    semantics via ``^…$``). The previous form was only tail-anchored
    (``/handoff/<nick>.md$``), which together with ``re.search`` matched any
    path whose tail looked right — e.g. ``/etc/secrets/handoff/<nick>.md`` or
    a path-traversal smuggle. Anchoring to the literal handoff_path_for(nick)
    closes that surface.
    """
    return {
        "tool": "Write",
        "input_regex": rf"^{re.escape(handoff_path_for(nick))}$",
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
# High-risk tool gate (sticky-approval narrowing)
# ---------------------------------------------------------------------------

# Tools that MUST carry an ``input_regex`` to be sticky-approved (``scope=always``).
# A bare ``--always allow`` for any of these would whitelist every invocation of
# the tool (e.g. one approved ``Bash ls`` would auto-allow ``rm -rf /``). The
# broker refuses such approvals at write time and the boss CLI/dashboard demote
# them to ``scope=once`` instead — see ``BareStickyApproveRefusedError``.
_HIGH_RISK_TOOLS: tuple[str, ...] = ("Bash", "Edit", "Write")
_HIGH_RISK_TOOL_REGEX = re.compile(r"^mcp__.*")


def _is_high_risk_tool(tool_name: str) -> bool:
    """True iff a tool requires ``input_regex`` for sticky approvals.

    Matches the literal names in ``_HIGH_RISK_TOOLS`` plus any ``mcp__*`` tool.
    Pattern strings on the approver side (e.g. ``decision.get("pattern")``) are
    classified the same way — see ``_append_sticky_rule``.
    """
    if tool_name in _HIGH_RISK_TOOLS:
        return True
    return _HIGH_RISK_TOOL_REGEX.fullmatch(tool_name) is not None


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


def valid_request_id(request_id: object) -> bool:
    # Must reject non-strings too: approvers pass ids from untrusted JSON bodies,
    # where ``{"id": 123}`` / ``true`` / ``[...]`` would make re.fullmatch raise
    # an uncaught TypeError (a 500 at the dashboard boundary). A wrong type is an
    # invalid id, not a crash.
    return isinstance(request_id, str) and _REQUEST_ID_RE.fullmatch(request_id) is not None


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


def cleanup_stale(running_nicks: set[str]) -> dict[str, int]:
    """GC permission-queue requests whose helper isn't running + orphan decisions.

    A request from a dead helper lingers forever (no one consumes the verdict);
    a decision with no matching queue file is an orphan. ``running_nicks`` is the
    set of currently-alive agent nicks (the caller determines liveness). Returns
    ``{"stale_requests": n, "orphan_decisions": m}``. Pure I/O — no daemon needed.
    """
    queue_dir = _queue_dir()
    decisions_dir = _decisions_dir()
    stale = 0
    orphans = 0
    try:
        queue_names = [n for n in os.listdir(queue_dir) if n.endswith(".json")]
    except OSError:
        queue_names = []
    for name in queue_names:
        path = os.path.join(queue_dir, name)
        try:
            with open(path, encoding="utf-8") as handle:
                nick = json.load(handle).get("helper_nick", "")
        except (OSError, json.JSONDecodeError):
            continue
        # An empty/absent helper_nick is unattributable; skip rather than risk
        # deleting a request we can't prove is dead (the broker always sets it).
        if nick and nick not in running_nicks:
            _best_effort_unlink_path(path)
            stale += 1
    try:
        decision_names = [n for n in os.listdir(decisions_dir) if n.endswith(".json")]
    except OSError:
        decision_names = []
    for name in decision_names:
        if not os.path.exists(os.path.join(queue_dir, name)):
            _best_effort_unlink_path(os.path.join(decisions_dir, name))
            orphans += 1
    return {"stale_requests": stale, "orphan_decisions": orphans}


def _best_effort_unlink_path(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


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


class BareStickyApproveRefusedError(ValueError):
    """Raised when a sticky --always approval for a high-risk tool lacks input_regex."""


def demote_notice_path_for(request_id: str) -> str:
    """Path to a demote-notice file for a given request id.

    The path is rejected if ``request_id`` is not a valid broker id so an
    attacker-supplied id can never escape ``~/.culture/perm-demote-notices/``.
    """
    if not valid_request_id(request_id):
        raise InvalidRequestIdError(request_id)
    return os.path.join(_demote_notices_dir(), f"{request_id}.json")


def _write_demote_notice(
    request_id: str,
    original_tool: str,
    demote_reason: str,
    *,
    boss: str = "",
    helper_nick: str = "",
) -> None:
    """Drop a demote-notice JSON marking a sticky-allow that was demoted to once.

    Best-effort: a write failure is logged but not raised — the demote itself
    already happened (the broker continues with scope=once). The notice is the
    file-bus contract the bridge's fs_observer reads to surface the demote to
    the boss/dashboard. The observer mirrors these key names exactly — keep
    them aligned (see ``culture/clients/bridge/_fs_observer.py::_build_payload``).
    """
    try:
        path = demote_notice_path_for(request_id)
    except InvalidRequestIdError:
        # Should never happen — request_id is broker-minted — but log and skip.
        logger.warning("Cannot write demote-notice for invalid id %r", request_id)
        return
    try:
        _atomic_write_json(
            path,
            {
                "request_id": request_id,
                "original_tool": original_tool,
                "demote_reason": demote_reason,
                "noticed_at": _now_iso(),
                "boss": boss,
                "helper_nick": helper_nick,
            },
        )
    except OSError:
        logger.warning("Failed to write demote-notice %s", path, exc_info=True)


def write_decision(
    request_id: str,
    *,
    verdict: str,
    scope: str = "once",
    reason: str = "",
    pattern: str = "",
    decided_by: str = "boss",
    tool_name: str | None = None,
    input_regex: str | None = None,
) -> str:
    """Write a decision file (first-writer-wins via O_CREAT|O_EXCL + atomic rename).

    Raises :class:`InvalidRequestIdError` if ``request_id`` is not path-safe
    (approvers pass it from untrusted input), :class:`DecisionExistsError` if a
    decision already exists.

    Raises :class:`BareStickyApproveRefusedError` when ``scope='always'`` and
    ``verdict='allow'`` is requested for a high-risk tool (``Bash``/``Edit``/
    ``Write``/``mcp__*``) without a narrowing ``input_regex``. A bare sticky
    allow for any of these would whitelist every invocation of the tool — the
    caller must demote to ``scope='once'`` or supply ``input_regex``. The
    ``tool_name`` kwarg drives the classification; ``pattern`` is also checked
    (a ``--pattern Bash`` approval for an arbitrary tool would otherwise
    smuggle a bare Bash allow past the gate).

    Returns the decision path.
    """
    # High-risk sticky-allow narrowing gate (T3 / NT-12). The check fires when
    # the approver explicitly opts into a persistent grant — scope=once is
    # always permitted because the rule lives only for one tool call.
    if scope == "always" and verdict == "allow":
        regex_present = bool(input_regex)
        if tool_name and _is_high_risk_tool(tool_name) and not regex_present:
            raise BareStickyApproveRefusedError(
                f"sticky --always allow for high-risk tool {tool_name!r} " "requires an input_regex"
            )
        # An override ``pattern`` becomes the rule's ``tool`` field, so the
        # same narrowing rule must apply to it — otherwise an approver could
        # smuggle a bare Bash allow via ``--pattern Bash --tool Foo``.
        if pattern and _is_high_risk_tool(pattern) and not regex_present:
            raise BareStickyApproveRefusedError(
                f"sticky --always allow with high-risk pattern {pattern!r} "
                "requires an input_regex"
            )

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
    if input_regex:
        payload["input_regex"] = input_regex
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
        boss: str = "",
    ) -> None:
        if not nick:
            raise ValueError("PermissionBroker requires a non-empty nick")
        self._nick = nick
        # The owning boss, recorded INTO each request so approvers can attribute
        # ownership without re-reading this worker's culture.yaml (which may be
        # missing/corrupt) — keeps team isolation from failing open.
        self._boss = boss or ""
        self._cache: _PolicyCache | None = None

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
            "boss": self._boss,
            "tool_name": tool_name,
            "input": _safe_jsonable(input_dict),
            "created_at": _now_iso(),
        }
        _atomic_write_json(queue_path, payload)

        try:
            decision = await self._await_decision(decision_path, request_id=request_id)
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
            try:
                self._append_sticky_rule(verdict, tool_name, decision)
            except BareStickyApproveRefusedError:
                # Demote-rather-than-fail (Task 5.1c). A sticky --always allow
                # for a high-risk tool without input_regex would whitelist every
                # invocation of the tool; the broker treats this approval as
                # scope=once instead (the in-flight call is still honored) and
                # drops a notice so the boss/dashboard can surface the demote.
                logger.warning(
                    "Demoting sticky allow to scope=once for %s on %s: "
                    "no input_regex for high-risk tool",
                    request_id,
                    tool_name,
                )
                _write_demote_notice(
                    request_id,
                    tool_name,
                    "no input_regex for high-risk tool",
                    boss=self._boss,
                    helper_nick=self._nick,
                )

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

    async def _await_decision(self, decision_path: str, request_id: str = "") -> dict[str, Any]:
        """Wait for the decision file to appear, then return its parsed content.

        Two implementations, chosen at runtime:

        - **watchdog path (Phase 5.6, ``_HAS_WATCHDOG=True``).** Schedule
          a ``watchdog.observers.Observer`` on the decisions directory
          filtered for ``<request_id>.json``. On file creation the
          observer thread schedules ``future.set_result(decision)`` via
          ``loop.call_soon_threadsafe``; the broker awaits the future.
          Wall-clock latency drops from ~125 ms median (half the 250 ms
          poll) to single-digit milliseconds on Linux/macOS.

        - **polling fallback.** When ``watchdog`` is unavailable (or the
          ``CULTURE_DISABLE_WATCHDOG=1`` env override is set), the
          legacy 250 ms poll loop is used. Identical behaviour to the
          pre-Phase-5.6 broker so a minimal-deps deploy still works.

        Timeout behaviour is the same in both paths: after
        ``_PERM_DECISION_TIMEOUT_SECONDS`` of no decision, the broker
        returns a deny with ``auto=True`` and a timeout ``reason``.
        This prevents a dead/unresponsive boss from hanging the
        worker's SDK call forever — the SDK sees an honest deny and the
        agent can proceed (try a different tool, ask the human, or
        exit cleanly).

        Reads are best-effort: a transient ``OSError`` (the file was
        removed between the existence check and the open) or
        ``JSONDecodeError`` (a non-atomic writer mid-write) is
        swallowed and the wait continues. The boss scripts write
        atomically via ``os.replace`` so a complete, valid file is the
        steady state.
        """
        # Fast path: decision already exists. Avoids the cost of
        # spinning up an observer for the (common) case where the
        # boss decided before the worker entered this method.
        decision = self._try_read_decision(decision_path)
        if decision is not None:
            return decision

        if _HAS_WATCHDOG:
            try:
                return await self._await_decision_watchdog(
                    decision_path=decision_path,
                    request_id=request_id,
                )
            except Exception:  # noqa: BLE001 — fall back to polling
                logger.warning(
                    "watchdog path failed for %s; falling back to polling",
                    request_id or decision_path,
                    exc_info=True,
                )
        return await self._await_decision_polling(
            decision_path=decision_path,
            request_id=request_id,
        )

    async def _await_decision_watchdog(
        self,
        decision_path: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Watchdog-backed wait: Observer fires a Future on file create."""
        # Local imports so a watchdog ImportError at runtime falls back
        # cleanly via the outer try/except even when ``_HAS_WATCHDOG``
        # was True at module load time.
        from watchdog.events import PatternMatchingEventHandler
        from watchdog.observers import Observer

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        target_basename = os.path.basename(decision_path)
        target_dir = os.path.dirname(decision_path)

        # Make sure the directory exists so Observer.schedule does not
        # raise. The boss's write_decision creates the directory too,
        # but the broker can be called before any boss decision has
        # been written for the first time.
        _mkdir_secure(target_dir)

        def _try_resolve_from(path: str) -> bool:
            """Read+parse path; resolve the future if successful."""
            decision = self._try_read_decision(path)
            if decision is None:
                return False
            if not future.done():
                future.set_result(decision)
            return True

        class _DecisionHandler(PatternMatchingEventHandler):
            def __init__(self, parent_target: str) -> None:
                super().__init__(
                    patterns=[parent_target, "*.json"],
                    ignore_patterns=[".tmp-*", "*.tmp"],
                    ignore_directories=True,
                    case_sensitive=True,
                )
                self._target = parent_target

            def _maybe(self, path: str) -> None:
                if os.path.basename(path) != self._target:
                    return
                # Schedule the read on the asyncio loop thread so
                # ``future.set_result`` happens on the right thread.
                loop.call_soon_threadsafe(_try_resolve_from, path)

            def on_created(self, event):  # type: ignore[no-untyped-def]
                if not event.is_directory:
                    self._maybe(event.src_path)

            def on_moved(self, event):  # type: ignore[no-untyped-def]
                if event.is_directory:
                    return
                dest = getattr(event, "dest_path", "") or ""
                if dest:
                    self._maybe(dest)

        observer = Observer()
        handler = _DecisionHandler(target_basename)
        observer.schedule(handler, path=target_dir, recursive=False)
        observer.daemon = True
        observer.start()

        try:
            # Re-check: the file could have appeared between the fast
            # path above and the observer starting. Drop the result
            # straight into the future; the observer (if it also fires)
            # will be a no-op via the ``future.done()`` guard.
            if _try_resolve_from(decision_path):
                return future.result()
            try:
                return await asyncio.wait_for(
                    future,
                    timeout=_PERM_DECISION_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Perm-broker timeout on %s: boss did not decide in %ds; auto-deny.",
                    request_id or decision_path,
                    _PERM_DECISION_TIMEOUT_SECONDS,
                )
                return {
                    "verdict": "deny",
                    "reason": (
                        f"timeout: boss did not respond in {_PERM_DECISION_TIMEOUT_SECONDS}s"
                    ),
                    "scope": "once",
                    "auto": True,
                }
        finally:
            try:
                observer.stop()
                observer.join(timeout=2.0)
            except Exception:  # noqa: BLE001
                logger.debug("watchdog Observer teardown raised", exc_info=True)

    async def _await_decision_polling(
        self,
        decision_path: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Legacy 250 ms polling path. Used when watchdog is unavailable."""
        deadline = time.monotonic() + _PERM_DECISION_TIMEOUT_SECONDS
        while True:
            decision = self._try_read_decision(decision_path)
            if decision is not None:
                return decision
            if time.monotonic() >= deadline:
                logger.warning(
                    "Perm-broker timeout on %s: boss did not decide in %ds; auto-deny.",
                    request_id or decision_path,
                    _PERM_DECISION_TIMEOUT_SECONDS,
                )
                return {
                    "verdict": "deny",
                    "reason": (
                        f"timeout: boss did not respond in " f"{_PERM_DECISION_TIMEOUT_SECONDS}s"
                    ),
                    "scope": "once",
                    "auto": True,
                }
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
        """Append a sticky rule to this helper's policy file.

        For ``verdict='allow'`` on a high-risk tool (``Bash``/``Edit``/
        ``Write``/``mcp__*``) the decision MUST carry a non-empty
        ``input_regex`` — a bare sticky allow would whitelist every invocation
        of the tool. The check inspects BOTH ``tool_name`` AND the resolved
        ``decision['pattern']`` so a ``--pattern Bash --tool Foo`` approval
        cannot smuggle a bare Bash allow past the gate.

        Raises :class:`BareStickyApproveRefusedError` on a bare high-risk
        sticky allow. The caller (``_request_from_boss``) catches this and
        demotes the approval to ``scope=once`` (writing a demote notice).
        """
        input_regex = decision.get("input_regex")
        pattern_override = decision.get("pattern") or ""
        if verdict == "allow":
            # Tool itself OR the override pattern: either being high-risk
            # without an input_regex is a bypass surface.
            high_risk_tool = _is_high_risk_tool(tool_name)
            high_risk_pattern = bool(pattern_override) and _is_high_risk_tool(pattern_override)
            if (high_risk_tool or high_risk_pattern) and not input_regex:
                raise BareStickyApproveRefusedError(
                    f"sticky --always allow for high-risk tool "
                    f"{pattern_override or tool_name!r} requires an input_regex"
                )

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
        # otherwise the rule is an exact-tool-name match. A narrowing
        # ``input_regex`` (when present) is copied verbatim.
        rule: dict[str, Any] = {"tool": pattern_override or tool_name}
        if isinstance(input_regex, str) and input_regex:
            rule["input_regex"] = input_regex
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
