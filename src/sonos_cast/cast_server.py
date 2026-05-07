"""Async TLS server implementing the Cast V2 receiver protocol."""
from __future__ import annotations

import asyncio
import logging
import os
import ssl
import struct
import tempfile
import time
from typing import Optional

from sonos_cast import cast_channel_pb2 as pb
from sonos_cast.bridge import CastBridge
from sonos_cast.cast_auth import build_auth_response, build_tls_cert_pem
from sonos_cast.cert import write_cert_files
from sonos_cast.protocol import (
    CastMessage,
    NS_AUTH,
    decode_message,
    encode_message,
)
from sonos_cast.sonos_controller import SonosController

log = logging.getLogger(__name__)

CAST_PORT = 8009
_RECV_CHUNK = 4096


class CastServer:
    def __init__(
        self,
        sonos_controller: SonosController,
        device_name: str,
        port: int = CAST_PORT,
    ) -> None:
        self._sonos = sonos_controller
        self._device_name = device_name
        self._port = port
        self._server: Optional[asyncio.AbstractServer] = None
        self._tmpdir: Optional[tempfile.TemporaryDirectory] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        cert_pem, key_pem = build_tls_cert_pem(int(time.time()))
        cert_path = os.path.join(self._tmpdir.name, "cert.pem")
        key_path  = os.path.join(self._tmpdir.name, "key.pem")
        write_cert_files(cert_pem, key_pem, cert_path, key_path)

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

        self._server = await asyncio.start_server(
            self._handle_client,
            "0.0.0.0",
            self._port,
            ssl=ssl_ctx,
        )
        log.info("Cast server listening on port %d (shanocast auth enabled)", self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    @property
    def port(self) -> int:
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    # ------------------------------------------------------------------
    # Connection handler
    # ------------------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        log.debug("Cast client connected: %s", peer)

        async def send(msg: CastMessage) -> None:
            log.debug("<<< [%s] %s", msg.namespace.split(".")[-1], msg.payload_utf8[:120] if msg.payload_utf8 else "(binary)")
            try:
                writer.write(encode_message(msg))
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass

        bridge = CastBridge(
            sonos_controller=self._sonos,
            send_fn=send,
            device_name=self._device_name,
        )

        buf = b""
        try:
            while not bridge.closed:
                chunk = await reader.read(_RECV_CHUNK)
                if not chunk:
                    break
                buf += chunk
                while True:
                    result = decode_message(buf)
                    if result is None:
                        break
                    msg, consumed = result
                    buf = buf[consumed:]
                    if msg.namespace == NS_AUTH and msg.payload_binary:
                        log.debug(">>> AUTH (binary %d bytes)", len(msg.payload_binary))
                        await self._handle_auth(msg, writer)
                    else:
                        log.debug(">>> [%s] %s", msg.namespace.split(".")[-1], msg.payload_utf8[:120] if msg.payload_utf8 else "(binary)")
                        await bridge.handle(msg)
        except (asyncio.IncompleteReadError, ConnectionResetError, ssl.SSLError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            log.debug("Cast client disconnected: %s", peer)

    # ------------------------------------------------------------------
    # Shanocast auth
    # ------------------------------------------------------------------

    async def _handle_auth(
        self, msg: CastMessage, writer: asyncio.StreamWriter
    ) -> None:
        challenge = pb.DeviceAuthMessage()
        challenge.ParseFromString(msg.payload_binary)
        auth_resp = build_auth_response(challenge)

        proto = pb.CastMessage()
        proto.protocol_version = pb.CastMessage.CASTV2_1_0
        proto.source_id = "receiver-0"
        proto.destination_id = msg.source_id
        proto.namespace = NS_AUTH
        proto.payload_type = pb.CastMessage.BINARY
        proto.payload_binary = auth_resp.SerializeToString()

        body = proto.SerializeToString()
        try:
            writer.write(struct.pack(">I", len(body)) + body)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
