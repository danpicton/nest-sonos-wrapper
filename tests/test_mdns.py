"""Tests for mDNS / Zeroconf advertisement of the Cast receiver."""
import uuid
from unittest.mock import MagicMock, patch

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
    # Audio-out = 4, multizone = 4096; combined = 4100
    assert CAST_CAPABILITIES_AUDIO == 4100


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


def test_cast_advertiser_registers_service_on_start():
    mock_zeroconf = MagicMock()
    mock_zeroconf_class = MagicMock(return_value=mock_zeroconf)
    mock_service_info = MagicMock()
    mock_service_info_class = MagicMock(return_value=mock_service_info)

    with patch("sonos_cast.mdns.Zeroconf", mock_zeroconf_class), \
         patch("sonos_cast.mdns.ServiceInfo", mock_service_info_class):
        advertiser = CastAdvertiser(
            friendly_name="Sonos Test",
            device_id="aabbccddeeff",
            port=8009,
            host_ip="192.168.1.50",
        )
        advertiser.start()
        mock_zeroconf.register_service.assert_called_once_with(mock_service_info)


def test_cast_advertiser_unregisters_service_on_stop():
    mock_zeroconf = MagicMock()
    mock_zeroconf_class = MagicMock(return_value=mock_zeroconf)
    mock_service_info = MagicMock()
    mock_service_info_class = MagicMock(return_value=mock_service_info)

    with patch("sonos_cast.mdns.Zeroconf", mock_zeroconf_class), \
         patch("sonos_cast.mdns.ServiceInfo", mock_service_info_class):
        advertiser = CastAdvertiser(
            friendly_name="Sonos Test",
            device_id="aabbccddeeff",
            port=8009,
            host_ip="192.168.1.50",
        )
        advertiser.start()
        advertiser.stop()
        mock_zeroconf.unregister_service.assert_called_once()
        mock_zeroconf.close.assert_called_once()
