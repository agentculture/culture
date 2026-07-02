"""Packaging and scope-guard validation for the culture console commands.

This module contains fast, deterministic guards that verify the packaging
contract of the merged tree (culture#462): the ``culture`` distribution ships
BOTH packages (``culture`` front-door + ``culture_core`` engine) and declares
two console scripts — ``culture`` and its compatibility alias ``culture-core``
— both resolving to the same engine entry point. There is no separate
``culture-core`` distribution anymore, so the historical install-order
entry-point collision between the two dists cannot recur.

## What the fast guards cover

1. ``pyproject.toml`` declares exactly the ``culture`` and ``culture-core``
   scripts, both targeting ``culture_core.cli:main``.
2. The ``culture_core`` import namespace is intact.
3. Telemetry identity strings stay as ``culture.*`` wire strings — they are
   NOT swept to ``culture_core.*``.
4. The config filename constant ``CULTURE_YAML`` remains ``"culture.yaml"``.

## Optional wheel-inspection test

An optional test (``test_wheel_entry_points``) is included below.  It is
skipped automatically when ``uv`` is not on PATH, keeping the default suite
fast.  Run it manually or in a packaging-focused CI stage with::

    pytest tests/test_packaging_entrypoints.py -v -k wheel
"""

import importlib
import pathlib
import shutil
import subprocess
import sys
import tomllib
import zipfile

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).parent.parent


def _load_pyproject() -> dict:
    with open(_REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------------------
# Guard 1: the culture + culture-core console scripts are declared
# ---------------------------------------------------------------------------


def test_console_scripts_declared():
    """pyproject.toml [project.scripts] must be exactly {'culture', 'culture-core'}.

    Since the merge-back (culture#462) the single ``culture`` distribution owns
    both command names: ``culture`` is the primary CLI and ``culture-core`` is
    kept as a compatibility alias for standalone-era installs and generated
    service units.  Both must resolve to the same engine entry point — the CLI
    derives its program name from ``argv[0]``, so help/usage text follows
    whichever command was invoked.
    """
    pyproject = _load_pyproject()
    scripts: dict = pyproject.get("project", {}).get("scripts", {})

    assert set(scripts.keys()) == {"culture", "culture-core"}, (
        f"Expected exactly {{'culture', 'culture-core'}} in [project.scripts], "
        f"got: {set(scripts.keys())}"
    )

    for name in ("culture", "culture-core"):
        assert (
            scripts[name] == "culture_core.cli:main"
        ), f"{name} script must point to 'culture_core.cli:main', got: {scripts[name]!r}"


# ---------------------------------------------------------------------------
# Guard 2: import namespace is intact
# ---------------------------------------------------------------------------


def test_console_script_target_resolves():
    """The ``culture-core`` console-script target ``culture_core.cli:main`` must resolve.

    Guards the import-namespace contract non-vacuously: if the package were renamed
    or ``main`` moved, ``pyproject.toml``'s ``culture-core = "culture_core.cli:main"``
    entry point would break at install. (``module.__name__`` is a tautology and can
    never catch this — a rename makes ``import culture_core`` raise ImportError.)
    """
    import importlib

    cli = importlib.import_module("culture_core.cli")
    assert callable(getattr(cli, "main", None)), "culture_core.cli:main must be callable"


# ---------------------------------------------------------------------------
# Guard 3: telemetry identity strings preserved as ``culture.*`` wire strings
# ---------------------------------------------------------------------------


def test_tracer_name_is_culture_wire_string():
    """tracing._CULTURE_TRACER_NAME must be the 'culture.agentirc' wire string."""
    from culture_core.telemetry import tracing

    assert tracing._CULTURE_TRACER_NAME == "culture.agentirc", (
        f"Telemetry tracer name must remain 'culture.agentirc' (wire string), "
        f"got {tracing._CULTURE_TRACER_NAME!r}"
    )


def test_meter_name_is_culture_wire_string():
    """metrics._CULTURE_METER_NAME must be the 'culture.agentirc' wire string."""
    from culture_core.telemetry import metrics

    assert metrics._CULTURE_METER_NAME == "culture.agentirc", (
        f"Telemetry meter name must remain 'culture.agentirc' (wire string), "
        f"got {metrics._CULTURE_METER_NAME!r}"
    )


def test_metric_name_bytes_sent_preserved():
    """The 'culture.irc.bytes_sent' metric name must appear in metrics source."""
    metrics_src = (_REPO_ROOT / "culture_core" / "telemetry" / "metrics.py").read_text()
    assert "culture.irc.bytes_sent" in metrics_src, (
        "The metric name 'culture.irc.bytes_sent' must remain in "
        "culture_core/telemetry/metrics.py — it is a wire/identity string."
    )


def test_no_culture_core_prefixed_telemetry_name():
    """No telemetry constant may use a 'culture_core.*' name — they are wire strings."""
    from culture_core.telemetry import metrics, tracing

    for obj, attr in [
        (tracing, "_CULTURE_TRACER_NAME"),
        (metrics, "_CULTURE_METER_NAME"),
    ]:
        value = getattr(obj, attr)
        assert not value.startswith("culture_core."), (
            f"{obj.__name__}.{attr} must NOT start with 'culture_core.' "
            f"(it is a telemetry wire string): got {value!r}"
        )


# ---------------------------------------------------------------------------
# Guard 4: config filename constant preserved
# ---------------------------------------------------------------------------


def test_culture_yaml_constant_preserved():
    """culture_core.config.CULTURE_YAML must equal 'culture.yaml'."""
    from culture_core.config import CULTURE_YAML

    assert CULTURE_YAML == "culture.yaml", (
        f"CULTURE_YAML must remain 'culture.yaml' (wire/filesystem string), "
        f"got {CULTURE_YAML!r}"
    )


# ---------------------------------------------------------------------------
# Optional: wheel entry_points inspection (skipped when uv is unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_wheel_entry_points():
    """Build the wheel and verify entry_points.txt lists both console scripts.

    This test is intentionally slow (~10 s for a build) and is skipped in the
    default suite when ``uv`` is unavailable.  Run it explicitly::

        pytest tests/test_packaging_entrypoints.py -v -k wheel

    The guard is a belt-and-suspenders check on top of the pyproject parse in
    ``test_console_scripts_declared`` — it exercises the actual build
    toolchain (hatchling) rather than just the TOML source.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as dist_dir:
        result = subprocess.run(
            ["uv", "build", "--out-dir", dist_dir],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"uv build failed:\n{result.stdout}\n{result.stderr}"

        wheels = list(pathlib.Path(dist_dir).glob("*.whl"))
        assert wheels, f"No .whl found in {dist_dir}"

        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as zf:
            ep_entries = [n for n in zf.namelist() if "entry_points.txt" in n]
            assert ep_entries, f"No entry_points.txt in wheel {wheel.name}"
            ep_text = zf.read(ep_entries[0]).decode()

        assert (
            "[console_scripts]" in ep_text
        ), f"[console_scripts] section missing from entry_points.txt:\n{ep_text}"
        # Both commands ship from this one wheel, pointing at the same target.
        for script in ("culture", "culture-core"):
            assert (
                f"{script} = culture_core.cli:main" in ep_text
            ), f"'{script} = culture_core.cli:main' not in entry_points.txt:\n{ep_text}"
