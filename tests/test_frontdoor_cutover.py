"""Pin the culture.* → culture_core.* alias seam (issues #454, #462).

The ``culture_core`` engine ships in-tree again since the 14.0.0 merge-back
(culture#462), but the import seam introduced by the #454 cutover is unchanged:
``culture/__init__.py`` installs a meta-path finder that aliases every
``culture.<x>`` import to the identical ``culture_core.<x>`` module object
(MODULE IDENTITY), and the ``culture`` console command resolves to
``culture_core.cli:main``.

These tests guard the seam invariants: the entry point target, the
single-distribution packaging contract (no external culture-core dist), and
module-identity aliasing (the property that keeps the engine suite's
``mock.patch("culture....")`` targets resolving against the live engine).
They run against the real in-tree engine — the behavior-free fake
``culture_core`` harness from the split era is gone, because its purpose
(decoupling culture CI from an externally pinned engine dist) no longer exists.
"""

from importlib import metadata
from unittest import mock

import pytest

import culture
import culture_core


def test_culture_console_entry_point_targets_engine():
    """The `culture` command repoints to culture_core.cli:main (dual entry)."""
    scripts = metadata.entry_points(group="console_scripts")
    culture_ep = {ep.name: ep.value for ep in scripts}.get("culture")
    assert culture_ep == "culture_core.cli:main"


def test_engine_ships_in_tree_not_as_dependency():
    """The merged packaging contract (culture#462): one distribution, both packages.

    culture must NOT declare the retired ``culture-core`` distribution as a
    dependency — the engine ships inside the ``culture`` wheel itself. And the
    imported ``culture_core`` must be the sibling package of the ``culture``
    front-door (same install root), not a module resolved from a separately
    installed engine dist.
    """
    requires = metadata.requires("culture") or []
    stale = [req for req in requires if req.split()[0].lower().startswith("culture-core")]
    assert not stale, f"culture must not depend on the retired culture-core dist: {stale}"

    import pathlib

    culture_root = pathlib.Path(culture.__file__).resolve().parent.parent
    engine_root = pathlib.Path(culture_core.__file__).resolve().parent.parent
    assert engine_root == culture_root, (
        "culture_core must ship alongside the culture front-door package "
        f"(one distribution): {engine_root} != {culture_root}"
    )


def test_import_culture_succeeds_and_installs_alias_finder():
    import importlib
    import sys

    assert culture.__version__  # front-door distribution version, not the engine's
    # The cutover installs exactly one alias finder ahead of the default ones.
    finder_names = [type(f).__name__ for f in sys.meta_path]
    assert finder_names.count("_CultureCoreAliasFinder") == 1
    # A culture.* import resolves through it and lands in sys.modules.
    importlib.import_module("culture.cli")
    assert "culture.cli" in sys.modules


def test_submodule_identity_aliasing():
    """culture.<x> IS culture_core.<x> — same object, not a re-export copy."""
    import culture.cli
    import culture.clients.claude
    import culture.persistence
    import culture.protocol
    import culture.telemetry

    assert culture.cli is culture_core.cli
    assert culture.persistence is culture_core.persistence
    assert culture.protocol is culture_core.protocol
    assert culture.telemetry is culture_core.telemetry
    assert culture.clients.claude is culture_core.clients.claude


def test_deeply_nested_module_identity():
    """The finder handles arbitrary depth: a module inside a subpackage aliases too."""
    import culture.clients.claude.config

    assert culture.clients.claude.config is culture_core.clients.claude.config


def test_alias_preserves_engine_module_spec_and_resources():
    """Importing culture.<x> must NOT corrupt the shared engine module's __spec__.

    Because culture.x IS culture_core.x, the import machinery's _init_module_attrs
    would otherwise overwrite culture_core.x.__spec__ with the alias spec (name
    'culture.x', no submodule_search_locations), which breaks importlib.resources
    on engine packages. The finder restores the canonical spec in exec_module.
    """
    import importlib

    importlib.import_module("culture.skills")  # alias the engine package

    spec = culture_core.skills.__spec__
    assert spec.name == "culture_core.skills"
    assert spec.submodule_search_locations is not None


def test_bare_import_culture_is_the_real_front_door():
    """`import culture` returns the real front-door module (it has no dot, so the
    finder ignores it) — not an alias of culture_core."""
    assert culture is not culture_core
    assert culture.__name__ == "culture"
    # The front-door file lives in the culture package, not the engine install.
    assert "culture_core" not in (culture.__file__ or "")


def test_missing_submodule_raises_module_not_found():
    """A name absent from culture_core raises ModuleNotFoundError — the finder
    does not silently swallow it (absence parity with the engine)."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("culture.this_module_does_not_exist")


def test_doctor_resolves_through_the_alias():
    """The doctor feature (culture#453, forwarded in culture-core 0.5.0) is reachable."""
    import culture.cli.doctor
    import culture.doctor

    assert culture.doctor is culture_core.doctor
    assert culture.cli.doctor is culture_core.cli.doctor


def test_mock_patch_culture_namespace_hits_live_engine():
    """mock.patch("culture.x.y") patches the attribute on the live engine module.

    Patching via the culture.* name must replace the attribute on the SAME module
    object culture_core code reads from — that identity is what keeps the retained
    suite's mock.patch("culture....") targets effective against the engine.
    """
    with mock.patch("culture.pidfile.read_pid", return_value="PATCHED") as patched:
        assert culture_core.pidfile.read_pid is patched


def test_python_dash_m_culture_entry_module():
    """`python -m culture` runs the real culture/__main__.py, which delegates to
    the engine CLI. It is a real front-door file (the alias finder defers
    `culture.__main__`), so importing it exposes the same `main` as the engine."""
    import importlib

    main_mod = importlib.import_module("culture.__main__")
    assert main_mod.main is culture_core.cli.main
