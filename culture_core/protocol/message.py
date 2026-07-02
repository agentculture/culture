from dataclasses import dataclass, field

_TAG_UNESCAPE = {
    "\\:": ";",
    "\\s": " ",
    "\\\\": "\\",
    "\\r": "\r",
    "\\n": "\n",
}
_TAG_ESCAPE = {v: k for k, v in _TAG_UNESCAPE.items()}


def _unescape_tag_value(value: str) -> str:
    out = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            two = value[i : i + 2]
            # Per IRCv3 spec, unknown escapes drop the backslash (yield only
            # the second char). Known escapes map to their defined character.
            out.append(_TAG_UNESCAPE.get(two, value[i + 1]))
            i += 2
            continue
        out.append(value[i])
        i += 1
    return "".join(out)


def _escape_tag_value(value: str) -> str:
    out = []
    for ch in value:
        if ch in _TAG_ESCAPE:
            out.append(_TAG_ESCAPE[ch])
        else:
            out.append(ch)
    return "".join(out)


@dataclass
class Message:
    """An IRC protocol message per RFC 2812 §2.3.1 with IRCv3 message-tags.

    Wire format: [@tags SPACE] [:prefix SPACE] command [params] CRLF
    """

    prefix: str | None = None
    command: str = ""
    params: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def _parse_tag_block(line: str) -> "tuple[dict[str, str], str]":
        """Extract leading @tag block from a wire line.

        Returns (tags_dict, remaining_line). If no tag block, returns ({}, line).
        """
        if not line.startswith("@"):
            return {}, line
        if " " not in line:
            return {}, ""  # malformed — no command after tags
        tag_blob, rest = line[1:].split(" ", 1)
        tags: dict[str, str] = {}
        for piece in tag_blob.split(";"):
            if not piece:
                continue
            if "=" in piece:
                key, value = piece.split("=", 1)
                tags[key] = _unescape_tag_value(value)
            else:
                tags[piece] = ""
        return tags, rest

    @classmethod
    def parse(cls, line: str) -> "Message":
        line = line.rstrip("\r\n")
        tags, line = cls._parse_tag_block(line)

        if not line:
            # malformed @-only input
            return cls(tags=tags, prefix=None, command="", params=[])

        prefix = None
        if line.startswith(":"):
            if " " not in line:
                return cls(tags=tags, prefix=None, command="", params=[])
            prefix, line = line.split(" ", 1)
            prefix = prefix[1:]

        trailing = None
        if " :" in line:
            line, trailing = line.split(" :", 1)

        parts = line.split()
        if not parts:
            return cls(tags=tags, prefix=prefix, command="", params=[])
        command = parts[0].upper()
        params = parts[1:]
        if trailing is not None:
            params.append(trailing)

        return cls(tags=tags, prefix=prefix, command=command, params=params)

    def format(self) -> str:
        parts = []

        if self.tags:
            tag_pieces = []
            for key, value in self.tags.items():
                if value == "":
                    tag_pieces.append(key)
                else:
                    tag_pieces.append(f"{key}={_escape_tag_value(value)}")
            parts.append("@" + ";".join(tag_pieces))

        if self.prefix:
            parts.append(f":{self.prefix}")
        parts.append(self.command)

        if self.params:
            for param in self.params[:-1]:
                parts.append(param)
            last = self.params[-1]
            if " " in last or not last or last.startswith(":"):
                parts.append(f":{last}")
            else:
                parts.append(last)

        return " ".join(parts) + "\r\n"
