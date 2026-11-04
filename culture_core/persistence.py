"""Platform-specific auto-start service generation."""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

LOG_DIR = os.path.expanduser("~/.culture/logs")

logger = logging.getLogger(__name__)

DEFAULT_CMD_TIMEOUT = 30.0

# A unit identifier interpolated into a generated unit file — the service
# ``name`` and any ``After=``/``Wants=`` target — must be free of whitespace,
# path separators, and control characters. Server names reach here from CLI
# ``--name`` and mesh/server config, neither strictly constrained, and a name
# carrying a space or newline would emit a unit file systemd refuses to load
# (silently dropping the intended ordering). Restrict to the character class
# systemd itself permits in a unit name; the ``.`` allows the ``.service``
# suffix on ``After=`` targets.
_UNIT_IDENT_RE = re.compile(r"^[A-Za-z0-9@:._-]+$")


def _validate_unit_identifier(value: str, *, kind: str) -> None:
    """Reject a service ``name`` / ordering target that isn't unit-safe.

    Raises :class:`CultureError` (lazily imported so this low-level module
    stays out of the CLI import graph, as in :func:`_build_systemd_unit`).
    """
    if not _UNIT_IDENT_RE.match(value):
        from culture_core.cli._errors import EXIT_USER_ERROR, CultureError

        raise CultureError(
            EXIT_USER_ERROR,
            f"invalid systemd unit {kind} {value!r}: contains characters not "
            "allowed in a unit name (whitespace, path separators, or control "
            "characters)",
            "use a server name of letters, digits, and '-_.@:' only (set it via "
            "'culture server start --name <name>' or ~/.culture/server.yaml)",
        )


# ---------------------------------------------------------------------------
# Interpreter provenance guard (2026-07-03 outage, always-on-mesh plan t3)
# ---------------------------------------------------------------------------
#
# A generated unit's ExecStart bakes in ``sys.executable``. If that interpreter
# lives in a *dev worktree / repo* virtualenv, removing the checkout leaves the
# unit pointing at a dead path and the service crash-loops (the 2026-07-03
# outage: 11,235 restarts). The guard classifies the interpreter and refuses to
# bake a fragile one unless the operator explicitly overrides.

# Conventional directory names for a *project-local* virtualenv — the fragile
# case. ``python -m venv .venv`` / ``uv venv`` create these inside a repo
# checkout or a git worktree; they vanish when the checkout does.
_PROJECT_VENV_DIRS = frozenset({".venv", "venv"})


class InterpreterClass(Enum):
    """How durable is a Python interpreter path baked into a service unit?

    Three of the four classes are *durable* (safe to bake); only
    :attr:`DEV_VENV` is *fragile* and triggers the provisioning guard.
    """

    #: Under a uv tools directory (``$UV_TOOL_DIR`` or ``.../uv/tools/<tool>/``).
    #: uv owns the venv; it survives repo/worktree churn. Durable.
    UV_TOOL = "uv-tool"
    #: Under a pipx venvs directory (``$PIPX_HOME/venvs`` or ``.../pipx/venvs/``).
    #: pipx owns the venv. Durable.
    PIPX = "pipx"
    #: A global/system interpreter not inside any project virtualenv
    #: (``/usr/bin/python3``, Homebrew, pyenv versions, a bare ``python`` on
    #: PATH, …). Not tied to a checkout. Durable.
    SYSTEM = "system"
    #: Inside a project-local virtualenv — a path component named ``.venv`` or
    #: ``venv`` (a repo checkout or a git worktree). FRAGILE: removing the
    #: checkout orphans the unit's ExecStart. This is the class the guard flags.
    DEV_VENV = "dev-venv"


def _path_parts(path: str) -> list[str]:
    """Split a path into components on either separator, dropping ``.``.

    Works for POSIX and Windows-style paths alike (units are per-platform, and
    a classifier that only understood the host separator couldn't be tested
    against the other platform's paths). Case is preserved.
    """
    return [p for p in re.split(r"[\\/]+", path) if p and p != "."]


def _has_adjacent(parts: list[str], first: str, second: str) -> bool:
    """True iff ``parts`` contains ``first`` immediately followed by ``second``."""
    return any(a == first and b == second for a, b in zip(parts, parts[1:]))


def _is_within(parts: list[str], prefix: list[str]) -> bool:
    """True iff the component list ``parts`` is under the directory ``prefix``."""
    return bool(prefix) and parts[: len(prefix)] == prefix


def classify_interpreter(
    interpreter: str, *, env: "dict[str, str] | None" = None
) -> InterpreterClass:
    """Classify an interpreter path by how durable it is to bake into a unit.

    Pure function — the classification depends only on ``interpreter`` and the
    optional ``env`` hint (never on the ambient process environment), so the
    heuristic is exhaustively unit-testable. ``env`` may carry ``UV_TOOL_DIR``
    and/or ``PIPX_HOME`` to recognize non-default tool locations; when omitted,
    classification is purely path-based.

    Classification rule (checked in order):

    1. **uv tool** (:attr:`InterpreterClass.UV_TOOL`, durable) — the path is
       under ``env['UV_TOOL_DIR']`` (explicit operator signal).
    2. **pipx** (:attr:`InterpreterClass.PIPX`, durable) — the path is under
       ``env['PIPX_HOME']/venvs`` (explicit operator signal).
    3. **dev venv** (:attr:`InterpreterClass.DEV_VENV`, FRAGILE) — a path
       component is exactly ``.venv`` or ``venv`` (a repo checkout or git
       worktree virtualenv). This is the outage class.
    4. **uv tool** (:attr:`InterpreterClass.UV_TOOL`, durable) — contains an
       adjacent ``uv``/``tools`` pair (the default
       ``~/.local/share/uv/tools/<tool>/bin/python`` layout).
    5. **pipx** (:attr:`InterpreterClass.PIPX`, durable) — contains an
       adjacent ``pipx``/``venvs`` pair
       (``~/.local/share/pipx/venvs/<pkg>/bin/python``).
    6. **system** (:attr:`InterpreterClass.SYSTEM`, durable) — anything else.

    Only the explicit ENV-PREFIX checks (steps 1-2) run before the
    ``.venv``/``venv`` check. The uv/pipx *adjacency* heuristics (steps 4-5)
    are weaker signals than an explicit path component named ``.venv``/
    ``venv``, so fragility wins: a project/worktree venv nested under a tree
    that happens to look like ``uv/tools/...`` or ``pipx/venvs/...`` (e.g.
    ``~/repos/uv/tools/culture/.venv/bin/python``) is still a fragile dev venv,
    not a tool-managed one. The real uv-tool/pipx layouts have no ``.venv``/
    ``venv`` component (the venv dir is named after the tool, not ``.venv``),
    so they are unaffected and still resolve via steps 4-5.

    Known limitation: a durable-but-conventionally-named deployment venv (e.g.
    ``/opt/app/venv``) is flagged as fragile. That false positive is the
    accepted risk of a name-based heuristic; the ``--allow-dev-interpreter``
    override on the install verbs is the escape hatch.
    """
    env = env or {}
    parts = _path_parts(interpreter or "")

    uv_tool_dir = env.get("UV_TOOL_DIR")
    if uv_tool_dir and _is_within(parts, _path_parts(uv_tool_dir)):
        return InterpreterClass.UV_TOOL

    pipx_home = env.get("PIPX_HOME")
    if pipx_home and _is_within(parts, [*_path_parts(pipx_home), "venvs"]):
        return InterpreterClass.PIPX

    if any(p in _PROJECT_VENV_DIRS for p in parts):
        return InterpreterClass.DEV_VENV

    if _has_adjacent(parts, "uv", "tools"):
        return InterpreterClass.UV_TOOL

    if _has_adjacent(parts, "pipx", "venvs"):
        return InterpreterClass.PIPX

    return InterpreterClass.SYSTEM


def _enforce_durable_interpreter(
    command: list[str],
    *,
    allow_dev_interpreter: bool,
    env: "dict[str, str] | None" = None,
    what: str = "interpreter",
) -> None:
    """Guard a provisioning command against baking a fragile interpreter.

    ``command[0]`` is the interpreter that will land in the unit's ExecStart.
    When it classifies as :attr:`InterpreterClass.DEV_VENV`:

    * ``allow_dev_interpreter=False`` -> raise :class:`CultureError` (exit 1),
      naming the exact path, before anything is written;
    * ``allow_dev_interpreter=True``  -> log + print a loud warning naming the
      path and proceed (the operator owns the durability risk).

    For any *durable* class this is a pure no-op — the unit is byte-identical to
    what today's code would write.
    """
    if not command:
        return
    interpreter = command[0]
    if classify_interpreter(interpreter, env=env) is not InterpreterClass.DEV_VENV:
        return

    if allow_dev_interpreter:
        logger.warning(
            "Baking a dev-virtualenv %s into a service unit: %s "
            "(--allow-dev-interpreter given; proceeding). If this venv is "
            "removed the unit's ExecStart dies and the service crash-loops.",
            what,
            interpreter,
        )
        print(
            f"WARNING: baking a dev-virtualenv {what} into the service "
            f"unit: {interpreter}\n"
            "         (--allow-dev-interpreter given; proceeding). If this "
            "venv is removed the ExecStart path dies and the service will "
            "crash-loop.",
            file=sys.stderr,
        )
        return

    # Lazy import — keep this low-level module out of the CLI import graph.
    from culture_core.cli._errors import EXIT_USER_ERROR, CultureError

    raise CultureError(
        EXIT_USER_ERROR,
        f"refusing to bake a fragile {what} into the service unit: "
        f"{interpreter} lives inside a project/worktree virtualenv "
        f"(.venv/venv), not an installed tool. If that checkout is removed the "
        f"unit's ExecStart points at a dead path and the service crash-loops "
        f"(the 2026-07-03 outage was exactly this).",
        "install culture as a tool ('uv tool install culture' or "
        "'pipx install culture') and rerun from the installed tool, or pass "
        "--allow-dev-interpreter to bake this path anyway (you own the risk).",
    )


def get_platform() -> str:
    """Detect the current platform."""
    if sys.platform == "darwin":
        return "macos"
    elif sys.platform == "win32":
        return "windows"
    return "linux"


def _systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _launchd_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _windows_service_dir() -> Path:
    return Path(os.path.expandvars(r"%USERPROFILE%\.culture\services"))


def _run_cmd(args: list[str], timeout: float = DEFAULT_CMD_TIMEOUT) -> bool:
    """Run a command, suppressing output.

    Returns True if the command completed (regardless of exit code), False
    if it timed out. A hung systemd unit can make ``systemctl restart``
    block indefinitely, so every caller needs a bounded wait.
    """
    try:
        subprocess.run(args, check=False, capture_output=True, timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        command = shlex.join(args) if args else "<empty command>"
        logger.warning("Command %s timed out after %.0fs", command, timeout)
        return False


# ---------------------------------------------------------------------------
# Builders — generate file content
# ---------------------------------------------------------------------------


def _build_systemd_unit(
    name: str, command: list[str], description: str, after: str | None = None
) -> str:
    exec_start = " ".join(shlex.quote(arg) for arg in command)
    # EXIT_DAEMON_PERMANENT (+ grant's 64 for wrapped units); lazily imported
    # inside the helper to keep this module out of the CLI import graph.
    prevent = _restart_prevent_status(command)
    # After= orders this unit behind *after* (e.g. the server unit) and
    # Wants= pulls it in, so a reboot brings the mesh up server-first.
    # Wants (not Requires): a crashed server must not tear down agents
    # that can outlast a brief server restart via their own reconnects.
    ordering = f"After={after}\nWants={after}\n" if after else ""
    # RestartPreventExitStatus parks the unit in a clear failed state when
    # the daemon child signals a permanent error (exit contract, #15) —
    # transient crashes still self-heal via Restart=on-failure.
    return (
        f"# {name}\n"
        f"[Unit]\n"
        f"Description={description}\n"
        f"{ordering}"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"ExecStart={exec_start}\n"
        f"Restart=on-failure\n"
        f"RestartSec=5\n"
        f"RestartPreventExitStatus={prevent}\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


def _build_launchd_plist(name: str, command: list[str], description: str) -> str:
    # launchd limitation (#15): a plist KeepAlive dict cannot express
    # "restart on failure EXCEPT this exit code", so permanent daemon
    # failures (exit EXIT_DAEMON_PERMANENT) still respawn on macOS. systemd
    # (RestartPreventExitStatus) and the Windows .bat loop both park on it.
    args = "\n".join(f"        <string>{xml_escape(arg)}</string>" for arg in command)
    log_path = os.path.join(LOG_DIR, f"{name}.log")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<!-- {xml_escape(description)} -->\n"
        f'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        f' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0">\n'
        f"<dict>\n"
        f"    <key>Label</key>\n"
        f"    <string>{name}</string>\n"
        f"    <key>ProgramArguments</key>\n"
        f"    <array>\n"
        f"{args}\n"
        f"    </array>\n"
        f"    <key>RunAtLoad</key>\n"
        f"    <true/>\n"
        f"    <key>KeepAlive</key>\n"
        f"    <true/>\n"
        f"    <key>StandardOutPath</key>\n"
        f"    <string>{log_path}</string>\n"
        f"    <key>StandardErrorPath</key>\n"
        f"    <string>{log_path}</string>\n"
        f"</dict>\n"
        f"</plist>\n"
    )


def _build_windows_bat(command: list[str]) -> str:
    # Lazy import — see _build_systemd_unit.
    from culture_core.cli._errors import EXIT_DAEMON_PERMANENT

    cmd_line = subprocess.list2cmdline(command)
    # Parity with systemd's RestartPreventExitStatus (#15): a permanent
    # daemon failure stops the retry loop instead of respawning forever.
    grant_park = (
        f"if %ERRORLEVEL% EQU {GRANT_RUN_FAILED} goto end\n" if is_grant_wrapped(command) else ""
    )
    return (
        f"@echo off\n"
        f":loop\n"
        f"{cmd_line}\n"
        f"if %ERRORLEVEL% EQU 0 goto end\n"
        f"if %ERRORLEVEL% EQU {EXIT_DAEMON_PERMANENT} goto end\n"
        f"{grant_park}"
        f"timeout /t 5\n"
        f"goto loop\n"
        f":end\n"
    )


# ---------------------------------------------------------------------------
# Install / Uninstall / List
# ---------------------------------------------------------------------------


def _install_linux_service(
    name: str, command: list[str], description: str, after: str | None = None
) -> Path:
    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    path = unit_dir / f"{name}.service"
    path.write_text(_build_systemd_unit(name, command, description, after=after))
    _run_cmd(["systemctl", "--user", "daemon-reload"])
    _run_cmd(["systemctl", "--user", "enable", name])
    return path


def _install_macos_service(
    name: str, command: list[str], description: str, after: str | None = None
) -> Path:
    # launchd limitation: user LaunchAgents have no inter-agent ordering
    # primitive, so *after* is accepted and ignored — KeepAlive retries
    # until the server is reachable. Same graceful degradation as
    # RestartPreventExitStatus above.
    del after
    agent_dir = _launchd_dir()
    agent_dir.mkdir(parents=True, exist_ok=True)
    plist_name = f"com.culture.{name}"
    path = agent_dir / f"{plist_name}.plist"
    path.write_text(_build_launchd_plist(plist_name, command, description))
    _run_cmd(["launchctl", "load", str(path)])
    return path


def _install_windows_service(
    name: str, command: list[str], description: str, after: str | None = None
) -> Path:
    # Scheduled tasks run ONLOGON with no inter-task ordering — *after* is
    # accepted and ignored; the .bat retry loop absorbs a not-yet-up server.
    del after
    svc_dir = _windows_service_dir()
    svc_dir.mkdir(parents=True, exist_ok=True)
    bat_path = svc_dir / f"{name}.bat"
    bat_path.write_text(_build_windows_bat(command))
    _run_cmd(
        [
            "schtasks",
            "/Create",
            "/TN",
            f"culture\\{name}",
            "/TR",
            subprocess.list2cmdline(["cmd.exe", "/c", str(bat_path)]),
            "/SC",
            "ONLOGON",
            "/F",
        ]
    )
    return bat_path


_PLATFORM_INSTALLERS = {
    "linux": _install_linux_service,
    "macos": _install_macos_service,
    "windows": _install_windows_service,
}


INJECT_ENV_VAR = "CULTURE_AGENT_SECRET"
# ``grant run`` exits 64 (grant's EXIT_USER_ERROR) when it cannot inject: the
# secret is missing (revoked/disabled account) or the spec is malformed. A
# restart never fixes that, so wrapped units park on it like on 78.
GRANT_RUN_FAILED = 64


def is_grant_wrapped(command: list[str]) -> bool:
    """True for ``<grant> run --inject VAR=SECRET [...] -- <command>``."""
    return _grant_separator(command) is not None


def _grant_separator(command: list[str]) -> int | None:
    """Index of a valid grant wrapper's ``--`` separator, else ``None``."""
    if len(command) < 6 or command[1] != "run":
        return None
    index = 2
    inject_count = 0
    while index < len(command) and command[index] == "--inject":
        if index + 1 >= len(command) or "=" not in command[index + 1]:
            return None
        inject_count += 1
        index += 2
    if inject_count == 0 or index >= len(command) - 1 or command[index] != "--":
        return None
    return index


def grant_unwrap(command: list[str]) -> list[str]:
    """Return the wrapped child argv, or *command* unchanged."""
    separator = _grant_separator(command)
    return command if separator is None else command[separator + 1 :]


def _grant_inject_specs(command: list[str]) -> list[str]:
    separator = _grant_separator(command)
    if separator is None:
        return []
    return command[3:separator:2]


def _prevent_status(wrapped: bool) -> str:
    from culture_core.cli._errors import EXIT_DAEMON_PERMANENT

    if wrapped:
        return f"{EXIT_DAEMON_PERMANENT} {GRANT_RUN_FAILED}"
    return str(EXIT_DAEMON_PERMANENT)


def _restart_prevent_status(command: list[str]) -> str:
    return _prevent_status(is_grant_wrapped(command))


def resolve_grant_bin(*, allow_dev_interpreter: bool | None = None) -> str:
    """Absolute path of the ``grant`` binary to bake into a unit.

    ``grant`` is a culture dependency, so it is taken from the same
    environment as the running interpreter (``uv tool install culture`` does
    not put dependency scripts on PATH); PATH is only a fallback. The path
    goes through the same provenance guard as the interpreter
    (*allow_dev_interpreter*, see :func:`install_service`): a ``grant`` inside
    a repo/worktree ``.venv`` dies with the checkout (the 2026-07-03 outage
    class).
    """
    exe = "grant.exe" if sys.platform == "win32" else "grant"
    sibling = Path(sys.executable).parent / exe
    found = str(sibling) if sibling.is_file() else shutil.which("grant")
    if not found:
        from culture_core.cli._errors import EXIT_ENV_ERROR, CultureError

        raise CultureError(
            EXIT_ENV_ERROR,
            f"grant binary not found next to {sys.executable} or on PATH; cannot wrap "
            "the unit's ExecStart for an agent with an account",
            "reinstall culture so its 'grant' dependency is present "
            "('uv tool install --reinstall culture'), or install it with "
            "'uv tool install grant', then re-run",
        )
    found = os.path.abspath(found)
    if allow_dev_interpreter is not None:
        _enforce_durable_interpreter(
            [found],
            allow_dev_interpreter=allow_dev_interpreter,
            env=dict(os.environ),
            what="grant binary",
        )
    return found


def grant_wrap(
    command: list[str],
    secret: str | None = None,
    grant_bin: str | None = None,
    *,
    allow_dev_interpreter: bool | None = None,
    injects: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Wrap *command* in ``grant run`` with one or several injects.

    The positional *secret* form remains the agent-account shorthand for
    ``CULTURE_AGENT_SECRET=<secret>``.  *injects* carries explicit
    ``(environment variable, grant entry)`` pairs for server link secrets.
    """
    pairs = list(injects or [])
    if secret is not None:
        pairs.insert(0, (INJECT_ENV_VAR, secret))
    if not pairs:
        raise ValueError("grant_wrap requires at least one secret inject")
    inject_argv = [item for env, name in pairs for item in ("--inject", f"{env}={name}")]
    return [
        grant_bin or resolve_grant_bin(allow_dev_interpreter=allow_dev_interpreter),
        "run",
        *inject_argv,
        "--",
        *command,
    ]


_EXEC_KEY = "ExecStart="


def _exec_start_argv(unit_text: str) -> list[str] | None:
    for line in unit_text.splitlines():
        if line.startswith(_EXEC_KEY):
            try:
                return shlex.split(line[len(_EXEC_KEY) :])
            except ValueError:
                return None
    return None


def exec_start_has_inject(unit_text: str, secret: str) -> bool:
    """True if the unit's ExecStart carries the grant inject wrapper for *secret*."""
    return secret in exec_start_injected_secrets(unit_text)


def exec_start_injected_secrets(unit_text: str) -> set[str]:
    """Grant secret names injected by a unit's ExecStart wrapper."""
    argv = _exec_start_argv(unit_text)
    return {spec.partition("=")[2] for spec in _grant_inject_specs(argv or [])}


def unit_exec_start(name: str, *, unit_dir: Path | None = None) -> list[str] | None:
    """ExecStart argv of the installed systemd unit *name*, or None."""
    path = _unit_file(name, unit_dir or _systemd_user_dir())
    if not path.is_file():
        return None
    return _exec_start_argv(path.read_text())


_PREVENT_KEY = "RestartPreventExitStatus="
# Written above a RestartPreventExitStatus= line culture changed while
# wrapping a unit whose line was missing or hand-edited; carries the original
# line (or "absent") so unwrapping restores the unit byte-for-byte.
_PREVENT_MARK = "# culture account wrap; original RestartPreventExitStatus: "
_PREVENT_ABSENT = "absent"


def _find_prefixed(lines: list[str], prefix: str) -> int | None:
    return next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)


def _park_on_grant_failure(lines: list[str], exec_index: int) -> None:
    """Make a wrapped unit park on grant's exit 64 (in place).

    A culture-built line (``78``) becomes ``78 64`` as before; a hand-edited
    line gets `` 64`` appended and a missing line is added under ExecStart
    (inside ``[Service]``) — both behind a marker naming the original.
    """
    if _find_prefixed(lines, _PREVENT_MARK) is not None:
        return  # already handled by an earlier wrap (re-wrap for a new secret)
    wrapped_status = _prevent_status(True)
    idx = _find_prefixed(lines, _PREVENT_KEY)
    if idx is None:
        lines[exec_index + 1 : exec_index + 1] = [
            f"{_PREVENT_MARK}{_PREVENT_ABSENT}\n",
            f"{_PREVENT_KEY}{wrapped_status}\n",
        ]
        return
    original = lines[idx].rstrip("\n")
    tokens = original[len(_PREVENT_KEY) :].split()
    if str(GRANT_RUN_FAILED) in tokens:
        return
    if tokens == _prevent_status(False).split():
        lines[idx] = f"{_PREVENT_KEY}{wrapped_status}\n"
        return
    lines[idx : idx + 1] = [
        f"{_PREVENT_MARK}{original}\n",
        f"{original} {GRANT_RUN_FAILED}\n",
    ]


def _unpark(lines: list[str]) -> None:
    """Undo :func:`_park_on_grant_failure` (in place)."""
    mark = _find_prefixed(lines, _PREVENT_MARK)
    if mark is not None:
        original = lines[mark].rstrip("\n")[len(_PREVENT_MARK) :]
        if original != _PREVENT_ABSENT and not original.startswith(_PREVENT_KEY):
            _refuse_unit_content(
                f"its wrap marker would restore {original!r}, which is not a "
                f"{_PREVENT_KEY} line"
            )
        end = (
            mark + 2
            if mark + 1 < len(lines) and lines[mark + 1].startswith(_PREVENT_KEY)
            else mark + 1
        )
        lines[mark:end] = [] if original == _PREVENT_ABSENT else [f"{original}\n"]
        return
    idx = _find_prefixed(lines, _PREVENT_KEY)
    if idx is not None and lines[idx].rstrip("\n") == f"{_PREVENT_KEY}{_prevent_status(True)}":
        lines[idx] = f"{_PREVENT_KEY}{_prevent_status(False)}\n"


def _rewrite_unit(path: Path, argv: list[str]) -> None:
    """Replace ExecStart with *argv* and align RestartPreventExitStatus with it.

    Wrapping makes the unit park on grant's exit 64 even when its
    ``RestartPreventExitStatus=`` line is missing (legacy unit) or
    hand-edited; unwrapping restores the original line(s) exactly.
    """
    if any(_UNIT_LINE_BREAKERS.intersection(arg) for arg in argv):
        _refuse_unit_content(
            "its ExecStart argument contains a line break or NUL, which would "
            "inject extra directives into the unit"
        )
    lines = path.read_text().splitlines(keepends=True)
    exec_index = _find_prefixed(lines, _EXEC_KEY)
    if exec_index is None:  # pragma: no cover - callers read ExecStart first
        return
    lines[exec_index] = _EXEC_KEY + " ".join(shlex.quote(a) for a in argv) + "\n"
    if is_grant_wrapped(argv):
        _park_on_grant_failure(lines, exec_index)
    else:
        _unpark(lines)
    with path.open("w") as fh:
        fh.write("".join(lines))
    _run_cmd(["systemctl", "--user", "daemon-reload"])


# Characters that would end a unit-file line early (letting a value smuggle a
# new directive into the unit) or that systemd rejects outright.
_UNIT_LINE_BREAKERS = frozenset("\n\r\0")


def _refuse_unit_content(reason: str) -> None:
    from culture_core.cli._errors import EXIT_ENV_ERROR, CultureError

    raise CultureError(
        EXIT_ENV_ERROR,
        f"refusing to rewrite the systemd unit: {reason}",
        "inspect the unit file by hand (it looks hand-edited or tampered with), "
        "restore its ExecStart/RestartPreventExitStatus lines, then re-run",
    )


def _unit_file(name: str, unit_dir: Path | str) -> Path:
    """``<unit_dir>/<name>.service``, confined to *unit_dir*.

    *name* is derived from an agent nick, which comes from config file
    content (culture.yaml / server.yaml / the accounts file). A name carrying
    a path separator or ``..`` must never make culture read, rewrite or
    restore a file outside the systemd user directory, so it has to be a
    plain unit identifier and the joined path must stay directly inside
    *unit_dir* (checked lexically — a unit the operator symlinked in with
    ``systemctl --user link`` is still followed, as before).
    """
    base = Path(unit_dir)
    path = base / f"{name}.service"
    if not _UNIT_IDENT_RE.fullmatch(name) or os.path.normpath(path.parent) != os.path.normpath(
        base
    ):
        from culture_core.cli._errors import EXIT_USER_ERROR, CultureError

        raise CultureError(
            EXIT_USER_ERROR,
            f"invalid systemd unit name {name!r}: it must be a plain unit name "
            f"(letters, digits, '-_.@:') that stays inside {base}",
            "check the agent nick in culture.yaml / ~/.culture/server.yaml",
        )
    return path


def _unit_path(name: str, unit_dir: Path | None) -> Path | None:
    if unit_dir is None:
        if get_platform() != "linux":
            return None
        unit_dir = _systemd_user_dir()
    path = _unit_file(name, unit_dir)
    return path if path.is_file() else None


def wrap_installed_unit(
    name: str,
    secret: str,
    *,
    unit_dir: Path | None = None,
    grant_bin: str | None = None,
    allow_dev_interpreter: bool | None = None,
) -> bool | None:
    """Rewrite an installed systemd unit's ExecStart with the grant wrapper.

    Returns ``None`` when no unit is installed (nothing to update), ``True``
    when rewritten (daemon-reload issued; the running process is NOT
    restarted), ``False`` when already wrapped. Linux/systemd only; other
    platforms return ``None``. A unit wrapped for a different secret is
    re-wrapped for *secret*.
    """
    path = _unit_path(name, unit_dir)
    if path is None:
        return None
    argv = _exec_start_argv(path.read_text())
    if not argv:
        return None
    if exec_start_has_inject(path.read_text(), secret):
        return False
    if is_grant_wrapped(argv):
        argv = grant_unwrap(argv)
    _rewrite_unit(
        path, grant_wrap(argv, secret, grant_bin, allow_dev_interpreter=allow_dev_interpreter)
    )
    return True


def unwrap_installed_unit(
    name: str, secret: str | None = None, *, unit_dir: Path | None = None
) -> bool | None:
    """Restore an installed unit's original (unwrapped) ExecStart.

    Inverse of :func:`wrap_installed_unit`: ``None`` when no unit is
    installed, ``True`` when the wrapper was removed (daemon-reload issued),
    ``False`` when the unit was not wrapped (for *secret*, when given).
    """
    path = _unit_path(name, unit_dir)
    if path is None:
        return None
    argv = _exec_start_argv(path.read_text())
    if not argv or not is_grant_wrapped(argv):
        return False
    if secret is not None and not exec_start_has_inject(path.read_text(), secret):
        return False
    _rewrite_unit(path, grant_unwrap(argv))
    return True


def read_unit(name: str, *, unit_dir: Path | None = None) -> str | None:
    """Text of the installed systemd unit *name*, or None (for :func:`restore_unit`)."""
    path = _unit_path(name, unit_dir)
    return path.read_text() if path is not None else None


def restore_unit(name: str, text: str | None, *, unit_dir: Path | None = None) -> bool:
    """Put back unit text saved by :func:`read_unit` (daemon-reload if it changed)."""
    path = _unit_path(name, unit_dir)
    if path is None or text is None:
        return False
    if path.read_text() != text:
        path.write_text(text)
        _run_cmd(["systemctl", "--user", "daemon-reload"])
    return True


def service_active_state(name: str) -> str | None:
    """systemd ``ActiveState`` of a user unit (``active``, ``failed``, ...), or None."""
    if get_platform() != "linux":
        return None
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", "-p", "ActiveState", "--value", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=DEFAULT_CMD_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _systemctl_unit(verb: list[str], name: str) -> bool:
    """``systemctl --user <verb...> <name>`` for an installed unit (Linux only)."""
    if get_platform() != "linux" or not (_systemd_user_dir() / f"{name}.service").exists():
        return False
    return _run_cmd(["systemctl", "--user", *verb, name])


def stop_service(name: str) -> bool:
    return _systemctl_unit(["stop"], name)


def start_service(name: str) -> bool:
    return _systemctl_unit(["start"], name)


def disable_service(name: str) -> bool:
    """Stop the unit and keep it from starting at boot (the file stays)."""
    return _systemctl_unit(["disable", "--now"], name)


def enable_service(name: str) -> bool:
    return _systemctl_unit(["enable"], name)


def service_installed(name: str) -> bool:
    """True if a culture auto-start entry *name* is installed on this platform."""
    return name in list_services()


def install_service(
    name: str,
    command: list[str],
    description: str,
    after: str | None = None,
    *,
    allow_dev_interpreter: bool | None = None,
    inject_secret: str | None = None,
    grant_bin: str | None = None,
    injects: list[tuple[str, str]] | None = None,
) -> Path:
    """Generate and install a platform-specific auto-start entry.

    *after* names a sibling service this one should start after (systemd
    ``After=``/``Wants=``); platforms without an ordering primitive
    (launchd, Windows scheduled tasks) accept and ignore it — their
    retry loops absorb a dependency that isn't up yet.

    *inject_secret* (a grant secret name) wraps ExecStart as
    ``grant run --inject CULTURE_AGENT_SECRET=<name> -- <cmd>`` so the agent's
    account secret reaches only the daemon process (culture#506).

    *injects* accepts explicit ``(environment variable, grant entry)`` pairs;
    server units use it to inject every link secret that already exists in
    grant. Multiple pairs share one wrapper and never place a value in the
    generated unit.

    *allow_dev_interpreter* engages the interpreter-provenance guard on
    ``command[0]`` (the interpreter baked into ExecStart):

    * ``None`` (default) — guard **not** applied. Preserves the behavior of
      callers that predate the guard (e.g. bulk ``mesh setup``); the unit is
      byte-identical to before.
    * ``False`` — enforce: raise :class:`CultureError` if the interpreter is a
      fragile dev/worktree virtualenv, before anything is written.
    * ``True`` — allow: warn loudly (naming the path) and proceed even for a
      fragile interpreter.

    The user-facing install verbs (``culture server/console/agents install``)
    pass ``False`` normally and ``True`` when ``--allow-dev-interpreter`` is
    given, so all three share this one guard.
    """
    _validate_unit_identifier(name, kind="name")
    if after is not None:
        _validate_unit_identifier(after, kind="After= target")
    if allow_dev_interpreter is not None:
        _enforce_durable_interpreter(
            command, allow_dev_interpreter=allow_dev_interpreter, env=dict(os.environ)
        )
    platform = get_platform()
    installer = _PLATFORM_INSTALLERS.get(platform)
    if installer is None:
        raise RuntimeError(f"Unsupported platform: {platform}")
    if inject_secret or injects:
        # Grant injects account/link secrets into the daemon only. The
        # interpreter guard above ran on the bare command; the grant binary
        # gets the same guard.
        command = grant_wrap(
            command,
            inject_secret,
            grant_bin,
            allow_dev_interpreter=allow_dev_interpreter,
            injects=injects,
        )
    return installer(name, command, description, after=after)


def _uninstall_linux_service(name: str) -> bool:
    _run_cmd(["systemctl", "--user", "disable", name])
    _run_cmd(["systemctl", "--user", "stop", name])
    path = _systemd_user_dir() / f"{name}.service"
    removed = path.exists()
    if removed:
        path.unlink()
    _run_cmd(["systemctl", "--user", "daemon-reload"])
    return removed


def _uninstall_macos_service(name: str) -> bool:
    plist_name = f"com.culture.{name}"
    path = _launchd_dir() / f"{plist_name}.plist"
    if path.exists():
        _run_cmd(["launchctl", "unload", str(path)])
        path.unlink()
        return True
    return False


def _uninstall_windows_service(name: str) -> bool:
    # /Delete runs unconditionally to also reap a stray scheduled task
    # whose .bat is already gone (partial state); the return value
    # reports on the .bat, the artifact install_service created.
    _run_cmd(["schtasks", "/Delete", "/TN", f"culture\\{name}", "/F"])
    bat_path = _windows_service_dir() / f"{name}.bat"
    removed = bat_path.exists()
    if removed:
        bat_path.unlink()
    return removed


_PLATFORM_UNINSTALLERS = {
    "linux": _uninstall_linux_service,
    "macos": _uninstall_macos_service,
    "windows": _uninstall_windows_service,
}


def uninstall_service(name: str) -> bool:
    """Remove a platform-specific auto-start entry.

    Returns True if an entry was found and removed, False if there was
    nothing to remove — callers use this for friendly no-op messages.
    """
    _validate_unit_identifier(name, kind="name")
    uninstaller = _PLATFORM_UNINSTALLERS.get(get_platform())
    if uninstaller is None:
        return False
    return uninstaller(name)


def _list_systemd_services() -> list[str]:
    names = []
    unit_dir = _systemd_user_dir()
    if unit_dir.exists():
        for f in unit_dir.iterdir():
            if f.name.startswith("culture-") and f.name.endswith(".service"):
                names.append(f.stem)
    return names


def _list_launchd_services() -> list[str]:
    names = []
    agent_dir = _launchd_dir()
    if agent_dir.exists():
        for f in agent_dir.iterdir():
            if f.name.startswith("com.culture.") and f.name.endswith(".plist"):
                names.append(f.stem.removeprefix("com.culture."))
    return names


def _list_windows_services() -> list[str]:
    names = []
    svc_dir = _windows_service_dir()
    if svc_dir.exists():
        for f in svc_dir.iterdir():
            if f.name.startswith("culture-") and f.name.endswith(".bat"):
                names.append(f.stem)
    return names


_PLATFORM_LISTERS = {
    "linux": _list_systemd_services,
    "macos": _list_launchd_services,
    "windows": _list_windows_services,
}


def list_services() -> list[str]:
    """Return names of installed culture auto-start services."""
    lister = _PLATFORM_LISTERS.get(get_platform())
    return lister() if lister is not None else []


def _restart_linux_service(name: str) -> bool:
    path = _systemd_user_dir() / f"{name}.service"
    if not path.exists():
        return False
    return _run_cmd(["systemctl", "--user", "restart", name])


def _restart_macos_service(name: str) -> bool:
    plist_name = f"com.culture.{name}"
    path = _launchd_dir() / f"{plist_name}.plist"
    if not path.exists():
        return False
    if not _run_cmd(["launchctl", "unload", str(path)]):
        return False
    return _run_cmd(["launchctl", "load", str(path)])


def _restart_windows_service(name: str) -> bool:
    try:
        probe = subprocess.run(
            ["schtasks", "/Query", "/TN", f"culture\\{name}"],
            capture_output=True,
            text=True,
            timeout=DEFAULT_CMD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning("schtasks query for %s timed out", name)
        return False
    if probe.returncode != 0:
        return False
    return _run_cmd(["schtasks", "/Run", "/TN", f"culture\\{name}"])


_PLATFORM_RESTARTERS = {
    "linux": _restart_linux_service,
    "macos": _restart_macos_service,
    "windows": _restart_windows_service,
}


def restart_service(name: str) -> bool:
    """Restart an installed service via the platform service manager.

    Returns True if the restart command was issued, False if no service found.
    """
    restarter = _PLATFORM_RESTARTERS.get(get_platform())
    return restarter(name) if restarter is not None else False
