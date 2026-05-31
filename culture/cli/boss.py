"""Boss subcommands: ``culture boss {init,spawn,brief,read,pending,approve,deny,audit,log,status,close}``.

The orchestration surface for a *boss agent* — an autonomous culture daemon that
manages worker agents (spawns them, drives them over IRC, and approves/denies
their tool requests bounded by a grant ceiling). Mirrors the IRC skill's
``culture channel`` shape; reuses ``culture.clients._perm_broker`` for all
queue/decision/ceiling operations so there is one implementation.

The boss's own nick comes from ``CULTURE_NICK`` (set by the agent runner).

Design spec: docs/superpowers/specs/2026-05-28-boss-agent-orchestration-design.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys

from culture.clients._audit import audit_path_for
from culture.clients._daemon_log import daemon_log_path_for
from culture.clients._perm_broker import (
    HIGH_RISK_STICKY_TOOLS,
    BareStickyApproveRefusedError,
    DecisionExistsError,
    InvalidRequestIdError,
    _tool_matches,
    cleanup_stale,
    culture_home,
    has_policy_file,
    is_above_ceiling,
    list_pending,
    read_request,
    seed_helper_policy,
    write_decision,
    write_default_boss_ceiling,
)
from culture.config import load_config_or_default

from .shared.constants import DEFAULT_CONFIG
from .shared.ipc import agent_socket_path, get_observer, ipc_request

NAME = "boss"

_ALL_CMDS = (
    "init|spawn|brief|read|pending|approve|deny|audit|log|status|close|cleanup|audit-policies"
)

_MANAGER_PROMPT = """\
You are {nick}, a manager agent on the culture mesh. A human briefs you in your
IRC channel ({channel}); that brief is your mission. You do NOT do the
implementation work yourself — you drive worker agents that do.

On a mission:
1. Read CLAUDE.md and any referenced plan/spec to ground yourself in the
   project's purpose and conventions. Ask clarifying questions in {channel} if
   the brief is ambiguous.
2. Spawn workers (`culture boss spawn <name>`) and drive each like a Claude Code
   session: ask what's open, scope what fits together, tell them to plan, then
   CHALLENGE their plan before they implement, then their implementation, then
   their claims — verify against `culture boss audit <name>`; never take "done"
   on faith.
3. Approve worker tool requests as they arrive (`culture boss approve|deny`).
   Grant `--always` for tools you trust a worker with. Some high-risk tools are
   above your grant ceiling — when `culture boss approve` refuses, do NOT retry;
   post the request to your human in {channel} and let them grant it.
4. Report progress and blockers to your human in {channel}. Escalate genuine
   judgment calls and above-ceiling requests; handle the rest yourself.

When you approach your context limit you'll be asked to write a handoff and
reminded to re-read it — re-ground on the mission, CLAUDE.md, and the plan, not
just the last few messages.
"""


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("boss", help="Boss-agent orchestration of worker agents")
    sub = p.add_subparsers(dest="boss_command")

    init_p = sub.add_parser("init", help="Create/refresh this boss agent's identity")
    init_p.add_argument("--nick", default="boss", help="Boss suffix (default: boss)")
    init_p.add_argument("--server", default=None, help="Server name (default: from config)")
    init_p.add_argument("--channel", default="#boss", help="Boss channel (default: #boss)")
    init_p.add_argument("--cwd", default=None, help="Boss working directory")
    init_p.add_argument(
        "--model",
        default="",
        help="Boss model (workers inherit it). Set this to your own model so the "
        "team runs on the parent model.",
    )
    init_p.add_argument("--config", default=DEFAULT_CONFIG)

    spawn_p = sub.add_parser("spawn", help="Create + start a worker under this boss")
    spawn_p.add_argument("name", help="Worker suffix (becomes <server>-<name>)")
    spawn_p.add_argument("--cwd", default=None, help="Worker working directory")
    spawn_p.add_argument("--server", default=None)
    spawn_p.add_argument(
        "--model", default="", help="Worker model (default: inherit the boss's model)"
    )
    spawn_p.add_argument(
        "--channels",
        default="",
        help="Extra channels for the worker to join (comma-separated, e.g. '#joint-fixes,#design')",
    )
    spawn_p.add_argument(
        "--role",
        default="",
        help='Worker role declaration, e.g. --role "qa-runner" or --role "stack-dev". '
        "Free-text; written to culture.yaml and surfaced on the dashboard. "
        "Optional — when omitted the worker has no role tag.",
    )
    spawn_p.add_argument(
        "--topic",
        default="",
        help="Channel topic for #task-<name> (v8.19.18). Sets the IRC TOPIC at "
        "spawn time and is shown as the task title in the dashboard's channels "
        "view. Optional — when omitted the title falls back to mission.md "
        "headline or `<nick>'s work`.",
    )
    spawn_p.add_argument("--config", default=DEFAULT_CONFIG)

    brief_p = sub.add_parser("brief", help="Send a task to a worker's channel")
    brief_p.add_argument("name", help="Worker suffix")
    brief_p.add_argument("task", help="Task text")

    note_p = sub.add_parser(
        "note",
        help="Append a non-task note to a channel's living brief (v8.19.24)",
    )
    note_p.add_argument(
        "channel_or_name",
        help="Channel name (e.g. '#team') OR a worker suffix (note lands in #task-<name>)",
    )
    note_p.add_argument("text", help="Note text — appended as a dated section to the channel brief")
    note_p.add_argument("--title", default="note", help="Section title; defaults to 'note'")

    read_p = sub.add_parser("read", help="Read recent worker channel messages")
    read_p.add_argument("name", help="Worker suffix")
    read_p.add_argument("--limit", "-n", type=int, default=30)

    sub.add_parser("pending", help="List pending worker permission requests")

    approve_p = sub.add_parser("approve", help="Grant a worker permission request")
    approve_p.add_argument("id", help="Request id")
    approve_p.add_argument("--always", action="store_true", help="Save a sticky allow rule")
    approve_p.add_argument("--pattern", default="", help="Tool pattern for the sticky rule")
    approve_p.add_argument(
        "--input-regex",
        default="",
        dest="input_regex",
        help=(
            "Input regex to constrain a sticky --always rule. Required for "
            "high-risk tools (Edit/Write/Bash/mcp__.*); without it those tools "
            "would auto-allow every future call regardless of input. "
            "Example: --input-regex '^ls(\\s|$)'"
        ),
    )

    deny_p = sub.add_parser("deny", help="Deny a worker permission request")
    deny_p.add_argument("id", help="Request id")
    deny_p.add_argument("reason", nargs="*", help="Reason (shown to the worker)")

    audit_p = sub.add_parser("audit", help="Read a worker's agent-message audit log")
    audit_p.add_argument("name", help="Worker suffix")
    audit_p.add_argument("--limit", "-n", type=int, default=30)

    log_p = sub.add_parser("log", help="Read a worker's daemon-action log")
    log_p.add_argument("name", help="Worker suffix")
    log_p.add_argument("--limit", "-n", type=int, default=30)

    sub.add_parser("status", help="Summarize workers + pending perms")

    sub.add_parser(
        "audit-policies",
        help="Scan worker perm-policy files for dangerously-bare high-risk auto_allow rules",
    )

    close_p = sub.add_parser("close", help="Stop a worker daemon")
    close_p.add_argument("name", help="Worker suffix")

    cleanup_p = sub.add_parser(
        "cleanup", help="GC stale permission requests (dead helpers) + orphan decisions"
    )
    cleanup_p.add_argument("--config", default=DEFAULT_CONFIG)


def dispatch(args: argparse.Namespace) -> None:
    if not getattr(args, "boss_command", None):
        print(f"Usage: culture boss {{{_ALL_CMDS}}}", file=sys.stderr)
        sys.exit(1)
    handlers = {
        "init": _cmd_init,
        "spawn": _cmd_spawn,
        "brief": _cmd_brief,
        "note": _cmd_note,
        "read": _cmd_read,
        "pending": _cmd_pending,
        "approve": _cmd_approve,
        "deny": _cmd_deny,
        "audit": _cmd_audit,
        "log": _cmd_log,
        "audit-policies": _cmd_audit_policies,
        "status": _cmd_status,
        "close": _cmd_close,
        "cleanup": _cmd_cleanup,
    }
    handler = handlers.get(args.boss_command)
    if not handler:
        print(f"Unknown boss command: {args.boss_command}", file=sys.stderr)
        sys.exit(1)
    handler(args)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _boss_nick() -> str:
    nick = os.environ.get("CULTURE_NICK", "")
    if not nick:
        print(
            "Error: CULTURE_NICK is not set. `culture boss` must run as the boss "
            "agent (the daemon sets CULTURE_NICK) or you must export it.",
            file=sys.stderr,
        )
        sys.exit(1)
    return nick


def _server_of(nick: str) -> str:
    return nick.split("-", 1)[0] if "-" in nick else "local"


# Worker suffixes become file paths via audit_path_for/daemon_log_path_for and
# IRC channel/nick names — validate every one that comes from argv so "../x" or
# "a/b" can't escape (path traversal). Same shape as a sanitized agent suffix.
_SUFFIX_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _require_worker_suffix(name: str) -> str:
    if not name or not _SUFFIX_RE.fullmatch(name):
        print(
            f"Error: invalid worker name {name!r} "
            "(use lowercase letters, digits, hyphens; must start alphanumeric)",
            file=sys.stderr,
        )
        sys.exit(1)
    return name


def _require_server(name: str) -> str:
    if not name or not _SUFFIX_RE.fullmatch(name):
        print(
            f"Error: invalid server name {name!r} "
            "(use lowercase letters, digits, hyphens; must start alphanumeric)",
            file=sys.stderr,
        )
        sys.exit(1)
    return name


def _task_channel(name: str) -> str:
    return f"#task-{name}"


def _owner_map() -> dict[str, str]:
    """Map of worker nick -> owning boss nick ('' if unowned), from the manifest.

    Each worker records its boss in its ``culture.yaml`` (``boss:`` field, written
    at spawn). The fallback is pinned to the same path so an absent manifest does
    not leak into the real ``~/.culture`` during tests.
    """
    server_yaml = os.path.join(culture_home(), "server.yaml")
    try:
        config = load_config_or_default(server_yaml, fallback=server_yaml)
    except Exception:  # noqa: BLE001 — unreadable manifest → treat as no ownership
        return {}
    return {a.nick: (getattr(a, "boss", "") or "") for a in config.agents}


def _foreign_worker(worker_nick: str, boss: str, owners: dict[str, str] | None = None) -> bool:
    """True iff ``worker_nick`` is NOT owned by ``boss`` (per the manifest).

    Ownership is derived from the manifest (``server.yaml`` + each worker's
    ``culture.yaml`` ``boss`` field), which is written by ``culture boss spawn``
    and is not worker-writable at runtime. A worker absent from the manifest is
    foreign to every boss — fail closed — because we cannot authoritatively
    attribute it. Use ``culture boss adopt <name>`` (planned) to claim an
    orphan deliberately.
    """
    owner = (owners if owners is not None else _owner_map()).get(worker_nick, "")
    return owner != boss


def _request_is_foreign(req: dict, boss: str) -> bool:
    """True iff a request belongs to another boss's worker, OR no boss at all.

    SECURITY: ownership is derived from the MANIFEST, NOT from ``req['boss']``.
    The request payload is worker-written — a buggy or malicious worker could
    forge ``boss: <other-boss>`` to route its tool requests to a different
    team's approver (escalation by spoofing). The manifest is spawn-recorded
    and not worker-writable, so it's the only safe source.

    A request whose ``helper_nick`` has no manifest entry is foreign to every
    boss (fail closed). Adopt orphans explicitly rather than approving them
    silently.
    """
    helper_nick = req.get("helper_nick", "")
    if not helper_nick:
        return True
    return _foreign_worker(helper_nick, boss)


def _boss_irc(msg_type: str, **kwargs) -> dict | None:
    """Route an IRC op through the boss daemon's own socket."""
    sock = agent_socket_path(_boss_nick())
    return asyncio.run(ipc_request(sock, msg_type, **kwargs))


def _tail_jsonl(path: str, limit: int) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _input_preview(tool: str, input_dict: dict) -> str:
    if tool == "Bash":
        value = input_dict.get("command", "")
    elif tool in ("Edit", "Write"):
        value = input_dict.get("file_path", "")
    else:
        try:
            value = json.dumps(input_dict)
        except (TypeError, ValueError):
            value = repr(input_dict)
    return str(value)[:70]


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _cmd_pending(args: argparse.Namespace) -> None:
    # A boss sees only its own team's requests. Without CULTURE_NICK (e.g. a bare
    # operator invocation) we don't filter — the dashboard is the all-teams view.
    boss = os.environ.get("CULTURE_NICK", "")
    reqs = list_pending()
    if boss:
        reqs = [r for r in reqs if not _request_is_foreign(r, boss)]
    if not reqs:
        return
    print(f"{'ID':<34}  {'WORKER':<16}  {'TOOL':<10}  INPUT")
    for r in reqs:
        print(
            f"{r.get('id', '?'):<34}  {r.get('helper_nick', '?'):<16}  "
            f"{r.get('tool_name', '?'):<10}  {_input_preview(r.get('tool_name', ''), r.get('input', {}))}"
        )


def _cmd_approve(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    req = read_request(args.id)
    if req is None:
        print(f"Error: no pending request {args.id}", file=sys.stderr)
        sys.exit(1)
    worker = req.get("helper_nick", "")
    if _request_is_foreign(req, boss):
        print(
            f"REFUSED: {worker} is not your worker (owned by another boss). "
            "Each boss manages only its own team.",
            file=sys.stderr,
        )
        sys.exit(2)
    tool = req.get("tool_name", "")
    if is_above_ceiling(tool, req.get("input", {}), boss):
        print(
            f"REFUSED: {tool} is above your grant ceiling. Do not retry — escalate "
            f"to your human in your boss channel and let them approve request "
            f"{args.id} from the Mission Control dashboard (the human is the top "
            f"authority and can grant above-ceiling tools).",
            file=sys.stderr,
        )
        sys.exit(2)
    scope = "always" if args.always else "once"
    try:
        write_decision(
            args.id,
            verdict="allow",
            scope=scope,
            pattern=args.pattern,
            input_regex=args.input_regex,
            tool_name=tool,
            decided_by=boss,
        )
    except InvalidRequestIdError:
        print(f"Error: invalid request id {args.id!r}", file=sys.stderr)
        sys.exit(1)
    except DecisionExistsError:
        print(f"Error: a decision already exists for {args.id}", file=sys.stderr)
        sys.exit(1)
    except BareStickyApproveRefusedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(2)
    print(f"approved {args.id} (scope={scope})")


def _cmd_deny(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    req = read_request(args.id)
    # Mirror approve: refuse a missing/unreadable request rather than writing an
    # orphan decision for an id that was never queued.
    if req is None:
        print(f"Error: no pending request {args.id}", file=sys.stderr)
        sys.exit(1)
    if _request_is_foreign(req, boss):
        print(
            f"REFUSED: {req.get('helper_nick', '?')} is not your worker "
            "(owned by another boss). Each boss manages only its own team.",
            file=sys.stderr,
        )
        sys.exit(2)
    reason = " ".join(args.reason) if args.reason else ""
    try:
        write_decision(args.id, verdict="deny", scope="once", reason=reason, decided_by=boss)
    except InvalidRequestIdError:
        print(f"Error: invalid request id {args.id!r}", file=sys.stderr)
        sys.exit(1)
    except DecisionExistsError:
        print(f"Error: a decision already exists for {args.id}", file=sys.stderr)
        sys.exit(1)
    print(f"denied {args.id}" + (f": {reason}" if reason else ""))


def _cmd_audit(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    nick = f"{_server_of(boss)}-{_require_worker_suffix(args.name)}"
    # Team isolation: a boss may only read its own workers' audit log. Same
    # gate as approve/deny/brief/close.
    if _foreign_worker(nick, boss):
        print(
            f"REFUSED: {nick} is not your worker (owned by another boss).",
            file=sys.stderr,
        )
        sys.exit(2)
    rows = _tail_jsonl(audit_path_for(nick), args.limit)
    if not rows:
        print(f"No audit entries for {nick}")
        return
    for r in rows:
        text = (r.get("text") or "").replace("\n", " ")[:120]
        tools = ",".join(t.get("name", "") for t in r.get("tool_uses", []))
        suffix = f"  [tools: {tools}]" if tools else ""
        print(f"{r.get('ts', '')}  {text}{suffix}")


def _cmd_log(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    nick = f"{_server_of(boss)}-{_require_worker_suffix(args.name)}"
    # Team isolation: a boss may only read its own workers' daemon-log.
    if _foreign_worker(nick, boss):
        print(
            f"REFUSED: {nick} is not your worker (owned by another boss).",
            file=sys.stderr,
        )
        sys.exit(2)
    rows = _tail_jsonl(daemon_log_path_for(nick), args.limit)
    if not rows:
        print(f"No daemon-log entries for {nick}")
        return
    for r in rows:
        detail = r.get("detail", {})
        detail_str = " ".join(f"{k}={v}" for k, v in detail.items()) if detail else ""
        print(f"{r.get('ts', '')}  {r.get('action', '?'):<18}  {detail_str}")


def _bare_high_risk_rules(policy: dict) -> list[dict]:
    """Return the auto_allow rules that match a high-risk tool with no input
    constraint — i.e. rules that auto-allow EVERY future call of a tool that
    can mutate state or reach external services."""
    if not isinstance(policy, dict):
        return []
    rules = policy.get("auto_allow", []) or []
    if not isinstance(rules, list):
        return []
    findings: list[dict] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        tool_pattern = rule.get("tool", "")
        if not isinstance(tool_pattern, str):
            continue
        if rule.get("input_regex"):
            continue
        # The rule's tool is the literal/pattern the boss approved. We treat
        # it as high-risk if it MATCHES one of the high-risk tool patterns
        # (e.g. literal "Bash" matches "Bash"; literal "mcp__playwright__x"
        # matches "mcp__.*"). Bare safe tools (Read, Glob, Grep) are fine
        # without input_regex and are excluded here.
        for hr_pattern in HIGH_RISK_STICKY_TOOLS:
            if _tool_matches(tool_pattern, hr_pattern):
                findings.append(rule)
                break
    return findings


def _cmd_audit_policies(args: argparse.Namespace) -> None:  # noqa: ARG001 — argparse signature
    """Scan worker perm-policy files for bare high-risk auto_allow rules.

    Companion to v8.19.32's ``BareStickyApproveRefusedError``: existing
    policies may carry rules that pre-date the new gate (e.g. earlier
    ``culture boss approve <id> --always`` calls that wrote
    ``- tool: Bash`` with no ``input_regex``). Each such rule auto-allows
    EVERY future invocation of that tool. This verb surfaces them so the
    boss can remediate by editing the policy file by hand and removing
    the bare entry — next time the worker invokes that tool the broker
    will re-route to the boss, who can re-approve with ``--input-regex``.
    """
    import glob

    policy_dir = os.path.join(culture_home(), "perm-policy")
    if not os.path.isdir(policy_dir):
        print(f"No policy directory at {policy_dir} — nothing to audit.")
        return

    files = sorted(glob.glob(os.path.join(policy_dir, "*.yaml")))
    total_findings = 0
    for path in files:
        try:
            with open(path, encoding="utf-8") as handle:
                import yaml

                policy = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError):
            print(f"  ! {path} — could not parse; skipping")
            continue
        findings = _bare_high_risk_rules(policy)
        if not findings:
            continue
        nick = os.path.basename(path)[: -len(".yaml")]
        print(f"\n{nick}  ({path})")
        for rule in findings:
            print(f"    DANGEROUS: tool={rule.get('tool')!r}  (no input_regex)")
        total_findings += len(findings)

    if total_findings == 0:
        print(f"No dangerous bare-tool rules found across {len(files)} policy file(s). ✓")
    else:
        print(
            f"\n{total_findings} dangerous rule(s) found. "
            "Edit each policy file by hand and delete the listed entry; the "
            "worker will re-route the tool to the boss on next use, and the "
            "v8.19.32 gate will require --input-regex on the re-approval."
        )


def _channel_members(channel: str) -> list[str]:
    """Nicks currently in a channel (via a transient observer WHO)."""
    return asyncio.run(get_observer(DEFAULT_CONFIG).who(channel))


def _cmd_brief(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    name = _require_worker_suffix(args.name)
    nick = f"{_server_of(boss)}-{name}"
    channel = _task_channel(name)
    # Team isolation: a boss may only brief its own workers (same gate as
    # approve/deny/close), so it can't inject tasks into another team's worker.
    if _foreign_worker(nick, boss):
        print(
            f"REFUSED: {nick} is not your worker (owned by another boss). "
            "Each boss manages only its own team.",
            file=sys.stderr,
        )
        sys.exit(2)
    # Honesty check: a brief is only "delivered" if the worker is actually in the
    # channel to hear it. Without this, briefing a worker that never joined
    # #task-<name> (e.g. one started ad-hoc into #general, not via `culture boss
    # spawn`) silently succeeds and the boss wrongly believes work has begun.
    try:
        members = _channel_members(channel)
    except Exception as exc:  # noqa: BLE001 — can't verify → don't claim delivery
        print(
            f"Error: could not verify {channel} membership ({exc}); brief NOT sent. "
            "Is the mesh server running?",
            file=sys.stderr,
        )
        sys.exit(1)
    if nick not in members:
        print(
            f"Error: {nick} is not in {channel} — brief NOT delivered. Spawn it with "
            f"`culture boss spawn {name}` (which joins it to {channel}) and confirm it "
            "is running before briefing.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Prefix the worker nick so its mention detector fires.
    text = f"@{nick} {args.task}"
    resp = _boss_irc("irc_send", channel=channel, message=text)
    if resp and resp.get("ok"):
        print(f"briefed {nick} in {channel}")
        # v8.19.18: capture the first brief as the channel seed (and set
        # the IRC TOPIC if neither --topic at spawn nor a prior brief
        # already claimed it). persist_seed is idempotent — write-once.
        from culture.clients._seed import load_seed, persist_seed

        if load_seed(channel) is None:
            if persist_seed(channel, args.task):
                irc_topic = " ".join(args.task.split())
                # Truncate for the topic line — IRC topic is a single
                # line; the full seed text lives in the file.
                if len(irc_topic) > 200:
                    irc_topic = irc_topic[:197] + "..."
                _boss_irc("irc_topic", channel=channel, topic=irc_topic)
        # v8.19.24: ALWAYS append to the LIVING channel brief — even
        # after the seed is written. The seed is the original mission;
        # the living brief is the running onboarding doc that a new
        # joiner reads. Every brief is a project decision worth
        # capturing.
        from culture.clients._channel_brief import persist_section

        persist_section(channel, f"brief → {nick}", args.task)
    else:
        # v8.19.23: actionable error. Without this the orchestrator has to
        # guess: is the IRC server down? Did the worker crash? Is "boss
        # daemon" a thing distinct from "boss identity"? Tell them exactly
        # what to run.
        boss = _boss_nick()
        print(
            f"Error: could not brief {nick} — the boss daemon ({boss}) is not\n"
            f"reachable over IPC. Start it with:\n"
            f"\n"
            f"    culture agent start {boss}\n"
            f"\n"
            f"Then re-run this brief.",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_note(args: argparse.Namespace) -> None:
    """Append a note to a channel's living brief (v8.19.24).

    `channel_or_name` accepts EITHER a channel name (starts with #) or
    a worker suffix (resolves to #task-<suffix>). The latter is the
    common case when the orchestrator is leaving notes for a worker's
    private room.
    """
    from culture.clients._channel_brief import persist_section

    target = args.channel_or_name
    channel = target if target.startswith("#") else _task_channel(target)
    ok = persist_section(channel, args.title, args.text)
    if ok:
        print(f"appended note to {channel}'s living brief")
    else:
        # Empty body or idempotence hit.
        print(
            f"note skipped (empty body or already present in {channel}'s brief)",
            file=sys.stderr,
        )


def _cmd_read(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    name = _require_worker_suffix(args.name)
    # Team isolation: a boss may only read its own workers' channels.
    if _foreign_worker(f"{_server_of(boss)}-{name}", boss):
        print(
            f"REFUSED: {_server_of(boss)}-{name} is not your worker " "(owned by another boss).",
            file=sys.stderr,
        )
        sys.exit(2)
    channel = _task_channel(name)
    resp = _boss_irc("irc_read", channel=channel, limit=args.limit)
    if not resp or not resp.get("ok"):
        print(f"Error: could not read {channel}", file=sys.stderr)
        sys.exit(1)
    for msg in resp.get("data", {}).get("messages", []):
        print(f"<{msg.get('nick', '???')}> {msg.get('text', '')}")


def _cmd_status(args: argparse.Namespace) -> None:
    # v8.19.23: lead with the BOSS's own daemon state. The orchestrator
    # needs to know "can I brief workers right now?" — the brief verb
    # depends on the boss daemon being up, but the worker table doesn't
    # surface that. The header line collapses the question into one row.
    from culture.pidfile import is_process_alive, read_pid

    boss = _boss_nick()
    boss_pid = read_pid(f"agent-{boss}")
    boss_alive = bool(boss_pid and is_process_alive(boss_pid))
    boss_state = "running" if boss_alive else "stopped"
    pid_label = str(boss_pid) if boss_alive else "-"
    print(f"BOSS  {boss:<28} {boss_state:<12} {pid_label}")
    if not boss_alive:
        print(f"      ↳ brief/approve/deny will fail until you run: culture agent start {boss}")
    print()
    sys.stdout.flush()  # flush before subprocess so the boss row prints first under pipes
    # Worker/agent states come from `culture agent status`; pending perms from
    # the queue.
    subprocess.run([sys.executable, "-m", "culture", "agent", "status"], check=False)
    reqs = list_pending()
    if reqs:
        print(f"\n{len(reqs)} pending permission request(s) — run: culture boss pending")


def _cmd_spawn(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    # Validate BOTH the worker suffix and the server before they touch any path —
    # both flow into worker_nick = f"{server}-{name}" → seed_helper_policy →
    # policy_path_for, so an unsanitized "../x" in either escapes CULTURE_HOME.
    server = _require_server(args.server) if args.server else _server_of(boss)
    name = _require_worker_suffix(args.name)
    worker_nick = f"{server}-{name}"
    if worker_nick == boss:
        print("Error: a boss cannot spawn a worker with its own nick", file=sys.stderr)
        sys.exit(1)
    cwd = args.cwd or os.path.join(culture_home(), "helpers", name)
    os.makedirs(cwd, exist_ok=True)

    # Create + start the worker via the agent CLI, then seed its policy and
    # record its boss.
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "culture",
            "agent",
            "create",
            "--server",
            server,
            "--nick",
            name,
            "--agent",
            "claude",
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if create.returncode != 0 and "already exists" not in (create.stderr + create.stdout):
        print(f"Error creating worker: {create.stderr or create.stdout}", file=sys.stderr)
        sys.exit(1)
    seed_helper_policy(worker_nick)
    # A worker imitates its parent (boss) — both MODEL and THINKING/effort.
    # An explicit --model overwrites; an inherited value only fills in (never
    # clobbers what the worker's culture.yaml already carries). Inherited
    # values come from the boss's daemon-log (its RUNTIME model+thinking),
    # not its yaml — that way there are no hardcoded model strings anywhere
    # in the inheritance chain.
    explicit_model = bool(args.model)
    inherited_model, inherited_thinking = _boss_inherits()
    model = args.model or inherited_model
    thinking = inherited_thinking
    # Parse extra channels from --channels flag. parse_channels_arg
    # validates each entry against CR/LF/NUL/space/comma/bell injection
    # (Qodo PR #30 #2 — security). An invalid entry hard-fails with
    # InvalidIRCTarget rather than being silently sanitized so the
    # operator sees the offending input.
    from culture.agentirc.irc_targets import InvalidIRCTarget, parse_channels_arg

    try:
        extra_channels = parse_channels_arg(args.channels)
    except InvalidIRCTarget as exc:
        print(f"Invalid --channels argument: {exc}", file=sys.stderr)
        sys.exit(2)
    _record_worker_boss(
        cwd,
        name,
        boss,
        model=model,
        thinking=thinking,
        overwrite_model=explicit_model,
        extra_channels=extra_channels,
        role=args.role,
    )
    # v8.19.23: pass --suffix so the register call succeeds when the cwd has
    # a multi-agent culture.yaml. Previously this printed "Multiple agents
    # in <path> — use --suffix" and continued, looking like a noisy error
    # while the worker actually came up. Spawn already KNOWS the suffix
    # (it's `name`), so passing it through is free.
    subprocess.run(
        [sys.executable, "-m", "culture", "agent", "register", cwd, "--suffix", name],
        check=False,
    )
    subprocess.run([sys.executable, "-m", "culture", "agent", "start", worker_nick], check=False)
    # Boss joins the worker's task channel so it sees replies + perm DMs.
    task_chan = _task_channel(name)
    _boss_irc("irc_join", channel=task_chan)
    for ch in extra_channels:
        _boss_irc("irc_join", channel=ch)
    joined = [task_chan] + extra_channels
    # v8.19.18: --topic sets the IRC TOPIC and writes the channel seed
    # so the dashboard can surface the original mission in its channel
    # card without re-reading the entire HISTORY. Optional: omit to
    # have the dashboard fall back to the mission.md headline.
    topic = (getattr(args, "topic", "") or "").strip()
    if topic:
        from culture.clients._seed import persist_seed

        # IRC TOPIC is single-line; collapse whitespace so a multi-line
        # --topic still fits the protocol. The seed file keeps the
        # original text including line breaks.
        irc_topic = " ".join(topic.split())
        _boss_irc("irc_topic", channel=task_chan, topic=irc_topic)
        persist_seed(task_chan, topic, overwrite=True)
    print(f"spawned {worker_nick} (boss={boss}, cwd={cwd}); channels {', '.join(joined)}")


def _boss_inherits() -> tuple[str, str]:
    """The boss's RUNTIME (model, thinking), read from its daemon-log.

    The daemon-log's last ``agent_start`` record captures what the boss is
    currently running with — that's the only honest source of truth for
    "what the parent looks like right now." A worker spawned from this boss
    inherits these values verbatim, so the worker imitates the boss exactly
    with no hardcoded model strings in code or yaml anywhere along the way.

    When the boss's YAML omits ``model`` (the inheritance-friendly default)
    the ``agent_start`` record carries an empty string. In that case we
    fall back to the most recent ``model_resolved`` action — the daemon
    latches that the moment the SDK's first AssistantMessage names a
    model — so the worker inherits the SDK-resolved runtime model rather
    than the SDK CLI's hardcoded default.

    When the daemon-log is unreadable or has no agent_start (boss never
    started), returns ``("", "")`` — caller writes empty fields and lets the
    SDK pick the current Claude at the worker's startup.
    """
    boss = _boss_nick()
    log_path = daemon_log_path_for(boss)
    if not os.path.exists(log_path):
        return ("", "")
    try:
        with open(log_path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return ("", "")
    model = ""
    thinking = ""
    resolved_model_after_start = ""
    saw_agent_start = False
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        action = rec.get("action")
        detail = rec.get("detail") or {}
        if not saw_agent_start and action == "model_resolved":
            candidate = detail.get("model")
            if isinstance(candidate, str) and candidate:
                # Only the most-recent model_resolved AFTER the latest
                # agent_start counts. We're iterating in reverse, so any
                # model_resolved we see before hitting agent_start belongs
                # to the current session.
                if not resolved_model_after_start:
                    resolved_model_after_start = candidate
            continue
        if action == "agent_start":
            saw_agent_start = True
            yaml_model = detail.get("model")
            yaml_thinking = detail.get("thinking")
            model = yaml_model if isinstance(yaml_model, str) else ""
            thinking = yaml_thinking if isinstance(yaml_thinking, str) else ""
            break
    # No ``agent_start`` was found → honor the docstring contract that the
    # caller gets ("", "") in this case. Any ``model_resolved`` we saw without
    # an anchoring start record is orphaned (the file is corrupt, truncated,
    # or pre-dates the v8.18.6 instrumentation) and must NOT propagate. Per
    # Qodo PR #24 #4.
    if not saw_agent_start:
        return ("", "")
    # YAML had no model → fall back to whatever the SDK resolved at runtime.
    if not model and resolved_model_after_start:
        model = resolved_model_after_start
    return (model, thinking)


# Back-compat alias — old name returned just the model string.
def _boss_model() -> str:
    return _boss_inherits()[0]


def _record_worker_boss(
    cwd: str,
    suffix: str,
    boss: str,
    model: str = "",
    thinking: str = "",
    overwrite_model: bool = False,
    extra_channels: list[str] | None = None,
    role: str = "",
) -> None:
    """Write boss/suffix/channels (and model+thinking+role, if given) into the worker's culture.yaml.

    An explicit model (``overwrite_model=True``, i.e. ``--model``) is always
    written; an inherited model OR thinking only fills in when the worker has
    none, so a re-spawn never clobbers what the operator hand-set on the worker.

    *role* is a free-text role declaration (``--role "qa-runner"`` etc).
    Written unconditionally when non-empty — a re-spawn with a new role
    overwrites the previous one (intentional; the operator may re-task the
    worker).
    """
    import yaml

    path = os.path.join(cwd, "culture.yaml")
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except OSError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    # If the directory holds a MULTI-agent culture.yaml (an `agents:` list — which
    # happens when several workers share one project dir), the loader uses that
    # list, so boss/channels MUST be written into this worker's entry inside it.
    # Writing them top-level would be silently shadowed → the worker lands
    # unassigned in #general instead of #task-<suffix> and can never be briefed.
    if isinstance(data.get("agents"), list):
        entry = next(
            (a for a in data["agents"] if isinstance(a, dict) and a.get("suffix") == suffix),
            None,
        )
        if entry is None:
            entry = {"suffix": suffix, "backend": "claude"}
            data["agents"].append(entry)
        target = entry
        # Drop any stray top-level single-agent fields a prior buggy write left.
        for stray in ("suffix", "boss", "channels", "model", "thinking"):
            data.pop(stray, None)
    else:
        data.setdefault("suffix", suffix)
        data.setdefault("backend", "claude")
        target = data

    target["boss"] = boss
    base_channels = ["#team", _task_channel(suffix)]
    if extra_channels:
        for ch in extra_channels:
            if ch not in base_channels:
                base_channels.append(ch)
    target["channels"] = base_channels
    if model and (overwrite_model or "model" not in target):
        target["model"] = model
    if thinking and "thinking" not in target:
        target["thinking"] = thinking
    if role:
        target["role"] = role
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _cmd_close(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    worker_nick = f"{_server_of(boss)}-{_require_worker_suffix(args.name)}"
    # Only a parent closes its children: a boss can't close itself or another
    # boss's worker. (The underlying `culture agent stop` enforces this too.)
    if worker_nick == boss:
        print("Error: a boss cannot close itself", file=sys.stderr)
        sys.exit(2)
    if _foreign_worker(worker_nick, boss):
        print(
            f"REFUSED: {worker_nick} is not your worker (owned by another boss).",
            file=sys.stderr,
        )
        sys.exit(2)
    # Report the delegate's actual result — don't claim "closed" if the underlying
    # `culture agent stop` refused (e.g. authority guard) or failed.
    res = subprocess.run(
        [sys.executable, "-m", "culture", "agent", "stop", worker_nick],
        check=False,
        capture_output=True,
        text=True,
    )
    if res.returncode == 0:
        print(f"closed {worker_nick}")
    else:
        detail = (res.stderr or res.stdout or "").strip()
        print(f"Error: could not close {worker_nick}: {detail}", file=sys.stderr)
        sys.exit(res.returncode)


def _cmd_cleanup(args: argparse.Namespace) -> None:
    from culture.pidfile import is_process_alive, read_pid

    config = load_config_or_default(args.config)
    running = {
        a.nick
        for a in config.agents
        if (pid := read_pid(f"agent-{a.nick}")) and is_process_alive(pid)
    }
    result = cleanup_stale(running)
    print(
        f"cleanup: removed {result['stale_requests']} stale request(s), "
        f"{result['orphan_decisions']} orphan decision(s)."
    )


def _cmd_init(args: argparse.Namespace) -> None:
    # Validate nick + server before they flow into file paths (boss_policy_path_for,
    # the boss cwd). Worker suffixes are already validated; the boss's own --nick/
    # --server must be too, or `--nick ../../x` becomes an arbitrary-file-write.
    suffix = _require_worker_suffix(args.nick)
    server = _require_server(args.server) if args.server else "local"
    nick = f"{server}-{suffix}"
    cwd = args.cwd or os.path.join(culture_home(), "boss")
    os.makedirs(cwd, exist_ok=True)

    # Deadlock guard: a boss must NOT be permission-supervised, or its own
    # `culture boss approve` calls would themselves require approval.
    if has_policy_file(nick):
        os.remove(_perm_policy_path(nick))
        print(f"warning: removed stray perm-policy for boss {nick}", file=sys.stderr)

    write_default_boss_ceiling(nick)
    _write_boss_yaml(cwd, suffix, nick, args.channel, model=args.model)
    _copy_boss_skill(cwd)
    subprocess.run([sys.executable, "-m", "culture", "agent", "register", cwd], check=False)
    print(
        f"boss {nick} initialized (cwd={cwd}, channel={args.channel}). "
        f"Start it: culture agent start {nick}, then brief it in {args.channel}."
    )


def _perm_policy_path(nick: str) -> str:
    from culture.clients._perm_broker import policy_path_for

    return policy_path_for(nick)


def _write_boss_yaml(cwd: str, suffix: str, nick: str, channel: str, model: str = "") -> None:
    import yaml

    path = os.path.join(cwd, "culture.yaml")
    data = {
        "suffix": suffix,
        "backend": "claude",
        "channels": ["#team", channel],
        "system_prompt": _MANAGER_PROMPT.format(nick=nick, channel=channel),
        "tags": ["boss"],
    }
    if model:
        data["model"] = model
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _copy_boss_skill(cwd: str) -> None:
    """Copy the in-repo boss SKILL.md into the boss cwd's .claude/skills/boss/."""
    import shutil

    src = os.path.join(os.path.dirname(__file__), "..", "clients", "claude", "skill", "boss")
    src = os.path.abspath(src)
    if not os.path.isdir(src):
        return
    dest = os.path.join(cwd, ".claude", "skills", "boss")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
