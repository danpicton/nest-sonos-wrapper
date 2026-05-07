"""Async TLS server implementing the Cast V2 receiver protocol."""
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Optional

from sonos_cast.bridge import CastBridge
from sonos_cast.protocol import CastMessage, decode_message, encode_message
from sonos_cast.sonos_controller import SonosController

log = logging.getLogger(__name__)

CAST_PORT = 8009
_RECV_CHUNK = 4096


class CastServer:
    def __init__(
        self,
        sonos_controller: SonosController,
        device_name: str,
        cert_path: str,
        key_path: str,
        port: int = CAST_PORT,
    ) -> None:
        self._sonos = sonos_controller
        self._device_name = device_name
        self._cert_path = cert_path
        self._key_path = key_path
        self._port = port
        self._server: Optional[asyncio.AbstractServer] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(certfile=self._cert_path, keyfile=self._key_path)

        self._server = await asyncio.start_server(
            self._handle_client,
            "0.0.0.0",
            self._port,
            ssl=ssl_ctx,
        )
        log.info("Cast server listening on port %d", self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    @property
    def port(self) -> int:
        if self._server:
            sockets = self._server.sockets
            if sockets:
                return sockets[0].getsockname()[1]
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
