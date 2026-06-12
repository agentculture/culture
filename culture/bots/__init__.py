"""culture.bots — the bot framework, now forwarded to ``agentirc.bots`` (issue #445).

The in-tree implementation moved to ``agentirc.bots``; the modules here
(``bot_manager``, ``bot``, ``config``, ``filter_dsl``, ``template_engine``,
``http_listener``) are thin ``sys.modules`` aliases of their agentirc
counterparts. culture keeps exactly one piece of real bot code,
``culture.bots.system`` (the welcome system bot), which agentirc does not
vendor.

agentirc's ``BotManager.load_system_bots()`` discovers system bots via
``from agentirc.bots.system import discover_system_bots`` and no-ops when that
module is absent — which it is in a stock agentirc install. The bridge below
registers ``culture.bots.system`` under that name in ``sys.modules`` so
agentirc's loader finds culture's welcome bot through its own lifecycle, with
no ``agentirc.bots.system`` package shipped upstream. See the cutover spec/plan
under ``docs/{specs,plans}/`` and the upstream naming follow-up agentirc#42.
"""

from __future__ import annotations

import sys


def install_system_bridge() -> None:
    """Register ``culture.bots.system`` as ``agentirc.bots.system`` in ``sys.modules``.

    Idempotent. Makes ``from agentirc.bots.system import discover_system_bots``
    (called by agentirc's ``BotManager.load_system_bots``) resolve to culture's
    loader. Must run before any ``BotManager.start()`` / ``load_system_bots()``
    call — production installs it explicitly before ``ircd.start()``; importing
    ``culture.bots`` installs it for every other path (e.g. the test fixtures
    that build a ``BotManager`` directly).
    """
    if "agentirc.bots.system" in sys.modules:
        return
    from culture.bots import system as _culture_system

    sys.modules["agentirc.bots.system"] = _culture_system


# Install on import so any code path that touches culture.bots (production or
# tests) bridges system-bot discovery before a BotManager starts.
install_system_bridge()
