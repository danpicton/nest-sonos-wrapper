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
    NS_SETUP,
    NS_SYSTEM,
    DEFAULT_MEDIA_RECEIVER_APP_ID,
)
from sonos_cast.sonos_controller import SonosController

SESSION_ID = "sonos-cast-session"
_TRANSPORT_ID = "sonos-cast-transport"

SendFn = Callable[[CastMessage], Awaitable[None]]


def _mac_from_device_id(device_id: str) -> str:
    """Derive a deterministic MAC-format string from the device id (last 6 hex bytes)."""
    tail = device_id[-12:].rjust(12, "0").upper()
    return ":".join(tail[i : i + 2] for i in range(0, 12, 2))


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
        device_id: str = "00000000000000000000000000000000",
    ) -> None:
        self._sonos = sonos_controller
        self._send = send_fn
        self._device_name = device_name
        self._device_id = device_id
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
        elif ns in (NS_SETUP, NS_SYSTEM):
            await self._handle_setup(msg, payload)

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
    # Setup / system namespace (gms_cast_prober device info probe)
    # ------------------------------------------------------------------

    async def _handle_setup(self, msg: CastMessage, payload: dict) -> None:
        if payload.get("type") == "eureka_info":
            await self._send(
                make_message(
                    msg.namespace,
                    self._eureka_info(payload.get("request_id", 0)),
                    msg.source_id,
                    msg.destination_id,
                )
            )

    def _eureka_info(self, request_id: int) -> dict:
        # gms_cast_prober requests specific dotted paths (e.g. "device_info.ssdp_udn",
        # "build_info.cast_build_revision"). It accepts a superset, so we always send
        # all the fields a real Chromecast Audio would advertise.
        return {
            "type": "eureka_info",
            "request_id": request_id,
            "name": self._device_name,
            "version": 8,
            "device_info": {
                "ssdp_udn": self._device_id,
                "uptime": 0.0,
                "manufacturer": "Google Inc.",
                "model_name": "Chromecast Audio",
                "product_name": "eureka",
                "cloud_device_id": self._device_id,
                "mac_address": _mac_from_device_id(self._device_id),
                "capabilities": {
                    "audio_hdr_supported": False,
                    "audio_surround_mode_supported": False,
                    "ble_supported": False,
                    "bluetooth_audio_sink_supported": False,
                    "bluetooth_audio_source_supported": False,
                    "bluetooth_supported": False,
                    "display_supported": False,
                    "hi_res_audio_supported": False,
                    "remote_ducking_supported": True,
                    "setup_supported": True,
                    "stats_reporting_supported": False,
                },
            },
            "build_info": {
                "build_type": 0,
                "cast_build_revision": "1.56.250548",
                "cast_control_version": 1,
                "preview_channel_state": 0,
                "release_track": "stable-channel",
                "system_build_number": "1.56.250548",
            },
            "multizone": {
                "audio_output_delay": 0,
                "audio_output_delay_hdmi_offset": 0,
                "audio_output_delay_oem": 0,
                "groups": [],
                "dynamic_groups": [],
                "multichannel_status": 0,
                "multizone_state": 0,
                "device_name": self._device_name,
            },
            "opt_in": {
                "opencast": False,
                "preview_channel": False,
                "remote_ducking": True,
                "stats": False,
            },
            "net": {
                "ethernet_connected": False,
                "online": True,
            },
            "audio": {"digital": False},
            "settings": {
                "control_notifications": 1,
                "country": "US",
                "locale": "en-US",
                "system_sound_effects": True,
                "time_format": 1,
                "timezone": "Etc/UTC",
                "wake_on_cast": 1,
            },
            "wifi": {"ssid": ""},
            "detail": {"icon_list": []},
        }

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

        elif msg_type == "GET_APP_AVAILABILITY":
            app_ids = payload.get("appId", [])
            await self._send(
                make_message(
                    NS_RECEIVER,
                    {
                        "type": "APP_AVAILABILITY",
                        "requestId": payload.get("requestId", 0),
                        "availability": {app_id: "APP_AVAILABLE" for app_id in app_ids},
                    },
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
        # Real Chromecast Audio receiver_status always includes `applications`
        # (empty array or list of running apps), `userEq`, and `volume` with the
        # full set of fields. Sparse status messages get rejected by some Cast
        # clients during device validation.
        applications = []
        if app_id:
            applications.append(
                {
                    "appId": app_id,
                    "displayName": "Default Media Receiver",
                    "iconUrl": "",
                    "isIdleScreen": False,
                    "launchedFromCloud": False,
                    "namespaces": [{"name": NS_MEDIA}],
                    "sessionId": SESSION_ID,
                    "statusText": "",
                    "transportId": _TRANSPORT_ID,
                    "universalAppId": app_id,
                }
            )
        status: dict = {
            "applications": applications,
            "userEq": {},
            "volume": {
                "level": await self._sonos.get_volume() / 100,
                "muted": False,
                "stepInterval": 0.05,
                "controlType": "master",
            },
            "isActiveInput": bool(app_id),
            "isStandBy": not app_id,
        }
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
