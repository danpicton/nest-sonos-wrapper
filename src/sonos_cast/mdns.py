"""mDNS advertisement: makes the Cast receiver visible on the local network."""
import logging
import re
import socket
from typing import Optional

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

log = logging.getLogger(__name__)

CAST_SERVICE_TYPE = "_googlecast._tcp.local."

# Matches the real Chromecast Audio value: AUDIO_OUT(4) | VIDEO_OUT(1).
# pychromecast uses model name (md=) not ca= to classify device type, but
# Google Home app uses this field, so mirror what a real Chromecast Audio sends.
CAST_CAPABILITIES_AUDIO = 5


def build_cast_txt_records(friendly_name: str, device_id: str) -> dict[str, str]:
    return {
        "id": device_id,
        "fn": friendly_name,
        "md": "Chromecast Audio",
        "ca": str(CAST_CAPABILITIES_AUDIO),
        "ve": "05",
        "st": "0",
        "bs": device_id,
        "rs": "",
        "ic": "/setup/icon.png",
    }


def _dns_safe(name: str) -> str:
    """Strip characters that are not valid in a DNS label (RFC 1123)."""
    sanitised = re.sub(r"[^a-zA-Z0-9-]", "-", name).strip("-")
    return sanitised or "sonos-cast"


class CastAdvertiser:
    def __init__(
        self,
        friendly_name: str,
        device_id: str,
        port: int,
        host_ip: str,
    ) -> None:
        self._friendly_name = friendly_name
        self._device_id = device_id
        self._port = port
        self._host_ip = host_ip
        self._azeroconf: Optional[AsyncZeroconf] = None
        self._info: Optional[ServiceInfo] = None

    async def start(self) -> None:
        txt = build_cast_txt_records(self._friendly_name, self._device_id)

        # Service instance name: use the device ID so it contains no special chars
        # and is stable across restarts.  The human-readable name is in the fn= TXT record.
        service_name = f"{self._device_id}.{CAST_SERVICE_TYPE}"

        # server must be a valid DNS hostname — strip apostrophes, spaces, etc.
        server = f"{_dns_safe(self._friendly_name)}.local."

        self._info = ServiceInfo(
            CAST_SERVICE_TYPE,
            service_name,
            addresses=[socket.inet_aton(self._host_ip)],
            port=self._port,
            properties=txt,
            server=server,
        )
        self._azeroconf = AsyncZeroconf()
        await self._azeroconf.async_register_service(self._info)
        log.info(
            "mDNS: registered %s  server=%s  addr=%s:%d",
            service_name,
            server,
            self._host_ip,
            self._port,
        )

    async def stop(self) -> None:
        if self._azeroconf and self._info:
            await self._azeroconf.async_unregister_service(self._info)
            await self._azeroconf.async_close()
