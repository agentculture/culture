"""Private one-way replication of irc-lens approved accounts."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from irc_lens.access_store import AccessStore
from irc_lens.guest_store import GuestStore, session_id_hash
from irc_lens.web import app_session

from culture_core.cli import console
from culture_core.cli._errors import EXIT_ENV_ERROR, CultureError
from culture_core.cli.shared import console_replica

EMAIL = "ada@example.test"
REMOVED_EMAIL = "removed@example.test"
HOST = "spark2"
PASSWORD = "a correct horse battery staple"
REMOTE_STORE = "/var/lib/irc-lens/accounts.db"
VERSION = "0.18.1"


def _seed_approved_store(path: Path) -> tuple[GuestStore, AccessStore]:
    guests = GuestStore(path)
    access = AccessStore(path)
    access.set_user(EMAIL, role="admin", state="approved", actor=None)
    access.set_nickname(EMAIL, "ada", actor=None)
    access.set_grant(EMAIL, "channel", "#ops", "write", actor=None)
    guests.set_password(EMAIL, PASSWORD)
    return guests, access


def _rows(path: Path, table: str) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        query = f'SELECT * FROM "{table}" ORDER BY rowid'  # noqa: S608 - fixed test names
        return [tuple(row) for row in connection.execute(query)]


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in rows}


@dataclass
class LocalTransport:
    """Injected, filesystem-only transport; no SSH or real service calls."""

    target: Path
    installed_version: str = VERSION
    fail_apply: bool = False
    wrong_hash: bool = False
    stage_calls: int = 0
    apply_calls: int = 0
    remove_calls: int = 0
    staged: Path | None = None

    def stage(self, replica: Path, host: str, destination: str) -> str:
        assert host == HOST
        assert destination == REMOTE_STORE
        self.staged = self.target.with_name(f".{self.target.name}.replica-test")
        shutil.copyfile(replica, self.staged)
        os.chmod(self.staged, 0o600)
        self.stage_calls += 1
        return str(self.staged)

    def apply(
        self, host: str, staged: str, destination: str, remote_culture: str, expected_digest: str
    ) -> str:
        assert host == HOST
        assert destination == REMOTE_STORE
        assert remote_culture == "~/.local/bin/culture"
        assert Path(staged) == self.staged
        self.apply_calls += 1
        if self.fail_apply:
            raise CultureError(EXIT_ENV_ERROR, "injected apply failure", "retry")
        summary = console_replica.apply_replica(
            Path(staged),
            self.target,
            expected_digest="0" * 64 if self.wrong_hash else expected_digest,
            installed_version=self.installed_version,
        )
        return console_replica._format_summary(summary)

    def remove(self, host: str, staged: str) -> None:
        assert host == HOST
        Path(staged).unlink(missing_ok=True)
        self.remove_calls += 1


def _replicate(
    source: Path,
    state: Path,
    transport: LocalTransport,
    *,
    now: float = 100.0,
    max_age: float = 86400,
    force: bool = False,
) -> console_replica.ReplicationResult:
    return console_replica.replicate(
        source,
        host=HOST,
        destination=REMOTE_STORE,
        state_path=state,
        max_age=max_age,
        force=force,
        transport=transport,
        source_version=VERSION,
        clock=lambda: now,
    )


def test_apply_keeps_open_stores_and_non_allow_listed_rows_working(
    tmp_path: Path,
) -> None:
    source = tmp_path / "primary.db"
    standby = tmp_path / "standby.db"
    staged = tmp_path / "staged.db"
    _seed_approved_store(source)

    # Both real stores predate apply. Their later calls open fresh connections
    # to the same live file, which is the regression that file replacement broke.
    standby_guests = GuestStore(standby)
    standby_access = AccessStore(standby)
    standby_access.set_user(EMAIL, role="member", state="approved", actor=None)
    standby_access.set_user(REMOVED_EMAIL, role="member", state="approved", actor=None)
    standby_access.set_grant(REMOVED_EMAIL, "channel", "#old", "read", actor=None)
    standby_guests.set_password(EMAIL, "old password")
    standby_guests.set_password(REMOVED_EMAIL, "removed password")
    token = standby_guests.issue_token(EMAIL, purpose="signin")
    standby_guests.bind_signin("pending-browser", token)
    session = standby_guests.create_session(EMAIL)
    standby_access.record_audit("replica.test", "preserve-me")
    before = {
        table: _rows(standby, table) for table in ("tokens", "signin_pending", "sessions", "audit")
    }
    assert standby_access.get_user(EMAIL)["role"] == "member"  # prime its cache

    expected = console_replica.create_replica(source, staged, source_version=VERSION)
    applied = console_replica.apply_replica(
        staged, standby, expected_digest=expected.digest, installed_version=VERSION
    )

    assert applied == expected
    assert not staged.exists()
    assert standby_access.get_user(EMAIL) == {
        "email": EMAIL,
        "role": "admin",
        "state": "approved",
        "nickname": "ada",
    }
    assert standby_access.grants_for(EMAIL) == [("channel", "#ops", "write")]
    assert standby_access.get_user(REMOVED_EMAIL) is None
    assert standby_guests.check_password(EMAIL, PASSWORD)
    assert standby_guests.get_session(session) is not None
    assert standby_guests.peek_signin_token(EMAIL, token, "pending-browser", purpose="signin")
    assert {
        table: _rows(standby, table) for table in ("tokens", "signin_pending", "sessions", "audit")
    } == before


@pytest.mark.asyncio
async def test_sweep_closes_session_of_account_removed_by_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "primary.db"
    standby = tmp_path / "standby.db"
    staged = tmp_path / "staged.db"
    _seed_approved_store(source)
    standby_guests = GuestStore(standby)
    standby_access = AccessStore(standby)
    standby_access.set_user(REMOVED_EMAIL, role="member", state="approved", actor=None)
    raw_session = standby_guests.create_session(REMOVED_EMAIL)
    built = console_replica.create_replica(source, staged, source_version=VERSION)
    console_replica.apply_replica(
        staged, standby, expected_digest=built.digest, installed_version=VERSION
    )

    class Registry:
        def __init__(self) -> None:
            self.open = {(REMOVED_EMAIL, "mesh")}
            self.closed: list[tuple[str, str]] = []

        def has(self, *key: str) -> bool:
            return tuple(key) in self.open

        async def close(self, *key: str) -> None:
            item = tuple(key)
            self.open.remove(item)
            self.closed.append(item)

    registry = Registry()
    key = (REMOVED_EMAIL, "mesh")
    config = SimpleNamespace(access_enabled=True, access_admins_only=False)
    app = {
        "config": config,
        "guest_store": standby_guests,
        app_session.ACCESS_STORE: standby_access,
        app_session.LINKS: {key: session_id_hash(raw_session)},
        app_session.REGISTRY_KEY: registry,
    }

    async def immediate(call, *args):
        return call(*args)

    # This sandbox cannot start worker threads. The sweep's decision and close
    # path are the behavior under test, so execute its store lookup inline.
    monkeypatch.setattr(app_session.asyncio, "to_thread", immediate)

    ended = await app_session.sweep_once(app)

    assert ended == [key]
    assert registry.closed == [key]
    assert standby_guests.get_session(raw_session) is not None


def test_apply_initialises_empty_destination_through_real_stores(tmp_path: Path) -> None:
    source = tmp_path / "primary.db"
    standby = tmp_path / "new-standby.db"
    staged = tmp_path / "staged.db"
    _seed_approved_store(source)
    built = console_replica.create_replica(source, staged, source_version=VERSION)

    summary = console_replica.apply_replica(
        staged, standby, expected_digest=built.digest, installed_version=VERSION
    )

    assert summary.users == 1
    assert GuestStore(standby).check_password(EMAIL, PASSWORD)
    assert AccessStore(standby).get_user(EMAIL)["role"] == "admin"
    assert "sessions" in _table_names(standby)
    assert "nickname_holders" in _table_names(standby)
    assert stat.S_IMODE(standby.stat().st_mode) == 0o600


def test_newer_source_is_refused_without_destination_change(tmp_path: Path) -> None:
    source = tmp_path / "primary.db"
    standby = tmp_path / "standby.db"
    staged = tmp_path / "staged.db"
    _seed_approved_store(source)
    _seed_approved_store(standby)
    console_replica.create_replica(source, staged, source_version="0.19.0")
    before = standby.read_bytes()

    with pytest.raises(CultureError) as excinfo:
        console_replica.apply_replica(
            staged, standby, expected_digest="0" * 64, installed_version=VERSION
        )

    assert "newer irc-lens" in excinfo.value.message
    assert standby.read_bytes() == before
    assert not staged.exists()


def test_failed_apply_always_removes_corrupt_stage(tmp_path: Path) -> None:
    staged = tmp_path / "staged.db"
    staged.write_bytes(b"not sqlite")

    with pytest.raises(CultureError) as excinfo:
        console_replica.apply_replica(staged, tmp_path / "standby.db", expected_digest="0" * 64)

    assert "not a readable SQLite" in excinfo.value.message
    assert not staged.exists()


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
        console_replica.create_replica(source, output, source_version=VERSION)
    finally:
        writer.rollback()
        writer.close()

    with sqlite3.connect(output) as connection:
        emails = {row[0] for row in connection.execute("SELECT email FROM users")}
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    assert emails == {EMAIL}
    assert integrity == "ok"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_hash_and_age_gate_changed_aged_and_forced_pushes(tmp_path: Path) -> None:
    source = tmp_path / "primary.db"
    standby = tmp_path / "standby.db"
    state = tmp_path / "culture-state" / "replica.json"
    _, access = _seed_approved_store(source)
    transport = LocalTransport(standby)

    first = _replicate(source, state, transport, now=100, max_age=50)
    unchanged = _replicate(source, state, transport, now=120, max_age=50)
    access.set_user("new@example.test", role="member", state="approved", actor=None)
    changed = _replicate(source, state, transport, now=120, max_age=50)
    aged = _replicate(source, state, transport, now=171, max_age=50)
    forced = _replicate(source, state, transport, now=172, max_age=50, force=True)

    assert first.pushed
    assert not unchanged.pushed
    assert changed.pushed
    assert aged.pushed
    assert forced.pushed
    assert transport.stage_calls == 4
    assert transport.apply_calls == 4
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert json.loads(state.read_text())["digest"] == forced.summary.digest


@pytest.mark.parametrize("wrong_hash", [False, True])
def test_failed_run_preserves_state_and_removes_remote_stage(
    tmp_path: Path, wrong_hash: bool
) -> None:
    source = tmp_path / "primary.db"
    standby = tmp_path / "standby.db"
    state = tmp_path / "state.json"
    _seed_approved_store(source)
    state.write_text(json.dumps({"digest": "1" * 64, "last_success": 1}) + "\n")
    before = state.read_bytes()
    transport = LocalTransport(standby, fail_apply=not wrong_hash, wrong_hash=wrong_hash)

    with pytest.raises(CultureError):
        _replicate(source, state, transport, now=100)

    assert state.read_bytes() == before
    assert transport.remove_calls == 1
    assert transport.staged is not None
    assert not transport.staged.exists()


@pytest.mark.parametrize(
    "host",
    ["spark2", "operator@spark2", "spark2.local", "spark2.tailnet.ts.net", "100.64.0.8"],
)
def test_private_transport_accepts_lan_and_tailnet_hosts(host: str) -> None:
    assert console_replica.validate_private_host(host) == host


@pytest.mark.parametrize("host", ["203.0.113.20", "-oProxyCommand=bad", "bad host"])
def test_private_transport_rejects_public_or_unsafe_host(host: str) -> None:
    with pytest.raises(CultureError):
        console_replica.validate_private_host(host)


def test_ssh_transport_uses_explicit_remote_culture_and_safe_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replica = tmp_path / "accounts.db"
    replica.write_bytes(b"private")
    calls: list[tuple[list[str], str | None]] = []
    summary = console_replica.RowSummary(1, 1, 1, "a" * 64)

    def fake_run(command: list[str], *, stdin: str | None = None):
        calls.append((command, stdin))
        if command[:3] == ["ssh", "-G", "--"]:
            return SimpleNamespace(
                stdout="hostname 100.64.0.8\nproxycommand none\nproxyjump none\n"
            )
        return SimpleNamespace(stdout=console_replica._format_summary(summary) + "\n")

    monkeypatch.setattr(console_replica, "_run", fake_run)
    monkeypatch.setattr(
        console_replica.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.8", 0))],
    )
    transport = console_replica.SshTransport("scp")
    staged = transport.stage(replica, HOST, REMOTE_STORE)
    output = transport.apply(HOST, staged, REMOTE_STORE, "/opt/culture/bin/culture", "a" * 64)
    transport.remove(HOST, staged)

    console_replica._require_verified(output, summary)
    assert "a" * 64 not in output
    assert "a" * 64 in calls[2][0]
    assert calls[0][0] == ["ssh", "-G", "--", HOST]
    assert calls[1][0][:6] == ["scp", "-q", "-p", "-o", "BatchMode=yes", "--"]
    assert calls[1][0][-1].startswith(f"{HOST}:/var/lib/irc-lens/.accounts.db.replica-")
    assert calls[2][0][:5] == ["ssh", "-o", "BatchMode=yes", "--", HOST]
    assert "/opt/culture/bin/culture" in calls[2][0]
    assert "irc-lens" not in calls[2][0]
    assert 'exec "$culture" console replica apply' in (calls[2][1] or "")
    assert calls[3][1] == 'rm -f -- "$1"\n'


def test_ssh_transport_refuses_alias_resolving_to_public_ip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replica = tmp_path / "accounts.db"
    replica.write_bytes(b"private")
    monkeypatch.setattr(
        console_replica,
        "_run",
        lambda command, stdin=None: SimpleNamespace(stdout="hostname public.example\n"),
    )
    monkeypatch.setattr(
        console_replica.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))],
    )

    transport = console_replica.SshTransport()
    with pytest.raises(CultureError) as excinfo:
        transport.stage(replica, HOST, REMOTE_STORE)

    assert "does not stay on the tailnet or LAN" in excinfo.value.message


def test_console_replica_parses_push_and_apply_verbs() -> None:
    push = console_replica.parse_args(
        [
            "--source",
            "/srv/irc-lens/accounts.db",
            "--host",
            HOST,
            "--destination",
            REMOTE_STORE,
            "--remote-culture",
            "/opt/culture/bin/culture",
            "--state-file",
            "/tmp/replica-state.json",
            "--max-age",
            "24h",
            "--force",
        ]
    )
    apply = console_replica.parse_args(
        [
            "apply",
            "/tmp/staged.db",
            "--destination",
            REMOTE_STORE,
            "--expected-hash",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ]
    )

    assert push.command == "push"
    assert push.max_age == 86400
    assert push.force
    assert push.remote_culture == "/opt/culture/bin/culture"
    assert apply.command == "apply"
    assert apply.staged == Path("/tmp/staged.db")


def test_console_dispatch_keeps_replica_out_of_irc_lens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(console_replica, "main", lambda argv: seen.append(argv))

    console.dispatch(
        SimpleNamespace(
            console_args=[
                "replica",
                "apply",
                "/tmp/staged.db",
                "--destination",
                REMOTE_STORE,
            ]
        )
    )

    assert seen == [["apply", "/tmp/staged.db", "--destination", REMOTE_STORE]]


def test_mismatched_hash_rolls_back_and_prints_no_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "primary.db"
    standby = tmp_path / "standby.db"
    staged = tmp_path / "accounts.stage"
    _seed_approved_store(source)
    built = console_replica.create_replica(source, staged, source_version=VERSION)
    first = console_replica.apply_replica(
        staged, standby, expected_digest=built.digest, installed_version=VERSION
    )
    before = _rows(standby, "users")
    print(console_replica._format_summary(first))
    assert built.digest not in capsys.readouterr().out

    _, access = _seed_approved_store(source)
    access.set_user("new@example.test", role="member", state="approved", actor=None)
    console_replica.create_replica(source, staged, source_version=VERSION)
    with pytest.raises(CultureError, match="unchanged"):
        console_replica.apply_replica(
            staged, standby, expected_digest="0" * 64, installed_version=VERSION
        )

    assert _rows(standby, "users") == before
    assert not staged.exists()
