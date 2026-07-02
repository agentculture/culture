"""Culture-core distribution sweep tests for culture#462 merge-back.

These tests keep the retired ``culture-core`` distribution from creeping back
into the packaging metadata.  The original motivation: pin drift between
``culture-core~=0.5.0`` and ``culture-core~=0.17.0`` across the two
distributions forced the 14.0.0 merge-back (#462).  Since then there is no
separate ``culture-core`` dist — the single ``culture`` distribution ships
both packages (``culture`` front-door + ``culture_core`` engine).

## What the guards cover

1. pyproject.toml: no dependency (base or optional) references the
   ``culture-core`` dist.
2. pyproject.toml: [project.scripts] contains BOTH ``culture`` and
   ``culture-core`` keys, both targeting ``culture_core.cli:main``.
3. uv.lock: no [[package]] entry is named ``culture-core``.
"""

import pathlib
import tomllib

import pytest

_REPO_ROOT = pathlib.Path(__file__).parent.parent


def _load_pyproject() -> dict:
    with open(_REPO_ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


def test_no_culture_core_dependency_in_pyproject():
    """No dependency in pyproject.toml may reference the 'culture-core' dist.

    The retired ``culture-core`` distribution must not appear as a dependency
    anywhere — base or optional.  Its presence would re-introduce the pin-drift
    that motivated the #462 merge-back.
    """
    pyproject = _load_pyproject()
    project = pyproject.get("project", {})

    # Check base dependencies.
    deps = project.get("dependencies", [])
    for dep in deps:
        # PEP 508: the name is the first token before any comma/semicolon/version op.
        name = dep.split(",")[0].split(";")[0].strip()
        # Handle extras like ``culture-core[extra]``.
        name_no_extra = name.split("[")[0]
        assert (
            name_no_extra != "culture-core"
        ), f"Base dependency {dep!r} references retired 'culture-core' dist"

    # Check optional dependencies.
    optional_deps = project.get("optional-dependencies", {})
    for group_name, group_deps in optional_deps.items():
        for dep in group_deps:
            name = dep.split(",")[0].split(";")[0].strip()
            name_no_extra = name.split("[")[0]
            assert name_no_extra != "culture-core", (
                f"Optional dependency [{group_name}] {dep!r} references retired "
                "'culture-core' dist"
            )


def test_console_scripts_both_declared():
    """[project.scripts] must contain BOTH 'culture' and 'culture-core', both targeting 'culture_core.cli:main'.

    The compat alias ``culture-core`` is intentional and stays through 14.x.
    Both commands ship from the single ``culture`` distribution since the
    merge-back (#462).
    """
    pyproject = _load_pyproject()
    scripts = pyproject.get("project", {}).get("scripts", {})

    assert "culture" in scripts, "'culture' must be in [project.scripts]"
    assert "culture-core" in scripts, "'culture-core' must be in [project.scripts]"

    assert (
        scripts["culture"] == "culture_core.cli:main"
    ), f"'culture' script must target 'culture_core.cli:main', got {scripts['culture']!r}"
    assert scripts["culture-core"] == "culture_core.cli:main", (
        f"'culture-core' script must target 'culture_core.cli:main', "
        f"got {scripts['culture-core']!r}"
    )


def test_no_culture_core_package_in_uv_lock():
    """No [[package]] entry in uv.lock may be named 'culture-core'.

    The retired ``culture-core`` distribution must not appear in the lock file.
    Its presence would indicate the packaging toolchain is still resolving
    the standalone dist, re-introducing the pin-drift that motivated the
    #462 merge-back.
    """
    lock_path = _REPO_ROOT / "uv.lock"
    if not lock_path.exists():
        pytest.skip("uv.lock not found")

    with open(lock_path, "rb") as fh:
        lock_data = tomllib.load(fh)

    packages = lock_data.get("package", [])
    for pkg in packages:
        name = pkg.get("name", "")
        assert name != "culture-core", (
            f"uv.lock contains [[package]] named 'culture-core': {pkg.get('version', '?')!r} — "
            "the retired dist must not appear in the lock file"
        )
