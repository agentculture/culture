"""Class 5: agent-account (SASL) drift checks (culture#506)."""

from __future__ import annotations

from pathlib import Path

from culture_core import accounts, accounts_policy
from culture_core.doctor.model import Finding
from culture_core.persistence import _systemd_user_dir, exec_start_has_inject


def is_external(agent) -> bool:
    # Inline (not cli.shared.external): importing the cli package from here is circular.
    return getattr(agent, "backend", None) == "external"


_OPEN_HOSTS = {"0.0.0.0", "::", ""}


def _own_nicks(config) -> list[tuple[str, object]]:
    return [(a.nick, a) for a in config.agents]


def check_agent_units(config, records, unit_dir: Path | None = None) -> list[Finding]:
    """Unit/account drift for culture-owned agents.

    * an active account whose installed unit lacks the grant inject wrapper;
    * a unit still wrapped although the account is gone or not active (it
      would fail on a missing secret or a refused login and park).

    External residents never get a culture-generated unit and are skipped.
    """
    unit_dir = Path(unit_dir) if unit_dir else _systemd_user_dir()
    by_nick = {r.nick: r for r in records}
    findings: list[Finding] = []
    for nick, agent in _own_nicks(config):
        if is_external(agent):
            continue
        path = unit_dir / f"culture-agent-{nick}.service"
        if not path.is_file():
            continue
        finding = _unit_drift(nick, path, by_nick.get(nick))
        if finding is not None:
            findings.append(finding)
    return findings


def _unit_drift(nick: str, path: Path, rec) -> Finding | None:
    """The drift finding for one installed culture-owned unit, or None."""
    wrapped = exec_start_has_inject(path.read_text(), accounts.grant_entry_name(nick))
    if rec is None or rec.state != "active":
        if not wrapped:
            return None
        state = rec.state if rec else "removed"
        return Finding(
            5,
            "error",
            nick,
            str(path),
            f"{nick}'s unit injects an account secret but its account is {state}; "
            "the unit will park on a missing secret or a refused login",
            f"culture agents account activate {nick}  "
            f"# or, to go back to legacy: culture agents install {nick}",
        )
    if wrapped:
        return None
    return Finding(
        5,
        "error",
        nick,
        str(path),
        f"{nick} has an account but its unit lacks the grant inject wrapper "
        f"(`grant run --inject CULTURE_AGENT_SECRET={accounts.grant_entry_name(nick)} --`); "
        "the daemon would start without its secret",
        f"culture agents account activate {nick}  # or: culture agents install {nick}",
    )


def check_client_version(records, version_of=None) -> list[Finding]:
    """Installed cultureagent below its SASL version while accounts exist."""
    if not records:
        return []
    problem = accounts.client_version_problem(version_of)
    if not problem:
        return []
    return [
        Finding(
            5,
            "error",
            "cultureagent",
            "",
            f"{problem}; agents with accounts cannot authenticate",
            f"uv tool install --upgrade 'cultureagent>={accounts.MIN_CULTUREAGENT_SASL}'",
        )
    ]


def check_server_version(records, version_of=None) -> list[Finding]:
    """Installed agentirc below the accounts-capable version while accounts exist."""
    version_of = version_of or accounts.installed_version
    if not records or accounts.version_at_least(
        version_of("agentirc-cli"), accounts.MIN_AGENTIRC_ACCOUNTS
    ):
        return []
    return [
        Finding(
            5,
            "error",
            "agentirc",
            "",
            f"installed agentirc {version_of('agentirc-cli') or '(not installed)'} is below "
            f"{accounts.MIN_AGENTIRC_ACCOUNTS}, the first release that enforces accounts",
            f"uv tool install --upgrade 'agentirc-cli>={accounts.MIN_AGENTIRC_ACCOUNTS}'",
        )
    ]


def check_open_bind(records, host: str, trusted_cidrs: list[str]) -> list[Finding]:
    """Server bound to 0.0.0.0 with accounts but no trusted CIDRs."""
    if not records or host.strip() not in _OPEN_HOSTS or trusted_cidrs:
        return []
    return [
        Finding(
            5,
            "error",
            "server",
            "",
            f"server binds {host.strip() or '0.0.0.0'} and has accounts but "
            "accounts.trusted_cidrs is empty: only loopback peers may use SASL, so remote "
            "agents with accounts cannot authenticate",
            "add the mesh/tailnet range under `accounts: trusted_cidrs:` in server.yaml "
            "(e.g. 100.64.0.0/10), or bind the server to a specific interface",
        )
    ]


def check_missing_accounts(config, records) -> list[Finding]:
    """Registered agents (external included) with no account: informational.

    Accounts are opt-in (culture#506 d7), so this never fails the report.
    """
    have = {r.nick for r in records}
    return [
        Finding(
            5,
            "info",
            nick,
            "",
            f"registered agent {nick} has no account (connects without SASL; accounts "
            "are opt-in)",
            f"culture agents account activate {nick}",
        )
        for nick, _ in _own_nicks(config)
        if nick not in have
    ]


def check_accounts(
    config,
    config_path: str | None = None,
    data_dir: str | Path | None = None,
    unit_dir: Path | None = None,
    version_of=None,
) -> list[Finding]:
    """Run every class-5 check. Tolerates a missing/unreadable accounts file."""
    try:
        records = accounts.load(data_dir)
    except accounts.AccountError as exc:
        return [
            Finding(
                5,
                "error",
                "accounts",
                str(accounts.accounts_path(data_dir)),
                f"accounts file unreadable: {exc}",
                "fix or remove the file; agents without a record connect the legacy way",
            )
        ]
    try:
        cidrs = accounts_policy.load_accounts_block(config_path).trusted_cidrs
    except accounts_policy.AccountsPolicyError as exc:
        return [
            Finding(
                5,
                "error",
                "accounts",
                str(config_path),
                f"invalid accounts: block: {exc}",
                "fix the 'accounts:' block in server.yaml; the server refuses to start with it",
            )
        ]
    findings = check_agent_units(config, records, unit_dir)
    findings += check_client_version(records, version_of)
    findings += check_server_version(records, version_of)
    findings += check_open_bind(records, config.server.host, list(cidrs))
    findings += check_missing_accounts(config, records)
    return findings
