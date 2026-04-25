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
class TelemetryConfig:
    """OpenTelemetry settings. Mirrors server.yaml `telemetry:` block."""

    enabled: bool = False
    service_name: str = "culture.agentirc"
    otlp_endpoint: str = "http://localhost:4317"
    otlp_protocol: str = "grpc"  # grpc | http/protobuf (only grpc supported initially)
    otlp_timeout_ms: int = 5000
    otlp_compression: str = "gzip"  # gzip | none
    traces_enabled: bool = True
    traces_sampler: str = "parentbased_always_on"
    metrics_enabled: bool = True
    metrics_export_interval_ms: int = 10000


@dataclass
class ServerConfig:
    """Configuration for a culture server instance."""

    name: str = "culture"
    host: str = "0.0.0.0"
    port: int = 6667
    webhook_port: int = 7680
    data_dir: str = ""
    links: list[LinkConfig] = field(default_factory=list)
    system_bots: dict = field(default_factory=dict)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
