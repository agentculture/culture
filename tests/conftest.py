"""pytest configuration for the culture front-door integration suite.

culture is the thin front-door over the culture_core engine: ``culture/__init__``
installs a meta-path finder that aliases every ``culture.<x>`` import to the
identical ``culture_core.<x>`` module object. To exercise the front-door (the
alias finder, the console entry point, module identity, mock.patch targeting) in
ISOLATION — without the real engine's behavior or bundled data — we seed a
behavior-free fake ``culture_core`` into ``sys.modules`` before any test imports
``culture.*``. The alias finder then resolves ``culture.<x>`` against the fake
exactly as it would against the real engine.

This is what makes culture's suite independent of culture_core: a bug in the
real engine cannot redden these tests, because the real engine is never imported
here. See docs/specs/2026-06-15-culture-s-test-suite-now-tests-only-the-front-door.md.
"""

import sys

from tests._fake_engine import build_fake_culture_core

# Seed the fake as culture_core (and every aliased submodule) at conftest-import
# time — pytest imports conftest before any test module, and afresh in each
# pytest-xdist worker process, so every worker resolves culture.* to the fake.
# Direct assignment (not setdefault) guarantees the fake wins even if something
# pulled in the real culture_core earlier in the process.
for _name, _module in build_fake_culture_core().items():
    sys.modules[_name] = _module
