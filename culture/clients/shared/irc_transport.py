"""Re-export shim — see ``cultureagent.clients.shared.irc_transport``.

IRC transport adapter. The implementation lives in cultureagent;
bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.shared.irc_transport import *  # noqa: F401, F403
from cultureagent.clients.shared.irc_transport import IRCTransport  # noqa: F401
