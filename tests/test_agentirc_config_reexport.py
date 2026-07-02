"""Pin the culture_core.agentirc.config re-export identity.

This test verifies that the three config dataclasses re-exported by
`culture_core.agentirc.config` are the same objects as those defined
in `agentirc.config`.
"""

import agentirc.config as upstream

from culture_core.agentirc import config as reexport


def test_reexport_is_identity():
    assert reexport.ServerConfig is upstream.ServerConfig
    assert reexport.LinkConfig is upstream.LinkConfig
    assert reexport.TelemetryConfig is upstream.TelemetryConfig
