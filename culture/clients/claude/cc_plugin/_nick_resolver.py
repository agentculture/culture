"""Project nick resolution for the CC plugin (Phase 4.2 — AD-2 + AD-7).

Resolves the project-named boss nick the bridge will use as its IRC
identity. Priority order:

    (a) explicit ``CULTURE_BOSS_NICK`` env var (operator override)
    (b) ``<cwd>/culture.yaml`` has a ``nick:`` field (project-pinned)
    (c) cwd's git remote-origin basename (strip ``.git``)
    (d) cwd basename (last-resort project guess)
    (e) legacy fallback ``local-boss`` with a warning logged

The resulting nick is sanitized to ``[A-Za-z0-9_-]`` (lowercased), and
clipped to 14 characters so ``<boss>-<worker-suffix>`` stays well under
IRC's 30-char nick cap. If sanitization leaves the candidate too short
(<3 chars), the resolver drops to (d) and finally (e). This module is
intentionally dependency-free — it runs from a CC hook subprocess where
``uv``-installed packages may or may not be importable.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# IRC-friendly sanitization. Lowercase the result so two CCs that pick
# the same project name don't end up registering different nicks just
# because one of them happened to capitalize.
_VALID_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_LEN = 14
_MIN_LEN = 3
_LEGACY_FALLBACK = "local-boss"

# Default server name used when ``~/.culture/server.yaml`` cannot be
# parsed. Matches ``ServerConfig.name = "culture"`` in
# ``culture/agentirc/config.py`` (Qodo PR #54 #4 — a previous draft of
# this module used ``local``, which is the value the user's local
# deployment happens to have but is NOT the dataclass default).
_DEFAULT_SERVER_NAME = "culture"


def _server_name() -> str:
    """Read the IRC server name from ``~/.culture/server.yaml`` so the
    resolved nick can be prefixed correctly (Rule 428343 —
    ``<server>-<agent>``). Avoids importing PyYAML: the file shape we
    care about starts with ``server:`` then ``  name: <value>`` on the
    next non-empty/non-comment line. Falls back to ``local`` when the
    file is missing or unparseable — this matches the default in
    ``culture/agentirc/config.py``.
    """
    path = os.path.expanduser("~/.culture/server.yaml")
    if not os.path.exists(path):
        return _DEFAULT_SERVER_NAME
    try:
        with open(path, encoding="utf-8") as fh:
            in_server_block = False
            for raw_line in fh:
                line = raw_line.rstrip("\n")
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue
                if not line.startswith(" ") and stripped.endswith(":"):
                    in_server_block = stripped == "server:"
                    continue
                if in_server_block and stripped.startswith("name:"):
                    raw = stripped.split(":", 1)[1]
                    # Qodo PR #54 #2: a trailing ``# comment`` would
                    # otherwise survive into the prefix and produce
                    # ``culture-<repo>#comment``, which the bridge CLI's
                    # nick validator rejects. Strip the comment first,
                    # THEN unwrap quotes.
                    comment_at = raw.find("#")
                    if comment_at >= 0:
                        raw = raw[:comment_at]
                    value = raw.strip().strip("\"'")
                    return value or _DEFAULT_SERVER_NAME
    except OSError:
        pass
    return _DEFAULT_SERVER_NAME


def _qualify(candidate: str) -> str:
    """Return *candidate* in canonical ``<server>-<agent>`` form
    (Rule 428343 / Qodo PR #51 #1).

    If *candidate* already contains a hyphen we keep it as-is (it's
    treated as already-qualified). Otherwise we prefix
    ``<server_name>-`` and re-clip the total to ``_MAX_LEN`` so the
    canonical worker-suffix budget is preserved.
    """
    if "-" in candidate:
        return candidate
    server = _server_name()
    agent_budget = max(_MIN_LEN, _MAX_LEN - len(server) - 1)  # 1 for hyphen
    return f"{server}-{candidate[:agent_budget]}"


def _sanitize(candidate: str) -> str:
    """Return ``candidate`` lowercased, stripped of non-[A-Za-z0-9_-]
    characters, and clipped to ``_MAX_LEN``. Returns empty string when
    nothing survives sanitization."""
    if not candidate:
        return ""
    cleaned = _VALID_CHARS_RE.sub("-", candidate).strip("-").lower()
    if not cleaned:
        return ""
    return cleaned[:_MAX_LEN]


def _is_acceptable(candidate: str) -> bool:
    """A sanitized candidate is acceptable iff it's at least ``_MIN_LEN``
    chars. Shorter values feel like noise (``a``, ``x``) and fall through
    to the next priority tier."""
    return len(candidate) >= _MIN_LEN


def _read_yaml_nick(cwd: str) -> Optional[str]:
    """Read the ``nick:`` field from ``<cwd>/culture.yaml`` without
    importing PyYAML. The CC plugin runs as a fast hook subprocess; we
    keep imports tiny. Only handles the ``nick: foo`` line shape
    (which is what ``culture boss init`` writes); a fancier value
    (``{nick: foo}``) falls through to git/basename."""
    path = os.path.join(cwd, "culture.yaml")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if line.startswith("#") or not line:
                    continue
                if line.startswith("nick:"):
                    value = line.split(":", 1)[1].strip().strip("\"'")
                    return value or None
    except OSError:
        return None
    return None


def _git_remote_basename(cwd: str) -> Optional[str]:
    """Return the basename of ``git config --get remote.origin.url``
    in ``cwd``, with the trailing ``.git`` (if any) stripped. Returns
    ``None`` when git is unavailable or the cwd isn't a repo."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    if not url:
        return None
    # Strip query strings / fragments defensively before basename.
    url = url.split("?", 1)[0].split("#", 1)[0]
    # ``git@github.com:foo/bar.git`` and ``https://github.com/foo/bar.git``
    # both end with the repo basename. ``os.path.basename`` handles ``/``
    # but not ``:`` — split on both.
    last = re.split(r"[/:]", url)[-1]
    if last.endswith(".git"):
        last = last[: -len(".git")]
    return last or None


def resolve_project_nick(cwd: str) -> str:
    """Resolve the boss nick for a CC session whose working directory
    is ``cwd``.

    See module docstring for the priority order. Always returns a
    non-empty, sanitized, IRC-safe nick — falls back to
    ``local-boss`` if every higher-priority option fails to produce
    an acceptable value (and logs a WARNING in that case).
    """
    # Every return passes through ``_qualify`` so the resolved value
    # ends up in canonical ``<server>-<agent>`` shape — without this
    # qualification ``culture bridge start`` rejects the spawn with
    # ``invalid nick — must match <server>-<agent> format`` and
    # SessionStart silently falls into "fake mesh presence". The legacy
    # fallback already includes the prefix, so ``_qualify`` is a no-op
    # on that path. (Qodo PR #51 #1 collision with this resolver.)

    # (a) explicit env override
    env_value = os.environ.get("CULTURE_BOSS_NICK", "").strip()
    if env_value:
        sanitized = _sanitize(env_value)
        if _is_acceptable(sanitized):
            return _qualify(sanitized)

    # (b) culture.yaml nick: field
    yaml_value = _read_yaml_nick(cwd)
    if yaml_value:
        sanitized = _sanitize(yaml_value)
        if _is_acceptable(sanitized):
            return _qualify(sanitized)

    # (c) git remote-origin basename
    git_basename = _git_remote_basename(cwd)
    if git_basename:
        sanitized = _sanitize(git_basename)
        if _is_acceptable(sanitized):
            return _qualify(sanitized)

    # (d) cwd basename
    cwd_basename = os.path.basename(cwd.rstrip("/")) if cwd else ""
    if cwd_basename:
        sanitized = _sanitize(cwd_basename)
        if _is_acceptable(sanitized):
            return _qualify(sanitized)

    # (e) legacy fallback — already qualified, ``_qualify`` is a no-op.
    logger.warning(
        "resolve_project_nick(%r): no env/yaml/git/basename resolved a "
        "valid nick — falling back to legacy %s",
        cwd,
        _LEGACY_FALLBACK,
    )
    return _LEGACY_FALLBACK
