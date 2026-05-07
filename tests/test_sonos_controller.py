"""Tests for SoCo-backed Sonos controller (mocked SoCo)."""
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import requests.exceptions

from sonos_cast.sonos_controller import SonosController, SonosNotFoundError


@pytest.fixture
def mock_soco():
    soco = MagicMock()
    soco.player_name = "Sonos Living Room"
    soco.ip_address = "192.168.1.100"
    volume_pm = PropertyMock(return_value=50)
    type(soco).volume = volume_pm
    soco._volume_pm = volume_pm  # keep a direct ref for setter assertions
    return soco


@pytest.fixture
def controller(mock_soco):
    return SonosController(soco_device=mock_soco)


# ---------------------------------------------------------------------------
# Construction / discovery
# ---------------------------------------------------------------------------


def test_from_ip_creates_controller():
    mock_device = MagicMock()
    mock_device.player_name = "Test"
    with patch("sonos_cast.sonos_controller.SoCo", return_value=mock_device):
        ctrl = SonosController.from_ip("192.168.1.100")
    assert ctrl is not None


def test_from_ip_rejects_invalid_ip():
    with pytest.raises(SonosNotFoundError, match="not a valid IP address"):
        SonosController.from_ip("192.168.024")


def test_from_ip_rejects_hostname_string():
    with pytest.raises(SonosNotFoundError, match="not a valid IP address"):
        SonosController.from_ip("sonos.local")


def test_player_name_wraps_connection_error(mock_soco):
    mock_soco.ip_address = "192.168.1.100"
    type(mock_soco).player_name = PropertyMock(
        side_effect=requests.exceptions.ConnectionError("No route to host")
    )
    ctrl = SonosController(soco_device=mock_soco)
    with pytest.raises(SonosNotFoundError, match="Could not reach Sonos"):
        _ = ctrl.player_name


def test_from_name_raises_when_not_found():
    with patch("sonos_cast.sonos_controller.soco.discover", return_value=set()):
        with pytest.raises(SonosNotFoundError):
            SonosController.from_name("Nonexistent Speaker")


def test_from_name_finds_matching_device():
    dev = MagicMock()
    dev.player_name = "Living Room"
    with patch("sonos_cast.sonos_controller.soco.discover", return_value={dev}):
        ctrl = SonosController.from_name("Living Room")
    assert ctrl is not None


# ---------------------------------------------------------------------------
# Playback control (async — SoCo calls run in executor)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_uri_calls_soco_play_uri(controller, mock_soco):
    await controller.play_uri("http://example.com/stream.mp3")
    mock_soco.play_uri.assert_called_once_with(
        "http://example.com/stream.mp3", title="Cast", force_radio=True
    )


@pytest.mark.asyncio
async def test_pause_calls_soco_pause(controller, mock_soco):
    await controller.pause()
    mock_soco.pause.assert_called_once()


@pytest.mark.asyncio
async def test_play_calls_soco_play(controller, mock_soco):
    await controller.play()
    mock_soco.play.assert_called_once()


@pytest.mark.asyncio
async def test_stop_calls_soco_stop(controller, mock_soco):
    await controller.stop()
    mock_soco.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_volume_clamps_to_100(controller, mock_soco):
    await controller.set_volume(150)
    # PropertyMock records setter calls as mock(value); check clamping to 100
    mock_soco._volume_pm.assert_any_call(100)


@pytest.mark.asyncio
async def test_set_volume_clamps_to_0(controller, mock_soco):
    await controller.set_volume(-5)
    mock_soco._volume_pm.assert_any_call(0)


@pytest.mark.asyncio
async def test_get_volume_returns_soco_volume(controller, mock_soco):
    vol = await controller.get_volume()
    assert vol == 50


# ---------------------------------------------------------------------------
# Mute / transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mute_delegates_to_soco(controller, mock_soco):
    await controller.set_mute(True)
    assert mock_soco.mute == True


@pytest.mark.asyncio
async def test_get_transport_state(controller, mock_soco):
    mock_soco.get_current_transport_info.return_value = {
        "current_transport_state": "PLAYING"
    }
    state = await controller.get_transport_state()
    assert state == "PLAYING"
