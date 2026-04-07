"""Bot subcommands: culture bot {create,start,stop,list,inspect}."""

from __future__ import annotations

import argparse
import sys
import time

from culture.clients.claude.config import load_config_or_default

from .shared.constants import _BOT_NAME_HELP, _CONFIG_HELP, BOT_CONFIG_FILE, DEFAULT_CONFIG

NAME = "bot"


def register(subparsers: argparse._SubParsersAction) -> None:
    bot_parser = subparsers.add_parser("bot", help="Manage bots and webhooks")
    bot_sub = bot_parser.add_subparsers(dest="bot_command")

    bot_create = bot_sub.add_parser("create", help="Create a new bot")
    bot_create.add_argument("name", help="Bot name (e.g. ghci)")
    bot_create.add_argument("--owner", required=True, help="Owner nick (e.g. spark-ori)")
    bot_create.add_argument("--channels", nargs="+", default=[], help="Channels to join")
    bot_create.add_argument(
        "--trigger", default="webhook", choices=["webhook"], help="Trigger type"
    )
    bot_create.add_argument("--mention", default=None, help="Agent to @mention on trigger")
    bot_create.add_argument("--template", default=None, help="Message template")
    bot_create.add_argument("--dm-owner", action="store_true", help="DM the owner on trigger")
    bot_create.add_argument("--description", default="", help="Bot description")
    bot_create.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    bot_start = bot_sub.add_parser("start", help="Start a bot")
    bot_start.add_argument("name", help=_BOT_NAME_HELP)
    bot_start.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    bot_stop = bot_sub.add_parser("stop", help="Stop a bot")
    bot_stop.add_argument("name", help=_BOT_NAME_HELP)
    bot_stop.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    bot_list = bot_sub.add_parser("list", help="List bots")
    bot_list.add_argument("owner", nargs="?", default=None, help="Filter by owner nick")
    bot_list.add_argument("--all", action="store_true", help="Include archived bots")

    bot_inspect = bot_sub.add_parser("inspect", help="Show bot details")
    bot_inspect.add_argument("name", help=_BOT_NAME_HELP)
    bot_inspect.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    bot_archive = bot_sub.add_parser("archive", help="Archive a bot")
    bot_archive.add_argument("name", help="Bot name to archive")
    bot_archive.add_argument("--reason", default="", help="Reason for archiving")

    bot_unarchive = bot_sub.add_parser("unarchive", help="Restore an archived bot")
    bot_unarchive.add_argument("name", help="Bot name to unarchive")


def dispatch(args: argparse.Namespace) -> None:
    if not args.bot_command:
        print(
            "Usage: culture bot {create|start|stop|list|inspect|archive|unarchive}",
            file=sys.stderr,
        )
        sys.exit(1)

    handlers = {
        "create": _bot_create,
        "start": _bot_start,
        "stop": _bot_stop,
        "list": _bot_list,
        "inspect": _bot_inspect,
        "archive": _bot_archive,
        "unarchive": _bot_unarchive,
    }
    handler = handlers.get(args.bot_command)
    if handler:
        handler(args)
    else:
        print(f"Unknown bot command: {args.bot_command}", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------


def _bot_create(args: argparse.Namespace) -> None:
    from culture.bots.config import BOTS_DIR, BotConfig, save_bot_config

    name = args.name
    config = load_config_or_default(args.config)
    server_name = config.server.name

    if not name.startswith(f"{server_name}-"):
        owner = args.owner
        if owner.startswith(f"{server_name}-"):
            owner_suffix = owner[len(server_name) + 1 :]
        else:
            owner_suffix = owner
        name = f"{server_name}-{owner_suffix}-{name}"

    bot_config = BotConfig(
        name=name,
        owner=args.owner,
        description=args.description,
        created=time.strftime("%Y-%m-%d"),
        trigger_type=args.trigger,
        channels=args.channels,
        dm_owner=args.dm_owner,
        mention=args.mention,
        template=args.template,
        fallback="json",
    )

    bot_dir = BOTS_DIR / name
    if (bot_dir / BOT_CONFIG_FILE).exists():
        print(f"Bot '{name}' already exists at {bot_dir}", file=sys.stderr)
        sys.exit(1)

    save_bot_config(bot_dir / BOT_CONFIG_FILE, bot_config)
    print(f"Bot '{name}' created at {bot_dir}")
    print(f"  Owner:    {args.owner}")
    print(f"  Trigger:  {args.trigger}")
    if args.channels:
        print(f"  Channels: {', '.join(args.channels)}")
    if args.mention:
        print(f"  Mentions: {args.mention}")
    print(f"\nTo activate, restart the server or run: culture bot start {name}")


def _bot_start(args: argparse.Namespace) -> None:
    from culture.bots.config import BOTS_DIR

    bot_dir = BOTS_DIR / args.name
    if not (bot_dir / BOT_CONFIG_FILE).exists():
        print(f"Bot '{args.name}' not found at {bot_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Bot '{args.name}' will be loaded on next server restart.")
    print("(Live reload via IPC will be available in a future release.)")


def _bot_stop(args: argparse.Namespace) -> None:
    from culture.bots.config import BOTS_DIR

    bot_dir = BOTS_DIR / args.name
    if not (bot_dir / BOT_CONFIG_FILE).exists():
        print(f"Bot '{args.name}' not found at {bot_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Bot '{args.name}' will be unloaded on next server restart.")
    print("(Live reload via IPC will be available in a future release.)")


def _load_and_filter_bots(args) -> list:
    """Load bot configs, filtering by owner and archived status."""
    from culture.bots.config import BOTS_DIR, load_bot_config

    if not BOTS_DIR.is_dir():
        return []
    show_all = getattr(args, "all", False)
    bots = []
    for bot_dir in sorted(BOTS_DIR.iterdir()):
        yaml_path = bot_dir / BOT_CONFIG_FILE
        if not yaml_path.is_file():
            continue
        try:
            config = load_bot_config(yaml_path)
        except Exception:
            continue
        if args.owner and config.owner != args.owner:
            continue
        if not show_all and config.archived:
            continue
        bots.append(config)
    return bots


def _bot_list(args: argparse.Namespace) -> None:
    from culture.bots.config import BOTS_DIR

    if not BOTS_DIR.is_dir():
        print("No bots configured.")
        return

    bots = _load_and_filter_bots(args)
    if not bots:
        if args.owner:
            print(f"No bots found for owner '{args.owner}'.")
        else:
            print("No bots configured.")
        return

    show_all = getattr(args, "all", False)
    print(f"{'NAME':<35} {'TRIGGER':<10} {'CHANNELS':<20} {'OWNER':<20}")
    for config in bots:
        channels = ", ".join(config.channels) if config.channels else "-"
        name = f"{config.name} [archived]" if show_all and config.archived else config.name
        print(f"{name:<35} {config.trigger_type:<10} {channels:<20} {config.owner:<20}")


def _bot_inspect(args: argparse.Namespace) -> None:
    from culture.bots.config import BOTS_DIR, load_bot_config

    bot_dir = BOTS_DIR / args.name
    yaml_path = bot_dir / BOT_CONFIG_FILE
    if not yaml_path.is_file():
        print(f"Bot '{args.name}' not found at {bot_dir}", file=sys.stderr)
        sys.exit(1)

    config = load_bot_config(yaml_path)

    webhook_port = 7680
    webhook_url = f"http://localhost:{webhook_port}/{config.name}"

    print(f"Bot:         {config.name}")
    print(f"Owner:       {config.owner}")
    print(f"Description: {config.description or '-'}")
    print(f"Created:     {config.created or '-'}")
    print(f"Trigger:     {config.trigger_type}")
    print(f"Webhook URL: {webhook_url} (default port)")
    print(f"Channels:    {', '.join(config.channels) if config.channels else '-'}")
    print(f"DM Owner:    {'yes' if config.dm_owner else 'no'}")
    print(f"Mentions:    {config.mention or '-'}")
    if config.template:
        first_line = config.template.strip().split("\n")[0]
        if len(first_line) > 60:
            first_line = first_line[:57] + "..."
        print(f"Template:    {first_line}")
    print(f"Handler:     {'custom (handler.py)' if config.has_handler else 'template'}")
    if config.archived:
        print(f"Archived:    yes (since {config.archived_at})")
        if config.archived_reason:
            print(f"Reason:      {config.archived_reason}")


# -----------------------------------------------------------------------
# Archive / Unarchive
# -----------------------------------------------------------------------


def _bot_archive(args: argparse.Namespace) -> None:
    import time as _time

    from culture.bots.config import BOTS_DIR, load_bot_config, save_bot_config

    bot_dir = BOTS_DIR / args.name
    yaml_path = bot_dir / BOT_CONFIG_FILE
    if not yaml_path.is_file():
        print(f"Bot '{args.name}' not found at {bot_dir}", file=sys.stderr)
        sys.exit(1)

    config = load_bot_config(yaml_path)
    if config.archived:
        print(f"Bot '{args.name}' is already archived")
        return

    config.archived = True
    config.archived_at = _time.strftime("%Y-%m-%d")
    config.archived_reason = args.reason
    save_bot_config(yaml_path, config)

    print(f"Bot archived: {args.name}")
    if args.reason:
        print(f"  Reason: {args.reason}")
    print(f"\nTo restore: culture bot unarchive {args.name}")


def _bot_unarchive(args: argparse.Namespace) -> None:
    from culture.bots.config import BOTS_DIR, load_bot_config, save_bot_config

    bot_dir = BOTS_DIR / args.name
    yaml_path = bot_dir / BOT_CONFIG_FILE
    if not yaml_path.is_file():
        print(f"Bot '{args.name}' not found at {bot_dir}", file=sys.stderr)
        sys.exit(1)

    config = load_bot_config(yaml_path)
    if not config.archived:
        print(f"Bot '{args.name}' is not archived", file=sys.stderr)
        sys.exit(1)

    config.archived = False
    config.archived_at = ""
    config.archived_reason = ""
    save_bot_config(yaml_path, config)

    print(f"Bot unarchived: {args.name}")
