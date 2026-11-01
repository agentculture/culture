"""Account store for agent accounts (culture#506).

Mints per-nick secrets into the ``grant`` store (hidden), and writes the
accounts file AgentIRC reads (``<data_dir>/accounts.json``) containing only
the sha256 of each secret.  Every change appends one JSON line to an audit
log (``<data_dir>/accounts-audit.jsonl``) that never carries secret material.

Accounts are opt-in: nothing here runs on ``culture agents register``; an
operator activates an agent explicitly (``culture agents account activate``,
see :func:`activate`).

A server's culture only ever writes its own ``<server>-`` nicks; records of
other servers found in the file are carried through every write unchanged.
Every read-modify-write of the file (and the grant write that goes with it)
runs under an exclusive ``flock`` on ``<data_dir>/.accounts.lock``.
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import ipaddress
import json
import os
import re
import shlex
import socket
import tempfile
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from grant import store as grant_store
from grant.generate import random_secret

try:  # POSIX only; Windows runs without the cross-process lock
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

ACCOUNTS_VERSION = 1
ROLES = ("resident", "operator", "guest")
STATES = ("active", "suspended", "revoked")
GRANT_LEVELS = ("read", "speak")
SECRET_PREFIX = "CULTURE_AGENT_"
SECRET_SOURCE = "culture agents account"
ACTIVATE_VERB = "culture agents account activate"
ROTATE_VERB = "culture agents account rotate"
DEFAULT_DATA_DIR = "~/.culture/data"
# Version floors for SASL accounts (deviation d5: t16 sets the real pins).
MIN_CULTUREAGENT_SASL = "0.16.0"
MIN_AGENTIRC_ACCOUNTS = "9.14.0"
ACCOUNTS_FILENAME = "accounts.json"
AUDIT_FILENAME = "accounts-audit.jsonl"
LOCK_FILENAME = ".accounts.lock"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
# grant names: ^[A-Z_][A-Z0-9_]{0,63}$ -> at most 64 characters.
_GRANT_NAME_MAX = 64
_SECRET_TAG_LEN = 10


class AccountError(Exception):
    """Invalid account operation (bad nick, role, state, grant, file...)."""


@dataclass
class AccountRecord:
    nick: str
    secret_sha256: str
    role: str = "resident"
    grants: dict[str, str] = field(default_factory=dict)
    state: str = "active"


# ---------------------------------------------------------------------------
# paths, names
# ---------------------------------------------------------------------------


def default_data_dir() -> Path:
    """Server data dir (same default as ``culture server start --data-dir``)."""
    return Path(os.path.expanduser(DEFAULT_DATA_DIR))


def _dir(data_dir: str | Path | None) -> Path:
    return Path(os.path.expanduser(str(data_dir))) if data_dir else default_data_dir()


def accounts_path(data_dir: str | Path | None = None) -> Path:
    return _dir(data_dir) / ACCOUNTS_FILENAME


def audit_path(data_dir: str | Path | None = None) -> Path:
    return _dir(data_dir) / AUDIT_FILENAME


def secret_name(nick: str) -> str:
    """grant secret name for a nick (injective).

    ``CULTURE_AGENT_<readable>_<tag>``: *readable* is the nick upper-cased
    with every other character mapped to ``_`` (truncated to fit), *tag* the
    first hex digits of sha256 of the exact nick, so ``spark-my_agent`` /
    ``spark-my-agent`` and ``spark-Alpha`` / ``spark-alpha`` never share a
    secret.  Always a valid grant name (``^[A-Z_][A-Z0-9_]{0,63}$``).
    """
    if not nick:
        raise AccountError("empty nick has no secret name")
    readable = re.sub(r"[^A-Z0-9]", "_", nick.upper())
    tag = hashlib.sha256(nick.encode("utf-8")).hexdigest()[:_SECRET_TAG_LEN].upper()
    room = _GRANT_NAME_MAX - len(SECRET_PREFIX) - 1 - len(tag)
    name = f"{SECRET_PREFIX}{readable[:room]}_{tag}"
    if not grant_store.NAME_RE.match(name):  # pragma: no cover - by construction
        raise AccountError(f"nick {nick!r} cannot be mapped to a valid grant secret name")
    return name


def secret_purpose(nick: str) -> str:
    """The grant ``purpose`` culture stamps on (and checks before touching) a secret."""
    return f"AgentIRC account secret for {nick}"


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _check_own(nick: str, server_name: str) -> None:
    if not server_name or not nick.startswith(f"{server_name}-") or nick == f"{server_name}-":
        raise AccountError(
            f"nick {nick!r} is not a {server_name}- nick; this server cannot write it"
        )


def _validate_record(rec: AccountRecord) -> None:
    if rec.role not in ROLES:
        raise AccountError(f"invalid role {rec.role!r} (expected one of {ROLES})")
    if rec.state not in STATES:
        raise AccountError(f"invalid state {rec.state!r} (expected one of {STATES})")
    if not _HASH_RE.match(rec.secret_sha256):
        raise AccountError(f"invalid secret hash for {rec.nick!r}")
    _validate_grants(rec.grants)


def _validate_grants(grants: Mapping[str, str]) -> None:
    for chan, level in grants.items():
        if not isinstance(chan, str) or not chan.startswith(("#", "&")) or len(chan) < 2:
            raise AccountError(f"invalid channel {chan!r} in grants")
        if level not in GRANT_LEVELS:
            raise AccountError(
                f"invalid grant level {level!r} for {chan} (expected {GRANT_LEVELS})"
            )


# ---------------------------------------------------------------------------
# locking (M1): one writer at a time per data dir, across processes
# ---------------------------------------------------------------------------

_LOCAL = threading.local()


@contextlib.contextmanager
def _locked(data_dir: str | Path | None):
    """Exclusive flock on ``<data_dir>/.accounts.lock``; re-entrant per thread."""
    d = _dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    key = str(d.resolve())
    held: set[str] = getattr(_LOCAL, "held", None) or set()
    _LOCAL.held = held
    if key in held:
        yield
        return
    fd = os.open(d / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# file I/O
# ---------------------------------------------------------------------------


def _read_raw(data_dir: str | Path | None) -> list[dict]:
    path = accounts_path(data_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != ACCOUNTS_VERSION:
            raise AccountError(f"{path}: unsupported version {raw.get('version')!r}")
        entries = raw["accounts"]
        if not isinstance(entries, list) or not all(isinstance(a, dict) for a in entries):
            raise AccountError(f"{path}: 'accounts' must be a list of objects")
    except AccountError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AccountError(f"{path}: unreadable accounts file: {exc}") from exc
    return entries


def load(data_dir: str | Path | None = None) -> list[AccountRecord]:
    """Read the accounts file (every server's records); a missing file is empty."""
    path = accounts_path(data_dir)
    try:
        return [
            AccountRecord(
                nick=a["nick"],
                secret_sha256=a["secret_sha256"],
                role=a["role"],
                grants=dict(a.get("grants", {})),
                state=a["state"],
            )
            for a in _read_raw(data_dir)
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AccountError(f"{path}: unreadable accounts file: {exc}") from exc


def _write_file(path: Path, payload: dict) -> None:
    """Atomically write *payload* as JSON to *path* (mode 0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save(
    records: list[AccountRecord], server_name: str, data_dir: str | Path | None = None
) -> Path:
    """Validate this server's *records*, then atomically rewrite the file.

    *records* is the complete set of ``<server_name>-`` accounts; records of
    other servers already in the file are carried through unchanged (M2).
    """
    seen: set[str] = set()
    for rec in records:
        _check_own(rec.nick, server_name)
        if rec.nick in seen:
            raise AccountError(f"duplicate nick {rec.nick!r}")
        seen.add(rec.nick)
        _validate_record(rec)
    prefix = f"{server_name}-"
    path = accounts_path(data_dir)
    with _locked(data_dir):
        foreign = [a for a in _read_raw(data_dir) if not str(a.get("nick", "")).startswith(prefix)]
        _write_file(
            path,
            {"version": ACCOUNTS_VERSION, "accounts": foreign + [asdict(r) for r in records]},
        )
    return path


def _audit(
    action: str, nick: str, data_dir: str | Path | None, changes: dict[str, Any] | None = None
) -> None:
    """Append one line; never include secret values or hashes."""
    try:
        actor = getpass.getuser()
    except Exception:  # pragma: no cover - no passwd entry
        actor = str(os.getuid())
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "action": action,
        "nick": nick,
        "changes": changes or {},
    }
    path = audit_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")


def _own_records(server_name: str, data_dir: str | Path | None) -> list[AccountRecord]:
    """Existing records for this server only."""
    prefix = f"{server_name}-"
    return [r for r in load(data_dir) if r.nick.startswith(prefix)]


def _find(records: list[AccountRecord], nick: str) -> AccountRecord:
    for rec in records:
        if rec.nick == nick:
            return rec
    raise AccountError(f"no account for {nick!r}")


def find(nick: str, server_name: str, data_dir: str | Path | None = None) -> AccountRecord | None:
    """Return the account for ``nick`` or None."""
    return next((r for r in _own_records(server_name, data_dir) if r.nick == nick), None)


# ---------------------------------------------------------------------------
# grant secrets (H5 ownership guard)
# ---------------------------------------------------------------------------


def _owned_grant_record(nick: str):
    """culture's grant record for *nick*, None if absent; refuses a foreign one."""
    name = secret_name(nick)
    try:
        rec = grant_store.get_record(name)
    except grant_store.NotFoundError:
        return None
    if rec.source != SECRET_SOURCE or rec.purpose != secret_purpose(nick):
        raise AccountError(
            f"grant secret {name} is not culture's account secret for {nick} "
            f"(source {rec.source!r}, purpose {rec.purpose!r}); refusing to touch it"
        )
    return rec


def _put_secret(nick: str, value: str) -> None:
    grant_store.set_secret(
        name=secret_name(nick),
        value=value,
        hidden=True,
        source=SECRET_SOURCE,
        purpose=secret_purpose(nick),
        rotation_howto=f"{ROTATE_VERB} {nick}",
    )


def _restore_secret(nick: str, prior) -> None:
    """Put back the grant record *prior* (or delete the secret if there was none)."""
    if prior is not None:
        _put_secret(nick, prior.value)
        return
    with contextlib.suppress(grant_store.NotFoundError):
        grant_store.delete(secret_name(nick))


def _delete_secret(nick: str) -> None:
    if _owned_grant_record(nick) is None:
        return
    with contextlib.suppress(grant_store.NotFoundError):
        grant_store.delete(secret_name(nick))


# ---------------------------------------------------------------------------
# create / rotate (with rollback, M4)
# ---------------------------------------------------------------------------


def _rekey(
    records: list[AccountRecord],
    rec: AccountRecord,
    server_name: str,
    data_dir: str | Path | None,
    action: str,
) -> AccountRecord:
    """New secret into grant, then save; a failed save restores the old secret."""
    prior = _owned_grant_record(rec.nick)
    secret = random_secret()
    rec.secret_sha256 = hash_secret(secret)
    _validate_record(rec)
    _put_secret(rec.nick, secret)
    try:
        save(records, server_name, data_dir)
    except BaseException:
        _restore_secret(rec.nick, prior)
        raise
    _audit(
        action,
        rec.nick,
        data_dir,
        {
            "role": rec.role,
            "grants": rec.grants,
            "state": rec.state,
            "secret_name": secret_name(rec.nick),
        },
    )
    return rec


def mint(
    agent_config: Any,
    server_name: str,
    *,
    role: str = "resident",
    data_dir: str | Path | None = None,
) -> AccountRecord:
    """Create or rotate the account for ``<server>-<suffix>`` (store-level).

    The secret goes to grant (hidden); only its sha256 reaches the accounts
    file.  New accounts get ``speak`` on every ``channels:`` entry; an
    existing account keeps its role/grants/state.  This touches no unit and
    no daemon: agents are activated through :func:`activate`.
    """
    nick = f"{server_name}-{agent_config.suffix}"
    _check_own(nick, server_name)
    if role not in ROLES:
        raise AccountError(f"invalid role {role!r} (expected one of {ROLES})")
    with _locked(data_dir):
        records = _own_records(server_name, data_dir)
        rec = next((r for r in records if r.nick == nick), None)
        action = "rotate"
        if rec is None:
            grants = {c: "speak" for c in (getattr(agent_config, "channels", None) or [])}
            _validate_grants(grants)
            rec = AccountRecord(nick=nick, secret_sha256="0" * 64, role=role, grants=grants)
            records.append(rec)
            action = "create"
        return _rekey(records, rec, server_name, data_dir, action)


def rotate(nick: str, server_name: str, data_dir: str | Path | None = None) -> AccountRecord:
    """Re-key an existing (non-revoked) account; role, grants and state are kept."""
    _check_own(nick, server_name)
    with _locked(data_dir):
        records = _own_records(server_name, data_dir)
        rec = _find(records, nick)
        if rec.state == "revoked":
            raise AccountError(
                f"account {nick} is revoked; reactivate it with '{ACTIVATE_VERB} {nick}'"
            )
        return _rekey(records, rec, server_name, data_dir, "rotate")


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------


def installed_version(dist: str) -> str | None:
    from importlib import metadata

    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def version_at_least(have: str | None, floor: str) -> bool:
    from packaging.version import InvalidVersion, Version

    if have is None:
        return False
    try:
        return Version(have) >= Version(floor)
    except InvalidVersion:
        return False


def client_version_problem(version_of=None) -> str | None:
    """Return a message if the cultureagent (per *version_of*) cannot speak SASL."""
    have = (version_of or installed_version)("cultureagent")
    if version_at_least(have, MIN_CULTUREAGENT_SASL):
        return None
    return (
        f"cultureagent {have or '(not installed)'} is below "
        f"{MIN_CULTUREAGENT_SASL}, the first release with SASL support"
    )


# ---------------------------------------------------------------------------
# which data dir is the server's? (M5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerDataDir:
    """The data dir a server reads ``accounts.json`` from (None: --no-persist)."""

    path: Path | None
    source: str

    @property
    def persist(self) -> bool:
        return self.path is not None


def _data_dir_from_argv(argv: list[str]) -> tuple[Path | None, bool]:
    """``(data_dir, persist)`` from a ``culture server start`` command line."""
    if "--no-persist" in argv:
        return None, False
    for i, arg in enumerate(argv):
        if arg == "--data-dir" and i + 1 < len(argv):
            return Path(os.path.expanduser(argv[i + 1])), True
        if arg.startswith("--data-dir="):
            return Path(os.path.expanduser(arg.split("=", 1)[1])), True
    return default_data_dir(), True


def _running_server_argv(server_name: str) -> list[str] | None:
    """Command line of the running ``server-<name>`` process (Linux /proc)."""
    from culture_core.pidfile import is_process_alive, read_pid

    pid = read_pid(f"server-{server_name}")
    if not pid or not is_process_alive(pid):
        return None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    return argv if "server" in argv else None


def _unit_server_argv(server_name: str) -> list[str] | None:
    """ExecStart of the installed ``culture-server-<name>`` systemd unit."""
    from culture_core import persistence

    path = persistence._systemd_user_dir() / f"culture-server-{server_name}.service"
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        if line.startswith("ExecStart="):
            try:
                return shlex.split(line[len("ExecStart=") :])
            except ValueError:
                return None
    return None


def _probe_server_argv(server_name: str) -> list[str] | None:
    """How the server was started: the live process first, else its unit."""
    return _running_server_argv(server_name) or _unit_server_argv(server_name)


def resolve_server_data_dir(server_name: str, explicit: str | Path | None = None) -> ServerDataDir:
    """The data dir server *server_name* uses: ``--data-dir`` > the running
    process's command line > its installed unit > the ``server start`` default."""
    if explicit:
        return ServerDataDir(Path(os.path.expanduser(str(explicit))), "--data-dir")
    argv = _probe_server_argv(server_name)
    if argv:
        path, _ = _data_dir_from_argv(argv)
        return ServerDataDir(path, f"server {server_name} command line")
    return ServerDataDir(default_data_dir(), "default")


def is_local_host(host: str) -> bool:
    """True if *host* names this machine (loopback, wildcard or a local address)."""
    h = (host or "").strip().strip("[]")
    if h in ("", "0.0.0.0", "::", "localhost"):  # nosec B104 - compared, never bound
        return True
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        return False
    for family, _, _, _, sockaddr in infos:
        addr = str(sockaddr[0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_unspecified:
            return True
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.bind((addr, 0))
            return True
        except OSError:
            continue
    return False


def activation_data_dir(server_name: str, host: str, explicit: str | Path | None = None) -> Path:
    """The data dir to activate accounts in; refuses servers that can't enforce them."""
    resolved = resolve_server_data_dir(server_name, explicit)
    if resolved.path is None:
        raise AccountError(
            f"server {server_name} runs with --no-persist ({resolved.source}): it reads "
            "no accounts file, so an account could never be enforced"
        )
    if not is_local_host(host):
        raise AccountError(
            f"server host {host} is not this machine: AgentIRC reads accounts.json from "
            "its own data dir, so activate the account on the server's host"
        )
    return resolved.path


# ---------------------------------------------------------------------------
# ordered, opt-in activation (culture#506 d7)
# ---------------------------------------------------------------------------


class UnitOps(Protocol):
    """Service-manager hooks :func:`activate` drives for culture-owned agents."""

    def exists(self, nick: str) -> bool: ...
    def client_version(self, nick: str) -> str | None: ...
    def wrap(self, nick: str, name: str) -> bool | None: ...
    def unwrap(self, nick: str, name: str) -> bool | None: ...
    def restart(self, nick: str) -> bool: ...
    def verify(self, nick: str) -> str | None: ...


class ActivationError(AccountError):
    """An activation step failed; ``step`` names it. Everything was rolled back
    unless ``rollback_errors`` says otherwise."""

    def __init__(self, step: str, message: str, rollback_errors: list[str] | None = None):
        super().__init__(step, message, rollback_errors)
        self.step = step
        self.message = message
        self.rollback_errors = list(rollback_errors or [])

    def __str__(self) -> str:
        head = f"activation step '{self.step}' failed: {self.message}"
        if self.rollback_errors:
            return f"{head}; ROLLBACK INCOMPLETE: {'; '.join(self.rollback_errors)}"
        return f"{head}; rolled back (accounts file, unit and grant secret as before)"


@dataclass
class ActivationResult:
    nick: str
    secret_name: str
    reused: bool
    published: bool
    pending_owner: bool = False
    record: AccountRecord | None = None


def inject_secret_for(nick: str, server_name: str, data_dir: str | Path | None = None):
    """grant secret name to inject into the nick's unit, or None.

    Only an *active* account is injected: a suspended/revoked/absent account
    never gets a wrapper baked into a unit (its secret may be gone).
    """
    rec = find(nick, server_name, data_dir)
    return secret_name(nick) if rec is not None and rec.state == "active" else None


def secret_for_start(nick: str, server_name: str, data_dir: str | Path | None = None):
    """The secret ``culture agents start`` hands the daemon, or None (no account).

    Raises :class:`AccountError` for an inactive account or a grant secret
    that is missing or doesn't match the published hash.
    """
    rec = find(nick, server_name, data_dir)
    if rec is None:
        return None
    if rec.state != "active":
        raise AccountError(
            f"account {nick} is {rec.state}: the server refuses it "
            f"(reactivate with '{ACTIVATE_VERB} {nick}')"
        )
    grec = _owned_grant_record(nick)
    if grec is None:
        raise AccountError(
            f"account {nick} is active but grant has no secret {secret_name(nick)} "
            f"(re-run '{ACTIVATE_VERB} {nick}')"
        )
    if hash_secret(grec.value) != rec.secret_sha256:
        raise AccountError(
            f"grant secret {secret_name(nick)} does not match the published account "
            f"for {nick} (re-run '{ACTIVATE_VERB} {nick}')"
        )
    return grec.value


def _restore_entry(
    nick: str, server_name: str, data_dir: str | Path | None, prior: AccountRecord | None
) -> None:
    with _locked(data_dir):
        records = [r for r in _own_records(server_name, data_dir) if r.nick != nick]
        if prior is not None:
            records.append(prior)
        save(records, server_name, data_dir)
        _audit("activate-rollback", nick, data_dir, {"restored": prior is not None})


def activate(
    agent_config: Any,
    server_name: str,
    *,
    role: str = "resident",
    data_dir: str | Path | None = None,
    external: bool = False,
    owner_ready: bool = False,
    unit: UnitOps | None = None,
) -> ActivationResult:
    """Ordered, explicit account activation with full rollback.

    1. preflight: culture-owned agents need ``unit`` and an installed unit,
       and the cultureagent the unit runs must be >= ``MIN_CULTUREAGENT_SASL``
       (skipped for external residents, whose process culture doesn't own);
    2. mint into grant, or reuse culture's secret whose hash matches the
       published one (or, with no entry yet, the secret a previous
       un-published external activation minted);
    3. culture-owned: wrap the unit (must report wrapped/already wrapped);
    4. publish the entry as ``active`` (external: only with ``owner_ready``,
       otherwise stop after step 2 -> ``pending_owner``);
    5. culture-owned: restart the unit;
    6. culture-owned: ``unit.verify`` that the agent reconnected.

    A failure raises :class:`ActivationError` naming the step after undoing
    the earlier steps in reverse: the previous entry (or none) is restored,
    the original ExecStart is put back, the minted secret is restored or
    deleted, and a unit that was already restarted is restarted again so the
    agent comes back exactly as before.
    """
    nick = f"{server_name}-{agent_config.suffix}"
    try:
        _check_own(nick, server_name)
        name = secret_name(nick)
        if role not in ROLES:
            raise AccountError(f"invalid role {role!r} (expected one of {ROLES})")
    except AccountError as exc:
        raise ActivationError("preflight", str(exc)) from exc

    if not external:
        if unit is None:
            raise ActivationError(
                "preflight", f"no service manager available to wrap {nick}'s unit"
            )
        if not unit.exists(nick):
            raise ActivationError(
                "preflight",
                f"{nick} has no installed unit, so nothing would hand the daemon its "
                f"secret; install it first (culture agents install {nick}) and rerun",
            )
        problem = client_version_problem(lambda _dist: unit.client_version(nick))
        if problem:
            raise ActivationError("preflight", f"the agent's unit runs {problem}")

    state = {"minted": False, "wrapped": False, "published": False, "restarted": False}
    prior_entry: AccountRecord | None = None
    prior_secret = None

    def fail(step: str, exc_or_msg) -> ActivationError:
        errors: list[str] = []
        if state["published"]:
            try:
                _restore_entry(nick, server_name, data_dir, prior_entry)
            except Exception as e:  # noqa: BLE001 - report, keep undoing
                errors.append(f"accounts entry: {e}")
        if state["wrapped"]:
            try:
                unit.unwrap(nick, name)
            except Exception as e:  # noqa: BLE001
                errors.append(f"unit ExecStart: {e}")
        if state["minted"]:
            try:
                _restore_secret(nick, prior_secret)
            except Exception as e:  # noqa: BLE001
                errors.append(f"grant secret {name}: {e}")
        if state["restarted"]:
            try:
                if not unit.restart(nick):
                    errors.append(f"restart of {nick}'s unit after rollback")
            except Exception as e:  # noqa: BLE001
                errors.append(f"restart after rollback: {e}")
        return ActivationError(step, str(exc_or_msg), errors)

    with _locked(data_dir):
        # -- 2. mint ---------------------------------------------------------
        try:
            records = _own_records(server_name, data_dir)
            existing = next((r for r in records if r.nick == nick), None)
            if existing is not None:
                prior_entry = replace(existing, grants=dict(existing.grants))
            prior_secret = _owned_grant_record(nick)
            reused = prior_secret is not None and (
                existing is None
                or (
                    existing.state != "revoked"
                    and hash_secret(prior_secret.value) == existing.secret_sha256
                )
            )
            if reused:
                secret = prior_secret.value
            else:
                secret = random_secret()
                state["minted"] = True
                _put_secret(nick, secret)
        except Exception as exc:
            raise fail("mint", exc) from exc

        if external and not owner_ready:
            return ActivationResult(nick, name, reused, published=False, pending_owner=True)

        # -- 3. unit ---------------------------------------------------------
        if not external:
            try:
                wrapped = unit.wrap(nick, name)
            except Exception as exc:
                raise fail("unit", exc) from exc
            if wrapped is None:
                raise fail(
                    "unit",
                    f"{nick}'s unit could not be wrapped with the grant inject (no systemd "
                    "unit found; launchd/Windows units are not wrapped in place)",
                )
            state["wrapped"] = wrapped is True

        # -- 4. publish ------------------------------------------------------
        try:
            if existing is None:
                grants = {c: "speak" for c in (getattr(agent_config, "channels", None) or [])}
                _validate_grants(grants)
                rec = AccountRecord(nick=nick, secret_sha256="0" * 64, role=role, grants=grants)
                records.append(rec)
            else:
                rec = existing
            rec.secret_sha256 = hash_secret(secret)
            rec.state = "active"
            _validate_record(rec)
            save(records, server_name, data_dir)  # atomic: written whole or not at all
            state["published"] = True
            _audit(
                "activate",
                nick,
                data_dir,
                {
                    "secret": "reused" if reused else "minted",
                    "state": {"from": prior_entry.state if prior_entry else None, "to": "active"},
                    "role": rec.role,
                    "grants": rec.grants,
                    "secret_name": name,
                },
            )
        except Exception as exc:
            raise fail("publish", exc) from exc

    result = ActivationResult(nick, name, reused, published=True, record=rec)
    if external:
        return result

    # -- 5. restart (outside the lock: it can take a while) --------------------
    state["restarted"] = True
    try:
        ok = unit.restart(nick)
    except Exception as exc:
        raise fail("restart", exc) from exc
    if not ok:
        raise fail("restart", f"restarting culture-agent-{nick} failed or timed out")

    # -- 6. verify ---------------------------------------------------------------
    try:
        problem = unit.verify(nick)
    except Exception as exc:
        raise fail("verify", exc) from exc
    if problem:
        raise fail("verify", problem)
    return result


# ---------------------------------------------------------------------------
# lifecycle mutations
# ---------------------------------------------------------------------------


def _mutate(nick: str, server_name: str, data_dir, action: str, apply) -> AccountRecord:
    _check_own(nick, server_name)
    with _locked(data_dir):
        records = _own_records(server_name, data_dir)
        rec = _find(records, nick)
        before = asdict(rec)
        apply(rec)
        _validate_record(rec)
        save(records, server_name, data_dir)
        after = asdict(rec)
        changes = {
            k: {"from": before[k], "to": after[k]}
            for k in ("role", "grants", "state")
            if before[k] != after[k]
        }
        _audit(action, nick, data_dir, changes)
    return rec


def set_state(nick: str, server_name: str, state: str, data_dir=None) -> AccountRecord:
    if state not in STATES:
        raise AccountError(f"invalid state {state!r} (expected one of {STATES})")
    return _mutate(nick, server_name, data_dir, "set_state", lambda r: setattr(r, "state", state))


def set_role(nick: str, server_name: str, role: str, data_dir=None) -> AccountRecord:
    if role not in ROLES:
        raise AccountError(f"invalid role {role!r} (expected one of {ROLES})")
    return _mutate(nick, server_name, data_dir, "set_role", lambda r: setattr(r, "role", role))


def set_grants(
    nick: str, server_name: str, grants: Mapping[str, str], data_dir=None
) -> AccountRecord:
    _validate_grants(grants)
    return _mutate(
        nick, server_name, data_dir, "set_grants", lambda r: setattr(r, "grants", dict(grants))
    )


def remove(nick: str, server_name: str, data_dir=None) -> AccountRecord:
    """Delete the account and its grant secret; returns the removed record."""
    _check_own(nick, server_name)
    with _locked(data_dir):
        records = _own_records(server_name, data_dir)
        rec = _find(records, nick)
        records.remove(rec)
        save(records, server_name, data_dir)
        _delete_secret(nick)
        _audit("remove", nick, data_dir, {"role": rec.role, "state": rec.state})
    return rec


def revoke(nick: str, server_name: str, data_dir=None) -> AccountRecord:
    """Revoke the account but keep a tombstone entry.

    The grant secret is deleted and the state becomes ``revoked``; the entry
    stays in the accounts file so nobody else can take the nick.  Use
    :func:`remove` to drop the entry (returns the nick to legacy behaviour),
    or :func:`activate` to reactivate it with a fresh secret.
    """
    _check_own(nick, server_name)
    with _locked(data_dir):
        rec = _mutate(
            nick, server_name, data_dir, "revoke", lambda r: setattr(r, "state", "revoked")
        )
        _delete_secret(nick)
    return rec


__all__ = [
    "AccountError",
    "AccountRecord",
    "ActivationError",
    "ActivationResult",
    "ServerDataDir",
    "UnitOps",
    "activate",
    "activation_data_dir",
    "find",
    "inject_secret_for",
    "is_local_host",
    "load",
    "mint",
    "remove",
    "resolve_server_data_dir",
    "revoke",
    "rotate",
    "save",
    "secret_for_start",
    "secret_name",
    "set_grants",
    "set_role",
    "set_state",
]
