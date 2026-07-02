"""Re-export shim — see ``cultureagent.clients.colleague.constants``.

Backend timeout constants. The implementation lives in cultureagent;
bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.colleague.constants import *  # noqa: F401, F403
from cultureagent.clients.colleague.constants import (  # noqa: F401
    DEFAULT_TURN_TIMEOUT_SECONDS,
    INNER_ENGINE_TIMEOUT_SECONDS,
)
