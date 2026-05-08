import uuid

from culture.clients.shared.ipc import (
    MSG_TYPE_RESPONSE,
    MSG_TYPE_WHISPER,
    decode_message,
    encode_message,
    make_request,
    make_response,
    make_whisper,
)


def test_encode_decode_roundtrip():
    msg = {"type": "irc_send", "id": "abc", "channel": "#general", "message": "hello"}
    line = encode_message(msg)
    assert line.endswith(b"\n")
    decoded = decode_message(line)
    assert decoded == msg


def test_make_request_has_uuid():
    req = make_request("irc_send", channel="#general", message="hi")
    assert req["type"] == "irc_send"
    assert "id" in req
    uuid.UUID(req["id"])
    assert req["channel"] == "#general"
    assert req["message"] == "hi"


def test_make_response():
    resp = make_response("abc123", ok=True, data={"messages": []})
    assert resp["type"] == MSG_TYPE_RESPONSE
    assert resp["id"] == "abc123"
    assert resp["ok"] is True
    assert resp["data"] == {"messages": []}


def test_make_response_error():
    resp = make_response("abc123", ok=False, error="channel not found")
    assert resp["ok"] is False
    assert resp["error"] == "channel not found"


def test_make_whisper():
    w = make_whisper("You're spiraling", "CORRECTION")
    assert w["type"] == MSG_TYPE_WHISPER
    assert w["message"] == "You're spiraling"
    assert w["whisper_type"] == "CORRECTION"


def test_decode_ignores_blank_lines():
    assert decode_message(b"\n") is None
    assert decode_message(b"  \n") is None
    assert decode_message(b"") is None
