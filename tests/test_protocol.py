"""Tests for Cast V2 message encode/decode (protocol layer)."""
import json
import struct

import pytest

from sonos_cast.protocol import (
    CastMessage,
    decode_message,
    encode_message,
    make_message,
    NS_HEARTBEAT,
    NS_CONNECTION,
    NS_RECEIVER,
    NS_MEDIA,
    NS_AUTH,
)


# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------


def test_namespace_constants_are_correct_strings():
    assert NS_HEARTBEAT == "urn:x-cast:com.google.cast.tp.heartbeat"
    assert NS_CONNECTION == "urn:x-cast:com.google.cast.tp.connection"
    assert NS_RECEIVER == "urn:x-cast:com.google.cast.receiver"
    assert NS_MEDIA == "urn:x-cast:com.google.cast.media"
    assert NS_AUTH == "urn:x-cast:com.google.cast.tp.deviceauth"


# ---------------------------------------------------------------------------
# make_message
# ---------------------------------------------------------------------------


def test_make_message_returns_cast_message():
    msg = make_message(NS_HEARTBEAT, {"type": "PING"}, "sender-0", "receiver-0")
    assert isinstance(msg, CastMessage)


def test_make_message_sets_source_and_destination():
    msg = make_message(NS_HEARTBEAT, {"type": "PING"}, "sender-0", "receiver-0")
    assert msg.source_id == "receiver-0"
    assert msg.destination_id == "sender-0"


def test_make_message_sets_namespace():
    msg = make_message(NS_HEARTBEAT, {"type": "PONG"}, "s", "r")
    assert msg.namespace == NS_HEARTBEAT


def test_make_message_payload_is_json():
    payload = {"type": "PONG", "requestId": 42}
    msg = make_message(NS_HEARTBEAT, payload, "s", "r")
    parsed = json.loads(msg.payload_utf8)
    assert parsed["type"] == "PONG"
    assert parsed["requestId"] == 42


# ---------------------------------------------------------------------------
# encode / decode round-trip
# ---------------------------------------------------------------------------


def test_encode_produces_bytes_with_4_byte_length_prefix():
    msg = make_message(NS_HEARTBEAT, {"type": "PONG"}, "s", "r")
    data = encode_message(msg)
    assert isinstance(data, bytes)
    declared_len = struct.unpack(">I", data[:4])[0]
    assert declared_len == len(data) - 4


def test_decode_round_trips_through_encode():
    original = make_message(NS_HEARTBEAT, {"type": "PONG"}, "sender", "receiver")
    wire = encode_message(original)
    recovered, consumed = decode_message(wire)
    assert consumed == len(wire)
    assert recovered.namespace == NS_HEARTBEAT
    assert json.loads(recovered.payload_utf8)["type"] == "PONG"
    assert recovered.source_id == original.source_id
    assert recovered.destination_id == original.destination_id


def test_decode_returns_none_when_buffer_too_short():
    # Only the length header, no body yet
    data = struct.pack(">I", 100)
    result = decode_message(data)
    assert result is None


def test_decode_returns_none_on_empty_buffer():
    assert decode_message(b"") is None


def test_decode_returns_none_when_partial_header():
    assert decode_message(b"\x00\x00") is None
