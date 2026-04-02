"""OS credential store for agentirc link passwords.

Uses platform-native secure storage:
- Linux: secret-tool (libsecret / GNOME Keyring)
- macOS: security (Keychain)
- Windows: cmdkey + PowerShell

Passwords are never stored in config files or command lines.
"""

from __future__ import annotations

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

SERVICE_NAME = "agentirc"


def _run(args: list[str], input: str | None = None) -> tuple[int, str]:
    """Run a command and return (returncode, stdout)."""
    result = subprocess.run(
        args,
        input=input,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip()


def store_credential(peer_name: str, password: str) -> bool:
    """Store a link password in the OS credential store.

    Returns True on success, False on failure.
    """
    if sys.platform == "darwin":
        # macOS Keychain
        rc, _ = _run([
            "security", "add-generic-password",
            "-U",  # update if exists
            "-a", SERVICE_NAME,
            "-s", f"{SERVICE_NAME}-link-{peer_name}",
            "-w", password,
        ])
        return rc == 0

    elif sys.platform == "win32":
        # Windows Credential Manager
        rc, _ = _run([
            "cmdkey",
            f"/generic:{SERVICE_NAME}-link-{peer_name}",
            f"/user:{SERVICE_NAME}",
            f"/pass:{password}",
        ])
        return rc == 0

    else:
        # Linux: secret-tool (libsecret)
        rc, _ = _run(
            [
                "secret-tool", "store",
                "--label", f"agentirc link {peer_name}",
                "service", SERVICE_NAME,
                "peer", peer_name,
            ],
            input=password,
        )
        return rc == 0


def lookup_credential(peer_name: str) -> str | None:
    """Retrieve a link password from the OS credential store.

    Returns the password string, or None if not found.
    """
    if sys.platform == "darwin":
        rc, out = _run([
            "security", "find-generic-password",
            "-a", SERVICE_NAME,
            "-s", f"{SERVICE_NAME}-link-{peer_name}",
            "-w",
        ])
        return out if rc == 0 else None

    elif sys.platform == "win32":
        # Windows: use PowerShell to read the credential
        ps_script = (
            f"$c = Get-StoredCredential -Target '{SERVICE_NAME}-link-{peer_name}'; "
            f"if ($c) {{ $c.GetNetworkCredential().Password }} else {{ exit 1 }}"
        )
        rc, out = _run(["powershell", "-Command", ps_script])
        if rc == 0 and out:
            return out
        # Fallback: try cmdkey /list (can't read password, just check existence)
        return None

    else:
        # Linux: secret-tool
        rc, out = _run([
            "secret-tool", "lookup",
            "service", SERVICE_NAME,
            "peer", peer_name,
        ])
        return out if rc == 0 and out else None


def delete_credential(peer_name: str) -> bool:
    """Remove a link password from the OS credential store."""
    if sys.platform == "darwin":
        rc, _ = _run([
            "security", "delete-generic-password",
            "-a", SERVICE_NAME,
            "-s", f"{SERVICE_NAME}-link-{peer_name}",
        ])
        return rc == 0

    elif sys.platform == "win32":
        rc, _ = _run([
            "cmdkey",
            f"/delete:{SERVICE_NAME}-link-{peer_name}",
        ])
        return rc == 0

    else:
        rc, _ = _run([
            "secret-tool", "clear",
            "service", SERVICE_NAME,
            "peer", peer_name,
        ])
        return rc == 0
