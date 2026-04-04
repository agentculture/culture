from dataclasses import dataclass, field


@dataclass
class LinkConfig:
    """Configuration for a server-to-server link."""

    name: str
    host: str
    port: int
    password: str
    trust: str = "full"  # "full" or "restricted"


@dataclass
class ServerConfig:
    """Configuration for an agentirc server instance."""

    name: str = "agentirc"
    host: str = "0.0.0.0"
    port: int = 6667
    webhook_port: int = 7680
    data_dir: str = ""
    links: list[LinkConfig] = field(default_factory=list)
