"""All-backends citation parity tests (Plan 5 Task 8).

These tests lock down the citation invariant between the reference harness at
``packages/agent-harness/`` and each of the four cited copies in
``culture/clients/{claude,codex,copilot,acp}/``.

A test failure means a backend has drifted from the reference — either someone
edited a cited file beyond the two documented edit sites, or forgot to propagate
a reference update to all four backends.

No backend modules are imported — all inspection is done via AST (Python files)
or ``yaml.safe_load`` (YAML files) so that backend-specific SDK requirements
(claude-agent-sdk, codex-agent-sdk, copilot SDK) cannot break the test runner.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Path helpers — resolved relative to this file so xdist workers all agree.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_HARNESS_REF = _REPO_ROOT / "packages" / "agent-harness"
_CLIENTS = _REPO_ROOT / "culture" / "clients"

BACKENDS = ["claude", "codex", "copilot", "acp"]

# ---------------------------------------------------------------------------
# Normalization helpers for test 1
# ---------------------------------------------------------------------------

_TRACER_NAME_LINE_RE = re.compile(r'^_HARNESS_TRACER_NAME\s*=\s*"culture\.harness(\.\w+)?"')
_CITATION_LINE_RE = re.compile(r"^This is the \w+ citation of the reference module in$")
_CITATION_PATH_LINE_RE = re.compile(r"^``packages/agent-harness/telemetry\.py``\.$")


def _normalize_telemetry(text: str) -> list[str]:
    """Return the lines of a telemetry.py source after stripping backend-specific lines.

    Stripped lines:
    1. The ``_HARNESS_TRACER_NAME = "culture.harness..."`` assignment line.
    2. The per-backend citation sentence: "This is the X citation of the
       reference module in" (only present in cited copies).
    3. The path line that follows it: "``packages/agent-harness/telemetry.py``."
    4. A blank line that immediately follows lines 2+3 (the blank that separates
       the citation note from the "Backend-specific edit sites" heading).

    After these four categories of lines are removed, both the reference and any
    citation should produce an identical line list.
    """
    lines = text.splitlines()
    result: list[str] = []
    skip_next_blank = False

    for line in lines:
        stripped = line.strip()

        # Strip the tracer-name assignment (both reference and citations have it,
        # but with different values).
        if _TRACER_NAME_LINE_RE.match(stripped):
            continue

        # Strip citation-identity lines present only in cited copies.
        if _CITATION_LINE_RE.match(stripped):
            skip_next_blank = True
            continue
        if _CITATION_PATH_LINE_RE.match(stripped):
            continue

        # Strip the blank line that follows the citation block inside the docstring.
        if skip_next_blank and stripped == "":
            skip_next_blank = False
            continue
        skip_next_blank = False

        result.append(line)

    return result


# ---------------------------------------------------------------------------
# Test 1: telemetry.py byte-parity (after normalization)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_telemetry_py_byte_parity(backend: str) -> None:
    """After stripping the documented edit sites, each citation must equal the reference.

    This test enforces the citation-parity invariant described in Plan 5 §8 and
    the CLAUDE.md all-backends rule.  Any addition to a backend's telemetry.py
    beyond the two documented edit sites (``_HARNESS_TRACER_NAME`` and the
    citation-identifier docstring lines) will cause this test to fail.
    """
    ref_path = _HARNESS_REF / "telemetry.py"
    backend_path = _CLIENTS / backend / "telemetry.py"

    assert ref_path.exists(), f"Reference missing: {ref_path}"
    assert backend_path.exists(), f"Citation missing for {backend}: {backend_path}"

    ref_lines = _normalize_telemetry(ref_path.read_text(encoding="utf-8"))
    backend_lines = _normalize_telemetry(backend_path.read_text(encoding="utf-8"))

    if ref_lines != backend_lines:
        # Build a concise diff to pin down the exact divergence in the failure message.
        diff_lines: list[str] = []
        max_lines = max(len(ref_lines), len(backend_lines))
        for i in range(max_lines):
            ref_line = ref_lines[i] if i < len(ref_lines) else "<missing>"
            be_line = backend_lines[i] if i < len(backend_lines) else "<missing>"
            if ref_line != be_line:
                diff_lines.append(f"  line {i + 1}:")
                diff_lines.append(f"    ref:     {ref_line!r}")
                diff_lines.append(f"    {backend}: {be_line!r}")

        divergence = "\n".join(diff_lines)
        pytest.fail(
            f"telemetry.py citation drift detected for backend '{backend}'.\n"
            f"Normalized text differs from the reference after stripping documented edit sites.\n"
            f"Diverging lines:\n{divergence}\n\n"
            f"Fix: update culture/clients/{backend}/telemetry.py to match "
            f"packages/agent-harness/telemetry.py (keeping only the two documented edit sites)."
        )


# ---------------------------------------------------------------------------
# Test 2: config.py defines TelemetryConfig with all 10 expected fields
# ---------------------------------------------------------------------------

_EXPECTED_TELEMETRY_FIELDS = {
    "enabled",
    "service_name",
    "otlp_endpoint",
    "otlp_protocol",
    "otlp_timeout_ms",
    "otlp_compression",
    "traces_enabled",
    "traces_sampler",
    "metrics_enabled",
    "metrics_export_interval_ms",
}


def _ast_class_defaults(tree: ast.Module, class_name: str) -> dict[str, object]:
    """Walk an AST module and return {field_name: default_value} for a dataclass.

    Only handles simple scalar defaults (str, int, bool, None) — which is all
    ``TelemetryConfig`` uses.  Returns an empty dict if the class is not found.
    """
    defaults: dict[str, object] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field = item.target.id
                    if item.value is not None:
                        val = item.value
                        if isinstance(val, ast.Constant):
                            defaults[field] = val.value
                        # Handle negative ints/floats: UnaryOp(USub, Constant)
                        elif (
                            isinstance(val, ast.UnaryOp)
                            and isinstance(val.op, ast.USub)
                            and isinstance(val.operand, ast.Constant)
                        ):
                            defaults[field] = -val.operand.value
                    else:
                        defaults[field] = None
    return defaults


def _ast_daemon_telemetry_field_type(tree: ast.Module) -> str | None:
    """Return the type annotation string for ``DaemonConfig.telemetry``, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DaemonConfig":
            for item in node.body:
                if (
                    isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                    and item.target.id == "telemetry"
                ):
                    # Accept both bare Name ("TelemetryConfig") and subscript
                    ann = item.annotation
                    if isinstance(ann, ast.Name):
                        return ann.id
                    if isinstance(ann, ast.Attribute):
                        return ann.attr
    return None


@pytest.mark.parametrize("backend", BACKENDS)
def test_config_py_has_telemetry_dataclass(backend: str) -> None:
    """config.py must define TelemetryConfig with all 10 expected fields.

    Also checks that:
    - ``service_name`` default is ``"culture.harness.<backend>"``.
    - ``DaemonConfig`` has a ``telemetry`` field whose annotation resolves to
      ``TelemetryConfig``.

    Uses AST to avoid importing backend modules (which require backend-specific SDKs).
    """
    config_path = _CLIENTS / backend / "config.py"
    assert config_path.exists(), f"config.py missing for {backend}: {config_path}"

    tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))

    # Check TelemetryConfig class exists and has all 10 fields.
    telemetry_defaults = _ast_class_defaults(tree, "TelemetryConfig")
    assert telemetry_defaults, (
        f"TelemetryConfig class not found in {config_path}. "
        f"All backends must define TelemetryConfig."
    )

    missing_fields = _EXPECTED_TELEMETRY_FIELDS - telemetry_defaults.keys()
    assert not missing_fields, (
        f"TelemetryConfig in {backend}/config.py is missing fields: {sorted(missing_fields)}. "
        f"Expected all 10 fields: {sorted(_EXPECTED_TELEMETRY_FIELDS)}."
    )

    # Check service_name default matches backend.
    expected_service_name = f"culture.harness.{backend}"
    actual_service_name = telemetry_defaults.get("service_name")
    assert actual_service_name == expected_service_name, (
        f"TelemetryConfig.service_name default mismatch in {backend}/config.py.\n"
        f"  expected: {expected_service_name!r}\n"
        f"  got:      {actual_service_name!r}"
    )

    # Check DaemonConfig.telemetry field type annotation.
    telemetry_type = _ast_daemon_telemetry_field_type(tree)
    assert telemetry_type == "TelemetryConfig", (
        f"DaemonConfig.telemetry field not annotated as TelemetryConfig in {backend}/config.py.\n"
        f"  got annotation: {telemetry_type!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: culture.yaml has a telemetry block with required keys
# ---------------------------------------------------------------------------

_REQUIRED_YAML_TELEMETRY_KEYS = {
    "enabled",
    "service_name",
    "otlp_endpoint",
    "traces_enabled",
    "metrics_enabled",
}


@pytest.mark.parametrize("backend", BACKENDS)
def test_culture_yaml_has_telemetry_block(backend: str) -> None:
    """culture.yaml must have a top-level ``telemetry:`` key with 5 minimal sub-keys.

    Also checks that ``service_name`` matches ``"culture.harness.<backend>"``.
    """
    yaml_path = _CLIENTS / backend / "culture.yaml"
    assert yaml_path.exists(), f"culture.yaml missing for {backend}: {yaml_path}"

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    assert (
        "telemetry" in data
    ), f"culture.yaml for {backend} is missing the top-level 'telemetry:' key."

    telemetry = data["telemetry"]
    assert isinstance(
        telemetry, dict
    ), f"'telemetry' in {backend}/culture.yaml is not a mapping (got {type(telemetry)!r})."

    missing_keys = _REQUIRED_YAML_TELEMETRY_KEYS - telemetry.keys()
    assert not missing_keys, (
        f"culture.yaml for {backend} is missing telemetry sub-keys: {sorted(missing_keys)}. "
        f"Required: {sorted(_REQUIRED_YAML_TELEMETRY_KEYS)}."
    )

    expected_service_name = f"culture.harness.{backend}"
    actual_service_name = telemetry.get("service_name")
    assert actual_service_name == expected_service_name, (
        f"culture.yaml for {backend} has wrong telemetry.service_name.\n"
        f"  expected: {expected_service_name!r}\n"
        f"  got:      {actual_service_name!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: agent_runner.py imports record_llm_call from the backend's telemetry
# ---------------------------------------------------------------------------


def _ast_finds_import_of(tree: ast.Module, name: str, from_module_suffix: str) -> bool:
    """Return True if the AST has a top-level ``from X import ... name ...`` statement.

    ``from_module_suffix`` is matched as a suffix of the module path (e.g.
    ``"telemetry"`` matches ``from culture.clients.claude.telemetry import ...``).
    ``name`` must appear in the imported names.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith(from_module_suffix):
                for alias in node.names:
                    if alias.name == name:
                        return True
    return False


@pytest.mark.parametrize("backend", BACKENDS)
def test_agent_runner_py_imports_record_llm_call(backend: str) -> None:
    """agent_runner.py must import ``record_llm_call`` from the backend's telemetry module.

    This test catches backends where someone reverted to a lazy/local import or
    removed the ``record_llm_call`` import entirely.
    """
    runner_path = _CLIENTS / backend / "agent_runner.py"
    assert runner_path.exists(), f"agent_runner.py missing for {backend}: {runner_path}"

    tree = ast.parse(runner_path.read_text(encoding="utf-8"), filename=str(runner_path))

    expected_module = f"culture.clients.{backend}.telemetry"
    found = _ast_finds_import_of(tree, "record_llm_call", expected_module)
    assert found, (
        f"agent_runner.py for {backend} does not import 'record_llm_call' "
        f"from '{expected_module}'.\n"
        f"Add: from {expected_module} import record_llm_call"
    )


# ---------------------------------------------------------------------------
# Test 5: agent_runner.py imports _HARNESS_TRACER_NAME from the backend's telemetry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_agent_runner_py_imports_harness_tracer_name(backend: str) -> None:
    """agent_runner.py must import ``_HARNESS_TRACER_NAME`` from the backend's telemetry module.

    This ensures the agent runner references the backend-specific tracer name
    constant rather than hard-coding a string literal.
    """
    runner_path = _CLIENTS / backend / "agent_runner.py"
    assert runner_path.exists(), f"agent_runner.py missing for {backend}: {runner_path}"

    tree = ast.parse(runner_path.read_text(encoding="utf-8"), filename=str(runner_path))

    expected_module = f"culture.clients.{backend}.telemetry"
    found = _ast_finds_import_of(tree, "_HARNESS_TRACER_NAME", expected_module)
    assert found, (
        f"agent_runner.py for {backend} does not import '_HARNESS_TRACER_NAME' "
        f"from '{expected_module}'.\n"
        f"Add: from {expected_module} import _HARNESS_TRACER_NAME"
    )


# ---------------------------------------------------------------------------
# Test 6: irc_transport.py IRCTransport.__init__ accepts tracer, metrics, backend
# ---------------------------------------------------------------------------

_REQUIRED_TRANSPORT_PARAMS = {"tracer", "metrics", "backend"}


def _ast_init_params(tree: ast.Module, class_name: str) -> set[str]:
    """Return the set of parameter names for ``class_name.__init__``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    return {arg.arg for arg in item.args.args}
    return set()


@pytest.mark.parametrize("backend", BACKENDS)
def test_irc_transport_py_constructor_accepts_telemetry_kwargs(backend: str) -> None:
    """IRCTransport.__init__ must accept ``tracer``, ``metrics``, and ``backend`` parameters.

    Presence is required; defaults are not checked (any default is acceptable).
    This test fails if someone removes these parameters or forgets to add them
    when creating a new backend.
    """
    transport_path = _CLIENTS / backend / "irc_transport.py"
    assert transport_path.exists(), f"irc_transport.py missing for {backend}: {transport_path}"

    tree = ast.parse(transport_path.read_text(encoding="utf-8"), filename=str(transport_path))

    params = _ast_init_params(tree, "IRCTransport")
    assert params, f"IRCTransport class or __init__ not found in {backend}/irc_transport.py"

    missing_params = _REQUIRED_TRANSPORT_PARAMS - params
    assert not missing_params, (
        f"IRCTransport.__init__ in {backend}/irc_transport.py is missing "
        f"parameter(s): {sorted(missing_params)}.\n"
        f"Required: {sorted(_REQUIRED_TRANSPORT_PARAMS)}.\n"
        f"Found: {sorted(params)}."
    )
