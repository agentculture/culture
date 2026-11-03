"""PID file management for culture daemon instances."""

from __future__ import annotations

import os
import re
from pathlib import Path

PID_DIR = os.path.expanduser("~/.culture/pids")
PROC_DIR = "/proc"


def _safe_name(name: str) -> str:
    """Sanitize a daemon name to prevent path traversal."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", Path(name).name)


def write_pid(name: str, pid: int) -> Path:
    """Write a PID file for the named daemon. Creates the directory if needed."""
    pid_dir = Path(PID_DIR)
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path = pid_dir / f"{_safe_name(name)}.pid"
    identity = _process_identity(pid)
    if identity:
        content = f"{pid}\n{identity[0]} {identity[1]}\n"
    elif os.path.isdir(PROC_DIR):
        # A new file must never be mistaken for a legacy plain-integer file.
        content = f"{pid}\nunknown unknown\n"
    else:
        content = f"{pid}\n"
    pid_path.write_text(content)  # NOSONAR S2083: _safe_name strips every path component
    return pid_path


def read_pid(name: str) -> int | None:
    """Read the PID for the named daemon. Returns None if file is missing."""
    pid_path = Path(PID_DIR) / f"{_safe_name(name)}.pid"
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().splitlines()[0])
    except (ValueError, OSError, IndexError):
        return None


def remove_pid(name: str) -> None:
    """Remove the PID file for the named daemon if it exists."""
    pid_path = Path(PID_DIR) / f"{_safe_name(name)}.pid"
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass


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
            os.path.basename(t) in {"culture", "culture-core", "culture_core"}
            or t in {"culture", "culture_core"}
            for t in tokens
        )
    except OSError:
        # On Linux the proc filesystem exists but this PID cannot be read — fail closed.
        return False


def _process_identity(pid: int) -> tuple[str, str] | None:
    """Return Linux start ticks and boot ID, or None if they cannot be read."""
    if not os.path.isdir(PROC_DIR):
        return None
    try:
        stat = Path(f"{PROC_DIR}/{pid}/stat").read_text()
        # The comm field is parenthesized, but may itself contain parentheses.
        fields = stat[stat.rfind(")") + 2 :].split()
        start = fields[19]  # field 22; first remaining field is field 3
        boot = Path(f"{PROC_DIR}/sys/kernel/random/boot_id").read_text().strip()
        if not boot:
            return None
        return start, boot
    except (OSError, IndexError, ValueError):
        return None


def _legacy_args(pid: int) -> list[str] | None:
    raw = Path(f"{PROC_DIR}/{pid}/cmdline").read_bytes()
    argv = [part for part in raw.decode(errors="replace").split("\x00") if part]
    if not argv:
        return None
    if Path(argv[0]).name in {"culture", "culture-core", "culture_core"}:
        return argv[1:]
    if (
        len(argv) >= 3
        and Path(argv[0]).name.startswith(("python", "pypy"))
        and argv[1:3] in (["-m", "culture"], ["-m", "culture_core"])
    ):
        return argv[3:]
    if (
        len(argv) >= 2
        and Path(argv[1]).name in {"culture", "culture-core", "culture_core"}
        and Path(argv[0]).name.startswith(("python", "pypy"))
    ):
        return argv[2:]
    return None


def _legacy_role(pid: int, name: str) -> bool:
    """Recognize the command position of an older, plain integer pidfile."""
    args = _legacy_args(pid)
    if args is None:
        return False
    if name.startswith("server-"):
        return bool(args and args[0] == "server")
    if name.startswith("overview-"):
        return len(args) >= 2 and args[:2] == ["mesh", "overview"]
    # Older agent files had only a loose culture-process check. Preserve that
    # compatibility without inventing an agent command shape.
    return True


def _read_pidfile(path: Path) -> list[str] | str:
    try:
        return path.read_text().splitlines()
    except FileNotFoundError:
        return "stale"
    except OSError:
        return "unknown"


def _read_tgid(pid: int) -> int | None:
    try:
        status = Path(f"{PROC_DIR}/{pid}/status").read_text(errors="replace")
        return next(
            int(line.split()[1]) for line in status.splitlines() if line.startswith("Tgid:")
        )
    except (OSError, StopIteration, ValueError, IndexError):
        return None


def _compare_identity(lines: list[str], identity: tuple[str, str]) -> str:
    recorded = lines[1].split()
    if len(recorded) != 2 or recorded == ["unknown", "unknown"]:
        return "unknown"
    return "current" if tuple(recorded) == identity else "stale"


def classify_pidfile(name: str, expected_pid: int | None = None) -> str:
    """Classify a pidfile as 'current', 'stale', or 'unknown'.

    Self-PIDs are stale for both startup and stop, so no stop path signals its
    own process. Unknown means a live PID cannot be safely identified.
    """
    path = Path(PID_DIR) / f"{_safe_name(name)}.pid"
    lines = _read_pidfile(path)
    if isinstance(lines, str):
        return lines
    try:
        pid = int(lines[0])
    except (IndexError, ValueError):
        return "stale"
    if pid <= 0 or (expected_pid is not None and pid != expected_pid):
        return "stale"
    if pid == os.getpid() or not is_process_alive(pid):
        return "stale"
    if not os.path.isdir(PROC_DIR):
        return "unknown"
    tgid = _read_tgid(pid)
    if tgid is None:
        return "unknown"
    if tgid != pid:
        return "stale"
    identity = _process_identity(pid)
    if identity is None:
        return "unknown"
    if len(lines) > 1:
        return _compare_identity(lines, identity)
    try:
        return "current" if _legacy_role(pid, name) else "stale"
    except OSError:
        return "unknown"


def is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
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
    for suffix in (".pid", ".port"):
        old_path = pid_dir / f"{_safe_name(old_name)}{suffix}"
        new_path = pid_dir / f"{_safe_name(new_name)}{suffix}"
        if old_path.exists():
            try:
                old_path.rename(new_path)
                renamed = True
            except OSError:
                pass
    return renamed
