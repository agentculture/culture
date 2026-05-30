import argparse
import asyncio

from culture.agentirc.config import LinkConfig, ServerConfig
from culture.agentirc.ircd import IRCd


def parse_link(value: str) -> LinkConfig:
    """Parse a link spec: name:host:port:password[:trust]

    Trust is extracted from the end if it matches a known value.
    This allows passwords containing colons.
    """
    trust = "full"
    if value.endswith(":full") or value.endswith(":restricted"):
        value, trust = value.rsplit(":", 1)

    parts = value.split(":", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"Link must be name:host:port:password[:trust], got: {value}"
        )
    name, host, port_str, password = parts
    try:
        port = int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid port: {port_str}")
    return LinkConfig(name=name, host=host, port=port, password=password, trust=trust)


async def main() -> None:
    parser = argparse.ArgumentParser(description="culture IRC server")
    parser.add_argument("--name", default="culture", help="Server name (used in nick prefix)")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Listen address (default 127.0.0.1; pass 0.0.0.0 to opt into LAN exposure — note the IRCd has no C2S auth)",
    )
    parser.add_argument("--port", type=int, default=6667, help="Listen port")
    parser.add_argument(
        "--link",
        type=parse_link,
        action="append",
        default=[],
        help="Link to peer: name:host:port:password",
    )
    args = parser.parse_args()

    config = ServerConfig(name=args.name, host=args.host, port=args.port, links=args.link)
    ircd = IRCd(config)
    await ircd.start()
    print(f"culture '{config.name}' listening on {config.host}:{config.port}")

    # Connect to configured peers
    for lc in config.links:
        try:
            await ircd.connect_to_peer(lc.host, lc.port, lc.password, lc.trust)
            print(f"Linking to {lc.name} at {lc.host}:{lc.port}")
        except Exception as e:
            print(f"Failed to link to {lc.name}: {e}")

    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await ircd.stop()


if __name__ == "__main__":
    asyncio.run(main())
