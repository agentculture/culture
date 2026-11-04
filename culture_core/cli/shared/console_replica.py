"""Private, one-way replication of approved irc-lens accounts.

The source database is first copied with SQLite's online backup API.  A fresh
database is then built from an explicit table-and-column allow-list, so deleted
guest data cannot survive in free pages and future tables are private by
default.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import ipaddress
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Protocol

from culture_core.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CultureError

_APPROVED = "approved"
_BATCH_MODE = "BatchMode=yes"
_INITIALISED_MARK = "store-initialised"
_LENS_PACKAGE = "irc-lens"
_SAFE_SSH_NAME = re.compile(r"[A-Za-z0-9_.@-]+")
_SAFE_REMOTE_PATH = re.compile(r"/[A-Za-z0-9_./-]+")
_VERSION = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)
_PRIVATE_DNS_SUFFIXES = (".home.arpa", ".lan", ".local", ".ts.net")

_REPLICA_SCHEMA = """
CREATE TABLE users (
    email TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    state TEXT NOT NULL,
    nickname TEXT);
CREATE UNIQUE INDEX users_nickname_idx ON users (nickname COLLATE NOCASE);
CREATE TABLE grants (
    email TEXT NOT NULL,
    resource_kind TEXT NOT NULL,
    resource TEXT NOT NULL,
    level TEXT NOT NULL,
    PRIMARY KEY (email, resource_kind, resource));
CREATE TABLE passwords (email TEXT PRIMARY KEY, hash TEXT NOT NULL);
CREATE TABLE admin_imports (email_hash TEXT PRIMARY KEY);
"""
_COLUMNS = {
    "users": ("email", "role", "state", "nickname"),
    "grants": ("email", "resource_kind", "resource", "level"),
    "passwords": ("email", "hash"),
}
_RELATED_QUERIES = {
    "grants": (
        "SELECT email, resource_kind, resource, level FROM grants "
        "WHERE email IN (SELECT email FROM users WHERE state=?) ORDER BY email"
    ),
    "passwords": (
        "SELECT email, hash FROM passwords "
        "WHERE email IN (SELECT email FROM users WHERE state=?) ORDER BY email"
    ),
}


class ReplicaTransport(Protocol):
    """The narrow transport seam used by no-network tests."""

    def lens_version(self, host: str) -> str: ...

    def push(self, replica: Path, host: str, destination: str) -> None: ...


def _culture_error(message: str, remediation: str, *, environment: bool = False) -> CultureError:
    code = EXIT_ENV_ERROR if environment else EXIT_USER_ERROR
    return CultureError(code, message, remediation)


def _host_name(ssh_target: str) -> str:
    if not _SAFE_SSH_NAME.fullmatch(ssh_target) or ssh_target.count("@") > 1:
        raise _culture_error(
            "replica host must be a simple SSH user and host name",
            "use a configured SSH alias such as 'spark2' or 'operator@spark2'",
        )
    host = ssh_target.rsplit("@", 1)[-1].lower().rstrip(".")
    if not host:
        raise _culture_error(
            "replica host is empty",
            "pass a tailnet or LAN SSH host with --host",
        )
    return host


def _is_private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in _PRIVATE_NETWORKS)


def _ssh_route(host: str) -> tuple[str, bool]:
    result = _run(["ssh", "-G", host])
    options: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(" ")
        if separator and key in {"hostname", "proxycommand", "proxyjump"}:
            options[key] = value.strip()
    hostname = options.get("hostname")
    if not hostname:
        raise _culture_error(
            "the configured SSH host has no destination",
            "fix the standby entry in SSH config, then retry",
            environment=True,
        )
    proxied = any(options.get(key, "none") != "none" for key in ("proxycommand", "proxyjump"))
    return hostname, proxied


def _resolved_addresses(hostname: str) -> set[str]:
    try:
        answers = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise _culture_error(
            "the standby SSH host could not be resolved privately",
            "check tailnet or LAN name resolution on spark",
            environment=True,
        ) from exc
    return {answer[4][0] for answer in answers}


def _require_private_route(host: str) -> None:
    hostname, proxied = _ssh_route(host)
    addresses = _resolved_addresses(hostname)
    if addresses and not proxied and all(_is_private_ip(address) for address in addresses):
        return
    raise _culture_error(
        "the configured SSH route does not stay on the tailnet or LAN",
        "point the standby SSH alias directly at private or Tailscale addresses without a proxy",
    )


def validate_private_host(ssh_target: str) -> str:
    """Accept only SSH aliases, private IPs, and explicit LAN/tailnet DNS."""
    host = _host_name(ssh_target)
    is_alias = "." not in host and ":" not in host
    is_private_dns = host.endswith(_PRIVATE_DNS_SUFFIXES)
    if is_alias or is_private_dns or _is_private_ip(host):
        return ssh_target
    raise _culture_error(
        "replica transport is restricted to a tailnet or LAN host",
        "use an SSH config alias, a private address, or a .ts.net/.local host",
    )


def _validated_destination(raw: str) -> str:
    path = PurePosixPath(raw)
    if not _SAFE_REMOTE_PATH.fullmatch(raw) or not path.is_absolute() or ".." in path.parts:
        raise _culture_error(
            "replica destination must be a simple absolute POSIX path",
            "configure an absolute destination containing letters, digits, '/', '.', '_' or '-'",
        )
    return raw


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION.search(value)
    if match is None:
        raise _culture_error(
            "could not determine the irc-lens schema version",
            "ensure irc-lens --version prints a semantic version on both machines",
            environment=True,
        )
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def require_compatible(source_version: str, target_version: str, host: str) -> None:
    """Refuse a conservative downgrade before any private bytes are sent."""
    if _version_tuple(source_version) <= _version_tuple(target_version):
        return
    raise _culture_error(
        "refusing to copy a newer irc-lens schema to an older standby lens",
        f"upgrade irc-lens on {host}, then rerun culture console replica",
    )


def _run(command: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise _culture_error(
            "the private replica SSH transport failed",
            "check the configured SSH host, credentials, destination directory, and irc-lens install",
            environment=True,
        ) from exc


class SshTransport:
    """Push a 0600 staging file, then atomically rename it over SSH."""

    def __init__(self, method: str = "scp") -> None:
        if method not in {"scp", "rsync"}:
            raise _culture_error(
                f"unsupported replica transport {method!r}",
                "choose --transport scp or --transport rsync",
            )
        self.method = method

    def lens_version(self, host: str) -> str:
        _require_private_route(host)
        result = _run(["ssh", "-o", _BATCH_MODE, host, _LENS_PACKAGE, "--version"])
        return result.stdout.strip()

    def push(self, replica: Path, host: str, destination: str) -> None:
        staged = f"{destination}.replica-{uuid.uuid4().hex}"
        self._copy(replica, host, staged)
        script = 'set -eu\nchmod 600 -- "$1"\nmv -f -- "$1" "$2"\n'
        _run(
            ["ssh", "-o", _BATCH_MODE, host, "sh", "-s", "--", staged, destination],
            stdin=script,
        )

    def _copy(self, replica: Path, host: str, staged: str) -> None:
        remote = f"{host}:{staged}"
        if self.method == "scp":
            _run(["scp", "-q", "-p", "-o", _BATCH_MODE, str(replica), remote])
            return
        _run(
            [
                "rsync",
                "--archive",
                "--chmod=F600",
                "--rsh",
                f"ssh -o {_BATCH_MODE}",
                str(replica),
                remote,
            ]
        )


def _backup(source: Path, snapshot: Path) -> None:
    """Take one transactionally consistent online SQLite snapshot."""
    source_uri = f"{source.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_db:
        with closing(sqlite3.connect(snapshot)) as snapshot_db:
            source_db.backup(snapshot_db)
    os.chmod(snapshot, 0o600)


def _columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = connection.execute("SELECT name FROM pragma_table_info(?) ORDER BY cid", (table,))
    return tuple(row[0] for row in rows)


def _validate_source_schema(snapshot: sqlite3.Connection) -> None:
    for table, required in _COLUMNS.items():
        present = set(_columns(snapshot, table))
        missing = set(required) - present
        if missing:
            raise _culture_error(
                "the source irc-lens account schema is not supported",
                "upgrade irc-lens on spark and retry the replica",
            )


def _approved_rows(snapshot: sqlite3.Connection) -> list[tuple]:
    return snapshot.execute(
        "SELECT email, role, state, nickname FROM users WHERE state=? ORDER BY email",
        (_APPROVED,),
    ).fetchall()


def _related_rows(snapshot: sqlite3.Connection, table: str) -> list[tuple]:
    return snapshot.execute(_RELATED_QUERIES[table], (_APPROVED,)).fetchall()


def _write_filtered(snapshot_path: Path, output: Path) -> int:
    with closing(sqlite3.connect(snapshot_path)) as snapshot:
        _validate_source_schema(snapshot)
        users = _approved_rows(snapshot)
        grants = _related_rows(snapshot, "grants")
        passwords = _related_rows(snapshot, "passwords")
    with closing(sqlite3.connect(output)) as replica, replica:
        replica.executescript(_REPLICA_SCHEMA)
        replica.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", users)
        replica.executemany("INSERT INTO grants VALUES (?, ?, ?, ?)", grants)
        replica.executemany("INSERT INTO passwords VALUES (?, ?)", passwords)
        replica.execute("INSERT INTO admin_imports VALUES (?)", (_INITIALISED_MARK,))
    os.chmod(output, 0o600)
    return len(users)


def create_replica(source: Path, output: Path) -> int:
    """Create a fresh, approved-account-only replica from a live store."""
    source = Path(source)
    output = Path(output)
    if not source.is_file():
        raise _culture_error(
            "the irc-lens source store does not exist",
            "pass its configured guest_mode.store_path with --source",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".culture-replica-", dir=output.parent) as raw_dir:
        private_dir = Path(raw_dir)
        snapshot = private_dir / "snapshot.db"
        filtered = private_dir / "accounts.db"
        _backup(source, snapshot)
        copied = _write_filtered(snapshot, filtered)
        os.replace(filtered, output)
    os.chmod(output, 0o600)
    return copied


def install_local(replica: Path, destination: Path) -> None:
    """Test/local transport primitive matching the remote atomic install."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_staged = tempfile.mkstemp(
        prefix=f".{destination.name}.replica-", dir=destination.parent
    )
    os.close(descriptor)
    staged = Path(raw_staged)
    try:
        shutil.copyfile(replica, staged)
        os.chmod(staged, 0o600)
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def replicate(
    source: Path,
    *,
    host: str,
    destination: str,
    method: str = "scp",
    transport: ReplicaTransport | None = None,
    source_version: str | None = None,
) -> int:
    """Build and push one private replica; return approved accounts copied."""
    host = validate_private_host(host)
    destination = _validated_destination(destination)
    selected_transport = transport or SshTransport(method)
    local_version = source_version or importlib.metadata.version(_LENS_PACKAGE)
    remote_version = selected_transport.lens_version(host)
    require_compatible(local_version, remote_version, host)
    with tempfile.TemporaryDirectory(prefix="culture-console-replica-") as raw_dir:
        replica = Path(raw_dir) / "accounts.db"
        copied = create_replica(Path(source), replica)
        selected_transport.push(replica, host, destination)
    return copied


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="culture console replica",
        description="Privately copy approved irc-lens accounts to a warm standby",
    )
    parser.add_argument("--source", required=True, type=Path, help="Local irc-lens SQLite store")
    parser.add_argument("--host", required=True, help="Standby SSH host on the tailnet or LAN")
    parser.add_argument("--destination", required=True, help="Absolute standby store path")
    parser.add_argument("--transport", choices=("scp", "rsync"), default="scp")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> None:
    copied = replicate(
        args.source,
        host=args.host,
        destination=args.destination,
        method=args.transport,
    )
    noun = "account" if copied == 1 else "accounts"
    print(f"Replicated {copied} approved {noun} to {args.host}.")


def main(argv: list[str]) -> None:
    run(parse_args(argv))
