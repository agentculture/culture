"""Re-export shim — see ``cultureagent.clients.shared.attention``.

This module is kept so existing
``from culture.clients.shared.attention import AttentionTracker`` calls in
culture's CLI and tests keep working. The implementation lives in
cultureagent; bug reports go upstream.
"""

# pylint: disable=wildcard-import,unused-wildcard-import
from cultureagent.clients.shared.attention import *  # noqa: F401, F403
from cultureagent.clients.shared.attention import (  # noqa: F401
    CAUSE_AMBIENT,
    CAUSE_DECAY,
    CAUSE_DIRECT,
    CAUSE_MANUAL,
    AttentionConfig,
    AttentionTracker,
    Band,
    BandSpec,
    TargetState,
    default_bands,
)
