"""culture_core.bots.filter_dsl — thin alias of agentirc.bots.filter_dsl (issue #445 cutover).

The in-tree implementation now lives in agentirc.bots. This module makes
culture_core.bots.filter_dsl resolve to the single agentirc source, so EVERY symbol —
public classes, private helpers (e.g. _check_rate), and module-level
constants the tests monkeypatch (e.g. BOTS_DIR) — is the same object the
agentirc runtime uses.
"""

import sys

from agentirc.bots import filter_dsl as _agentirc_module

sys.modules[__name__] = _agentirc_module
