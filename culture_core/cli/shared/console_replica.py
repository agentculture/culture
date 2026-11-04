"""Private, one-way replication of approved irc-lens accounts.

The primary builds an allow-listed staging database from a SQLite online
backup. The standby merges those rows into its live store in one transaction;
the live database file is never replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import ipaddress
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol

from irc_lens.access_store import AccessStore
from irc_lens.guest_store import GuestStore

from culture_core.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CultureError

_APPROVED = "approved"
_BATCH_MODE = "BatchMode=yes"
_DEFAULT_MAX_AGE = 24 * 60 * 60
_INITIALISED_MARK = "store-initialised"
_LENS_PACKAGE = "irc-lens"
_STATE_FILE = Path("~/.culture/state/console-replica.json")
_REMOTE_PATH_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-")
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",  # NOSONAR - private CIDR intentionally checked
        "100.64.0.0/10",  # NOSONAR - Tailscale/shared private CIDR
        "127.0.0.0/8",  # NOSONAR - loopback CIDR intentionally checked
        "169.254.0.0/16",  # NOSONAR - link-local CIDR intentionally checked
        "172.16.0.0/12",  # NOSONAR - private CIDR intentionally checked
        "192.168.0.0/16",  # NOSONAR - private CIDR intentionally checked
        "::1/128",  # NOSONAR - IPv6 loopback intentionally checked
        "fc00::/7",  # NOSONAR - IPv6 private CIDR intentionally checked
        "fe80::/10",  # NOSONAR - IPv6 link-local intentionally checked
    )
)
_PRIVATE_DNS_SUFFIXES = (".home.arpa", ".lan", ".local", ".ts.net")
_REMOTE_EXEC_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./~-"
)
_SSH_TARGET_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.@:-")

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
CREATE TABLE replica_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
_COLUMNS = {
    "users": ("email", "role", "state", "nickname"),
    "grants": ("email", "resource_kind", "resource", "level"),
    "passwords": ("email", "hash"),
    "admin_imports": ("email_hash",),
}
_RELATED_QUERIES = {
    "grants": (
        "SELECT email, resource_kind, resource, level FROM grants "
        "WHERE email IN (SELECT email FROM users WHERE state=?) "
        "ORDER BY email, resource_kind, resource"
    ),
    "passwords": (
        "SELECT email, hash FROM passwords "
        "WHERE email IN (SELECT email FROM users WHERE state=?) ORDER BY email"
    ),
}
_APPLIED_PREFIX = "Applied "
_VERIFIED = "verified"
_RERUN_HINT = "discard it and rerun culture console replica on the primary"


@dataclass(frozen=True)
class RowSummary:
    """Public, non-secret result of building or applying a replica."""

    users: int
    grants: int
    login_rows: int
    digest: str


@dataclass(frozen=True)
class ReplicationResult:
    """Whether a primary-side run pushed, plus its row summary."""

    pushed: bool
    summary: RowSummary


@dataclass(frozen=True)
class _ReplicaData:
    users: tuple[tuple, ...]
    grants: tuple[tuple, ...]
    passwords: tuple[tuple, ...]
    source_version: str
    source_columns: dict[str, tuple[str, ...]]

    @property
    def summary(self) -> RowSummary:
        return _summarise(self.users, self.grants, self.passwords)


class ReplicaTransport(Protocol):
    """The narrow transport seam used by no-network tests."""

    def stage(self, replica: Path, host: str, destination: str) -> str: ...

    def apply(
        self, host: str, staged: str, destination: str, remote_culture: str, expected_digest: str
    ) -> str: ...

    def remove(self, host: str, staged: str) -> None: ...


def _culture_error(message: str, remediation: str, *, environment: bool = False) -> CultureError:
    code = EXIT_ENV_ERROR if environment else EXIT_USER_ERROR
    return CultureError(code, message, remediation)


def _host_name(ssh_target: str) -> str:
    if (
        not ssh_target
        or ssh_target.startswith("-")
        or ssh_target.count("@") > 1
        or any(character not in _SSH_TARGET_CHARS for character in ssh_target)
    ):
        raise _culture_error(
            "replica host must be a simple SSH user and host name",
            "use a configured SSH alias such as 'spark2' or 'operator@spark2'",
        )
    user, separator, raw_host = ssh_target.rpartition("@")
    host = raw_host.lower().rstrip(".")
    if not host or host.startswith("-") or (separator and not user):
        raise _culture_error(
            "replica host is empty or unsafe",
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
    result = _run(["ssh", "-G", "--", host])
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
            "check tailnet or LAN name resolution on the primary",
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
    if is_alias or host.endswith(_PRIVATE_DNS_SUFFIXES) or _is_private_ip(host):
        return ssh_target
    raise _culture_error(
        "replica transport is restricted to a tailnet or LAN host",
        "use an SSH config alias, a private address, or a .ts.net/.local host",
    )


def _validated_destination(raw: str) -> str:
    path = PurePosixPath(raw)
    if (
        raw.startswith("-")
        or any(character not in _REMOTE_PATH_CHARS for character in raw)
        or not path.is_absolute()
        or ".." in path.parts
    ):
        raise _culture_error(
            "replica destination must be a simple absolute POSIX path",
            "configure an absolute destination containing letters, digits, '/', '.', '_' or '-'",
        )
    return raw


def _validated_digest(raw: str) -> str:
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise _culture_error(
            "the expected row hash is not valid",
            "pass the 64-character hex hash the primary computed",
        )
    return raw


def _validated_remote_culture(raw: str) -> str:
    path_text = raw[2:] if raw.startswith("~/") else raw
    path = PurePosixPath(path_text)
    if (
        raw.startswith("-")
        or not raw.startswith(("~/", "/"))
        or any(character not in _REMOTE_EXEC_CHARS for character in raw)
        or ".." in path.parts
    ):
        raise _culture_error(
            "remote culture executable path is unsafe",
            "pass an absolute path or a simple ~/ path with --remote-culture",
        )
    return raw


def _numeric_version(value: str) -> tuple[int, int, int]:
    """Parse the three numeric release components without a complex regex."""
    parts = value.strip().split(".")
    if len(parts) != 3 or any(not part.isascii() or not part.isdigit() for part in parts):
        raise _culture_error(
            "could not determine the irc-lens schema version",
            "ensure the installed irc-lens package has a three-part numeric version",
            environment=True,
        )
    major, minor, patch = (int(part) for part in parts)
    return major, minor, patch


def _require_compatible(source_version: str, target_version: str) -> None:
    if _numeric_version(source_version) <= _numeric_version(target_version):
        return
    raise _culture_error(
        "the staged accounts come from a newer irc-lens than this standby",
        "upgrade irc-lens on the standby, then retry the replica",
    )


def _run(command: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        # No local shell. Remote arguments are charset allow-listed before they get here,
        # because ssh joins them into a remote shell command.
        return subprocess.run(  # NOSONAR
            command,
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise _culture_error(
            "the private replica SSH transport failed",
            "check the configured SSH host, credentials, destination directory, and culture install",
            environment=True,
        ) from exc


class SshTransport:
    """Copy a 0600 sibling staging file and invoke culture on the standby."""

    def __init__(self, method: str = "scp") -> None:
        if method not in {"scp", "rsync"}:
            raise _culture_error(
                f"unsupported replica transport {method!r}",
                "choose --transport scp or --transport rsync",
            )
        self.method = method

    def stage(self, replica: Path, host: str, destination: str) -> str:
        host = validate_private_host(host)
        destination = _validated_destination(destination)
        _require_private_route(host)
        destination_path = PurePosixPath(destination)
        staged = str(
            destination_path.with_name(f".{destination_path.name}.replica-{uuid.uuid4().hex}")
        )
        self._copy(replica, host, staged)
        return staged

    def apply(
        self, host: str, staged: str, destination: str, remote_culture: str, expected_digest: str
    ) -> str:
        expected_digest = _validated_digest(expected_digest)
        host = validate_private_host(host)
        staged = _validated_destination(staged)
        destination = _validated_destination(destination)
        remote_culture = _validated_remote_culture(remote_culture)
        script = (
            "set -eu\n"
            "culture=$1\n"
            'case "$culture" in "~/"*) culture="$HOME/${culture#\\~/}" ;; esac\n'
            'chmod 600 -- "$2"\n'
            'exec "$culture" console replica apply "$2" --destination "$3" --expected-hash "$4"\n'
        )
        result = _run(
            [
                "ssh",
                "-o",
                _BATCH_MODE,
                "--",
                host,
                "sh",
                "-s",
                "--",
                remote_culture,
                staged,
                destination,
                expected_digest,
            ],
            stdin=script,
        )
        return result.stdout

    def remove(self, host: str, staged: str) -> None:
        host = validate_private_host(host)
        staged = _validated_destination(staged)
        script = 'rm -f -- "$1"\n'
        _run(
            ["ssh", "-o", _BATCH_MODE, "--", host, "sh", "-s", "--", staged],
            stdin=script,
        )

    def _copy(self, replica: Path, host: str, staged: str) -> None:
        remote = f"{host}:{staged}"
        if self.method == "scp":
            _run(["scp", "-q", "-p", "-o", _BATCH_MODE, "--", str(replica), remote])
            return
        _run(
            [
                "rsync",
                "--archive",
                "--chmod=F600",
                "--rsh",
                f"ssh -o {_BATCH_MODE}",
                "--",
                str(replica),
                remote,
            ]
        )


def _backup(source: Path, snapshot: Path) -> None:
    """Take one transactionally consistent online SQLite snapshot."""
    source_uri = f"{source.resolve().as_uri()}?mode=ro"
    with (
        closing(sqlite3.connect(source_uri, uri=True)) as source_db,
        closing(sqlite3.connect(snapshot)) as snapshot_db,
    ):
        source_db.backup(snapshot_db)
    os.chmod(snapshot, 0o600)


def _columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = connection.execute("SELECT name FROM pragma_table_info(?) ORDER BY cid", (table,))
    return tuple(row[0] for row in rows)


def _validate_required_columns(columns: dict[str, tuple[str, ...]], where: str) -> None:
    for table, required in _COLUMNS.items():
        missing = set(required) - set(columns.get(table, ()))
        if missing:
            raise _culture_error(
                f"the {where} irc-lens account schema is not supported",
                "install matching irc-lens versions on both machines and retry",
            )


def _approved_rows(snapshot: sqlite3.Connection) -> tuple[tuple, ...]:
    rows = snapshot.execute(
        "SELECT email, role, state, nickname FROM users WHERE state=? ORDER BY email",
        (_APPROVED,),
    )
    return tuple(tuple(row) for row in rows)


def _related_rows(snapshot: sqlite3.Connection, table: str) -> tuple[tuple, ...]:
    rows = snapshot.execute(_RELATED_QUERIES[table], (_APPROVED,))
    return tuple(tuple(row) for row in rows)


def _digest_rows(
    users: tuple[tuple, ...],
    grants: tuple[tuple, ...],
    passwords: tuple[tuple, ...],
) -> str:
    payload = json.dumps(
        {"grants": grants, "passwords": passwords, "users": users},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _summarise(
    users: tuple[tuple, ...],
    grants: tuple[tuple, ...],
    passwords: tuple[tuple, ...],
) -> RowSummary:
    return RowSummary(
        users=len(users),
        grants=len(grants),
        login_rows=len(passwords),
        digest=_digest_rows(users, grants, passwords),
    )


def _write_filtered(snapshot_path: Path, output: Path, source_version: str) -> RowSummary:
    with closing(sqlite3.connect(snapshot_path)) as snapshot:
        source_columns = {table: _columns(snapshot, table) for table in _COLUMNS}
        _validate_required_columns(source_columns, "source")
        data = _ReplicaData(
            users=_approved_rows(snapshot),
            grants=_related_rows(snapshot, "grants"),
            passwords=_related_rows(snapshot, "passwords"),
            source_version=source_version,
            source_columns=source_columns,
        )
    with closing(sqlite3.connect(output)) as replica, replica:
        replica.executescript(_REPLICA_SCHEMA)
        replica.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", data.users)
        replica.executemany("INSERT INTO grants VALUES (?, ?, ?, ?)", data.grants)
        replica.executemany("INSERT INTO passwords VALUES (?, ?)", data.passwords)
        replica.execute("INSERT INTO admin_imports VALUES (?)", (_INITIALISED_MARK,))
        replica.executemany(
            "INSERT INTO replica_metadata VALUES (?, ?)",
            (
                ("source_version", source_version),
                (
                    "source_columns",
                    json.dumps(source_columns, separators=(",", ":"), sort_keys=True),
                ),
            ),
        )
    os.chmod(output, 0o600)
    return data.summary


def create_replica(source: Path, output: Path, *, source_version: str | None = None) -> RowSummary:
    """Create a fresh, approved-account-only staging database."""
    source = Path(source)
    output = Path(output)
    if not source.is_file():
        raise _culture_error(
            "the irc-lens source store does not exist",
            "pass its configured guest_mode.store_path with --source",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    version = source_version or importlib.metadata.version(_LENS_PACKAGE)
    with tempfile.TemporaryDirectory(prefix=".culture-replica-", dir=output.parent) as raw_dir:
        private_dir = Path(raw_dir)
        snapshot = private_dir / "snapshot.db"
        filtered = private_dir / "accounts.db"
        _backup(source, snapshot)
        summary = _write_filtered(snapshot, filtered, version)
        os.replace(filtered, output)
    os.chmod(output, 0o600)
    return summary


def _metadata(connection: sqlite3.Connection) -> tuple[str, dict[str, tuple[str, ...]]]:
    rows = dict(connection.execute("SELECT key, value FROM replica_metadata"))
    try:
        raw_columns = json.loads(rows["source_columns"])
        source_version = rows["source_version"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise _culture_error(
            "the staged replica metadata is invalid",
            _RERUN_HINT,
        ) from exc
    if not isinstance(source_version, str) or not isinstance(raw_columns, dict):
        raise _culture_error(
            "the staged replica metadata is invalid",
            _RERUN_HINT,
        )
    columns: dict[str, tuple[str, ...]] = {}
    for table, raw in raw_columns.items():
        if (
            not isinstance(table, str)
            or not isinstance(raw, list)
            or not all(isinstance(column, str) for column in raw)
        ):
            raise _culture_error(
                "the staged replica column metadata is invalid",
                _RERUN_HINT,
            )
        columns[table] = tuple(raw)
    return source_version, columns


def _read_replica(staged: Path) -> _ReplicaData:
    try:
        uri = f"{staged.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as replica:
            source_version, source_columns = _metadata(replica)
            _validate_required_columns(source_columns, "staged source")
            staged_columns = {table: _columns(replica, table) for table in _COLUMNS}
            _validate_required_columns(staged_columns, "staged")
            marker_rows = tuple(replica.execute("SELECT email_hash FROM admin_imports"))
            if marker_rows != ((_INITIALISED_MARK,),):
                raise _culture_error(
                    "the staged initialization marker is invalid",
                    _RERUN_HINT,
                )
            users = tuple(
                tuple(row)
                for row in replica.execute(
                    "SELECT email, role, state, nickname FROM users ORDER BY email"
                )
            )
            grants = tuple(
                tuple(row)
                for row in replica.execute(
                    "SELECT email, resource_kind, resource, level FROM grants "
                    "ORDER BY email, resource_kind, resource"
                )
            )
            passwords = tuple(
                tuple(row)
                for row in replica.execute("SELECT email, hash FROM passwords ORDER BY email")
            )
        return _ReplicaData(users, grants, passwords, source_version, source_columns)
    except sqlite3.Error as exc:
        raise _culture_error(
            "the staged replica is not a readable SQLite account database",
            _RERUN_HINT,
        ) from exc


def _table_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"  # NOSONAR - validated by _validated_destination
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return {row[0] for row in rows}
    except sqlite3.Error as exc:
        raise _culture_error(
            "the destination is not a readable irc-lens SQLite store",
            "repair or remove the destination before retrying",
        ) from exc


def _prepare_destination(destination: Path) -> None:
    tables = _table_names(destination)
    if not tables:
        GuestStore(destination)
        AccessStore(destination)
        os.chmod(destination, 0o600)  # NOSONAR - validated by _validated_destination
        return
    if not set(_COLUMNS).issubset(tables):
        raise _culture_error(
            "the destination has only part of the irc-lens account schema",
            "start irc-lens once to finish its store setup, then retry",
        )


def _validate_destination_columns(
    connection: sqlite3.Connection, source_columns: dict[str, tuple[str, ...]]
) -> None:
    destination_columns = {table: _columns(connection, table) for table in _COLUMNS}
    _validate_required_columns(destination_columns, "destination")
    for table, columns in source_columns.items():
        if table in _COLUMNS and not set(columns).issubset(destination_columns[table]):
            raise _culture_error(
                "the staged account columns are newer than this standby supports",
                "upgrade irc-lens on the standby, then retry the replica",
            )


def _merge_rows(
    destination: Path, data: _ReplicaData, busy_timeout_ms: int, expected_digest: str
) -> RowSummary:
    with closing(
        sqlite3.connect(  # NOSONAR - validated by _validated_destination
            destination, timeout=busy_timeout_ms / 1000, isolation_level=None
        )
    ) as connection:
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        _validate_destination_columns(connection, data.source_columns)
        connection.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            connection.execute("DELETE FROM grants")
            connection.execute("DELETE FROM passwords")
            connection.execute("DELETE FROM users")
            connection.executemany(
                "INSERT INTO users (email, role, state, nickname) VALUES (?, ?, ?, ?)",
                data.users,
            )
            connection.executemany(
                "INSERT INTO grants (email, resource_kind, resource, level) VALUES (?, ?, ?, ?)",
                data.grants,
            )
            connection.executemany(
                "INSERT INTO passwords (email, hash) VALUES (?, ?)", data.passwords
            )
            connection.execute(
                "INSERT OR IGNORE INTO admin_imports (email_hash) VALUES (?)",
                (_INITIALISED_MARK,),
            )
            applied = _ReplicaData(
                _approved_rows(connection),
                _related_rows(connection, "grants"),
                _related_rows(connection, "passwords"),
                data.source_version,
                data.source_columns,
            )
            summary = applied.summary
            if summary.digest != expected_digest:
                raise _culture_error(
                    "the applied rows do not match the primary; the standby is unchanged",
                    "retry the replica from the primary",
                    environment=True,
                )
            connection.execute("COMMIT")
            committed = True
            return summary
        finally:
            if not committed:
                connection.execute("ROLLBACK")


def apply_replica(
    staged: Path,
    destination: Path,
    *,
    expected_digest: str,
    installed_version: str | None = None,
    busy_timeout_ms: int = 30_000,
) -> RowSummary:
    """Merge staged account rows into a live store and always remove the stage."""
    staged = Path(_validated_destination(str(staged)))
    destination = Path(_validated_destination(str(destination)))
    if staged.resolve() == destination.resolve():
        raise _culture_error(
            "the staged replica and destination must be different files",
            "copy the stage beside the destination and retry",
        )
    try:
        data = _read_replica(staged)
        target_version = installed_version or importlib.metadata.version(_LENS_PACKAGE)
        _require_compatible(data.source_version, target_version)
        _prepare_destination(destination)
        return _merge_rows(destination, data, busy_timeout_ms, _validated_digest(expected_digest))
    finally:
        staged.unlink(missing_ok=True)


def _format_summary(summary: RowSummary) -> str:
    """Counts only: nothing derived from the hashed credentials is ever printed."""
    return (
        f"{_APPLIED_PREFIX}users={summary.users} grants={summary.grants} "
        f"logins={summary.login_rows} {_VERIFIED}"
    )


def _require_verified(output: str, expected: RowSummary) -> None:
    """Accept the standby's report only if it verified the rows and the counts agree."""
    line = next(
        (line for line in reversed(output.splitlines()) if line.startswith(_APPLIED_PREFIX)),
        "",
    )
    if line != _format_summary(expected):
        raise _culture_error(
            "the standby did not confirm the applied rows",
            "inspect the standby culture installation and retry; the state file was not updated",
            environment=True,
        )


def _read_state(path: Path) -> tuple[str, float] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        digest = raw["digest"]
        last_success = raw["last_success"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise _culture_error(
            "the console replica state file is invalid",
            "remove the state file to force a fresh successful push",
        ) from exc
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or isinstance(last_success, bool)
        or not isinstance(last_success, (int, float))
    ):
        raise _culture_error(
            "the console replica state file is invalid",
            "remove the state file to force a fresh successful push",
        )
    return digest, float(last_success)


def _write_state(path: Path, digest: str, last_success: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            descriptor = -1
            json.dump(
                {"digest": digest, "last_success": last_success},
                stream,
                separators=(",", ":"),
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _should_push(
    summary: RowSummary,
    state: tuple[str, float] | None,
    *,
    now: float,
    max_age: float,
    force: bool,
) -> bool:
    if force or state is None:
        return True
    old_digest, last_success = state
    return old_digest != summary.digest or now - last_success > max_age


def _best_effort_remove(transport: ReplicaTransport, host: str, staged: str | None) -> None:
    if staged is None:
        return
    try:
        transport.remove(host, staged)
    except Exception:  # noqa: BLE001 - preserve the original failed push
        pass


def replicate(
    source: Path,
    *,
    host: str,
    destination: str,
    state_path: Path,
    max_age: float = _DEFAULT_MAX_AGE,
    force: bool = False,
    remote_culture: str = "~/.local/bin/culture",
    method: str = "scp",
    transport: ReplicaTransport | None = None,
    source_version: str | None = None,
    clock: Callable[[], float] = time.time,
) -> ReplicationResult:
    """Build, conditionally push, verify, and record one private replica."""
    host = validate_private_host(host)
    destination = _validated_destination(destination)
    remote_culture = _validated_remote_culture(remote_culture)
    if max_age < 0:
        raise _culture_error("replica max age cannot be negative", "pass a non-negative --max-age")
    selected_transport = transport or SshTransport(method)
    with tempfile.TemporaryDirectory(prefix="culture-console-replica-") as raw_dir:
        replica = Path(raw_dir) / "accounts.db"
        expected = create_replica(source, replica, source_version=source_version)
        state = _read_state(Path(state_path))
        if not _should_push(expected, state, now=clock(), max_age=max_age, force=force):
            return ReplicationResult(False, expected)
        staged: str | None = None
        try:
            staged = selected_transport.stage(replica, host, destination)
            output = selected_transport.apply(
                host, staged, destination, remote_culture, expected.digest
            )
            _require_verified(output, expected)
        except BaseException:
            _best_effort_remove(selected_transport, host, staged)
            raise
        _write_state(Path(state_path), expected.digest, clock())
        return ReplicationResult(True, expected)


def _parse_max_age(raw: str) -> float:
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = raw[-1:].lower()
    number = raw[:-1] if suffix in multipliers else raw
    multiplier = multipliers.get(suffix, 1)
    try:
        value = float(number) * multiplier
    except ValueError as exc:
        raise argparse.ArgumentTypeError("max age must be seconds or end in s, m, h, or d") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("max age cannot be negative")
    return value


def parse_args(argv: list[str]) -> argparse.Namespace:
    if argv[:1] == ["apply"]:
        parser = argparse.ArgumentParser(
            prog="culture console replica apply",
            description="Transactionally apply staged account rows to a live standby store",
        )
        parser.set_defaults(command="apply")
        parser.add_argument("staged", type=Path, help="Sibling staging database")
        parser.add_argument("--destination", required=True, type=Path, help="Live standby store")
        parser.add_argument(
            "--expected-hash", required=True, help="Row hash computed by the primary"
        )
        parser.add_argument("--busy-timeout", type=int, default=30_000, help=argparse.SUPPRESS)
        return parser.parse_args(argv[1:])

    parser = argparse.ArgumentParser(
        prog="culture console replica",
        description="Privately merge approved irc-lens accounts into a warm standby",
    )
    parser.set_defaults(command="push")
    parser.add_argument("--source", required=True, type=Path, help="Local irc-lens SQLite store")
    parser.add_argument("--host", required=True, help="Standby SSH host on the tailnet or LAN")
    parser.add_argument("--destination", required=True, help="Absolute standby store path")
    parser.add_argument("--transport", choices=("scp", "rsync"), default="scp")
    parser.add_argument(
        "--remote-culture",
        default="~/.local/bin/culture",
        help="Explicit culture executable path on the standby",
    )
    parser.add_argument(
        "--state-file",
        "--state",
        dest="state_file",
        type=Path,
        default=_STATE_FILE,
        help="Private successful-push state file",
    )
    parser.add_argument(
        "--max-age",
        type=_parse_max_age,
        default=float(_DEFAULT_MAX_AGE),
        help="Push unchanged rows after this age (default: 24h)",
    )
    parser.add_argument("--force", action="store_true", help="Push even when unchanged")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> None:
    if args.command == "apply":
        summary = apply_replica(
            args.staged,
            args.destination,
            expected_digest=args.expected_hash,
            busy_timeout_ms=args.busy_timeout,
        )
        print(_format_summary(summary))
        return
    result = replicate(
        args.source,
        host=args.host,
        destination=args.destination,
        state_path=args.state_file.expanduser(),
        max_age=args.max_age,
        force=args.force,
        remote_culture=args.remote_culture,
        method=args.transport,
    )
    if result.pushed:
        print(_format_summary(result.summary))
    else:
        print("Unchanged; no push.")


def main(argv: list[str]) -> None:
    run(parse_args(argv))
