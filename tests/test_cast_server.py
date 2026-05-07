"""Tests for the async Cast TLS server (shanocast auth enabled)."""
import asyncio
import json
import ssl
import struct
from unittest.mock import MagicMock

import pytest

from sonos_cast import cast_channel_pb2 as pb
from sonos_cast.cast_server import CastServer, CAST_PORT
from sonos_cast.protocol import encode_message, decode_message, make_message, NS_HEARTBEAT, NS_AUTH


def test_cast_port_is_8009():
    assert CAST_PORT == 8009


@pytest.fixture
def mock_sonos():
    ctrl = MagicMock()
    ctrl.get_volume.return_value = 30
    ctrl.get_transport_state.return_value = "STOPPED"
    return ctrl


def _insecure_client_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@pytest.mark.asyncio
async def test_server_starts_and_stops(mock_sonos):
    server = CastServer(sonos_controller=mock_sonos, device_name="Test", port=0)
    await server.start()
    assert server.is_running
    await server.stop()
    assert not server.is_running


@pytest.mark.asyncio
async def test_ping_pong_over_tls(mock_sonos):
    """Connect a TLS client, send PING, verify PONG is received."""
    server = CastServer(sonos_controller=mock_sonos, device_name="Test", port=0)
    await server.start()
    port = server.port

    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=_insecure_client_ctx()
        )
        ping = make_message(NS_HEARTBEAT, {"type": "PING"}, "sender-0", "receiver-0")
        writer.write(encode_message(ping))
        await writer.drain()

        header = await asyncio.wait_for(reader.readexactly(4), timeout=3.0)
        (body_len,) = struct.unpack(">I", header)
        body = await asyncio.wait_for(reader.readexactly(body_len), timeout=3.0)
        response, _ = decode_message(header + body)

        assert json.loads(response.payload_utf8)["type"] == "PONG"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_challenge_receives_auth_response(mock_sonos):
    """Server replies to a DeviceAuthMessage challenge with a valid AuthResponse."""
    server = CastServer(sonos_controller=mock_sonos, device_name="Test", port=0)
    await server.start()
    port = server.port

    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=_insecure_client_ctx()
        )

        # Build and send a DeviceAuthMessage challenge (empty AuthChallenge)
        challenge = pb.DeviceAuthMessage()
        challenge.challenge.CopyFrom(pb.AuthChallenge())

        proto = pb.CastMessage()
        proto.protocol_version = pb.CastMessage.CASTV2_1_0
        proto.source_id = "sender-0"
        proto.destination_id = "receiver-0"
        proto.namespace = NS_AUTH
        proto.payload_type = pb.CastMessage.BINARY
        proto.payload_binary = challenge.SerializeToString()

        body = proto.SerializeToString()
        writer.write(struct.pack(">I", len(body)) + body)
        await writer.drain()

        # Read response
        header = await asyncio.wait_for(reader.readexactly(4), timeout=3.0)
        (body_len,) = struct.unpack(">I", header)
        body = await asyncio.wait_for(reader.readexactly(body_len), timeout=3.0)

        resp_proto = pb.CastMessage()
        resp_proto.ParseFromString(header[4:4] + body)  # skip the 4 length bytes we already consumed
        resp_proto.ParseFromString(body)

        assert resp_proto.namespace == NS_AUTH
        assert resp_proto.payload_type == pb.CastMessage.BINARY

        auth_resp = pb.DeviceAuthMessage()
        auth_resp.ParseFromString(resp_proto.payload_binary)
        assert auth_resp.HasField("response")
        assert len(auth_resp.response.signature) == 256
        assert len(auth_resp.response.client_auth_certificate) == 943

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
