"""mDNS advertisement: makes the Cast receiver visible on the local network."""
import socket
from typing import Optional

from zeroconf import ServiceInfo, Zeroconf

CAST_SERVICE_TYPE = "_googlecast._tcp.local."

# Bitmask values: AUDIO_OUT=4, MULTIZONE=4096
CAST_CAPABILITIES_AUDIO = 4100


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
        self._zeroconf: Optional[Zeroconf] = None
        self._info: Optional[ServiceInfo] = None

    def start(self) -> None:
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
        self._zeroconf = Zeroconf()
        self._zeroconf.register_service(self._info)

    def stop(self) -> None:
        if self._zeroconf and self._info:
            self._zeroconf.unregister_service(self._info)
            self._zeroconf.close()
