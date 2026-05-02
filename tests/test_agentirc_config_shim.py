"""Pin the culture.agentirc.config → agentirc.config re-export shim.

`culture/agentirc/config.py` re-exports the three config dataclasses
from the published `agentirc-cli` PyPI package so that legacy
`from culture.agentirc.config import ...` call sites resolve to the
same class objects as `from agentirc.config import ...`. Identity
collapse is what lets the in-process IRCd at `culture/agentirc/ircd.py`
keep working unchanged while telemetry/CLI/conftest call sites import
directly from `agentirc.config`.

Delete this test alongside the shim in Phase A3 (see
`agentculture/culture#308`).
"""

import agentirc.config as upstream

from culture.agentirc import config as shim


def test_shim_is_identity():
    assert shim.ServerConfig is upstream.ServerConfig
    assert shim.LinkConfig is upstream.LinkConfig
    assert shim.TelemetryConfig is upstream.TelemetryConfig
