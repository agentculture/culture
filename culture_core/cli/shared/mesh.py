"""Mesh and link configuration helpers for culture CLI."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from culture_core.config import load_config

from .constants import DEFAULT_CONFIG

logger = logging.getLogger("culture")


def link_secret_names(links: list) -> dict[str, str]:
    """Map peer names to grant/env names, rejecting normalization collisions."""
    from culture_core.credentials import link_secret_name

    peers_by_secret: dict[str, str] = {}
    names_by_peer: dict[str, str] = {}
    for link in links:
        secret_name = link_secret_name(link.name)
        previous = peers_by_secret.get(secret_name)
        if previous is not None and previous != link.name:
            from culture_core.cli._errors import EXIT_USER_ERROR, CultureError

            raise CultureError(
                EXIT_USER_ERROR,
                f"link secret name collision: peers '{previous}' and '{link.name}' "
                f"both map to {secret_name}",
                "rename one link in mesh.yaml so its normalized grant name is unique",
            )
        peers_by_secret[secret_name] = link.name
        names_by_peer[link.name] = secret_name
    return names_by_peer


def grant_link_injects(
    mesh, *, allow_dev_interpreter: bool | None = None
) -> tuple[str | None, list[tuple[str, str]]]:
    """Grant binary and inject specs for link secrets present in grant."""
    from culture_core.credentials import grant_secret_names
    from culture_core.persistence import resolve_grant_bin

    names_by_peer = link_secret_names(mesh.server.links)
    if not names_by_peer:
        return None, []
    grant_bin = resolve_grant_bin(allow_dev_interpreter=allow_dev_interpreter)
    try:
        existing = grant_secret_names(grant_bin)
    except RuntimeError as exc:
        from culture_core.cli._errors import EXIT_ENV_ERROR, CultureError

        raise CultureError(
            EXIT_ENV_ERROR,
            f"cannot inspect grant link-secret names: {exc}",
            "run 'grant list --json', fix the reported grant problem, then retry",
        ) from exc
    injects = [
        (secret_name, secret_name)
        for secret_name in names_by_peer.values()
        if secret_name in existing
    ]
    return grant_bin, injects


def parse_link(value: str):
    """Parse a link spec: name:host:port:password[:trust]

    Trust is extracted from the end if it matches a known value.
    This allows passwords containing colons.
    """
    from agentirc.config import LinkConfig

    trust = "full"
    if value.endswith((":full", ":restricted")):
        value, trust = value.rsplit(":", 1)

    parts = value.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"Link must be name:host:port:password[:trust], got: {value}"
        )
    name, host, port_str, password = parts
    try:
        port = int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid port: {port_str}")
    return LinkConfig(name=name, host=host, port=port, password=password, trust=trust)


def resolve_links_from_mesh(mesh_config_path: str) -> list:
    """Load link configs from mesh.yaml, looking up injected/keyring passwords."""
    from agentirc.config import LinkConfig

    from culture_core.credentials import lookup_credential
    from culture_core.mesh_config import load_mesh_config

    mesh = load_mesh_config(mesh_config_path)
    names_by_peer = link_secret_names(mesh.server.links)
    links = []
    for lc in mesh.server.links:
        try:
            password = lookup_credential(lc.name)
        except ValueError as exc:
            # A malformed link name in mesh.yaml must not crash server
            # start — skip the link with a pointer at the bad entry.
            logger.warning("Skipping link '%s': %s. Fix the link name in mesh.yaml.", lc.name, exc)
            continue
        if not password:
            logger.warning(
                "No credential found for peer '%s' in %s or the OS keyring — "
                "link will not be established. Run 'culture mesh secrets-to-grant' "
                "after storing the keyring password.",
                lc.name,
                names_by_peer[lc.name],
            )
            continue
        links.append(
            LinkConfig(
                name=lc.name,
                host=lc.host,
                port=lc.port,
                password=password,
                trust=lc.trust,
            )
        )
    return links


def generate_mesh_from_agents(mesh_config_path: str):
    """Fall back to generating mesh.yaml from agents.yaml when mesh.yaml is missing."""
    from culture_core.mesh_config import from_daemon_config, save_mesh_config

    if not os.path.isfile(DEFAULT_CONFIG):
        print(f"Mesh config not found: {mesh_config_path}", file=sys.stderr)
        print(f"Agent config not found either: {DEFAULT_CONFIG}", file=sys.stderr)
        return None

    daemon_config = load_config(DEFAULT_CONFIG)
    mesh = from_daemon_config(daemon_config)
    save_mesh_config(mesh, mesh_config_path)
    print(f"No mesh.yaml found — generated from {DEFAULT_CONFIG}")
    return mesh


def load_mesh_or_generate(config_path: str):
    """Load mesh.yaml, falling back to generating it from the server manifest.

    This is the one config resolution used by every verb that provisions
    the server's service unit (``culture mesh setup``,
    ``culture server install``/``uninstall``): read *config_path*
    (mesh.yaml); if it doesn't exist, derive one from ``DEFAULT_CONFIG``
    (``~/.culture/server.yaml``) and save it. Returns ``None`` when
    neither file exists — callers raise their own user error.
    """
    from culture_core.mesh_config import load_mesh_config

    try:
        return load_mesh_config(config_path)
    except FileNotFoundError:
        return generate_mesh_from_agents(config_path)


def build_server_start_cmd(
    mesh, culture_cmd: "list[str] | str", mesh_config_path: str
) -> list[str]:
    """Build the server start command with --foreground and --mesh-config.

    *culture_cmd* is either an interpreter prefix list (e.g.
    ``[sys.executable, "-m", "culture_core"]``) or a legacy bare binary
    string (e.g. ``"/usr/bin/culture"``).
    """
    prefix: list[str] = [culture_cmd] if isinstance(culture_cmd, str) else list(culture_cmd)
    return [
        *prefix,
        "server",
        "start",
        "--foreground",
        "--name",
        mesh.server.name,
        "--host",
        mesh.server.host,
        "--port",
        str(mesh.server.port),
        "--mesh-config",
        mesh_config_path,
    ]


SANDBOX_LOOPBACK_HOST = "127.0.0.1"


def build_standalone_server_start_cmd(
    culture_cmd: "list[str] | str",
    *,
    name: str,
    port: int,
    webhook_port: int,
    data_dir: str | None,
    host: str = SANDBOX_LOOPBACK_HOST,
) -> list[str]:
    """Build an isolated, unlinked ``server start`` command (guest sandbox).

    Unlike :func:`build_server_start_cmd` this never emits ``--link`` or
    ``--mesh-config``, so the server can never federate; every listener
    (``--port``, ``--webhook-port``) and the ``--data-dir`` are explicit.
    ``data_dir=None`` emits ``--no-persist`` instead: history and rooms stay
    in memory only (the guest sandbox, so deletion is never undone by a
    shutdown flush).
    """
    prefix: list[str] = [culture_cmd] if isinstance(culture_cmd, str) else list(culture_cmd)
    return [
        *prefix,
        "server",
        "start",
        "--foreground",
        "--name",
        name,
        "--host",
        host,
        "--port",
        str(port),
        "--webhook-port",
        str(webhook_port),
        *(["--no-persist"] if data_dir is None else ["--data-dir", data_dir]),
    ]
