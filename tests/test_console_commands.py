"""Tests for console command parser."""

from culture.console.commands import CommandType, parse_command


def test_parse_chat_message():
    result = parse_command("hello world")
    assert result.type == CommandType.CHAT
    assert result.text == "hello world"


def test_parse_channels():
    result = parse_command("/channels")
    assert result.type == CommandType.CHANNELS


def test_parse_join():
    result = parse_command("/join #ops")
    assert result.type == CommandType.JOIN
    assert result.args == ["#ops"]


def test_parse_part():
    result = parse_command("/part #ops")
    assert result.type == CommandType.PART
    assert result.args == ["#ops"]


def test_parse_who():
    result = parse_command("/who #general")
    assert result.type == CommandType.WHO
    assert result.args == ["#general"]


def test_parse_send():
    result = parse_command("/send #ops hello agents")
    assert result.type == CommandType.SEND
    assert result.args == ["#ops"]
    assert result.text == "hello agents"


def test_parse_overview():
    result = parse_command("/overview")
    assert result.type == CommandType.OVERVIEW


def test_parse_status():
    result = parse_command("/status spark-claude")
    assert result.type == CommandType.STATUS
    assert result.args == ["spark-claude"]


def test_parse_status_no_args():
    result = parse_command("/status")
    assert result.type == CommandType.STATUS
    assert result.args == []


def test_parse_agents():
    result = parse_command("/agents")
    assert result.type == CommandType.AGENTS


def test_parse_start():
    result = parse_command("/start spark-claude")
    assert result.type == CommandType.START
    assert result.args == ["spark-claude"]


def test_parse_stop():
    result = parse_command("/stop spark-claude")
    assert result.type == CommandType.STOP
    assert result.args == ["spark-claude"]


def test_parse_restart():
    result = parse_command("/restart spark-claude")
    assert result.type == CommandType.RESTART
    assert result.args == ["spark-claude"]


def test_parse_icon():
    result = parse_command("/icon spark-claude ★")
    assert result.type == CommandType.ICON
    assert result.args == ["spark-claude", "★"]


def test_parse_read():
    result = parse_command("/read #ops -n 20")
    assert result.type == CommandType.READ
    assert result.args == ["#ops", "-n", "20"]


def test_parse_topic():
    result = parse_command("/topic #ops New topic text")
    assert result.type == CommandType.TOPIC
    assert result.args == ["#ops"]
    assert result.text == "New topic text"


def test_parse_kick():
    result = parse_command("/kick #ops baduser")
    assert result.type == CommandType.KICK
    assert result.args == ["#ops", "baduser"]


def test_parse_invite():
    result = parse_command("/invite #ops newuser")
    assert result.type == CommandType.INVITE
    assert result.args == ["#ops", "newuser"]


def test_parse_server():
    result = parse_command("/server thor")
    assert result.type == CommandType.SERVER
    assert result.args == ["thor"]


def test_parse_quit():
    result = parse_command("/quit")
    assert result.type == CommandType.QUIT


def test_parse_unknown_command():
    result = parse_command("/unknown foo bar")
    assert result.type == CommandType.UNKNOWN
    assert result.text == "/unknown foo bar"
