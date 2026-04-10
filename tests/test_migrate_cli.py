import argparse

import pytest


def test_migrate_creates_server_yaml_and_culture_yamls(tmp_path):
    """migrate converts agents.yaml in-place to manifest + per-dir culture.yaml."""
    from culture.config import load_culture_yaml, load_server_config

    proj_a = tmp_path / "proj-a"
    proj_a.mkdir()
    proj_b = tmp_path / "proj-b"
    proj_b.mkdir()

    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text(f"""\
server:
  name: spark
  host: localhost
  port: 6667

supervisor:
  model: claude-sonnet-4-6
  thinking: medium

webhooks:
  url: https://hooks.example.com
  irc_channel: "#alerts"

buffer_size: 300
poll_interval: 30

agents:
  - nick: spark-culture
    agent: claude
    directory: {proj_a}
    channels: ["#general"]
    model: claude-opus-4-6
    thinking: medium
    system_prompt: "Be helpful."
  - nick: spark-codex
    agent: codex
    directory: {proj_a}
    channels: ["#general"]
    model: gpt-5.4
  - nick: spark-daria
    agent: acp
    directory: {proj_b}
    channels: ["#general"]
    model: claude-sonnet-4-6
    acp_command: ["opencode", "acp"]
""")

    from culture.cli.agent import _cmd_migrate

    args = argparse.Namespace(config=str(agents_yaml))
    _cmd_migrate(args)

    # agents.yaml is now in manifest format (converted in-place)
    config = load_server_config(str(agents_yaml))
    assert config.server.name == "spark"
    assert config.supervisor.model == "claude-sonnet-4-6"
    assert config.webhooks.url == "https://hooks.example.com"
    assert config.buffer_size == 300
    assert len(config.manifest) == 3
    assert config.manifest["culture"] == str(proj_a)
    assert config.manifest["codex"] == str(proj_a)
    assert config.manifest["daria"] == str(proj_b)

    # proj_a gets multi-agent culture.yaml
    agents_a = load_culture_yaml(str(proj_a))
    assert len(agents_a) == 2
    suffixes = {a.suffix for a in agents_a}
    assert suffixes == {"culture", "codex"}

    # proj_b gets single-agent culture.yaml
    agents_b = load_culture_yaml(str(proj_b))
    assert len(agents_b) == 1
    assert agents_b[0].suffix == "daria"
    assert agents_b[0].backend == "acp"
    assert agents_b[0].acp_command == ["opencode", "acp"]


def test_migrate_roundtrip_starts(tmp_path):
    """After migration, load_config on the converted file resolves all agents."""
    from culture.config import load_config

    proj = tmp_path / "proj"
    proj.mkdir()

    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text(f"""\
server:
  name: spark
agents:
  - nick: spark-culture
    agent: claude
    directory: {proj}
    channels: ["#general"]
""")

    from culture.cli.agent import _cmd_migrate

    args = argparse.Namespace(config=str(agents_yaml))
    _cmd_migrate(args)

    config = load_config(str(agents_yaml))
    assert len(config.agents) == 1
    assert config.agents[0].nick == "spark-culture"
    assert config.agents[0].backend == "claude"


def test_migrate_already_manifest(tmp_path):
    """Migrating a file already in manifest format exits with error."""
    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("""\
server:
  name: spark
agents:
  culture: /tmp/project
""")

    from culture.cli.agent import _cmd_migrate

    args = argparse.Namespace(config=str(server_yaml))
    with pytest.raises(SystemExit) as exc_info:
        _cmd_migrate(args)
    assert exc_info.value.code == 1
