"""PID file management for culture daemon instances."""

from __future__ import annotations

import os
import re
import signal
import sys
import tempfile
import time
from pathlib import Path

PID_DIR = os.path.expanduser("~/.culture/pids")
PROC_DIR = "/proc"
_NO_BOOT_ID = "no-boot-id"
_CULTURE_LAUNCHERS = {"culture", "culture-core", "culture_core"}
_CULTURE_MODULES = {"culture", "culture_core"}
_AGENTIRC = "agentirc"
_CULTUREAGENT = "cultureagent"
_AGENTIRC_LAUNCHERS = {_AGENTIRC, "agentirc-cli"}
_SIBLING_MODULES = {_AGENTIRC, _CULTUREAGENT}
_CULTUREAGENT_MODULES = {
    f"{_CULTUREAGENT}.clients.{backend}"
    for backend in ("acp", "claude", "codex", "colleague", "copilot")
}
_PYTHON_PREFIXES = ("python", "pypy")
_PYTHON_FLAGS = {"-b", "-B", "-d", "-E", "-i", "-I", "-O", "-OO", "-q", "-s", "-S", "-u", "-v"}
_PYTHON_VALUE_FLAGS = {"-W", "-X", "--check-hash-based-pycs"}
_POLL_INTERVAL = 0.1


def _safe_name(name: str) -> str:
    """Sanitize a daemon name to prevent path traversal."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", Path(name).name)


def _atomic_write(path: Path, content: str) -> None:
    """Publish *content* without exposing a partially written file."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(temp_path, 0o666 & ~umask)
        os.replace(temp_path, path)  # NOSONAR S2083: caller uses _safe_name
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def write_pid(name: str, pid: int) -> Path:
    """Write a PID file for the named daemon. Creates the directory if needed."""
    pid_dir = Path(PID_DIR)
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path = pid_dir / f"{_safe_name(name)}.pid"
    identity_path = pid_dir / f"{_safe_name(name)}.identity"
    # Remove the old identity before publishing a new PID, including when /proc is unavailable.
    identity_path.unlink(missing_ok=True)
    identity = _process_identity(pid)
    if identity:
        _atomic_write(
            identity_path,  # NOSONAR S2083: _safe_name strips every path component
            f"{pid} {identity[0]} {identity[1]}\n",
        )
    _atomic_write(pid_path, str(pid))  # NOSONAR S2083: _safe_name strips every path component
    return pid_path


def read_pid(name: str) -> int | None:
    """Read the PID for the named daemon. Returns None if file is missing."""
    pid_path = Path(PID_DIR) / f"{_safe_name(name)}.pid"
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return None


def remove_pid(name: str, expected_pid: int | None = None) -> None:
    """Remove a daemon's files only while its PID still matches, when supplied."""
    pid_dir = Path(PID_DIR)
    current = read_pid(name)
    if expected_pid is not None and current not in (expected_pid, None):
        return
    for suffix in (".pid", ".identity"):
        (pid_dir / f"{_safe_name(name)}{suffix}").unlink(missing_ok=True)


def write_port(name: str, port: int) -> Path:
    """Write a port file for the named daemon. Creates the directory if needed."""
    pid_dir = Path(PID_DIR)
    pid_dir.mkdir(parents=True, exist_ok=True)
    port_path = pid_dir / f"{_safe_name(name)}.port"
    port_path.write_text(str(port))
    return port_path


def read_port(name: str) -> int | None:
    """Read the port for the named daemon. Returns None if file is missing."""
    port_path = Path(PID_DIR) / f"{_safe_name(name)}.port"
    if not port_path.exists():
        return None
    try:
        return int(port_path.read_text().strip())
    except (ValueError, OSError):
        return None


def remove_port(name: str) -> None:
    """Remove the port file for the named daemon if it exists."""
    port_path = Path(PID_DIR) / f"{_safe_name(name)}.port"
    try:
        port_path.unlink()
    except FileNotFoundError:
        pass


def is_culture_process(pid: int) -> bool:
    """Check whether the given PID belongs to a culture process.

    Reads the Linux process cmdline and checks NUL-separated argv
    tokens for an exact "culture" match (e.g. argv[0] basename or a
    ``-m culture`` argument). On platforms without the proc filesystem, returns True
    (assumes valid).  On Linux, read/parse failures return False (fail
    closed) to avoid killing unrelated processes after PID reuse.
    """
    if not os.path.isdir(PROC_DIR):
        # The proc filesystem is not available (macOS / Windows) — assume valid.
        return True
    try:
        raw = Path(f"{PROC_DIR}/{pid}/cmdline").read_bytes()
        tokens = [token for token in raw.decode(errors="replace").split("\x00") if token]
        return any(
            os.path.basename(token) in _CULTURE_LAUNCHERS or token in _CULTURE_MODULES
            for token in tokens
        )
    except OSError:
        # On Linux the proc filesystem exists but this PID cannot be read — fail closed.
        return False


def _process_identity(pid: int) -> tuple[str, str] | None:
    """Return Linux start ticks and boot ID, or None if they cannot be read."""
    if not os.path.isdir(PROC_DIR):
        return None
    try:
        _, identity = _read_stat_identity(pid)
        return identity
    except (OSError, IndexError, ValueError):
        return None


def _read_stat_identity(pid: int) -> tuple[str, tuple[str, str]]:
    stat = Path(f"{PROC_DIR}/{pid}/stat").read_bytes()
    # comm may contain parentheses: fields begin after the last closing one.
    fields = stat[stat.rfind(b")") + 2 :].split()
    state = fields[0].decode("ascii")
    start = fields[19].decode("ascii")  # field 22; first remaining field is field 3
    try:
        boot = Path(f"{PROC_DIR}/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        boot = ""
    # Some sandboxes hide the boot id: the start time alone still identifies a PID.
    return state, (start, boot or _NO_BOOT_ID)


def _read_argv(pid: int) -> list[str]:
    raw = Path(f"{PROC_DIR}/{pid}/cmdline").read_bytes()
    return [part for part in raw.decode(errors="replace").split("\x00") if part]


def _python_command_position(argv: list[str]) -> int | None:
    """Return the Python script/module flag position, or None for an opaque command."""
    position = 1
    while position < len(argv):
        arg = argv[position]
        if arg in _PYTHON_FLAGS:
            position += 1
            continue
        if arg in _PYTHON_VALUE_FLAGS:
            position += 2
            continue
        if any(arg.startswith(prefix) and arg != prefix for prefix in ("-W", "-X")):
            position += 1
            continue
        if arg.startswith("--check-hash-based-pycs="):
            position += 1
            continue
        return position
    return None


def _launcher_kind(launcher: str) -> str | None:
    if launcher in _CULTURE_LAUNCHERS:
        return "culture"
    if launcher in _AGENTIRC_LAUNCHERS:
        return _AGENTIRC
    if launcher == _CULTUREAGENT:
        return _CULTUREAGENT
    return None


def _module_kind(module: str) -> str | None:
    if module in _CULTURE_MODULES:
        return "culture"
    if module in _CULTUREAGENT_MODULES:
        return _CULTUREAGENT
    return module if module in _SIBLING_MODULES else None


def _python_launcher_command(argv: list[str], position: int) -> tuple[str, list[str]] | None:
    if position + 1 < len(argv) and argv[position] == "-m":
        kind = _module_kind(argv[position + 1])
        return (kind, argv[position + 2 :]) if kind else None
    if position >= len(argv):
        return None
    kind = _launcher_kind(Path(argv[position]).name)
    return (kind, argv[position + 1 :]) if kind else None


def _launcher_command(argv: list[str]) -> tuple[str, list[str]] | None:
    if not argv:
        return None
    launcher = Path(argv[0]).name
    kind = _launcher_kind(launcher)
    if kind:
        return kind, argv[1:]
    if not launcher.startswith(_PYTHON_PREFIXES):
        return None
    position = _python_command_position(argv)
    return _python_launcher_command(argv, position) if position is not None else None


def _single_agent_nick(args: list[str]) -> str | None:
    """Extract one unambiguous nick from an agent-start argument tail."""
    if "--all" in args:
        return None
    nicks: list[str] = []
    position = 0
    while position < len(args):
        arg = args[position]
        if arg == "--foreground":
            position += 1
        elif arg == "--config":
            position += 2
        elif arg.startswith("--config="):
            position += 1
        elif arg.startswith("-"):
            return None
        else:
            nicks.append(arg)
            position += 1
    return nicks[0] if len(nicks) == 1 else None


def _server_name_matches(args: list[str], name: str) -> bool:
    """Return whether every explicit --name names this server."""
    expected = name.removeprefix("server-")
    values = []
    position = 0
    while position < len(args):
        arg = args[position]
        if arg == "--name":
            if position + 1 >= len(args):
                return False
            values.append(args[position + 1])
            position += 2
            continue
        if arg.startswith("--name="):
            values.append(arg.partition("=")[2])
        position += 1
    return all(value == expected for value in values)


def _is_opaque_python_culture(argv: list[str]) -> bool:
    """True when a Python command names culture but stops the parser at an unknown flag."""
    if not argv or not Path(argv[0]).name.startswith(_PYTHON_PREFIXES):
        return False
    position = _python_command_position(argv)
    if position is None or not argv[position].startswith("-") or argv[position] in ("-c", "-m"):
        return False
    return any(
        Path(arg).name in _CULTURE_LAUNCHERS or arg.removeprefix("-m") in _CULTURE_MODULES
        for arg in argv[position:]
    )


def _legacy_server_role(launcher: str, args: list[str], name: str) -> bool | None:
    if launcher == "culture" and args[:2] in (["server", "start"], ["server", "restart"]):
        server_args = args[2:]
    elif launcher == _AGENTIRC and args[:1] in (["start"], ["restart"]):
        server_args = args[1:]
    else:
        return False
    return True if _server_name_matches(server_args, name) else None


def _legacy_agent_role(launcher: str, args: list[str], name: str) -> bool | None:
    if launcher == "culture" and len(args) >= 2 and args[:2] == ["agents", "start"]:
        agent_args = args[2:]
    elif launcher == _CULTUREAGENT and args[:1] == ["start"]:
        agent_args = args[1:]
    else:
        return False
    nick = _single_agent_nick(agent_args)
    if nick is None:
        return None
    return True if nick == name.removeprefix("agent-") else None


def _legacy_role(pid: int, name: str) -> bool | None:
    """Recognize the command position of an older, plain integer pidfile."""
    argv = _read_argv(pid)
    command = _launcher_command(argv)
    if command is None:
        return None if _is_opaque_python_culture(argv) else False
    launcher, args = command
    if name.startswith("server-"):
        return _legacy_server_role(launcher, args, name)
    if name.startswith("overview-"):
        return launcher == "culture" and len(args) >= 2 and args[:2] == ["mesh", "overview"]
    if name.startswith("agent-"):
        return _legacy_agent_role(launcher, args, name)
    return False


def _read_tgid(pid: int) -> int | None:
    try:
        status = Path(f"{PROC_DIR}/{pid}/status").read_text(errors="replace")
        return next(
            int(line.split()[1]) for line in status.splitlines() if line.startswith("Tgid:")
        )
    except (StopIteration, ValueError, IndexError):
        return None


def _recorded_identity(name: str, pid: int) -> tuple[str, str] | None:
    try:
        fields = (Path(PID_DIR) / f"{_safe_name(name)}.identity").read_text().split()
        if len(fields) == 3 and int(fields[0]) == pid:
            return fields[1], fields[2]
    except (OSError, ValueError):
        pass
    return None


def _classify_legacy(pid: int, name: str) -> str:
    role = _legacy_role(pid, name)
    if role is None:
        return "unknown"
    return "current" if role else "stale"


def _pid_to_classify(name: str, expected_pid: int | None) -> int | str:
    path = Path(PID_DIR) / f"{_safe_name(name)}.pid"
    try:
        content = path.read_text().strip()
    except (FileNotFoundError, UnicodeDecodeError):
        return "stale"
    except OSError:
        return "unknown"
    try:
        pid = int(content)
    except ValueError:
        return "stale"
    if expected_pid is not None and pid != expected_pid:
        return "changed"
    if pid <= 0:
        return "stale"
    return pid


def _identity_matches(recorded: tuple[str, str], identity: tuple[str, str]) -> bool:
    """Compare start times, and boot ids only when both sides could read one."""
    if recorded[0] != identity[0]:
        return False
    return _NO_BOOT_ID in (recorded[1], identity[1]) or recorded[1] == identity[1]


def _classify_linux(pid: int, name: str) -> str:
    try:
        tgid = _read_tgid(pid)
        state, identity = _read_stat_identity(pid)
        if state in {"Z", "X"} or (tgid is not None and tgid != pid):
            return "stale"
        if tgid is None:
            return "unknown"
        recorded = _recorded_identity(name, pid)
        if recorded is not None:
            return "current" if _identity_matches(recorded, identity) else "stale"
        return _classify_legacy(pid, name)
    except (FileNotFoundError, ProcessLookupError):
        return "stale"
    except (OSError, ValueError, IndexError):
        return "unknown"


def classify_pidfile(name: str, expected_pid: int | None = None) -> str:
    """Classify a pidfile as 'current', 'stale', 'unknown', or 'changed'.

    Self-PIDs are stale for both startup and stop, so no stop path signals its
    own process. Unknown means a live PID cannot be safely identified.
    """
    pid = _pid_to_classify(name, expected_pid)
    if isinstance(pid, str):
        return pid
    if pid == os.getpid() or not is_process_alive(pid):
        return "stale"
    if not os.path.isdir(PROC_DIR):
        # No /proc (e.g. macOS): retain the historical live-PID assumption.
        return "current"
    return _classify_linux(pid, name)


def capture_process_identity(pid: int) -> tuple[str, str] | None:
    """Capture the Linux start-time identity used for later safe signalling."""
    return _process_identity(pid) if os.path.isdir(PROC_DIR) else None


def _process_stopped(pid: int) -> bool:
    if not is_process_alive(pid):
        return True
    if not os.path.isdir(PROC_DIR):
        return False
    try:
        state, _ = _read_stat_identity(pid)
    except (FileNotFoundError, ProcessLookupError):
        return True
    except (OSError, ValueError, IndexError):
        return False
    return state in {"Z", "X"}


def _terminate_captured(pid: int, initial: tuple[str, str] | None, timeout: float) -> str:
    try:
        os.kill(pid, signal.SIGTERM)  # NOSONAR S4828: verified PID identity
    except ProcessLookupError:
        return "stopped"
    except PermissionError:
        return "refused"
    for _ in range(max(0, int(timeout / _POLL_INTERVAL))):
        state = _stopped_or_reused(pid, initial)
        if state != "live":
            return state
        time.sleep(_POLL_INTERVAL)
    state = _stopped_or_reused(pid, initial)
    if state != "live":
        return state
    try:
        sig = signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL
        os.kill(pid, sig)  # NOSONAR S4828: start time unchanged since SIGTERM
    except ProcessLookupError:
        return "stopped"
    except PermissionError:
        return "refused"
    for _ in range(10):  # the port can stay held until the process is gone
        if _process_stopped(pid):
            break
        time.sleep(_POLL_INTERVAL)
    return "killed"


def terminate_captured(pid: int, initial: tuple[str, str] | None, timeout: float = 5.0) -> str:
    """Stop only if *pid* still has the captured process start identity."""
    if os.path.isdir(PROC_DIR):
        if initial is None:
            return "stopped" if _process_stopped(pid) else "refused"
        try:
            state, current = _read_stat_identity(pid)
        except (FileNotFoundError, ProcessLookupError):
            return "stopped"
        except (OSError, ValueError, IndexError):
            return "refused"
        if state in {"Z", "X"}:
            return "stopped"
        if current != initial:
            return "refused"
    elif not is_process_alive(pid):
        return "stopped"
    return _terminate_captured(pid, initial, timeout)


def terminate_verified(pid_name: str, pid: int, timeout: float = 5.0) -> str:
    """Stop a verified process; return 'stopped', 'killed', or 'refused'."""
    if classify_pidfile(pid_name, pid) != "current":
        return "stopped" if _process_stopped(pid) else "refused"
    initial = capture_process_identity(pid)
    if os.path.isdir(PROC_DIR) and initial is None:
        return "stopped" if _process_stopped(pid) else "refused"
    return _terminate_captured(pid, initial, timeout)


def _stopped_or_reused(pid: int, initial: tuple[str, str] | None) -> str:
    if not is_process_alive(pid):
        return "stopped"
    if initial is None:
        return "live"  # no /proc on this platform
    try:
        state, current = _read_stat_identity(pid)
    except (FileNotFoundError, ProcessLookupError):
        return "stopped"
    except (OSError, ValueError, IndexError):
        return "live"  # only proof of reuse can veto escalation
    if state in {"Z", "X"}:
        return "stopped"
    return "refused" if current != initial else "live"


def is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID is alive."""
    try:
        os.kill(pid, 0)  # NOSONAR S4828: existence probe only
        return True
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it
        return True


def list_servers() -> list[dict]:
    """List running culture servers.

    Returns list of dicts with keys: name, pid, port.
    """
    pid_dir = Path(PID_DIR)
    if not pid_dir.exists():
        return []
    servers = []
    prefix = "server-"
    for pid_path in sorted(pid_dir.glob(f"{prefix}*.pid")):
        pid_name = pid_path.stem  # e.g. "server-spark"
        name = pid_name[len(prefix) :]  # e.g. "spark"
        pid = read_pid(pid_name)
        if pid is None or classify_pidfile(pid_name, pid) not in ("current", "unknown"):
            continue
        port = read_port(pid_name) or 6667
        servers.append({"name": name, "pid": pid, "port": port})
    return servers


def read_default_server() -> str | None:
    """Read the default server name. Returns None if unset."""
    default_path = Path(PID_DIR) / "default_server"
    if not default_path.exists():
        return None
    try:
        return default_path.read_text().strip() or None
    except OSError:
        return None


def write_default_server(name: str) -> None:
    """Set the default server name."""
    pid_dir = Path(PID_DIR)
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "default_server").write_text(name)


def rename_pid(old_name: str, new_name: str) -> bool:
    """Rename a PID file and its associated port file.

    Best-effort: returns True if at least one file was renamed.
    Logs warnings on failure instead of raising.
    """
    pid_dir = Path(PID_DIR)
    renamed = False
    for suffix in (".pid", ".identity", ".port"):
        old_path = pid_dir / f"{_safe_name(old_name)}{suffix}"
        new_path = pid_dir / f"{_safe_name(new_name)}{suffix}"
        if old_path.exists():
            try:
                old_path.rename(new_path)
                renamed = True
            except OSError:
                pass
    return renamed
