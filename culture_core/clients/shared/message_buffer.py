"""Re-export shim — see ``cultureagent.clients.shared.message_buffer``.

Per-channel message buffer with thread-prefix support. The implementation
lives in cultureagent; bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.shared.message_buffer import *  # noqa: F401, F403
from cultureagent.clients.shared.message_buffer import (  # noqa: F401
    BufferedMessage,
    MessageBuffer,
)
