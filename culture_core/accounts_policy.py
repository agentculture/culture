"""The server.yaml ``accounts:`` block (culture#506): one reader for every consumer.

The block carries the AgentIRC server's SASL policy for agent accounts
(``trusted_cidrs``, ``trust_loopback``, ``guest_channel_prefixes``,
``auth_failure_limit``, ``auth_failure_window``). Three culture surfaces need
the same view of it:

- ``culture server start`` hands it to the IRCd (``ServerConfig(accounts=)``);
- ``culture agents account ...`` validates the accounts file it writes
  against the same ``guest_channel_prefixes`` the server enforces;
- ``culture doctor`` checks ``trusted_cidrs`` against the bind address.

When the installed agentirc ships ``build_accounts_config`` (9.14.0+) the
block is validated by agentirc's own builder, so culture never accepts a
block the server would refuse (or the reverse). On an older agentirc the
block is only shape-checked; it has no server-side effect there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# First agentirc-cli release with agent accounts (``agentirc.accounts``,
# ``build_accounts_config``, SASL AUTHENTICATE).
MIN_AGENTIRC_ACCOUNTS = "9.14.0"
ACCOUNTS_KEYS = (
    "trusted_cidrs",
    "trust_loopback",
    "guest_channel_prefixes",
    "auth_failure_limit",
    "auth_failure_window",
)


class AccountsPolicyError(ValueError):
    """The ``accounts:`` block (or the file holding it) is invalid."""


@dataclass(frozen=True)
class AccountsBlock:
    """The ``accounts:`` mapping of one server.yaml, plus whose server it is."""

    path: str
    server_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def present(self) -> bool:
        return bool(self.raw)

    @property
    def guest_channel_prefixes(self) -> tuple[str, ...]:
        return tuple(self.raw.get("guest_channel_prefixes") or ())

    @property
    def trusted_cidrs(self) -> list[str]:
        return [str(c) for c in (self.raw.get("trusted_cidrs") or [])]


def agentirc_builder():
    """agentirc's ``build_accounts_config``, or None on an agentirc without accounts."""
    try:
        from agentirc.config import build_accounts_config
    except ImportError:
        return None
    return build_accounts_config


def _shape_check(block: dict[str, Any]) -> None:
    """Minimal checks for an agentirc that cannot validate the block itself."""
    prefixes = block.get("guest_channel_prefixes", [])
    if not isinstance(prefixes, list) or not all(
        isinstance(p, str) and p.startswith("#") for p in prefixes
    ):
        raise AccountsPolicyError(
            "accounts.guest_channel_prefixes must be a list of strings starting with '#', "
            f"got {prefixes!r}"
        )
    cidrs = block.get("trusted_cidrs", [])
    if not isinstance(cidrs, list):
        raise AccountsPolicyError(f"accounts.trusted_cidrs must be a list, got {cidrs!r}")


def validate_block(block: object) -> dict[str, Any]:
    """Return *block* as a mapping, refusing anything agentirc would refuse."""
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise AccountsPolicyError(f"accounts: must be a mapping, got {block!r}")
    builder = agentirc_builder()
    if builder is None:
        _shape_check(block)
        return block
    try:
        builder(block)
    except (TypeError, ValueError) as exc:
        raise AccountsPolicyError(str(exc)) from exc
    return block


def load_accounts_block(config_path: str | Path | None) -> AccountsBlock:
    """Read and validate the ``accounts:`` block of *config_path*.

    A missing file (or no ``accounts:`` key) is an empty block; unreadable
    YAML or an invalid block raises :class:`AccountsPolicyError`.
    """
    path = Path(str(config_path)).expanduser() if config_path else None
    if path is None or not path.is_file():
        return AccountsBlock(path=str(path or ""))
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise AccountsPolicyError(f"{path}: unreadable server config: {exc}") from exc
    if not isinstance(raw, dict):
        return AccountsBlock(path=str(path))
    server = raw.get("server") if isinstance(raw.get("server"), dict) else {}
    try:
        block = validate_block(raw.get("accounts"))
    except AccountsPolicyError as exc:
        raise AccountsPolicyError(f"{path}: {exc}") from exc
    return AccountsBlock(path=str(path), server_name=server.get("name"), raw=block)


def server_accounts_config(block: AccountsBlock):
    """agentirc ``AccountsConfig`` for *block*, built by agentirc's own builder.

    None when the block is empty or the installed agentirc predates agent
    accounts (callers keep agentirc's defaults then).
    """
    builder = agentirc_builder()
    if builder is None or not block.present:
        return None
    try:
        return builder(block.raw)
    except (TypeError, ValueError) as exc:
        raise AccountsPolicyError(f"{block.path}: {exc}") from exc


__all__ = [
    "ACCOUNTS_KEYS",
    "AccountsBlock",
    "AccountsPolicyError",
    "MIN_AGENTIRC_ACCOUNTS",
    "agentirc_builder",
    "load_accounts_block",
    "server_accounts_config",
    "validate_block",
]
