"""PID file management for culture daemon instances."""

from __future__ import annotations

import os
import re
from pathlib import Path

PID_DIR = os.path.expanduser("~/.culture/pids")
PROC_DIR = "/proc"
_CULTURE_LAUNCHERS = {"culture", "culture-core", "culture_core"}
_CULTURE_MODULES = {"culture", "culture_core"}
_PYTHON_PREFIXES = ("python", "pypy")


def _safe_name(name: str) -> str:
    """Sanitize a daemon name to prevent path traversal."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", Path(name).name)


def write_pid(name: str, pid: int) -> Path:
    """Write a PID file for the named daemon. Creates the directory if needed."""
    pid_dir = Path(PID_DIR)
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path = pid_dir / f"{_safe_name(name)}.pid"
    identity_path = pid_dir / f"{_safe_name(name)}.identity"
    # Remove the old identity before publishing a new PID, including when /proc is unavailable.
    identity_path.unlink(missing_ok=True)
    pid_path.write_text(str(pid))  # NOSONAR S2083: _safe_name strips every path component
    identity = _process_identity(pid)
    if identity:
        identity_path.write_text(  # NOSONAR S2083: _safe_name strips every path component
            f"{pid} {identity[0]} {identity[1]}\n"
        )
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


def remove_pid(name: str) -> None:
    """Remove the PID file for the named daemon if it exists."""
    pid_dir = Path(PID_DIR)
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
        tokens = [t for t in raw.decode(errors="replace").split("\x00") if t]
        return any(
            os.path.basename(t) in _CULTURE_LAUNCHERS or t in _CULTURE_MODULES for t in tokens
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
    stat = Path(f"{PROC_DIR}/{pid}/stat").read_text()
    # comm may contain parentheses: fields begin after the last closing one.
    fields = stat[stat.rfind(")") + 2 :].split()
    state = fields[0]
    start = fields[19]  # field 22; first remaining field is field 3
    try:
        boot = Path(f"{PROC_DIR}/sys/kernel/random/boot_id").read_text().strip()
    except (FileNotFoundError, ProcessLookupError) as exc:
        # A missing global boot ID does not imply that this PID has exited.
        raise OSError("boot ID unavailable") from exc
    if not boot:
        raise ValueError("empty boot ID")
    return state, (start, boot)


def _legacy_args(pid: int) -> list[str] | None:
    raw = Path(f"{PROC_DIR}/{pid}/cmdline").read_bytes()
    argv = [part for part in raw.decode(errors="replace").split("\x00") if part]
    if not argv:
        return None
    if Path(argv[0]).name in _CULTURE_LAUNCHERS:
        return argv[1:]
    if Path(argv[0]).name.startswith(_PYTHON_PREFIXES):
        position = 1
        while position < len(argv) and argv[position] in {"-u", "-B", "-E", "-s", "-S", "-I"}:
            position += 1
        if (
            position + 1 < len(argv)
            and argv[position] == "-m"
            and argv[position + 1] in _CULTURE_MODULES
        ):
            return argv[position + 2 :]
    if (
        len(argv) >= 2
        and Path(argv[1]).name in _CULTURE_LAUNCHERS
        and Path(argv[0]).name.startswith(_PYTHON_PREFIXES)
    ):
        return argv[2:]
    return None


def _legacy_role(pid: int, name: str) -> bool | None:
    """Recognize the command position of an older, plain integer pidfile."""
    args = _legacy_args(pid)
    if args is None:
        return None
    if name.startswith("server-"):
        return bool(args and args[0] == "server")
    if name.startswith("overview-"):
        return len(args) >= 2 and args[:2] == ["mesh", "overview"]
    return bool(args and args[0] == "agents")


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
        return "unknown" if is_culture_process(pid) else "stale"
    return "current" if role else "stale"


def _pid_to_classify(name: str, expected_pid: int | None) -> int | str:
    path = Path(PID_DIR) / f"{_safe_name(name)}.pid"
    try:
        content = path.read_text().strip()
    except FileNotFoundError:
        return "stale"
    except OSError:
        return "unknown"
    try:
        pid = int(content)
    except ValueError:
        return "stale"
    if pid <= 0 or (expected_pid is not None and pid != expected_pid):
        return "stale"
    return pid


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
            return "current" if recorded == identity else "stale"
        return _classify_legacy(pid, name)
    except (FileNotFoundError, ProcessLookupError):
        return "stale"
    except (OSError, ValueError, IndexError):
        return "unknown"


def classify_pidfile(name: str, expected_pid: int | None = None) -> str:
    """Classify a pidfile as 'current', 'stale', or 'unknown'.

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


def is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
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
        if pid is None or classify_pidfile(pid_name, pid) != "current":
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
