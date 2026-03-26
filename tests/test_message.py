from agentirc.protocol.message import Message


class TestMessageParse:
    def test_simple_command(self):
        msg = Message.parse("QUIT\r\n")
        assert msg.command == "QUIT"
        assert msg.prefix is None
        assert msg.params == []

    def test_command_with_params(self):
        msg = Message.parse("NICK spark-agentirc\r\n")
        assert msg.command == "NICK"
        assert msg.params == ["spark-agentirc"]

    def test_command_with_trailing(self):
        msg = Message.parse("PRIVMSG #general :Hello world\r\n")
        assert msg.command == "PRIVMSG"
        assert msg.params == ["#general", "Hello world"]

    def test_command_with_prefix(self):
        msg = Message.parse(":spark-ori!ori@localhost PRIVMSG #general :hi\r\n")
        assert msg.prefix == "spark-ori!ori@localhost"
        assert msg.command == "PRIVMSG"
        assert msg.params == ["#general", "hi"]

    def test_user_command(self):
        msg = Message.parse("USER ori 0 * :Ori Nachum\r\n")
        assert msg.command == "USER"
        assert msg.params == ["ori", "0", "*", "Ori Nachum"]

    def test_command_case_normalized(self):
        msg = Message.parse("nick spark-agentirc\r\n")
        assert msg.command == "NICK"

    def test_no_trailing_crlf(self):
        msg = Message.parse("PING :token123")
        assert msg.command == "PING"
        assert msg.params == ["token123"]

    def test_prefix_only_no_command(self):
        msg = Message.parse(":badprefix\r\n")
        assert msg.command == ""

    def test_empty_trailing(self):
        msg = Message.parse("PRIVMSG #general :\r\n")
        assert msg.params == ["#general", ""]

    def test_multiple_middle_params(self):
        msg = Message.parse("MODE #channel +o spark-agentirc\r\n")
        assert msg.command == "MODE"
        assert msg.params == ["#channel", "+o", "spark-agentirc"]


class TestMessageFormat:
    def test_simple_command(self):
        msg = Message(prefix=None, command="QUIT", params=[])
        assert msg.format() == "QUIT\r\n"

    def test_with_prefix(self):
        msg = Message(prefix="server", command="PONG", params=["server", "token"])
        assert msg.format() == ":server PONG server token\r\n"

    def test_trailing_with_spaces(self):
        msg = Message(prefix=None, command="PRIVMSG", params=["#general", "Hello world"])
        assert msg.format() == "PRIVMSG #general :Hello world\r\n"

    def test_trailing_empty(self):
        msg = Message(prefix=None, command="PRIVMSG", params=["#general", ""])
        assert msg.format() == "PRIVMSG #general :\r\n"

    def test_single_word_trailing(self):
        msg = Message(prefix=None, command="NICK", params=["spark-agentirc"])
        assert msg.format() == "NICK spark-agentirc\r\n"

    def test_roundtrip(self):
        original = ":spark-ori!ori@localhost PRIVMSG #general :Hello world"
        msg = Message.parse(original + "\r\n")
        assert msg.format() == original + "\r\n"
