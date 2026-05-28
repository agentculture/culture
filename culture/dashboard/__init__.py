"""Mission Control dashboard — a local web app to watch and steer the mesh.

Design spec: docs/superpowers/specs/2026-05-29-mission-control-dashboard-design.md
"""

from culture.dashboard.server import build_app, serve_dashboard

__all__ = ["build_app", "serve_dashboard"]
