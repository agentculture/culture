from __future__ import annotations

import json
import uuid
from typing import Any

MSG_TYPE_RESPONSE = "response"
MSG_TYPE_WHISPER = "whisper"


def encode_message(msg: dict[str, Any]) -> bytes:
    return json.dumps(msg, separators=(",", ":")).encode() + b"\n"


def decode_message(line: bytes) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    return json.loads(stripped)


def make_request(msg_type: str, **kwargs: Any) -> dict[str, Any]:
    return {"type": msg_type, "id": str(uuid.uuid4()), **kwargs}


def make_response(
    request_id: str,
    ok: bool = True,
    data: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": MSG_TYPE_RESPONSE, "id": request_id, "ok": ok}
    if data is not None:
        msg["data"] = data
    if error is not None:
        msg["error"] = error
    return msg


def make_whisper(message: str, whisper_type: str) -> dict[str, Any]:
    return {"type": MSG_TYPE_WHISPER, "message": message, "whisper_type": whisper_type}
