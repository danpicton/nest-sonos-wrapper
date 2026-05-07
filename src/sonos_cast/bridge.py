"""Cast→Sonos bridge: translates Cast protocol messages into Sonos commands."""
from __future__ import annotations

import json
import uuid
from typing import Awaitable, Callable

from sonos_cast.protocol import (
    CastMessage,
    make_message,
    NS_HEARTBEAT,
    NS_CONNECTION,
    NS_RECEIVER,
    NS_MEDIA,
    NS_AUTH,
    DEFAULT_MEDIA_RECEIVER_APP_ID,
)
from sonos_cast.sonos_controller import SonosController

SESSION_ID = "sonos-cast-session"
_TRANSPORT_ID = "sonos-cast-transport"

SendFn = Callable[[CastMessage], Awaitable[None]]


class CastBridge:
    """
    Stateful per-connection handler.

    Receives decoded CastMessages and drives both Cast replies (via send_fn)
    and Sonos commands (via sonos_controller).
    """

    def __init__(
        self,
        sonos_controller: SonosController,
        send_fn: SendFn,
        device_name: str = "Sonos",
    ) -> None:
        self._sonos = sonos_controller
        self._send = send_fn
        self._device_name = device_name
        self._media_session_id: int = 1
        self._current_content_id: str = ""
        self._player_state: str = "IDLE"
        self.closed: bool = False

    async def handle(self, msg: CastMessage) -> None:
        ns = msg.namespace
        payload = json.loads(msg.payload_utf8) if msg.payload_utf8 else {}

        if ns == NS_AUTH:
            return  # handled at server level before reaching the bridge

        if ns == NS_HEARTBEAT:
            await self._handle_heartbeat(msg, payload)
        elif ns == NS_CONNECTION:
            self._handle_connection(payload)
        elif ns == NS_RECEIVER:
            await self._handle_receiver(msg, payload)
        elif ns == NS_MEDIA:
            await self._handle_media(msg, payload)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _handle_heartbeat(self, msg: CastMessage, payload: dict) -> None:
        if payload.get("type") == "PING":
            await self._send(
                make_message(NS_HEARTBEAT, {"type": "PONG"}, msg.source_id, msg.destination_id)
            )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _handle_connection(self, payload: dict) -> None:
        if payload.get("type") == "CLOSE":
            self.closed = True

    # ------------------------------------------------------------------
    # Receiver namespace
    # ------------------------------------------------------------------

    async def _handle_receiver(self, msg: CastMessage, payload: dict) -> None:
        msg_type = payload.get("type")

        if msg_type == "GET_STATUS":
            await self._send(
                make_message(
                    NS_RECEIVER,
                    await self._receiver_status(payload.get("requestId", 0)),
                    msg.source_id,
                    msg.destination_id,
                )
            )

        elif msg_type == "LAUNCH":
            await self._send(
                make_message(
                    NS_RECEIVER,
                    await self._receiver_status(
                        payload.get("requestId", 0),
                        app_id=payload.get("appId", DEFAULT_MEDIA_RECEIVER_APP_ID),
                    ),
                    msg.source_id,
                    msg.destination_id,
                )
            )

        elif msg_type == "SET_VOLUME":
            vol_info = payload.get("volume", {})
            if "level" in vol_info:
                await self._sonos.set_volume(round(vol_info["level"] * 100))
            if "muted" in vol_info:
                await self._sonos.set_mute(vol_info["muted"])

    async def _receiver_status(self, request_id: int, app_id: str | None = None) -> dict:
        status: dict = {
            "volume": {
                "level": await self._sonos.get_volume() / 100,
                "muted": False,
                "stepInterval": 0.05,
                "controlType": "master",
            },
            "isActiveInput": True,
            "isStandBy": False,
        }
        if app_id:
            status["applications"] = [
                {
                    "appId": app_id,
                    "displayName": "Default Media Receiver",
                    "namespaces": [{"name": NS_MEDIA}],
                    "sessionId": SESSION_ID,
                    "statusText": "",
                    "transportId": _TRANSPORT_ID,
                }
            ]
        return {"type": "RECEIVER_STATUS", "requestId": request_id, "status": status}

    # ------------------------------------------------------------------
    # Media namespace
    # ------------------------------------------------------------------

    async def _handle_media(self, msg: CastMessage, payload: dict) -> None:
        msg_type = payload.get("type")

        if msg_type == "LOAD":
            media = payload.get("media", {})
            content_id = media.get("contentId", "")
            self._current_content_id = content_id
            self._player_state = "PLAYING"
            self._media_session_id = int(uuid.uuid4()) & 0xFFFF or 1
            await self._sonos.play_uri(content_id, title="Cast")
            await self._send(
                make_message(
                    NS_MEDIA,
                    await self._media_status(payload.get("requestId", 0)),
                    msg.source_id,
                    msg.destination_id,
                )
            )

        elif msg_type == "PAUSE":
            self._player_state = "PAUSED"
            await self._sonos.pause()
            await self._send(
                make_message(
                    NS_MEDIA,
                    await self._media_status(payload.get("requestId", 0)),
                    msg.source_id,
                    msg.destination_id,
                )
            )

        elif msg_type == "PLAY":
            self._player_state = "PLAYING"
            await self._sonos.play()
            await self._send(
                make_message(
                    NS_MEDIA,
                    await self._media_status(payload.get("requestId", 0)),
                    msg.source_id,
                    msg.destination_id,
                )
            )

        elif msg_type == "STOP":
            self._player_state = "IDLE"
            await self._sonos.stop()
            await self._send(
                make_message(
                    NS_MEDIA,
                    await self._media_status(payload.get("requestId", 0)),
                    msg.source_id,
                    msg.destination_id,
                )
            )

    async def _media_status(self, request_id: int) -> dict:
        return {
            "type": "MEDIA_STATUS",
            "requestId": request_id,
            "status": [
                {
                    "mediaSessionId": self._media_session_id,
                    "playerState": self._player_state,
                    "idleReason": None if self._player_state != "IDLE" else "FINISHED",
                    "media": {
                        "contentId": self._current_content_id,
                        "streamType": "LIVE",
                        "contentType": "audio/mpeg",
                    },
                    "volume": {
                        "level": await self._sonos.get_volume() / 100,
                        "muted": False,
                    },
                    "currentTime": 0,
                    "supportedMediaCommands": 15,
                }
            ],
        }
