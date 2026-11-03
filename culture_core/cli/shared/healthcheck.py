"""Install and run the console readiness ping without persisting its secret URL."""

from __future__ import annotations

import argparse
import http.client
import os
import subprocess  # nosec B404
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from culture_core.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CultureError

SECRET_NAME = "CULTURE_HEALTHCHECK_URL"
UNIT_NAME = "culture-healthcheck"
CONFIG_FLAG = "--config"
_HEALTHCHECK_LINUX_ERR = "healthcheck timer requires Linux systemd"
_HEALTHCHECK_LINUX_HINT = "run this on a Linux host with systemd user services"

_USER_AGENT = "culture-healthcheck"  # Cloudflare refuses Python's default user agent


def _request_ok(url: str, *, use_proxy: bool = True, attempts: int = 1) -> bool:
    """Return True on an HTTP 2xx. Exceptions can contain the secret URL: never print them."""
    handlers = [] if use_proxy else [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    for _ in range(attempts):
        try:
            with opener.open(request, timeout=15) as response:  # noqa: S310 # nosec B310
                if 200 <= response.status < 300:
                    return True
        except (OSError, ValueError, http.client.HTTPException):
            pass
    return False


def check_readiness(local_address: str, public_address: str) -> int:
    """Probe both endpoints and report failure unless both return HTTP success.

    The grant-injected URL is read only at runtime. Return nonzero for missing
    credentials or a failed reporting request, not for a site that isn't ready.
    """
    report_url = os.environ.get(SECRET_NAME, "").strip()
    if not report_url:
        return 1
    local_ok = _request_ok(local_address.rstrip("/") + "/readyz", use_proxy=False)
    public_ok = _request_ok(public_address.rstrip("/") + "/readyz")
    ready = local_ok and public_ok
    # A /fail that gets through is a success for this job: exit status says only
    # whether healthchecks.io heard. Silence is what its alert catches otherwise.
    suffix = "" if ready else "/fail"
    return 0 if _request_ok(report_url.rstrip("/") + suffix, attempts=3) else 1


def read_addresses(config_path: Path | None = None) -> tuple[str, str]:
    """Read lens's web port and public media base URL (no host-specific default)."""
    if config_path is None:
        from irc_lens.config import default_config_path

        config_path = default_config_path()
    try:
        config = yaml.safe_load(config_path.read_text())
        web = config.get("web", {})
        port = int(web.get("port", 8765))
        bind = str(web.get("bind", "127.0.0.1"))
        address = config.get("media", {}).get("public_base_url", "")
        parsed = urlsplit(address)
        if (
            not 0 < port < 65536 or parsed.scheme not in {"http", "https"} or not parsed.hostname
        ):  # NOSONAR S5332: operator's configured URL
            raise ValueError
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
        raise CultureError(
            EXIT_USER_ERROR,
            "healthcheck needs a lens config with web.port and media.public_base_url",
            "pass --config PATH with a valid public HTTP(S) address",
        ) from None
    host = "[::1]" if bind == "::" else "127.0.0.1"
    if bind not in {"0.0.0.0", "::", "127.0.0.1", "localhost"}:
        host = f"[{bind}]" if ":" in bind else bind
    return f"http://{host}:{port}", address  # NOSONAR S5332: local loopback probe


def _systemctl(action: list[str]) -> bool:
    """Run systemctl, true only on a zero exit: persistence._run_cmd ignores exit codes."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", *action],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def install_healthcheck(config_path: str | None, *, allow_dev_interpreter: bool) -> Path:
    """Generate a user oneshot and timer using persistence's unit conventions."""
    from culture_core import persistence

    if persistence.get_platform() != "linux":
        raise CultureError(
            EXIT_ENV_ERROR,
            _HEALTHCHECK_LINUX_ERR,
            _HEALTHCHECK_LINUX_HINT,
        )
    path = Path(config_path).expanduser().resolve() if config_path else None
    read_addresses(path)
    command = [sys.executable, "-m", "culture_core.cli.shared.healthcheck"]
    if path:
        command += [CONFIG_FLAG, str(path)]
    persistence._enforce_durable_interpreter(
        command, allow_dev_interpreter=allow_dev_interpreter, env=dict(os.environ)
    )
    grant = persistence.resolve_grant_bin(allow_dev_interpreter=allow_dev_interpreter)
    command = [grant, "run", "--inject", f"{SECRET_NAME}={SECRET_NAME}", "--", *command]
    if any("\n" in arg or "\r" in arg for arg in command):
        raise CultureError(
            EXIT_USER_ERROR,
            "healthcheck command paths cannot contain newlines",
            "move the config or install to a path without newlines",
        )
    command = [arg.replace("%", "%%").replace("$", "$$") for arg in command]
    unit = persistence._build_systemd_unit(UNIT_NAME, command, "Console readiness health ping")
    unit = unit.replace("Type=simple", "Type=oneshot")
    unit = "\n".join(line for line in unit.splitlines() if not line.startswith("Restart")) + "\n"
    unit_dir = persistence._systemd_user_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / f"{UNIT_NAME}.service").write_text(unit)
    timer = unit_dir / f"{UNIT_NAME}.timer"
    timer.write_text(
        "[Unit]\nDescription=Report console readiness every five minutes\n\n"
        "[Timer]\nOnBootSec=2min\nOnUnitActiveSec=5min\nAccuracySec=30s\n"
        "Persistent=true\n\n[Install]\nWantedBy=timers.target\n"
    )
    for action in (
        ["daemon-reload"],
        ["enable", "--now", timer.name],
        ["start", "--no-block", f"{UNIT_NAME}.service"],
    ):
        if not _systemctl(action):
            raise CultureError(
                EXIT_ENV_ERROR,
                "could not activate the healthcheck service/timer",
                "check 'systemctl --user status culture-healthcheck.timer'",
            )
    return timer


def uninstall_healthcheck() -> None:
    """Stop and remove the timer and service. Safe to run when they aren't installed."""
    from culture_core import persistence

    if persistence.get_platform() != "linux":
        raise CultureError(
            EXIT_ENV_ERROR,
            _HEALTHCHECK_LINUX_ERR,
            _HEALTHCHECK_LINUX_HINT,
        )
    _systemctl(["disable", "--now", f"{UNIT_NAME}.timer"])
    unit_dir = persistence._systemd_user_dir()
    for name in (f"{UNIT_NAME}.timer", f"{UNIT_NAME}.service"):
        (unit_dir / name).unlink(missing_ok=True)
    _systemctl(["daemon-reload"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(CONFIG_FLAG, type=Path)
    args = parser.parse_args()
    try:
        addresses = read_addresses(args.config)
    except CultureError:
        print("Invalid healthcheck lens configuration", file=sys.stderr)
        return 1
    return check_readiness(*addresses)


if __name__ == "__main__":
    sys.exit(main())
