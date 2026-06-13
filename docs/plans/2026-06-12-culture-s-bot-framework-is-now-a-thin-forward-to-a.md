# Build Plan — culture's bot framework is now a thin forward to agentirc.bots — culture/bots/* and culture/cli/bot.py are re-export shims, the in-tree implementation is gone, and every culture bot verb plus the bot-coupled tests work unchanged against the agentirc-owned BotManager

slug: `culture-s-bot-framework-is-now-a-thin-forward-to-a` · status: `exported` · from frame: `culture-s-bot-framework-is-now-a-thin-forward-to-a`

> culture's bot framework is now a thin forward to agentirc.bots — culture/bots/* and culture/cli/bot.py are re-export shims, the in-tree implementation is gone, and every culture bot verb plus the bot-coupled tests work unchanged against the agentirc-owned BotManager

## Tasks

### t1 — Pin agentirc-cli and refresh the lockfile

- covers: h9, c7
- acceptance:
  - pyproject.toml pins agentirc-cli>=9.7.0,<10 (up from >=9.6,<10)
  - uv.lock refreshed in the same change; 'uv run python -c "import agentirc.bots"' succeeds

### t2 — Forward the six core bot modules to agentirc.bots.* as re-export shims

- depends on: t1
- covers: c3, h1, c10, h10
- acceptance:
  - culture/bots/{bot_manager,bot,config,filter_dsl,template_engine,http_listener}.py each re-export their agentirc.bots counterpart and contain no implementation logic
  - importing culture.bots.bot_manager/.bot/.config/.filter_dsl/.template_engine/.http_listener yields the agentirc classes with no AttributeError on `EmitEventSpec`, `_check_rate`, `_DynamicEventType`, `_render_data_values`
  - the two agentirc bugfixes (validate_bot_name path-traversal guard, registry-corruption-on-partial-start) are present via forwarding with no buggy logic re-ported; test_bot and test_events_bot_trigger green

### t3 — Rework culture/cli/server.py: sys.modules system-bot bridge + single BotManager

- depends on: t1
- covers: c9, c12, h4, h12
- acceptance:
  - _run_server injects sys.modules['agentirc.bots.system']=culture.bots.system BEFORE ircd.start()
  - _run_server constructs no second BotManager (IRCd.start() owns lifecycle); webhook_port binds exactly once (no double-start)

### t4 — Forward culture/cli/bot.py to agentirc verbs, preserving 3-part naming

- depends on: t1, t2
- covers: c8, h3
- acceptance:
  - culture bot create/start/stop/list/inspect/archive/unarchive forward to the agentirc bot verbs
  - culture bot create yields a nick of the form <server>-<owner>-<name>; test_cli_bot green

### t5 — Keep culture/bots/system as real code; welcome bot loads via the bridge

- depends on: t2, t3
- covers: h5
- acceptance:
  - culture.bots.system stays real culture code (not deleted/forwarded); its imports resolve against the config shim
  - the welcome system bot is discovered and registered through agentirc's BotManager.load_system_bots via the sys.modules bridge; test_welcome_bot green

### t6 — Delete the in-tree implementation; preserve YAML/on-disk contract

- depends on: t2, t3, t4
- covers: c1, c4, c6, h7, h11
- acceptance:
  - git shows the in-tree bot implementation deleted; culture/bots/* and culture/cli/bot.py contain only re-exports/forwards
  - the bot YAML spec and ~/.culture/bots on-disk layout (BOTS_DIR, BOT_CONFIG_FILE) are unchanged; no other backend harness is touched

### t7 — Full verification: tests green + behaviour parity, no coverage gap window

- depends on: t2, t3, t4, t5, t6
- covers: c2, c5, h2, h8
- acceptance:
  - /run-tests green incl. test_bot_event_dispatch_span, test_bot_run_span, test_metrics_bots, test_welcome_bot, test_cli_bot, test_bot, test_events_bot_trigger, and test_cli_server bot cases
  - culture bot verbs + IRCd bot/webhook lifecycle behave identically to pre-cutover (CLI verbs, webhook dispatch, bot events); pin->forward->delete done in one PR with no window where the framework exists in neither repo
