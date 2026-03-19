from dataclasses import dataclass, field


@dataclass
class Message:
    """An IRC protocol message per RFC 2812 §2.3.1.

    Wire format: [:prefix SPACE] command [params] CRLF
    """

    prefix: str | None
    command: str
    params: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, line: str) -> "Message":
        """Parse a raw IRC line into a Message."""
        line = line.rstrip("\r\n")

        prefix = None
        if line.startswith(":"):
            if " " not in line:
                return cls(prefix=None, command="", params=[])
            prefix, line = line.split(" ", 1)
            prefix = prefix[1:]  # strip leading ':'

        trailing = None
        if " :" in line:
            line, trailing = line.split(" :", 1)

        parts = line.split()
        if not parts:
            return cls(prefix=prefix, command="", params=[])
        command = parts[0].upper()
        params = parts[1:]

        if trailing is not None:
            params.append(trailing)

        return cls(prefix=prefix, command=command, params=params)

    def format(self) -> str:
        """Format this message as an IRC wire line."""
        parts = []
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
