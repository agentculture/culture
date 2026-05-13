"""Platform-specific auto-start service generation."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

LOG_DIR = os.path.expanduser("~/.culture/logs")

logger = logging.getLogger(__name__)

DEFAULT_CMD_TIMEOUT = 30.0


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


def _build_systemd_unit(name: str, command: list[str], description: str) -> str:
    exec_start = " ".join(shlex.quote(arg) for arg in command)
    return (
        f"# {name}\n"
        f"[Unit]\n"
        f"Description={description}\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"ExecStart={exec_start}\n"
        f"Restart=on-failure\n"
        f"RestartSec=5\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


def _build_launchd_plist(name: str, command: list[str], description: str) -> str:
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
    cmd_line = subprocess.list2cmdline(command)
    return (
        f"@echo off\n"
        f":loop\n"
        f"{cmd_line}\n"
        f"if %ERRORLEVEL% EQU 0 goto end\n"
        f"timeout /t 5\n"
        f"goto loop\n"
        f":end\n"
    )


# ---------------------------------------------------------------------------
# Install / Uninstall / List
# ---------------------------------------------------------------------------


def _install_linux_service(name: str, command: list[str], description: str) -> Path:
    unit_dir = _systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    path = unit_dir / f"{name}.service"
    path.write_text(_build_systemd_unit(name, command, description))
    _run_cmd(["systemctl", "--user", "daemon-reload"])
    _run_cmd(["systemctl", "--user", "enable", name])
    return path


def _install_macos_service(name: str, command: list[str], description: str) -> Path:
    agent_dir = _launchd_dir()
    agent_dir.mkdir(parents=True, exist_ok=True)
    plist_name = f"com.culture.{name}"
    path = agent_dir / f"{plist_name}.plist"
    path.write_text(_build_launchd_plist(plist_name, command, description))
    _run_cmd(["launchctl", "load", str(path)])
    return path


def _install_windows_service(name: str, command: list[str], description: str) -> Path:
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


def install_service(name: str, command: list[str], description: str) -> Path:
    """Generate and install a platform-specific auto-start entry."""
    platform = get_platform()
    installer = _PLATFORM_INSTALLERS.get(platform)
    if installer is None:
        raise RuntimeError(f"Unsupported platform: {platform}")
    return installer(name, command, description)


def _uninstall_linux_service(name: str) -> None:
    _run_cmd(["systemctl", "--user", "disable", name])
    _run_cmd(["systemctl", "--user", "stop", name])
    path = _systemd_user_dir() / f"{name}.service"
    if path.exists():
        path.unlink()
    _run_cmd(["systemctl", "--user", "daemon-reload"])


def _uninstall_macos_service(name: str) -> None:
    plist_name = f"com.culture.{name}"
    path = _launchd_dir() / f"{plist_name}.plist"
    if path.exists():
        _run_cmd(["launchctl", "unload", str(path)])
        path.unlink()


def _uninstall_windows_service(name: str) -> None:
    _run_cmd(["schtasks", "/Delete", "/TN", f"culture\\{name}", "/F"])
    bat_path = _windows_service_dir() / f"{name}.bat"
    if bat_path.exists():
        bat_path.unlink()


_PLATFORM_UNINSTALLERS = {
    "linux": _uninstall_linux_service,
    "macos": _uninstall_macos_service,
    "windows": _uninstall_windows_service,
}


def uninstall_service(name: str) -> None:
    """Remove a platform-specific auto-start entry."""
    uninstaller = _PLATFORM_UNINSTALLERS.get(get_platform())
    if uninstaller is not None:
        uninstaller(name)


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
