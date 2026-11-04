"""Private one-way replication of irc-lens approved accounts."""

from __future__ import annotations

import os
import socket
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from irc_lens.access_store import AccessStore
from irc_lens.guest_store import GuestStore
from irc_lens.web import app_session

from culture_core.cli import console
from culture_core.cli._errors import CultureError
from culture_core.cli.shared import console_replica

EMAIL = "ada@example.test"
GUEST_EMAIL = "guest@example.test"
HOST = "spark2"
PASSWORD = "a correct horse battery staple"
REMOTE_STORE = "/var/lib/irc-lens/accounts.db"
PRIVATE_TABLES = {
    "agent_credentials",
    "agent_extensions",
    "attempts",
    "audit",
    "bans",
    "consents",
    "deletions",
    "flags",
    "guests",
    "inputs",
    "rooms",
    "sessions",
    "signin_budget",
    "signin_pending",
    "tokens",
    "trusted_devices",
}


def _seed_approved_store(path: Path) -> tuple[GuestStore, AccessStore]:
    guests = GuestStore(path)
    access = AccessStore(path)
    access.set_user(EMAIL, role="admin", state="approved", actor=None)
    access.set_nickname(EMAIL, "ada", actor=None)
    access.set_grant(EMAIL, "channel", "#ops", "write", actor=None)
    guests.set_password(EMAIL, PASSWORD)
    return guests, access


@dataclass
class LocalTransport:
    """No-network transport exercising the same compatibility gate."""

    target: Path
    target_version: str = "0.18.1"
    pushes: int = 0

    def lens_version(self, host: str) -> str:
        assert host == HOST
        return self.target_version

    def push(self, replica: Path, host: str, destination: str) -> None:
        assert host == HOST
        assert destination == REMOTE_STORE
        console_replica.install_local(replica, self.target)
        self.pushes += 1


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return {row[0] for row in rows}


def _row_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        query = f'SELECT count(*) FROM "{table}"'  # noqa: S608 - fixed test table names
        return connection.execute(query).fetchone()[0]


def test_two_real_stores_preserve_approved_signin_role_and_grants(tmp_path: Path) -> None:
    source = tmp_path / "spark.db"
    standby = tmp_path / "spark2.db"
    guests, access = _seed_approved_store(source)
    access.set_user("waiting@example.test", role="member", state="pending", actor=None)
    guests.set_password("waiting@example.test", "not replicated")
    guests.record_guest(GUEST_EMAIL, "visitor", "192.0.2.10")
    guests.record_consent(
        GUEST_EMAIL,
        "192.0.2.10",
        tos_version="1",
        privacy_version="1",
    )
    guests.issue_token(EMAIL, purpose="signin")
    guests.create_session(EMAIL)
    transport = LocalTransport(standby)

    copied = console_replica.replicate(
        source,
        host=HOST,
        destination=REMOTE_STORE,
        transport=transport,
        source_version="0.18.1",
    )

    standby_guests = GuestStore(standby)
    standby_config = SimpleNamespace(
        access_enabled=True,
        access_admins_only=False,
        allowed_emails=("waiting@example.test",),
        guest_store_path=str(standby),
    )
    standby_app = {"config": standby_config}
    standby_access = app_session.install_access(standby_app)
    assert copied == 1
    assert transport.pushes == 1
    assert standby_access is not None
    assert app_session.can_sign_in(standby_app, EMAIL)
    assert standby_guests.check_password(EMAIL, PASSWORD)
    assert app_session.approved_on_signin(standby_app, EMAIL) == {
        "email": EMAIL,
        "role": "admin",
        "state": "approved",
        "nickname": "ada",
    }
    assert standby_access.grants_for(EMAIL) == [("channel", "#ops", "write")]
    assert standby_access.get_user("waiting@example.test") is None
    assert all(_row_count(standby, table) == 0 for table in PRIVATE_TABLES)


def test_replica_is_allow_listed_and_omits_guest_only_tables(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "replica.db"
    guests, _ = _seed_approved_store(source)
    guests.record_guest(GUEST_EMAIL, "visitor", "192.0.2.10")

    console_replica.create_replica(source, output)

    assert _table_names(output) == {"admin_imports", "grants", "passwords", "users"}
    assert PRIVATE_TABLES.isdisjoint(_table_names(output))
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with sqlite3.connect(output) as connection:
        user_columns = [row[1] for row in connection.execute("PRAGMA table_info(users)")]
    assert user_columns == ["email", "role", "state", "nickname"]


def test_online_backup_excludes_an_uncommitted_live_write(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    output = tmp_path / "replica.db"
    _seed_approved_store(source)
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "INSERT INTO users (email, role, state) VALUES (?, ?, ?)",
        ("mid-copy@example.test", "member", "approved"),
    )

    try:
        console_replica.create_replica(source, output)
    finally:
        writer.rollback()
        writer.close()

    with sqlite3.connect(output) as connection:
        emails = {row[0] for row in connection.execute("SELECT email FROM users")}
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    assert emails == {EMAIL}
    assert integrity == "ok"


def test_install_local_is_mode_0600_and_replaces_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "replica.db"
    destination = tmp_path / "standby.db"
    source.write_bytes(b"new private database")
    destination.write_bytes(b"old database")
    replaced: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(staged: Path, target: Path) -> None:
        replaced.append((staged, target))
        real_replace(staged, target)

    monkeypatch.setattr(console_replica.os, "replace", recording_replace)
    console_replica.install_local(source, destination)

    assert destination.read_bytes() == b"new private database"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert len(replaced) == 1
    assert replaced[0][1] == destination
    assert replaced[0][0].parent == destination.parent


def test_newer_lens_refuses_to_replace_older_standby(tmp_path: Path) -> None:
    source = tmp_path / "spark.db"
    standby = tmp_path / "spark2.db"
    _seed_approved_store(source)
    standby.write_bytes(b"keep me")
    transport = LocalTransport(standby, target_version="0.17.0")

    with pytest.raises(CultureError) as excinfo:
        console_replica.replicate(
            source,
            host=HOST,
            destination=REMOTE_STORE,
            transport=transport,
            source_version="0.18.1",
        )

    assert "newer irc-lens schema" in excinfo.value.message
    assert f"upgrade irc-lens on {HOST}" in excinfo.value.remediation
    assert standby.read_bytes() == b"keep me"
    assert transport.pushes == 0


@pytest.mark.parametrize(
    "host",
    ["spark2", "operator@spark2", "spark2.local", "spark2.tailnet.ts.net", "100.64.0.8"],
)
def test_private_transport_accepts_lan_and_tailnet_hosts(host: str) -> None:
    assert console_replica.validate_private_host(host) == host


def test_private_transport_rejects_public_host() -> None:
    with pytest.raises(CultureError) as excinfo:
        console_replica.validate_private_host("203.0.113.20")
    assert "tailnet or LAN" in excinfo.value.message


def test_scp_transport_stages_mode_0600_then_renames_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replica = tmp_path / "accounts.db"
    replica.write_bytes(b"private")
    calls: list[tuple[list[str], str | None]] = []

    def fake_run(command: list[str], *, stdin: str | None = None):
        calls.append((command, stdin))
        if command[:2] == ["ssh", "-G"]:
            return type(
                "Result",
                (),
                {"stdout": "hostname 100.64.0.8\nproxycommand none\nproxyjump none\n"},
            )()
        return type("Result", (), {"stdout": "irc-lens 0.18.1\n"})()

    monkeypatch.setattr(console_replica, "_run", fake_run)
    monkeypatch.setattr(
        console_replica.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.8", 0))],
    )
    transport = console_replica.SshTransport("scp")
    version = transport.lens_version(HOST)
    transport.push(replica, HOST, REMOTE_STORE)

    assert version == "irc-lens 0.18.1"
    assert calls[0][0] == ["ssh", "-G", HOST]
    assert calls[1][0] == ["ssh", "-o", "BatchMode=yes", HOST, "irc-lens", "--version"]
    assert calls[2][0][:5] == ["scp", "-q", "-p", "-o", "BatchMode=yes"]
    assert calls[2][0][-1].startswith(f"{HOST}:{REMOTE_STORE}.replica-")
    assert calls[3][0][:4] == ["ssh", "-o", "BatchMode=yes", HOST]
    assert calls[3][1] == 'set -eu\nchmod 600 -- "$1"\nmv -f -- "$1" "$2"\n'
    assert calls[3][0][-1] == REMOTE_STORE


def test_ssh_transport_refuses_alias_resolving_to_public_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], *, stdin: str | None = None):
        del command, stdin
        return type("Result", (), {"stdout": "hostname public.example\n"})()

    monkeypatch.setattr(console_replica, "_run", fake_run)
    monkeypatch.setattr(
        console_replica.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )
    transport = console_replica.SshTransport("scp")

    with pytest.raises(CultureError) as excinfo:
        transport.lens_version(HOST)

    assert "does not stay on the tailnet or LAN" in excinfo.value.message


def test_console_replica_verb_parses_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, str, str, str]] = []

    def fake_replicate(source: Path, *, host: str, destination: str, method: str) -> int:
        calls.append((source, host, destination, method))
        return 2

    monkeypatch.setattr(console_replica, "replicate", fake_replicate)
    args = console_replica.parse_args(
        [
            "--source",
            "/srv/irc-lens/accounts.db",
            "--host",
            HOST,
            "--destination",
            REMOTE_STORE,
            "--transport",
            "scp",
        ]
    )
    console_replica.run(args)

    assert calls == [
        (
            Path("/srv/irc-lens/accounts.db"),
            HOST,
            REMOTE_STORE,
            "scp",
        )
    ]


def test_console_dispatch_keeps_replica_out_of_irc_lens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    monkeypatch.setattr(console_replica, "main", lambda argv: seen.append(argv))
    console.dispatch(
        type(
            "Args",
            (),
            {
                "console_args": [
                    "replica",
                    "--source",
                    "/source.db",
                    "--host",
                    "spark2",
                    "--destination",
                    "/destination.db",
                ]
            },
        )()
    )

    assert seen == [
        [
            "--source",
            "/source.db",
            "--host",
            "spark2",
            "--destination",
            "/destination.db",
        ]
    ]
