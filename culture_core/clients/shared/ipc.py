"""Re-export shim — see ``cultureagent.clients.shared.ipc``.

Whisper protocol primitives. The implementation lives in cultureagent;
bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.shared.ipc import *  # noqa: F401, F403
from cultureagent.clients.shared.ipc import (  # noqa: F401
    MSG_TYPE_RESPONSE,
    MSG_TYPE_WHISPER,
    decode_message,
    encode_message,
    make_request,
    make_response,
    make_whisper,
)
