"""Re-export shim — see ``cultureagent.clients.shared.webhook_types``.

Webhook configuration dataclasses. The implementation lives in cultureagent;
bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.shared.webhook_types import *  # noqa: F401, F403
from cultureagent.clients.shared.webhook_types import WebhookConfig  # noqa: F401
