"""Platform-specific auto-start service generation."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

LOG_DIR = os.path.expanduser("~/.agentirc/logs")


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
    return Path(os.path.expandvars(r"%USERPROFILE%\.agentirc\services"))


def _run_cmd(args: list[str]) -> None:
    """Run a command, suppressing output."""
    subprocess.run(args, check=False, capture_output=True)


# ---------------------------------------------------------------------------
# Builders — generate file content
# ---------------------------------------------------------------------------

def _build_systemd_unit(name: str, command: list[str], description: str) -> str:
    exec_start = " ".join(shlex.quote(arg) for arg in command)
    return (
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

def install_service(name: str, command: list[str], description: str) -> Path:
    """Generate and install a platform-specific auto-start entry."""
    platform = get_platform()

    if platform == "linux":
        unit_dir = _systemd_user_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        path = unit_dir / f"{name}.service"
        path.write_text(_build_systemd_unit(name, command, description))
        _run_cmd(["systemctl", "--user", "daemon-reload"])
        _run_cmd(["systemctl", "--user", "enable", name])
        return path

    elif platform == "macos":
        agent_dir = _launchd_dir()
        agent_dir.mkdir(parents=True, exist_ok=True)
        plist_name = f"com.agentirc.{name}"
        path = agent_dir / f"{plist_name}.plist"
        path.write_text(_build_launchd_plist(plist_name, command, description))
        _run_cmd(["launchctl", "load", str(path)])
        return path

    elif platform == "windows":
        svc_dir = _windows_service_dir()
        svc_dir.mkdir(parents=True, exist_ok=True)
        bat_path = svc_dir / f"{name}.bat"
        bat_path.write_text(_build_windows_bat(command))
        _run_cmd([
            "schtasks", "/Create",
            "/TN", f"agentirc\\{name}",
            "/TR", subprocess.list2cmdline(["cmd.exe", "/c", str(bat_path)]),
            "/SC", "ONLOGON",
            "/F",
        ])
        return bat_path

    raise RuntimeError(f"Unsupported platform: {platform}")


def uninstall_service(name: str) -> None:
    """Remove a platform-specific auto-start entry."""
    platform = get_platform()

    if platform == "linux":
        _run_cmd(["systemctl", "--user", "disable", name])
        _run_cmd(["systemctl", "--user", "stop", name])
        path = _systemd_user_dir() / f"{name}.service"
        if path.exists():
            path.unlink()
        _run_cmd(["systemctl", "--user", "daemon-reload"])

    elif platform == "macos":
        plist_name = f"com.agentirc.{name}"
        path = _launchd_dir() / f"{plist_name}.plist"
        if path.exists():
            _run_cmd(["launchctl", "unload", str(path)])
            path.unlink()

    elif platform == "windows":
        _run_cmd(["schtasks", "/Delete", "/TN", f"agentirc\\{name}", "/F"])
        bat_path = _windows_service_dir() / f"{name}.bat"
        if bat_path.exists():
            bat_path.unlink()


def list_services() -> list[str]:
    """Return names of installed agentirc auto-start services."""
    platform = get_platform()
    names = []

    if platform == "linux":
        unit_dir = _systemd_user_dir()
        if unit_dir.exists():
            for f in unit_dir.iterdir():
                if f.name.startswith("agentirc-") and f.name.endswith(".service"):
                    names.append(f.stem)

    elif platform == "macos":
        agent_dir = _launchd_dir()
        if agent_dir.exists():
            for f in agent_dir.iterdir():
                if f.name.startswith("com.agentirc.") and f.name.endswith(".plist"):
                    names.append(f.stem.removeprefix("com.agentirc."))

    elif platform == "windows":
        svc_dir = _windows_service_dir()
        if svc_dir.exists():
            for f in svc_dir.iterdir():
                if f.name.startswith("agentirc-") and f.name.endswith(".bat"):
                    names.append(f.stem)

    return names


def restart_service(name: str) -> bool:
    """Restart an installed service via the platform service manager.

    Returns True if the restart command was issued, False if no service found.
    """
    platform = get_platform()

    if platform == "linux":
        path = _systemd_user_dir() / f"{name}.service"
        if path.exists():
            _run_cmd(["systemctl", "--user", "restart", name])
            return True

    elif platform == "macos":
        plist_name = f"com.agentirc.{name}"
        path = _launchd_dir() / f"{plist_name}.plist"
        if path.exists():
            _run_cmd(["launchctl", "unload", str(path)])
            _run_cmd(["launchctl", "load", str(path)])
            return True

    elif platform == "windows":
        # Check if the scheduled task exists before attempting to run it
        probe = subprocess.run(
            ["schtasks", "/Query", "/TN", f"agentirc\\{name}"],
            capture_output=True, text=True,
        )
        if probe.returncode != 0:
            return False
        _run_cmd(["schtasks", "/Run", "/TN", f"agentirc\\{name}"])
        return True

    return False
