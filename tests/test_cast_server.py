"""Tests for the async Cast TLS server."""
import asyncio
import json
import ssl
import struct
import tempfile
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sonos_cast.cast_server import CastServer, CAST_PORT
from sonos_cast.cert import generate_self_signed_cert, write_cert_files
from sonos_cast.protocol import encode_message, decode_message, make_message, NS_HEARTBEAT


def test_cast_port_is_8009():
    assert CAST_PORT == 8009


@pytest.fixture
def tls_files(tmp_path):
    cert_pem, key_pem = generate_self_signed_cert("test")
    cert_path = str(tmp_path / "cert.pem")
    key_path = str(tmp_path / "key.pem")
    write_cert_files(cert_pem, key_pem, cert_path, key_path)
    return cert_path, key_path


@pytest.fixture
def mock_sonos():
    ctrl = MagicMock()
    ctrl.get_volume.return_value = 30
    ctrl.get_transport_state.return_value = "STOPPED"
    return ctrl


@pytest.mark.asyncio
async def test_server_starts_and_stops(tls_files, mock_sonos):
    cert_path, key_path = tls_files
    server = CastServer(
        sonos_controller=mock_sonos,
        device_name="Test",
        cert_path=cert_path,
        key_path=key_path,
        port=0,  # OS picks a free port
    )
    await server.start()
    assert server.is_running
    await server.stop()
    assert not server.is_running


@pytest.mark.asyncio
async def test_ping_pong_over_tls(tls_files, mock_sonos):
    """Connect a TLS client, send PING, verify PONG is received."""
    cert_path, key_path = tls_files

    server = CastServer(
        sonos_controller=mock_sonos,
        device_name="Test",
        cert_path=cert_path,
        key_path=key_path,
        port=0,
    )
    await server.start()
    port = server.port

    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE

    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=client_ctx
        )
        ping = make_message(NS_HEARTBEAT, {"type": "PING"}, "sender-0", "receiver-0")
        writer.write(encode_message(ping))
        await writer.drain()

        # Read the 4-byte length header
        header = await asyncio.wait_for(reader.readexactly(4), timeout=3.0)
        (body_len,) = struct.unpack(">I", header)
        body = await asyncio.wait_for(reader.readexactly(body_len), timeout=3.0)
        response, _ = decode_message(header + body)

        assert json.loads(response.payload_utf8)["type"] == "PONG"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
