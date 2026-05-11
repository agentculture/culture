"""Re-export shim — see ``cultureagent.clients.shared.rooms``.

Room-metadata parsing helpers. The implementation lives in cultureagent;
bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.shared.rooms import *  # noqa: F401, F403
from cultureagent.clients.shared.rooms import parse_room_meta  # noqa: F401
