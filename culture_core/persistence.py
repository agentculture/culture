"""Platform-specific auto-start service generation."""

from __future__ import annotations

import logging
import os
import re
import shlex
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
    command: list[str], *, allow_dev_interpreter: bool, env: "dict[str, str] | None" = None
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
            "Baking a dev-virtualenv interpreter into a service unit: %s "
            "(--allow-dev-interpreter given; proceeding). If this venv is "
            "removed the unit's ExecStart dies and the service crash-loops.",
            interpreter,
        )
        print(
            "WARNING: baking a dev-virtualenv interpreter into the service "
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
        f"refusing to bake a fragile interpreter into the service unit: "
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
    # Lazy import: keep this low-level module from pulling in the whole CLI
    # package (culture_core.cli.__init__ imports every command group) at load.
    from culture_core.cli._errors import EXIT_DAEMON_PERMANENT

    exec_start = " ".join(shlex.quote(arg) for arg in command)
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
        f"RestartPreventExitStatus={EXIT_DAEMON_PERMANENT}\n"
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
    return (
        f"@echo off\n"
        f":loop\n"
        f"{cmd_line}\n"
        f"if %ERRORLEVEL% EQU 0 goto end\n"
        f"if %ERRORLEVEL% EQU {EXIT_DAEMON_PERMANENT} goto end\n"
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


def install_service(
    name: str,
    command: list[str],
    description: str,
    after: str | None = None,
    *,
    allow_dev_interpreter: bool | None = None,
) -> Path:
    """Generate and install a platform-specific auto-start entry.

    *after* names a sibling service this one should start after (systemd
    ``After=``/``Wants=``); platforms without an ordering primitive
    (launchd, Windows scheduled tasks) accept and ignore it — their
    retry loops absorb a dependency that isn't up yet.

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
