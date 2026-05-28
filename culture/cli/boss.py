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
    DecisionExistsError,
    InvalidRequestIdError,
    culture_home,
    has_policy_file,
    is_above_ceiling,
    list_pending,
    read_request,
    seed_helper_policy,
    write_decision,
    write_default_boss_ceiling,
)

from .shared.constants import DEFAULT_CONFIG
from .shared.ipc import agent_socket_path, ipc_request

NAME = "boss"

_ALL_CMDS = "init|spawn|brief|read|pending|approve|deny|audit|log|status|close"

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
    init_p.add_argument("--config", default=DEFAULT_CONFIG)

    spawn_p = sub.add_parser("spawn", help="Create + start a worker under this boss")
    spawn_p.add_argument("name", help="Worker suffix (becomes <server>-<name>)")
    spawn_p.add_argument("--cwd", default=None, help="Worker working directory")
    spawn_p.add_argument("--server", default=None)
    spawn_p.add_argument("--config", default=DEFAULT_CONFIG)

    brief_p = sub.add_parser("brief", help="Send a task to a worker's channel")
    brief_p.add_argument("name", help="Worker suffix")
    brief_p.add_argument("task", help="Task text")

    read_p = sub.add_parser("read", help="Read recent worker channel messages")
    read_p.add_argument("name", help="Worker suffix")
    read_p.add_argument("--limit", "-n", type=int, default=30)

    sub.add_parser("pending", help="List pending worker permission requests")

    approve_p = sub.add_parser("approve", help="Grant a worker permission request")
    approve_p.add_argument("id", help="Request id")
    approve_p.add_argument("--always", action="store_true", help="Save a sticky allow rule")
    approve_p.add_argument("--pattern", default="", help="Tool pattern for the sticky rule")

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

    close_p = sub.add_parser("close", help="Stop a worker daemon")
    close_p.add_argument("name", help="Worker suffix")


def dispatch(args: argparse.Namespace) -> None:
    if not getattr(args, "boss_command", None):
        print(f"Usage: culture boss {{{_ALL_CMDS}}}", file=sys.stderr)
        sys.exit(1)
    handlers = {
        "init": _cmd_init,
        "spawn": _cmd_spawn,
        "brief": _cmd_brief,
        "read": _cmd_read,
        "pending": _cmd_pending,
        "approve": _cmd_approve,
        "deny": _cmd_deny,
        "audit": _cmd_audit,
        "log": _cmd_log,
        "status": _cmd_status,
        "close": _cmd_close,
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


def _task_channel(name: str) -> str:
    return f"#task-{name}"


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
    reqs = list_pending()
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
    tool = req.get("tool_name", "")
    if is_above_ceiling(tool, req.get("input", {}), boss):
        print(
            f"REFUSED: {tool} is above your grant ceiling. Do not retry — escalate "
            f"to your human in your boss channel and let them run "
            f"`approve.sh {args.id}` (or `culture boss` with a widened ceiling).",
            file=sys.stderr,
        )
        sys.exit(2)
    scope = "always" if args.always else "once"
    try:
        write_decision(args.id, verdict="allow", scope=scope, pattern=args.pattern, decided_by=boss)
    except InvalidRequestIdError:
        print(f"Error: invalid request id {args.id!r}", file=sys.stderr)
        sys.exit(1)
    except DecisionExistsError:
        print(f"Error: a decision already exists for {args.id}", file=sys.stderr)
        sys.exit(1)
    print(f"approved {args.id} (scope={scope})")


def _cmd_deny(args: argparse.Namespace) -> None:
    boss = _boss_nick()
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
    nick = f"{_server_of(_boss_nick())}-{_require_worker_suffix(args.name)}"
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
    nick = f"{_server_of(_boss_nick())}-{_require_worker_suffix(args.name)}"
    rows = _tail_jsonl(daemon_log_path_for(nick), args.limit)
    if not rows:
        print(f"No daemon-log entries for {nick}")
        return
    for r in rows:
        detail = r.get("detail", {})
        detail_str = " ".join(f"{k}={v}" for k, v in detail.items()) if detail else ""
        print(f"{r.get('ts', '')}  {r.get('action', '?'):<18}  {detail_str}")


def _cmd_brief(args: argparse.Namespace) -> None:
    name = _require_worker_suffix(args.name)
    nick = f"{_server_of(_boss_nick())}-{name}"
    channel = _task_channel(name)
    # Prefix the worker nick so its mention detector fires.
    text = f"@{nick} {args.task}"
    resp = _boss_irc("irc_send", channel=channel, message=text)
    if resp and resp.get("ok"):
        print(f"briefed {nick} in {channel}")
    else:
        print(f"Error: could not brief {nick} (is the boss daemon running?)", file=sys.stderr)
        sys.exit(1)


def _cmd_read(args: argparse.Namespace) -> None:
    channel = _task_channel(_require_worker_suffix(args.name))
    resp = _boss_irc("irc_read", channel=channel, limit=args.limit)
    if not resp or not resp.get("ok"):
        print(f"Error: could not read {channel}", file=sys.stderr)
        sys.exit(1)
    for msg in resp.get("data", {}).get("messages", []):
        print(f"<{msg.get('nick', '???')}> {msg.get('text', '')}")


def _cmd_status(args: argparse.Namespace) -> None:
    # Worker/agent states come from `culture agent status`; pending perms from
    # the queue.
    subprocess.run([sys.executable, "-m", "culture", "agent", "status"], check=False)
    reqs = list_pending()
    if reqs:
        print(f"\n{len(reqs)} pending permission request(s) — run: culture boss pending")


def _cmd_spawn(args: argparse.Namespace) -> None:
    boss = _boss_nick()
    server = args.server or _server_of(boss)
    # Validate the worker suffix before it touches any path — `name` flows into
    # os.path.join(...helpers, name); an unsanitized value like "../x" would
    # escape the helpers dir and let a (mis-briefed) boss write outside it.
    name = _require_worker_suffix(args.name)
    worker_nick = f"{server}-{name}"
    if worker_nick == boss:
        print("Error: a boss cannot spawn a worker with its own nick", file=sys.stderr)
        sys.exit(1)
    cwd = args.cwd or os.path.join(culture_home(), "helpers", name)
    os.makedirs(cwd, exist_ok=True)

    # Create + start the worker via the agent CLI (same operations as
    # spawn-helper.sh), then seed its policy and record its boss.
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
    _record_worker_boss(cwd, name, boss)
    subprocess.run([sys.executable, "-m", "culture", "agent", "register", cwd], check=False)
    subprocess.run([sys.executable, "-m", "culture", "agent", "start", worker_nick], check=False)
    # Boss joins the worker's task channel so it sees replies + perm DMs.
    _boss_irc("irc_join", channel=_task_channel(name))
    print(f"spawned {worker_nick} (boss={boss}, cwd={cwd}); channel {_task_channel(name)}")


def _record_worker_boss(cwd: str, suffix: str, boss: str) -> None:
    """Write boss/suffix/channels into the worker's culture.yaml."""
    import yaml

    path = os.path.join(cwd, "culture.yaml")
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except OSError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("suffix", suffix)
    data.setdefault("backend", "claude")
    data["boss"] = boss
    data["channels"] = ["#team", _task_channel(suffix)]
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _cmd_close(args: argparse.Namespace) -> None:
    worker_nick = f"{_server_of(_boss_nick())}-{_require_worker_suffix(args.name)}"
    subprocess.run([sys.executable, "-m", "culture", "agent", "stop", worker_nick], check=False)
    print(f"closed {worker_nick}")


def _cmd_init(args: argparse.Namespace) -> None:
    server = args.server or "local"
    nick = f"{server}-{args.nick}"
    cwd = args.cwd or os.path.join(culture_home(), "boss")
    os.makedirs(cwd, exist_ok=True)

    # Deadlock guard: a boss must NOT be permission-supervised, or its own
    # `culture boss approve` calls would themselves require approval.
    if has_policy_file(nick):
        os.remove(_perm_policy_path(nick))
        print(f"warning: removed stray perm-policy for boss {nick}", file=sys.stderr)

    write_default_boss_ceiling(nick)
    _write_boss_yaml(cwd, args.nick, nick, args.channel)
    _copy_boss_skill(cwd)
    subprocess.run([sys.executable, "-m", "culture", "agent", "register", cwd], check=False)
    print(
        f"boss {nick} initialized (cwd={cwd}, channel={args.channel}). "
        f"Start it: culture agent start {nick}, then brief it in {args.channel}."
    )


def _perm_policy_path(nick: str) -> str:
    from culture.clients._perm_broker import policy_path_for

    return policy_path_for(nick)


def _write_boss_yaml(cwd: str, suffix: str, nick: str, channel: str) -> None:
    import yaml

    path = os.path.join(cwd, "culture.yaml")
    data = {
        "suffix": suffix,
        "backend": "claude",
        "channels": ["#team", channel],
        "system_prompt": _MANAGER_PROMPT.format(nick=nick, channel=channel),
        "tags": ["boss"],
    }
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
