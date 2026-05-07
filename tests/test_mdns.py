"""Tests for mDNS / Zeroconf advertisement of the Cast receiver."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sonos_cast.mdns import (
    CAST_SERVICE_TYPE,
    CAST_CAPABILITIES_AUDIO,
    build_cast_txt_records,
    CastAdvertiser,
)


def test_service_type():
    assert CAST_SERVICE_TYPE == "_googlecast._tcp.local."


def test_audio_capability_flag():
    # Mirrors real Chromecast Audio: AUDIO_OUT(4) | VIDEO_OUT(1) = 5
    assert CAST_CAPABILITIES_AUDIO == 5


def test_build_txt_records_contains_required_keys():
    records = build_cast_txt_records(
        friendly_name="Sonos Living Room",
        device_id="abcdef123456",
    )
    for key in ("id", "fn", "md", "ca", "ve", "st", "bs"):
        assert key in records, f"missing key: {key}"


def test_build_txt_records_friendly_name():
    records = build_cast_txt_records("My Sonos", "aabbccddeeff")
    assert records["fn"] == "My Sonos"


def test_build_txt_records_capability_is_audio():
    records = build_cast_txt_records("X", "aabbccddeeff")
    assert records["ca"] == str(CAST_CAPABILITIES_AUDIO)


def test_build_txt_records_model_is_chromecast_audio():
    records = build_cast_txt_records("X", "aabbccddeeff")
    assert "Chromecast" in records["md"]


def test_build_txt_records_version_is_05():
    records = build_cast_txt_records("X", "aabbccddeeff")
    assert records["ve"] == "05"


@pytest.mark.asyncio
async def test_cast_advertiser_registers_service_on_start():
    mock_azeroconf = MagicMock()
    mock_azeroconf.async_register_service = AsyncMock()
    mock_azeroconf_class = MagicMock(return_value=mock_azeroconf)
    mock_service_info = MagicMock()
    mock_service_info_class = MagicMock(return_value=mock_service_info)

    with patch("sonos_cast.mdns.AsyncZeroconf", mock_azeroconf_class), \
         patch("sonos_cast.mdns.ServiceInfo", mock_service_info_class):
        advertiser = CastAdvertiser(
            friendly_name="Sonos Test",
            device_id="aabbccddeeff",
            port=8009,
            host_ip="192.168.1.50",
        )
        await advertiser.start()
        mock_azeroconf.async_register_service.assert_called_once_with(mock_service_info)


@pytest.mark.asyncio
async def test_cast_advertiser_unregisters_service_on_stop():
    mock_azeroconf = MagicMock()
    mock_azeroconf.async_register_service = AsyncMock()
    mock_azeroconf.async_unregister_service = AsyncMock()
    mock_azeroconf.async_close = AsyncMock()
    mock_azeroconf_class = MagicMock(return_value=mock_azeroconf)
    mock_service_info = MagicMock()
    mock_service_info_class = MagicMock(return_value=mock_service_info)

    with patch("sonos_cast.mdns.AsyncZeroconf", mock_azeroconf_class), \
         patch("sonos_cast.mdns.ServiceInfo", mock_service_info_class):
        advertiser = CastAdvertiser(
            friendly_name="Sonos Test",
            device_id="aabbccddeeff",
            port=8009,
            host_ip="192.168.1.50",
        )
        await advertiser.start()
        await advertiser.stop()
        mock_azeroconf.async_unregister_service.assert_called_once()
        mock_azeroconf.async_close.assert_called_once()
