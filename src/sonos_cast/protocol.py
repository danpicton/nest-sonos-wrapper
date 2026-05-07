"""Cast V2 wire protocol: message construction, encoding, and decoding."""
import json
import struct
from dataclasses import dataclass
from typing import Optional

from sonos_cast import cast_channel_pb2 as _pb

# Cast namespace URNs
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_CONNECTION = "urn:x-cast:com.google.cast.tp.connection"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"
NS_AUTH = "urn:x-cast:com.google.cast.tp.deviceauth"
# Setup / system info — gms_cast_prober sends eureka_info on one of these
NS_SETUP = "urn:x-cast:com.google.cast.tp.setup"
NS_SYSTEM = "urn:x-cast:com.google.cast.system"

# The Google Default Media Receiver app that we claim to run
DEFAULT_MEDIA_RECEIVER_APP_ID = "CC1AD845"


@dataclass
class CastMessage:
    source_id: str
    destination_id: str
    namespace: str
    payload_utf8: str = ""
    payload_binary: bytes = b""


def make_message(
    namespace: str,
    payload: dict,
    sender_id: str,
    receiver_id: str,
) -> CastMessage:
    """Build a CastMessage sent *from* receiver_id *to* sender_id."""
    return CastMessage(
        source_id=receiver_id,
        destination_id=sender_id,
        namespace=namespace,
        payload_utf8=json.dumps(payload),
    )


def encode_message(msg: CastMessage) -> bytes:
    """Serialise a CastMessage to the Cast wire format: 4-byte length + protobuf."""
    proto = _pb.CastMessage()
    proto.protocol_version = _pb.CastMessage.CASTV2_1_0
    proto.source_id = msg.source_id
    proto.destination_id = msg.destination_id
    proto.namespace = msg.namespace
    if msg.payload_binary:
        proto.payload_type = _pb.CastMessage.BINARY
        proto.payload_binary = msg.payload_binary
    else:
        proto.payload_type = _pb.CastMessage.STRING
        proto.payload_utf8 = msg.payload_utf8
    body = proto.SerializeToString()
    return struct.pack(">I", len(body)) + body


def decode_message(data: bytes) -> Optional[tuple[CastMessage, int]]:
    """
    Parse one CastMessage from a byte buffer.

    Returns (CastMessage, bytes_consumed) or None if the buffer is incomplete.
    """
    if len(data) < 4:
        return None
    (body_len,) = struct.unpack(">I", data[:4])
    total = 4 + body_len
    if len(data) < total:
        return None
    proto = _pb.CastMessage()
    proto.ParseFromString(data[4:total])
    msg = CastMessage(
        source_id=proto.source_id,
        destination_id=proto.destination_id,
        namespace=proto.namespace,
        payload_utf8=proto.payload_utf8,
        payload_binary=bytes(proto.payload_binary),
    )
    return msg, total
