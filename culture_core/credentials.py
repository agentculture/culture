"""OS credential store for culture link passwords.

Uses platform-native secure storage:
- Linux: secret-tool (libsecret / GNOME Keyring).
- macOS: the ``security`` Keychain tool.
- Windows: PowerShell ``New-StoredCredential`` (Credential Manager).

Passwords never land in config files and never transit argv on any
platform (argv is visible to ``ps`` / process-listing tools):

- Linux pipes the password to ``secret-tool store`` on stdin.
- macOS runs ``security -i`` (interactive mode reads commands from
  stdin) and writes the full ``add-generic-password … -w <password>``
  command line on stdin, with the password quoted for the tool's line
  tokenizer by :func:`_security_quote`.
- Windows keeps the PowerShell script free of the secret — the script
  reads the password from stdin via ``[Console]::In.ReadLine()`` (the
  trailing CR/LF appended here is stripped by ``ReadLine``) and passes
  it to ``New-StoredCredential`` as a variable. This also removes the
  quoting/injection hazard of interpolating the password into the
  command string.

Peer names are validated against ``^[A-Za-z0-9._-]+$`` before being
interpolated into any platform command (defense in depth against
injection via the peer name). Each platform command is produced by a
pure ``_build_*_command`` builder returning an ``(argv, stdin_input)``
pair; regression tests assert that secrets never appear in the built
argv.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys

logger = logging.getLogger(__name__)

SERVICE_NAME = "culture"

#: Peer names are interpolated into keychain item names, PowerShell
#: targets, and secret-tool attributes — restrict them to a safe charset.
_PEER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: PowerShell guard shared by every Windows command: bail out with exit
#: code 2 when the CredentialManager module is not installed.
_PS_MODULE_GUARD = "if (-not (Get-Module -ListAvailable -Name CredentialManager)) { exit 2 }\n"


def _validate_peer_name(peer_name: str) -> None:
    """Raise ValueError unless *peer_name* matches ``^[A-Za-z0-9._-]+$``."""
    if not _PEER_NAME_RE.match(peer_name):
        raise ValueError(f"invalid peer name {peer_name!r}: must match [A-Za-z0-9._-]+")


def _security_quote(value: str) -> str:
    """Quote *value* as a single token for the ``security -i`` tokenizer.

    ``security`` in interactive mode splits each input line with a small
    lexer (``split_line`` in Apple's SecurityTool ``security.c``): tokens
    are whitespace-separated; ``"`` or ``'`` opens a quoted token; inside
    (and outside) a quoted token a backslash escapes the next character
    literally. Wrapping in double quotes and backslash-escaping ``\\``
    and ``"`` is therefore lossless for any single-line string. The
    reader is line-based, so CR/LF cannot be represented and such values
    are rejected.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("value must not contain newline characters")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_store_command(
    platform: str, peer_name: str, password: str
) -> tuple[list[str], str | None]:
    """Build the ``(argv, stdin_input)`` pair that stores a link password.

    Pure — performs no I/O. On every platform the password is carried in
    the stdin payload, never in argv; regression tests assert this
    invariant. Raises ValueError for an invalid peer name, or for a
    password containing CR/LF on macOS/Windows (both stdin channels are
    line-based there).
    """
    _validate_peer_name(peer_name)

    if platform == "darwin":
        # macOS Keychain: `security -i` reads command lines from stdin,
        # so the password never appears in the process's argv.
        command = (
            "add-generic-password -U"  # -U: update if exists
            f" -a {_security_quote(SERVICE_NAME)}"
            f" -s {_security_quote(f'{SERVICE_NAME}-link-{peer_name}')}"
            f" -w {_security_quote(password)}\n"
        )
        return ["security", "-i"], command

    if platform == "win32":
        # Windows Credential Manager via the PowerShell CredentialManager
        # module. The script reads the password from stdin — ReadLine()
        # strips the trailing CR/LF appended below — so the secret is
        # neither in argv nor interpolated into the script text.
        if "\n" in password or "\r" in password:
            raise ValueError("password must not contain newline characters")
        ps = (
            _PS_MODULE_GUARD + "$pw = [Console]::In.ReadLine()\n"
            f"New-StoredCredential -Target '{SERVICE_NAME}-link-{peer_name}' "
            f"-UserName '{SERVICE_NAME}' -Password $pw "
            "-Persist LocalMachine | Out-Null\n"
        )
        return (
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            password + "\n",
        )

    # Linux (and default): secret-tool reads the secret verbatim from stdin.
    return (
        [
            "secret-tool",
            "store",
            "--label",
            f"culture link {peer_name}",
            "service",
            SERVICE_NAME,
            "peer",
            peer_name,
        ],
        password,
    )


def _build_lookup_command(platform: str, peer_name: str) -> tuple[list[str], str | None]:
    """Build the ``(argv, stdin_input)`` pair that looks up a link password.

    Pure — performs no I/O. Takes no password; the secret only flows
    back on stdout. Raises ValueError for an invalid peer name.
    """
    _validate_peer_name(peer_name)

    if platform == "darwin":
        return (
            [
                "security",
                "find-generic-password",
                "-a",
                SERVICE_NAME,
                "-s",
                f"{SERVICE_NAME}-link-{peer_name}",
                "-w",
            ],
            None,
        )

    if platform == "win32":
        ps = (
            _PS_MODULE_GUARD
            + f"$c = Get-StoredCredential -Target '{SERVICE_NAME}-link-{peer_name}'; "
            "if ($c) { $c.GetNetworkCredential().Password } else { exit 1 }\n"
        )
        return (
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            None,
        )

    return (
        ["secret-tool", "lookup", "service", SERVICE_NAME, "peer", peer_name],
        None,
    )


def _build_delete_command(platform: str, peer_name: str) -> tuple[list[str], str | None]:
    """Build the ``(argv, stdin_input)`` pair that deletes a link password.

    Pure — performs no I/O. Takes no password; delete never handles the
    secret. Raises ValueError for an invalid peer name.
    """
    _validate_peer_name(peer_name)

    if platform == "darwin":
        return (
            [
                "security",
                "delete-generic-password",
                "-a",
                SERVICE_NAME,
                "-s",
                f"{SERVICE_NAME}-link-{peer_name}",
            ],
            None,
        )

    if platform == "win32":
        ps = (
            _PS_MODULE_GUARD
            + f"Remove-StoredCredential -Target '{SERVICE_NAME}-link-{peer_name}' -Force\n"
        )
        return (
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            None,
        )

    return (
        ["secret-tool", "clear", "service", SERVICE_NAME, "peer", peer_name],
        None,
    )


def _chomp(out: str) -> str:
    """Strip exactly one trailing newline (LF or CRLF) from tool output.

    CLI tools append a newline to the secret they print; anything more
    aggressive (``str.strip``) would corrupt secrets with legitimate
    leading/trailing spaces or tabs.
    """
    if out.endswith("\r\n"):
        return out[:-2]
    if out.endswith("\n"):
        return out[:-1]
    return out


def _run(args: list[str], input: str | None = None) -> tuple[int, str]:
    """Run a command and return (returncode, stdout minus one trailing newline)."""
    try:
        result = subprocess.run(
            args,
            input=input,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        tool = {"darwin": "security", "win32": "powershell"}.get(sys.platform, "secret-tool")
        logger.warning("Credential tool '%s' not found — is it installed?", tool)
        return 127, ""
    return result.returncode, _chomp(result.stdout)


#: Lookup exit codes that positively mean "credential not found", per
#: platform: macOS ``security find-generic-password`` exits 44
#: (errSecItemNotFound); the Windows PowerShell script and Linux
#: ``secret-tool lookup`` exit 1. Any other nonzero code is a lookup
#: *failure* — it proves nothing about absence.
_LOOKUP_NOT_FOUND_RCS = {"darwin": {44}}
_LOOKUP_NOT_FOUND_RCS_DEFAULT = {1}


def store_credential(peer_name: str, password: str) -> bool:
    """Store a link password in the OS credential store.

    The password is passed to the platform tool via stdin on every
    platform — it never appears in argv. Raises ValueError if
    *peer_name* is not a valid peer name, or if the password contains
    newline characters on macOS/Windows (line-based stdin channels).

    Returns True on success, False on failure.
    """
    argv, stdin_input = _build_store_command(sys.platform, peer_name, password)
    rc, _ = _run(argv, input=stdin_input)
    return rc == 0


def lookup_credential(peer_name: str) -> str | None:
    """Retrieve a link password from the OS credential store.

    Raises ValueError if *peer_name* is not a valid peer name.
    Returns the password string, or None if not found.
    """
    argv, stdin_input = _build_lookup_command(sys.platform, peer_name)
    rc, out = _run(argv, input=stdin_input)
    if sys.platform == "darwin":
        return out if rc == 0 else None
    return out if rc == 0 and out else None


def delete_credential(peer_name: str) -> bool:
    """Remove a link password from the OS credential store.

    The result is *verified*: after the platform delete command runs,
    the credential is looked up again and True is returned only when the
    lookup exits with its platform's known "not found" code. Deleting a
    credential that does not exist therefore returns True (verifiably
    gone) regardless of the delete tool's exit code. A lookup exiting 0
    means the credential is still present (warning + False, even if
    stdout is empty); any *other* nonzero lookup code is a verification
    failure, not proof of absence — warning + False. If the credential
    tooling itself is unavailable (exit code 127 for a missing binary,
    or exit code 2 from the PowerShell script when the CredentialManager
    module is missing on Windows), False is returned without
    verification — a broken tool cannot verify anything.

    Raises ValueError if *peer_name* is not a valid peer name.
    """
    argv, stdin_input = _build_delete_command(sys.platform, peer_name)
    rc, _ = _run(argv, input=stdin_input)
    if rc == 127 or (sys.platform == "win32" and rc == 2):
        return False

    # Verify by the lookup tool's exit code, not lookup_credential()'s
    # collapsed None — "not found" must stay distinguishable from
    # "lookup failed".
    verify_argv, verify_stdin = _build_lookup_command(sys.platform, peer_name)
    verify_rc, _ = _run(verify_argv, input=verify_stdin)
    if verify_rc == 0:
        logger.warning("Credential for peer '%s' still present after delete", peer_name)
        return False
    if verify_rc in _LOOKUP_NOT_FOUND_RCS.get(sys.platform, _LOOKUP_NOT_FOUND_RCS_DEFAULT):
        return True
    logger.warning(
        "Could not verify deletion for peer '%s' (lookup exited %d)", peer_name, verify_rc
    )
    return False
