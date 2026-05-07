"""Tests for mDNS / Zeroconf advertisement of the Cast receiver."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sonos_cast.mdns import (
    CAST_SERVICE_TYPE,
    CAST_CAPABILITIES_AUDIO,
    build_cast_txt_records,
    CastAdvertiser,
    _dns_safe,
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


# ---------------------------------------------------------------------------
# _dns_safe
# ---------------------------------------------------------------------------


def test_dns_safe_removes_apostrophe():
    assert "'" not in _dns_safe("Dan's Office Sonos")


def test_dns_safe_removes_spaces():
    assert " " not in _dns_safe("Living Room")


def test_dns_safe_keeps_hyphens_and_alphanumeric():
    assert _dns_safe("My-Sonos1") == "My-Sonos1"


def test_dns_safe_fallback_on_empty_result():
    assert _dns_safe("'") == "sonos-cast"


# ---------------------------------------------------------------------------
# CastAdvertiser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cast_advertiser_uses_device_id_as_instance_name():
    """Service instance name must use device_id, not the friendly name."""
    mock_azeroconf = MagicMock()
    mock_azeroconf.async_register_service = AsyncMock()

    with patch("sonos_cast.mdns.AsyncZeroconf", MagicMock(return_value=mock_azeroconf)), \
         patch("sonos_cast.mdns.ServiceInfo", lambda *a, **kw: _make_info(*a, **kw)):
        advertiser = CastAdvertiser(
            friendly_name="Dan's Office Sonos",
            device_id="aabbccddeeff",
            port=8009,
            host_ip="192.168.1.50",
        )
        await advertiser.start()

    info_arg = mock_azeroconf.async_register_service.call_args[0][0]
    assert info_arg.name.startswith("aabbccddeeff.")


def _make_info(type_, name, **kwargs):
    m = MagicMock()
    m.name = name
    return m


@pytest.mark.asyncio
async def test_cast_advertiser_server_hostname_is_dns_safe():
    """Server hostname must not contain apostrophes or spaces."""
    mock_azeroconf = MagicMock()
    mock_azeroconf.async_register_service = AsyncMock()

    with patch("sonos_cast.mdns.AsyncZeroconf", MagicMock(return_value=mock_azeroconf)), \
         patch("sonos_cast.mdns.ServiceInfo", lambda *a, **kw: _make_info_with_server(*a, **kw)):
        advertiser = CastAdvertiser(
            friendly_name="Dan's Office Sonos",
            device_id="aabbccddeeff",
            port=8009,
            host_ip="192.168.1.50",
        )
        await advertiser.start()

    info_arg = mock_azeroconf.async_register_service.call_args[0][0]
    assert "'" not in info_arg.server
    assert " " not in info_arg.server


def _make_info_with_server(type_, name, server="", **kwargs):
    m = MagicMock()
    m.name = name
    m.server = server
    return m


@pytest.mark.asyncio
async def test_cast_advertiser_unregisters_service_on_stop():
    mock_azeroconf = MagicMock()
    mock_azeroconf.async_register_service = AsyncMock()
    mock_azeroconf.async_unregister_service = AsyncMock()
    mock_azeroconf.async_close = AsyncMock()

    with patch("sonos_cast.mdns.AsyncZeroconf", MagicMock(return_value=mock_azeroconf)), \
         patch("sonos_cast.mdns.ServiceInfo", MagicMock()):
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
