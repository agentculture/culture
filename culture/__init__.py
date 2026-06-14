"""culture — thin front-door over the culture_core engine.

The engine lives in the ``culture_core`` distribution. This package keeps the
public ``culture.*`` import namespace working by aliasing each submodule to its
``culture_core`` counterpart with MODULE IDENTITY (``culture.x is
culture_core.x``), so existing imports and ``mock.patch("culture....")`` targets
resolve unchanged against the live engine module.

Only the top-level ``culture`` package is real (this module): it owns the
front-door distribution version and installs the alias finder. Every
``culture.<submodule>`` resolves to the identical ``culture_core.<submodule>``.
"""

import importlib
import importlib.abc
import importlib.util
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _v

try:
    __version__ = _v("culture")
except PackageNotFoundError:  # pragma: no cover - only when running from a checkout
    __version__ = "0.0.0-dev"

_PREFIX = __name__ + "."  # "culture."
_CORE = "culture_core."


class _CultureCoreAliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ``culture.<x>`` to the identical ``culture_core.<x>`` module.

    A meta-path finder (not ``from culture_core.x import *`` re-exports) so that
    ``culture.x`` *is* the same module object as ``culture_core.x``: attribute
    patches via ``mock.patch("culture.x.y")`` land on the live engine module the
    code under test actually uses. Lazy — a submodule is only aliased when first
    imported — preserving the engine's import-time semantics. Modules absent from
    ``culture_core`` raise ``ModuleNotFoundError`` exactly as they would there.

    Limitation: ``importlib.reload(culture.x)`` is a no-op — ``create_module``
    returns the already-loaded ``culture_core.x`` and ``exec_module`` does
    nothing; reload the ``culture_core.x`` module directly if you need that.
    """

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_PREFIX):
            return None
        # `culture.__main__` is a real front-door file (so `python -m culture`
        # runs through runpy with a proper __file__/loader); let the default
        # path finder serve it instead of aliasing it.
        if fullname == _PREFIX + "__main__":
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        core_name = _CORE + spec.name[len(_PREFIX) :]
        module = importlib.import_module(core_name)
        sys.modules[spec.name] = module  # identity alias
        return module

    def exec_module(self, module):
        # Already executed as culture_core.<x>; nothing more to do.
        pass


if not any(isinstance(f, _CultureCoreAliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _CultureCoreAliasFinder())
