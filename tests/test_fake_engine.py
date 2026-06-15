"""Contract tests for the fake ``culture_core`` engine builder (TDD-first).

These tests pin the behavior of ``tests/_fake_engine.build_fake_culture_core``,
the behavior-free stand-in the isolated front-door suite imports against instead
of the real installed engine. The builder MUST NOT import the real
``culture_core`` — it constructs every module from scratch — so the surviving
front-door tests exercise only the alias finder, never engine behavior.

The whole front-door suite runs against this fake: ``tests/conftest.py`` seeds
it into ``sys.modules`` once per (xdist) worker, so the real engine is never
imported during the run. Each contract test below builds its OWN fresh fake and
seeds/restores it in a ``finally`` block (via the ``seeded_fake`` fixture) —
restoring the prior state relative to that conftest-seeded baseline, so a
contract test never disturbs the session-wide fake the rest of the suite uses.
"""

import importlib
import sys
import types

import pytest

from tests._fake_engine import build_fake_culture_core

# The exact touch-surface the surviving front-door tests reference.
EXPECTED_NAMES = [
    "culture_core",
    "culture_core.cli",
    "culture_core.cli.doctor",
    "culture_core.clients",
    "culture_core.clients.claude",
    "culture_core.clients.claude.config",
    "culture_core.persistence",
    "culture_core.protocol",
    "culture_core.telemetry",
    "culture_core.doctor",
    "culture_core.pidfile",
    "culture_core.skills",
]


@pytest.fixture
def seeded_fake():
    """Seed the fake into sys.modules; restore prior state afterwards.

    Saves and removes every EXPECTED_NAME entry first so the fake is the only
    thing resolving during the test, then in finally deletes the fakes and
    reinstates whatever was there before (the real engine, typically).
    """
    fake = build_fake_culture_core()
    saved = {name: sys.modules.get(name) for name in EXPECTED_NAMES}
    try:
        for name in EXPECTED_NAMES:
            sys.modules.pop(name, None)
        for name, module in fake.items():
            sys.modules[name] = module
        yield fake
    finally:
        for name in EXPECTED_NAMES:
            sys.modules.pop(name, None)
        for name, prior in saved.items():
            if prior is not None:
                sys.modules[name] = prior


def test_builder_does_not_pollute_sys_modules_by_itself():
    """Calling the builder is pure construction — it touches nothing global."""
    before = {name: sys.modules.get(name) for name in EXPECTED_NAMES}
    fake = build_fake_culture_core()
    after = {name: sys.modules.get(name) for name in EXPECTED_NAMES}
    assert before == after
    # And it returned brand-new objects, not whatever was already in sys.modules.
    for name in EXPECTED_NAMES:
        assert fake[name] is not before[name]


def test_returns_every_expected_name_as_a_module():
    fake = build_fake_culture_core()
    assert set(fake) == set(EXPECTED_NAMES)
    for name in EXPECTED_NAMES:
        mod = fake[name]
        assert isinstance(mod, types.ModuleType)
        assert mod.__name__ == name
        assert mod.__spec__ is not None
        assert mod.__spec__.name == name


def test_modules_are_not_the_real_installed_engine():
    """Acceptance #1: none of the returned modules is the real engine."""
    fake = build_fake_culture_core()
    # The real installed culture_core has a real __file__ in site-packages;
    # the fake's is None (freshly built, never imported from disk).
    assert fake["culture_core"].__file__ is None
    for name in EXPECTED_NAMES:
        mod = fake[name]
        assert getattr(mod, "__file__", None) is None
        # No module should resolve back into the installed package tree.
        spec_origin = mod.__spec__.origin
        assert spec_origin is None or "site-packages" not in str(spec_origin)


def test_package_search_locations_set_on_packages():
    """Packages carry submodule_search_locations + __path__; modules do not."""
    fake = build_fake_culture_core()
    packages = [
        "culture_core",
        "culture_core.cli",  # has the doctor submodule -> behaves as a package
        "culture_core.clients",
        "culture_core.clients.claude",
        "culture_core.skills",
    ]
    for name in packages:
        mod = fake[name]
        assert mod.__spec__.submodule_search_locations is not None, name
        assert hasattr(mod, "__path__"), name


def test_skills_is_a_package_with_search_locations(seeded_fake):
    """Acceptance #4: culture_core.skills.__spec__.submodule_search_locations is not None."""
    skills = importlib.import_module("culture_core.skills")
    assert skills.__spec__.submodule_search_locations is not None
    # No bundled data shipped with the fake.
    assert getattr(skills, "__file__", None) is None


def test_unseeded_submodule_raises_module_not_found(seeded_fake):
    """Acceptance #2a: an unseeded submodule raises ModuleNotFoundError.

    culture_core.__path__ is empty, so the import machinery finds no source for
    a name that was never seeded into sys.modules.
    """
    assert sys.modules["culture_core"].__path__ == []
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("culture_core.nope")


def test_seeded_names_resolve_to_the_fake(seeded_fake):
    """Acceptance #2b: every seeded name resolves to the fake object."""
    for name in EXPECTED_NAMES:
        resolved = importlib.import_module(name)
        assert resolved is seeded_fake[name]


def test_callables_and_dotted_access(seeded_fake):
    """Acceptance #3: main/read_pid callable; doctor and config dotted-accessible."""
    cli = importlib.import_module("culture_core.cli")
    pidfile = importlib.import_module("culture_core.pidfile")
    assert callable(cli.main)
    assert callable(pidfile.read_pid)
    # No-ops: invoking them must not blow up.
    assert cli.main() is None
    assert pidfile.read_pid() is None

    # Attribute access for the dotted children used by the front-door tests.
    assert cli.doctor is seeded_fake["culture_core.cli.doctor"]
    clients = importlib.import_module("culture_core.clients")
    claude = clients.claude
    assert claude is seeded_fake["culture_core.clients.claude"]
    assert claude.config is seeded_fake["culture_core.clients.claude.config"]

    # And dotted imports resolve too (the import-system path, not just attrs).
    config_mod = importlib.import_module("culture_core.clients.claude.config")
    assert config_mod is seeded_fake["culture_core.clients.claude.config"]
    doctor_mod = importlib.import_module("culture_core.cli.doctor")
    assert doctor_mod is seeded_fake["culture_core.cli.doctor"]


def test_top_level_exposes_child_attributes(seeded_fake):
    """Parent modules expose their children as attributes (for dotted access)."""
    core = sys.modules["culture_core"]
    assert core.cli is seeded_fake["culture_core.cli"]
    assert core.clients is seeded_fake["culture_core.clients"]
    assert core.persistence is seeded_fake["culture_core.persistence"]
    assert core.protocol is seeded_fake["culture_core.protocol"]
    assert core.telemetry is seeded_fake["culture_core.telemetry"]
    assert core.doctor is seeded_fake["culture_core.doctor"]
    assert core.pidfile is seeded_fake["culture_core.pidfile"]
    assert core.skills is seeded_fake["culture_core.skills"]


def test_fixture_restores_sys_modules_after_use():
    """The seeded_fake fixture must leave sys.modules as it found it.

    Run the seeding/unseeding inline (mirroring the fixture) and assert the
    pre/post snapshots match, proving the suite is never permanently polluted.
    """
    before = {name: sys.modules.get(name) for name in EXPECTED_NAMES}
    fake = build_fake_culture_core()
    saved = {name: sys.modules.get(name) for name in EXPECTED_NAMES}
    try:
        for name in EXPECTED_NAMES:
            sys.modules.pop(name, None)
        for name, module in fake.items():
            sys.modules[name] = module
    finally:
        for name in EXPECTED_NAMES:
            sys.modules.pop(name, None)
        for name, prior in saved.items():
            if prior is not None:
                sys.modules[name] = prior
    after = {name: sys.modules.get(name) for name in EXPECTED_NAMES}
    assert before == after
