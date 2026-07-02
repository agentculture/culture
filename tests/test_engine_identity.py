"""Engine-identity guard: ensures culture-core has no lingering culture-front migration identity.

This module contains fast, deterministic guards that verify the migration from
the monolithic ``culture`` package to the split ``culture-core`` / ``culture``
packages did not leave behind any references to the old front-package paths
or migration markers.  Together with the packaging entry-point guards in
``test_packaging_entrypoints.py``, these tests make the spec success-signal
enforceable: any accidental reintroduction of old paths or markers will fail
CI immediately.

## What the fast guards cover

1. No front-migration markers (e.g. ``Phase A3``, ``culture#308``) remain.
2. No stale engine-source paths (``culture/clients/``, ``culture/cli/``) remain.
3. The identity boundary is preserved: ``culture.yaml`` and ``culture.*``
   telemetry strings are still present (the sweep did not over-correct).
"""

import pathlib
import re

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROOT = pathlib.Path(__file__).parent.parent

_SKIP_PARTS = frozenset({"__pycache__", ".venv", ".git", "node_modules", ".colleague", ".devague"})
# Files exempt from the scans:
# - this guard file itself: it contains the forbidden patterns as literals;
# - the front-door suite: it deliberately exercises the public ``culture.*``
#   alias namespace (``culture.cli``, ``culture.protocol``, …) that the
#   meta-path finder in ``culture/__init__.py`` maps onto ``culture_core.*``.
#   Those dotted ``culture.<x>`` references are the feature under test there,
#   not stale engine module refs.
# - the slimness suite: it imports the ``culture`` front door under an SDK
#   blocker and asserts ``culture.__version__`` resolves — the alias namespace
#   is again the feature under test.
_SKIP_NAMES = frozenset(
    {"test_engine_identity.py", "test_frontdoor_cutover.py", "test_engine_slimness.py"}
)


def _scan_py_files():
    """Yield (path, text) for every .py file under culture_core/ and tests/.

    Skips directories in ``_SKIP_PARTS`` and the guard file itself.
    """
    for root_dir in (_ROOT / "culture_core", _ROOT / "tests"):
        for py_path in root_dir.rglob("*.py"):
            if _SKIP_PARTS.intersection(py_path.parts):
                continue
            if py_path.name in _SKIP_NAMES:
                continue
            yield py_path, py_path.read_text(encoding="utf-8")


def _find_violations(forbidden):
    """Return a list of (relpath, lineno, marker) for every forbidden hit."""
    violations: list[tuple[str, int, str]] = []
    for py_path, text in _scan_py_files():
        for lineno, line in enumerate(text.splitlines(), 1):
            for marker in forbidden:
                if marker in line:
                    relpath = str(py_path.relative_to(_ROOT))
                    violations.append((relpath, lineno, marker))
    return violations


# ---------------------------------------------------------------------------
# Guard 1: no front-migration markers
# ---------------------------------------------------------------------------


def test_no_front_migration_markers():
    """No file may contain culture-front migration markers.

    These strings were used during the migration (Phase A3, culture#308, etc.)
    and must not remain in the shipped codebase.
    """
    FORBIDDEN = [
        "Phase A3",
        "culture#308",
        "culture/agentirc/ircd",
        "culture/cli/agent.py",
        "removed alongside",
    ]
    violations = _find_violations(FORBIDDEN)
    assert not violations, "Front-migration markers found:\n" + "\n".join(
        f"  {fp}:{ln}: {mk!r}" for fp, ln, mk in violations
    )


# ---------------------------------------------------------------------------
# Guard 2: no stale engine-source paths
# ---------------------------------------------------------------------------


def test_no_stale_engine_source_paths():
    """No file may reference the old engine-source directories.

    The paths ``culture/clients/`` and ``culture/cli/`` were the old monolithic
    package layout.  They moved to ``culture_core/clients/`` and
    ``culture_core/cli/`` respectively.

    Note: protocol-spec-doc pointers like ``culture/protocol/extensions/*.md``
    and runtime paths like ``~/.culture/...`` are intentionally NOT forbidden —
    culture owns that spec, and those are real runtime locations.
    """
    FORBIDDEN = [
        "culture/clients/",
        "culture/cli/",
    ]
    violations = _find_violations(FORBIDDEN)
    assert not violations, "Stale engine-source paths found:\n" + "\n".join(
        f"  {fp}:{ln}: {mk!r}" for fp, ln, mk in violations
    )


# ---------------------------------------------------------------------------
# Guard 3: identity boundary preserved
# ---------------------------------------------------------------------------


def test_identity_boundary_preserved():
    """The sweep must not have renamed/removed culture.yaml or culture.* telemetry
    identity strings.

    Telemetry tracer/meter names are pinned against their **source-of-truth
    constants**, not a tree-wide substring scan. A substring scan is too weak for
    this: the old name lingers in test assertions and docstrings, so it would still
    match (and pass) even if the real definition were renamed. Asserting the actual
    constants means a rename in the engine source fails this guard.
    (Stale dotted ``culture.<module>`` refs are caught by Guard 4 below.)
    """
    from culture_core.telemetry import metrics, tracing

    assert (
        tracing._CULTURE_TRACER_NAME == "culture.agentirc"
    ), "telemetry tracer wire-name must remain 'culture.agentirc'"
    assert (
        metrics._CULTURE_METER_NAME == "culture.agentirc"
    ), "telemetry meter wire-name must remain 'culture.agentirc'"

    # culture.yaml: the config filename must still be referenced by the engine source
    # (scan culture_core/ only; a tests/ reference shouldn't mask a source removal).
    src_text = "".join(text for path, text in _scan_py_files() if "culture_core" in path.parts)
    assert (
        "culture.yaml" in src_text
    ), "The config wire-string 'culture.yaml' must remain in the engine source"


# ---------------------------------------------------------------------------
# Guard 4: no stale dotted culture.<module> refs (issue #7)
# ---------------------------------------------------------------------------

# Dotted ``culture.<token>`` occurrences that are NOT module paths and must be
# preserved byte-for-byte: telemetry metric/span/attribute names, the config
# filename, W3C trace tags, and attribute access on a local var named ``culture``.
# (``com.culture.*`` launchd labels are handled separately below.) Everything else
# dotted off ``culture.`` is a stale engine module ref that must be ``culture_core.``.
_WIRE_ALLOW = (
    "culture.yaml",
    "culture.agentirc",
    "culture.agentirc.",  # service_name variants in tests (culture.agentirc.alpha)
    "culture.dev",  # culture.dev/traceparent, culture.dev/tracestate
    "culture.backend",  # attribute access on a local AgentConfig var, not a module
    "culture.directory",
    # OpenTelemetry metric / span / attribute name families (string literals):
    "culture.trace.",
    "culture.harness.",
    "culture.attention.",
    "culture.federation.",
    "culture.clients.connected",
    "culture.client.",  # singular: session/command durations
    "culture.irc.",
    "culture.s2s.",
    "culture.events.",
    "culture.privmsg.",
    "culture.audit.",
    "culture.bot.",  # singular bot metrics; the plural "bots" form is a module (swept)
    # CLI verb spans (#17). Exact entries, NOT a "culture.cli." family:
    # culture.cli.* is also a real module family, and a prefix here would
    # mask stale module refs like ``culture.cli.agents``.
    "culture.cli",  # the CLI tracer name (exact)
    "culture.cli.agents.start",
    "culture.cli.agents.stop",
    "culture.cli.mode",
    "culture.agent.",  # span attributes: culture.agent.nicks / .backends (#17)
)

_DOTTED_CULTURE = re.compile(r"culture\.[A-Za-z_][\w.]*")


def _is_wire_string(token: str) -> bool:
    """True if ``token`` is an allowlisted wire/identity string, not a module path.

    Entries ending with ``.`` are name-family prefixes; all other entries
    match exactly, so a family entry can't accidentally whitelist a stale
    module ref that shares the prefix (e.g. ``culture.cli.agents``). Exact
    comparison ignores trailing dots the regex swallowed from prose (a
    docstring's sentence-ending ``culture.yaml.``).
    """
    stripped = token.rstrip(".")
    return any(
        token.startswith(allow) if allow.endswith(".") else stripped == allow
        for allow in _WIRE_ALLOW
    )


def test_no_stale_dotted_module_refs():
    """No file may carry a stale dotted ``culture.<module>`` reference.

    After the engine-identity split, module paths are ``culture_core.<module>``;
    only the wire/identity strings in ``_WIRE_ALLOW`` (and ``com.culture.*`` plist
    labels) may dot off the bare ``culture.`` prefix.  This catches docstring /
    comment drift that the slash-path guard (Guard 2) misses.  (issue #7)
    """
    violations: list[tuple[str, int, str]] = []
    for py_path, text in _scan_py_files():
        relpath = str(py_path.relative_to(_ROOT))
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _DOTTED_CULTURE.finditer(line):
                # ``com.culture.*`` launchd labels: the regex matches the
                # ``culture.*`` tail, so skip when immediately preceded by ``com.``.
                if line[max(0, match.start() - 4) : match.start()] == "com.":
                    continue
                token = match.group(0)
                if _is_wire_string(token):
                    continue
                violations.append((relpath, lineno, token))
    assert (
        not violations
    ), "Stale dotted culture.<module> refs found (use culture_core.<module>):\n" + "\n".join(
        f"  {fp}:{ln}: {mk!r}" for fp, ln, mk in violations
    )
