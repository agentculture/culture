"""Build a behavior-free fake ``culture_core`` engine for the front-door suite.

``culture`` is a thin front-door over the published ``culture_core`` engine:
``culture/__init__.py`` installs a meta-path finder that aliases every
``culture.<x>`` import to the identical ``culture_core.<x>`` module object. To
test the front-door (the alias finder, the entry point, module identity) in
isolation, we need a ``culture_core`` that has the right *shape* but NONE of the
real engine's behavior or bundled data.

``build_fake_culture_core`` constructs that shape from scratch — it never
imports the real ``culture_core`` — and returns a name -> module mapping. A
caller (conftest, a fixture) seeds the mapping into ``sys.modules``; the alias
finder then resolves ``culture.<x>`` against these fakes exactly as it would
against the real engine.

Construction rules (mirroring the import system so the finder behaves):

* Each module is a fresh ``types.ModuleType`` with a real
  ``importlib.machinery.ModuleSpec`` (correct ``__name__`` / ``__spec__``).
* ``__file__`` is left ``None`` on every module so callers can prove a returned
  object is the fake, not the real engine imported from site-packages.
* **Packages** carry ``submodule_search_locations`` on their spec and a matching
  ``__path__`` so the import system treats them as packages. The top-level
  ``culture_core`` package uses an **empty** search-locations list (``[]``):
  ``[] is not None`` is True (so it reads as a package), yet it points at no
  filesystem location, so importing an UNSEEDED submodule
  (``culture_core.nope``) raises ``ModuleNotFoundError`` while a submodule that
  was seeded into ``sys.modules`` still resolves by identity.
* Parents expose their children as attributes so dotted access
  (``culture_core.cli.doctor``, ``culture_core.clients.claude.config``) works
  without re-triggering the import machinery.
* ``culture_core.cli.main`` and ``culture_core.pidfile.read_pid`` are no-op
  callables (the only engine entry points the front-door tests poke).
"""

from importlib.machinery import ModuleSpec
from types import ModuleType


def _no_op(*_args, **_kwargs):
    """A behavior-free callable stand-in for an engine entry point."""
    return None


def _make_module(name, *, is_package):
    """Create one fresh, behavior-free module with a correct spec.

    Packages get ``submodule_search_locations`` ([] — empty but not None) and a
    matching ``__path__`` so the import system treats them as packages while
    pointing them at no real filesystem location.
    """
    spec = ModuleSpec(name, loader=None, is_package=is_package)
    module = ModuleType(name)
    module.__name__ = name
    module.__spec__ = spec
    # Explicitly behavior-free / not loaded from disk: lets callers distinguish
    # the fake from the real installed engine (whose __file__ is in site-packages).
    module.__file__ = None
    if is_package:
        # `[] is not None` is True, so the module reads as a package, but it
        # resolves no submodules from disk — unseeded children raise
        # ModuleNotFoundError; seeded children resolve from sys.modules.
        spec.submodule_search_locations = []
        module.__path__ = []
    return module


def build_fake_culture_core():
    """Construct the fake ``culture_core`` engine.

    Returns a ``dict[str, ModuleType]`` mapping each fully-qualified module name
    to its freshly built, behavior-free module. The real ``culture_core`` is
    never imported. The caller is responsible for seeding (and later removing)
    these into ``sys.modules``.
    """
    # (name, is_package) for every node in the fake touch-surface. `cli` is a
    # package because it owns the `doctor` submodule.
    layout = [
        ("culture_core", True),
        ("culture_core.cli", True),
        ("culture_core.cli.doctor", False),
        ("culture_core.clients", True),
        ("culture_core.clients.claude", True),
        ("culture_core.clients.claude.config", False),
        ("culture_core.persistence", False),
        ("culture_core.protocol", False),
        ("culture_core.telemetry", False),
        ("culture_core.doctor", False),
        ("culture_core.pidfile", False),
        ("culture_core.skills", True),
    ]

    modules = {name: _make_module(name, is_package=pkg) for name, pkg in layout}

    # Wire children onto their parents as attributes so dotted access works
    # (e.g. culture_core.cli.doctor, culture_core.clients.claude.config) without
    # re-entering the import machinery.
    for name in modules:
        parent, _, child = name.rpartition(".")
        if parent in modules:
            setattr(modules[parent], child, modules[name])

    # Behavior-free entry points the front-door tests reference.
    modules["culture_core.cli"].main = _no_op
    modules["culture_core.pidfile"].read_pid = _no_op

    return modules
