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
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Path helpers — resolved relative to this file so xdist workers all agree.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_HARNESS_REF = _REPO_ROOT / "packages" / "agent-harness"
_CLIENTS = _REPO_ROOT / "culture" / "clients"
_SHARED = _CLIENTS / "shared"

BACKENDS = ["claude", "codex", "copilot", "acp"]


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
    ``"telemetry"`` matches ``from culture.clients.shared.telemetry import ...``).
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
    """agent_runner.py must import ``record_llm_call`` from the shared telemetry module.

    This test catches backends where someone reverted to a lazy/local import or
    removed the ``record_llm_call`` import entirely.

    Telemetry was lifted into ``culture/clients/shared/telemetry.py`` — the
    expected import path is now ``culture.clients.shared.telemetry`` for every
    backend (``agent_runner.py`` itself remains per-backend).
    """
    runner_path = _CLIENTS / backend / "agent_runner.py"
    assert runner_path.exists(), f"agent_runner.py missing for {backend}: {runner_path}"

    tree = ast.parse(runner_path.read_text(encoding="utf-8"), filename=str(runner_path))

    expected_module = "culture.clients.shared.telemetry"
    found = _ast_finds_import_of(tree, "record_llm_call", expected_module)
    assert found, (
        f"agent_runner.py for {backend} does not import 'record_llm_call' "
        f"from '{expected_module}'.\n"
        f"Add: from {expected_module} import record_llm_call"
    )


# ---------------------------------------------------------------------------
# Test 5: agent_runner.py imports _HARNESS_TRACER_NAME from the shared telemetry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_agent_runner_py_imports_harness_tracer_name(backend: str) -> None:
    """agent_runner.py must import ``_HARNESS_TRACER_NAME`` from the shared telemetry module.

    This ensures the agent runner references the shared tracer name constant
    rather than hard-coding a string literal. After the shared-harness move,
    the import path is ``culture.clients.shared.telemetry`` for every backend.
    """
    runner_path = _CLIENTS / backend / "agent_runner.py"
    assert runner_path.exists(), f"agent_runner.py missing for {backend}: {runner_path}"

    tree = ast.parse(runner_path.read_text(encoding="utf-8"), filename=str(runner_path))

    expected_module = "culture.clients.shared.telemetry"
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


def test_irc_transport_py_constructor_accepts_telemetry_kwargs() -> None:
    """IRCTransport.__init__ must accept ``tracer``, ``metrics``, and ``backend`` parameters.

    Presence is required; defaults are not checked (any default is acceptable).
    This test fails if someone removes these parameters from the shared transport.

    irc_transport.py was lifted into ``culture/clients/shared/`` — there is now
    a single source file rather than a per-backend copy, so this test is no
    longer parametrized over backends.
    """
    transport_path = _SHARED / "irc_transport.py"
    assert transport_path.exists(), f"shared irc_transport.py missing: {transport_path}"

    tree = ast.parse(transport_path.read_text(encoding="utf-8"), filename=str(transport_path))

    params = _ast_init_params(tree, "IRCTransport")
    assert params, f"IRCTransport class or __init__ not found in {transport_path}"

    missing_params = _REQUIRED_TRANSPORT_PARAMS - params
    assert not missing_params, (
        f"IRCTransport.__init__ in {transport_path} is missing "
        f"parameter(s): {sorted(missing_params)}.\n"
        f"Required: {sorted(_REQUIRED_TRANSPORT_PARAMS)}.\n"
        f"Found: {sorted(params)}."
    )
