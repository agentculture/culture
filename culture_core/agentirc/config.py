"""Stable engine re-export of the agentirc.config dataclasses.

This module re-exports LinkConfig, ServerConfig, and TelemetryConfig
from the published `agentirc-cli` PyPI package so that
`culture_core` call sites continue to resolve the same class
objects as `agentirc.config`.
"""

from agentirc.config import LinkConfig, ServerConfig, TelemetryConfig

__all__ = ["LinkConfig", "ServerConfig", "TelemetryConfig"]
