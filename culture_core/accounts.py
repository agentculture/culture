"""Account store for external residents (culture#506).

Mints per-nick secrets into the ``grant`` store (hidden), and writes the
accounts file AgentIRC reads (``<data_dir>/accounts.json``) containing only
the sha256 of each secret.  Every change appends one JSON line to an audit
log (``<data_dir>/accounts-audit.jsonl``) that never carries secret material.

A server's culture only ever writes its own ``<server>-`` nicks.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from grant import store as grant_store
from grant.generate import random_secret

ACCOUNTS_VERSION = 1
ROLES = ("resident", "operator", "guest")
STATES = ("active", "suspended", "revoked")
GRANT_LEVELS = ("read", "speak")
SECRET_PREFIX = "CULTURE_AGENT_"
SECRET_SOURCE = "culture agents register"
ROTATE_VERB = "culture agents account rotate"
DEFAULT_DATA_DIR = "~/.culture/data"
# Version floors for SASL accounts (deviation d5: t16 sets the real pins).
MIN_CULTUREAGENT_SASL = "0.16.0"
MIN_AGENTIRC_ACCOUNTS = "9.14.0"
ACCOUNTS_FILENAME = "accounts.json"
AUDIT_FILENAME = "accounts-audit.jsonl"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AccountError(Exception):
    """Invalid account operation (bad nick, role, state, grant, file...)."""


@dataclass
class AccountRecord:
    nick: str
    secret_sha256: str
    role: str = "resident"
    grants: dict[str, str] = field(default_factory=dict)
    state: str = "active"


def default_data_dir() -> Path:
    """Server data dir (same default as ``culture server start --data-dir``)."""
    return Path(os.path.expanduser(DEFAULT_DATA_DIR))


def accounts_path(data_dir: str | Path | None = None) -> Path:
    return (
        Path(data_dir) / ACCOUNTS_FILENAME if data_dir else default_data_dir() / ACCOUNTS_FILENAME
    )


def audit_path(data_dir: str | Path | None = None) -> Path:
    return Path(data_dir) / AUDIT_FILENAME if data_dir else default_data_dir() / AUDIT_FILENAME


def secret_name(nick: str) -> str:
    """grant secret name for a nick: CULTURE_AGENT_<NICK upper, '-' -> '_'>."""
    name = SECRET_PREFIX + nick.upper().replace("-", "_")
    if not grant_store.NAME_RE.match(name):
        raise AccountError(
            f"nick {nick!r} cannot be mapped to a valid grant secret name "
            f"({name!r}; names are A-Z, 0-9, _ and at most 64 characters)"
        )
    return name


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


def load(data_dir: str | Path | None = None) -> list[AccountRecord]:
    """Read the accounts file; a missing file is an empty store."""
    path = accounts_path(data_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != ACCOUNTS_VERSION:
            raise AccountError(f"{path}: unsupported version {raw.get('version')!r}")
        recs = [
            AccountRecord(
                nick=a["nick"],
                secret_sha256=a["secret_sha256"],
                role=a["role"],
                grants=dict(a.get("grants", {})),
                state=a["state"],
            )
            for a in raw["accounts"]
        ]
    except AccountError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AccountError(f"{path}: unreadable accounts file: {exc}") from exc
    return recs


def save(
    records: list[AccountRecord], server_name: str, data_dir: str | Path | None = None
) -> Path:
    """Validate everything, then atomically write the accounts file (0600)."""
    seen: set[str] = set()
    for rec in records:
        _check_own(rec.nick, server_name)
        if rec.nick in seen:
            raise AccountError(f"duplicate nick {rec.nick!r}")
        seen.add(rec.nick)
        _validate_record(rec)
    path = accounts_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ACCOUNTS_VERSION,
        "accounts": [asdict(r) for r in records],
    }
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
    """Existing records for this server only (foreign nicks are dropped)."""
    prefix = f"{server_name}-"
    return [r for r in load(data_dir) if r.nick.startswith(prefix)]


def _find(records: list[AccountRecord], nick: str) -> AccountRecord:
    for rec in records:
        if rec.nick == nick:
            return rec
    raise AccountError(f"no account for {nick!r}")


def _stage(
    agent_config: Any, server_name: str, role: str, data_dir: str | Path | None
) -> tuple[list[AccountRecord], AccountRecord, bool]:
    """Write the secret to grant and return ``(records, rec, existed)`` WITHOUT
    saving the accounts file (the "publish" step)."""
    nick = f"{server_name}-{agent_config.suffix}"
    _check_own(nick, server_name)
    name = secret_name(nick)
    if role not in ROLES:
        raise AccountError(f"invalid role {role!r} (expected one of {ROLES})")
    records = _own_records(server_name, data_dir)
    existing = next((r for r in records if r.nick == nick), None)
    if existing is None:
        grants = {c: "speak" for c in (getattr(agent_config, "channels", None) or [])}
        _validate_grants(grants)
    secret = random_secret()
    digest = hash_secret(secret)
    if existing is None:
        rec = AccountRecord(nick=nick, secret_sha256=digest, role=role, grants=grants)
        records.append(rec)
    else:
        rec = existing
        rec.secret_sha256 = digest
    _validate_record(rec)
    # grant first: a failure there must not leave a hash with no secret behind.
    grant_store.set_secret(
        name=name,
        value=secret,
        hidden=True,
        source=SECRET_SOURCE,
        purpose=f"AgentIRC account secret for {nick}",
        rotation_howto=f"{ROTATE_VERB} {nick}",
    )
    return records, rec, existing is not None


def _publish(
    records: list[AccountRecord],
    rec: AccountRecord,
    server_name: str,
    data_dir: str | Path | None,
    existed: bool,
) -> None:
    save(records, server_name, data_dir)
    _audit(
        "rotate" if existed else "create",
        rec.nick,
        data_dir,
        {
            "role": rec.role,
            "grants": rec.grants,
            "state": rec.state,
            "secret_name": secret_name(rec.nick),
        },
    )


def mint(
    agent_config: Any,
    server_name: str,
    *,
    role: str = "resident",
    data_dir: str | Path | None = None,
) -> AccountRecord:
    """Create or rotate the account for ``<server>-<suffix>`` (mint + publish).

    The secret goes to grant (hidden); only its sha256 reaches the accounts
    file.  New accounts get ``speak`` on every ``channels:`` entry; an existing
    account keeps its role/grants/state across a rotation.  First-time
    activation should go through :func:`activate`, which orders the publish
    last so a failed step can never lock an agent out.
    """
    records, rec, existed = _stage(agent_config, server_name, role, data_dir)
    _publish(records, rec, server_name, data_dir, existed)
    return rec


class ActivationError(AccountError):
    """An activation step failed; ``step`` names it; nothing was published."""

    def __init__(self, step: str, message: str):
        super().__init__(step, message)
        self.step = step
        self.message = message

    def __str__(self) -> str:
        return f"activation step '{self.step}' failed: {self.message}; accounts file unchanged"


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
    """Return a message if the installed cultureagent cannot speak SASL."""
    have = (version_of or installed_version)("cultureagent")
    if version_at_least(have, MIN_CULTUREAGENT_SASL):
        return None
    return (
        f"installed cultureagent {have or '(not installed)'} is below "
        f"{MIN_CULTUREAGENT_SASL}, the first release with SASL support"
    )


def inject_secret_for(nick: str, server_name: str, data_dir: str | Path | None = None):
    """grant secret name to inject into the nick's unit, or None (no account)."""
    return secret_name(nick) if find(nick, server_name, data_dir) is not None else None


def activate(
    agent_config: Any,
    server_name: str,
    *,
    role: str = "resident",
    data_dir: str | Path | None = None,
    external: bool = False,
    update_unit=None,
    version_of=None,
) -> AccountRecord:
    """Ordered account activation (culture#506): mint -> unit -> client check -> publish.

    1. mint the secret into grant (staged; the accounts file is NOT touched);
    2. ``update_unit(nick, secret_name)`` wraps the agent's installed unit
       with the grant inject (skipped for ``external`` residents, which never
       get a culture unit; also skipped when ``update_unit`` is None);
    3. the installed cultureagent must be >= ``MIN_CULTUREAGENT_SASL``;
    4. only then publish the entry to the accounts file agentirc enforces.

    Any earlier failure raises :class:`ActivationError` and the accounts file
    is left as it was, so the agent keeps connecting the legacy way and is
    never locked out.  An already-active account keeps its grant secret
    (rotate is a separate verb) unless grant no longer holds it.
    """
    nick = f"{server_name}-{agent_config.suffix}"
    name = secret_name(nick)
    try:
        existing = find(nick, server_name, data_dir)
        have_secret = existing is not None
        if have_secret:
            try:
                grant_store.get_record(name)
            except Exception:  # secret missing/unreadable in grant -> re-mint
                have_secret = False
        if have_secret:
            records = _own_records(server_name, data_dir)
            rec = _find(records, nick)
            existed = True
        else:
            records, rec, existed = _stage(agent_config, server_name, role, data_dir)
    except Exception as exc:
        raise ActivationError("mint", str(exc)) from exc

    if not external and update_unit is not None:
        try:
            update_unit(nick, name)
        except Exception as exc:
            raise ActivationError("unit", str(exc)) from exc

    problem = client_version_problem(version_of)
    if problem:
        raise ActivationError("client-version", problem)

    rec.state = "active"
    try:
        _publish(records, rec, server_name, data_dir, existed)
    except Exception as exc:
        raise ActivationError("publish", str(exc)) from exc
    return rec


def _mutate(nick: str, server_name: str, data_dir, action: str, apply) -> AccountRecord:
    _check_own(nick, server_name)
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
    records = _own_records(server_name, data_dir)
    rec = _find(records, nick)
    records.remove(rec)
    save(records, server_name, data_dir)
    try:
        grant_store.delete(secret_name(nick))
    except grant_store.NotFoundError:
        pass
    _audit("remove", nick, data_dir, {"role": rec.role, "state": rec.state})
    return rec


def revoke(nick: str, server_name: str, data_dir=None) -> AccountRecord:
    """Revoke the account but keep a tombstone entry.

    The grant secret is deleted and the state becomes ``revoked``; the entry
    stays in the accounts file so nobody else can take the nick.  Use
    :func:`remove` to drop the entry (returns the nick to legacy behaviour).
    """
    _check_own(nick, server_name)
    rec = _mutate(nick, server_name, data_dir, "revoke", lambda r: setattr(r, "state", "revoked"))
    try:
        grant_store.delete(secret_name(nick))
    except grant_store.NotFoundError:
        pass
    return rec


def find(nick: str, server_name: str, data_dir: str | Path | None = None) -> AccountRecord | None:
    """Return the account for ``nick`` or None."""
    return next((r for r in _own_records(server_name, data_dir) if r.nick == nick), None)
