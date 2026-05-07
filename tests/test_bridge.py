"""Tests for the Cast→Sonos bridge message handler."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from sonos_cast.protocol import (
    CastMessage,
    NS_HEARTBEAT,
    NS_CONNECTION,
    NS_RECEIVER,
    NS_MEDIA,
    NS_AUTH,
    NS_SETUP,
    NS_SYSTEM,
)
from sonos_cast.bridge import CastBridge, SESSION_ID


@pytest.fixture
def sonos():
    ctrl = AsyncMock()
    ctrl.get_volume.return_value = 50
    ctrl.get_transport_state.return_value = "STOPPED"
    return ctrl


@pytest.fixture
def send():
    return AsyncMock()


@pytest.fixture
def bridge(sonos, send):
    return CastBridge(sonos_controller=sonos, send_fn=send, device_name="Test Sonos")


def msg(namespace: str, payload: dict, src: str = "sender-0", dst: str = "receiver-0") -> CastMessage:
    return CastMessage(
        source_id=src,
        destination_id=dst,
        namespace=namespace,
        payload_utf8=json.dumps(payload),
    )


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_replies_with_pong(bridge, send):
    await bridge.handle(msg(NS_HEARTBEAT, {"type": "PING"}))
    sent = send.call_args[0][0]
    assert json.loads(sent.payload_utf8)["type"] == "PONG"
    assert sent.namespace == NS_HEARTBEAT


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_is_acknowledged_silently(bridge, send):
    await bridge.handle(msg(NS_CONNECTION, {"type": "CONNECT"}))
    send.assert_not_called()


@pytest.mark.asyncio
async def test_close_sets_closed_flag(bridge):
    await bridge.handle(msg(NS_CONNECTION, {"type": "CLOSE"}))
    assert bridge.closed


# ---------------------------------------------------------------------------
# Receiver namespace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_replies_with_receiver_status(bridge, send):
    await bridge.handle(msg(NS_RECEIVER, {"type": "GET_STATUS", "requestId": 1}))
    sent = send.call_args[0][0]
    payload = json.loads(sent.payload_utf8)
    assert payload["type"] == "RECEIVER_STATUS"
    assert payload["requestId"] == 1
    assert "status" in payload


@pytest.mark.asyncio
async def test_launch_replies_with_receiver_status_with_app(bridge, send):
    await bridge.handle(
        msg(NS_RECEIVER, {"type": "LAUNCH", "appId": "CC1AD845", "requestId": 2})
    )
    sent = send.call_args[0][0]
    payload = json.loads(sent.payload_utf8)
    assert payload["type"] == "RECEIVER_STATUS"
    apps = payload["status"]["applications"]
    assert len(apps) == 1
    assert apps[0]["appId"] == "CC1AD845"


# ---------------------------------------------------------------------------
# Media namespace — LOAD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_calls_play_uri_on_sonos(bridge, sonos, send):
    await bridge.handle(
        msg(
            NS_MEDIA,
            {
                "type": "LOAD",
                "requestId": 3,
                "media": {
                    "contentId": "http://example.com/stream.mp3",
                    "contentType": "audio/mpeg",
                    "streamType": "LIVE",
                },
                "autoplay": True,
                "currentTime": 0,
            },
        )
    )
    sonos.play_uri.assert_called_once_with("http://example.com/stream.mp3", title="Cast")


@pytest.mark.asyncio
async def test_load_replies_with_media_status(bridge, send):
    await bridge.handle(
        msg(
            NS_MEDIA,
            {
                "type": "LOAD",
                "requestId": 3,
                "media": {"contentId": "http://example.com/s.mp3", "streamType": "LIVE"},
                "autoplay": True,
            },
        )
    )
    sent = send.call_args[0][0]
    payload = json.loads(sent.payload_utf8)
    assert payload["type"] == "MEDIA_STATUS"
    assert len(payload["status"]) == 1
    assert payload["status"][0]["playerState"] == "PLAYING"


# ---------------------------------------------------------------------------
# Media namespace — transport controls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_calls_sonos_pause(bridge, sonos):
    await bridge.handle(msg(NS_MEDIA, {"type": "PAUSE", "mediaSessionId": 1, "requestId": 4}))
    sonos.pause.assert_called_once()


@pytest.mark.asyncio
async def test_play_calls_sonos_play(bridge, sonos):
    await bridge.handle(msg(NS_MEDIA, {"type": "PLAY", "mediaSessionId": 1, "requestId": 5}))
    sonos.play.assert_called_once()


@pytest.mark.asyncio
async def test_stop_calls_sonos_stop(bridge, sonos):
    await bridge.handle(msg(NS_MEDIA, {"type": "STOP", "mediaSessionId": 1, "requestId": 6}))
    sonos.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_volume_calls_sonos_set_volume(bridge, sonos):
    await bridge.handle(
        msg(
            NS_RECEIVER,
            {"type": "SET_VOLUME", "volume": {"level": 0.75, "muted": False}, "requestId": 7},
        )
    )
    sonos.set_volume.assert_called_once_with(75)


@pytest.mark.asyncio
async def test_set_volume_muted_calls_set_mute(bridge, sonos):
    await bridge.handle(
        msg(
            NS_RECEIVER,
            {"type": "SET_VOLUME", "volume": {"level": 0.5, "muted": True}, "requestId": 8},
        )
    )
    sonos.set_mute.assert_called_once_with(True)


# ---------------------------------------------------------------------------
# GET_APP_AVAILABILITY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_app_availability_replies_all_available(bridge, send):
    await bridge.handle(
        msg(
            NS_RECEIVER,
            {"type": "GET_APP_AVAILABILITY", "appId": ["CC1AD845", "2FA4D21B"], "requestId": 9},
        )
    )
    sent = send.call_args[0][0]
    payload = json.loads(sent.payload_utf8)
    assert payload["type"] == "APP_AVAILABILITY"
    assert payload["requestId"] == 9
    assert payload["availability"]["CC1AD845"] == "APP_AVAILABLE"
    assert payload["availability"]["2FA4D21B"] == "APP_AVAILABLE"


# ---------------------------------------------------------------------------
# Setup / system namespace (eureka_info)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eureka_info_on_setup_ns_replies(bridge, send):
    await bridge.handle(
        msg(NS_SETUP, {"type": "eureka_info", "request_id": 3, "data": {"params": ["name"]}})
    )
    sent = send.call_args[0][0]
    payload = json.loads(sent.payload_utf8)
    assert payload["type"] == "eureka_info"
    assert payload["request_id"] == 3
    assert "name" in payload


@pytest.mark.asyncio
async def test_eureka_info_on_system_ns_replies(bridge, send):
    await bridge.handle(
        msg(NS_SYSTEM, {"type": "eureka_info", "request_id": 5, "data": {}})
    )
    sent = send.call_args[0][0]
    payload = json.loads(sent.payload_utf8)
    assert payload["type"] == "eureka_info"
    assert payload["request_id"] == 5
