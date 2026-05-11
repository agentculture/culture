"""Re-export shim — see ``cultureagent.clients.shared.webhook``.

Webhook fan-out client. The implementation lives in cultureagent;
bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.shared.webhook import *  # noqa: F401, F403
from cultureagent.clients.shared.webhook import (  # noqa: F401
    AlertEvent,
    WebhookClient,
)
