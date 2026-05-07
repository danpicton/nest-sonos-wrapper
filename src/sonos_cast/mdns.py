"""mDNS advertisement: makes the Cast receiver visible on the local network."""
import socket
from typing import Optional

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

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
        service_name = f"{self._friendly_name}.{CAST_SERVICE_TYPE}"
        self._info = ServiceInfo(
            CAST_SERVICE_TYPE,
            service_name,
            addresses=[socket.inet_aton(self._host_ip)],
            port=self._port,
            properties=txt,
            server=f"{self._friendly_name.replace(' ', '-')}.local.",
        )
        self._azeroconf = AsyncZeroconf()
        await self._azeroconf.async_register_service(self._info)

    async def stop(self) -> None:
        if self._azeroconf and self._info:
            await self._azeroconf.async_unregister_service(self._info)
            await self._azeroconf.async_close()
