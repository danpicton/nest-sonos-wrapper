"""Sonos S2 playback control via SoCo."""
from __future__ import annotations

import asyncio
import functools
import ipaddress
import socket

import requests.exceptions
import soco
from soco import SoCo


class SonosNotFoundError(Exception):
    pass


def _wrap_connection_errors(ip: str, fn):
    """Call fn(), converting network errors into SonosNotFoundError."""
    try:
        return fn()
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        OSError,
        socket.timeout,
    ) as exc:
        raise SonosNotFoundError(
            f"Could not reach Sonos at {ip} — check the IP address and that the "
            f"device is on the same network ({exc})"
        ) from exc


async def _run(fn, *args, **kwargs):
    """Run a blocking SoCo call in the default thread pool so the event loop stays free."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


class SonosController:
    def __init__(self, soco_device: SoCo) -> None:
        self._device = soco_device

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_ip(cls, ip: str) -> "SonosController":
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise SonosNotFoundError(
                f"{ip!r} is not a valid IP address — did you mean something like 192.168.1.100?"
            )
        return cls(SoCo(ip))

    @classmethod
    def from_name(cls, name: str) -> "SonosController":
        devices = soco.discover() or set()
        for dev in devices:
            if dev.player_name == name:
                return cls(dev)
        raise SonosNotFoundError(f"No Sonos device named {name!r} found on the network")

    # ------------------------------------------------------------------
    # Playback (all async — SoCo makes blocking SOAP calls)
    # ------------------------------------------------------------------

    async def play_uri(self, uri: str, title: str = "Cast") -> None:
        # S2 firmware ≥6.4.2 rejects plain http:/https: URIs; force_radio rewrites
        # them to the x-rincon-mp3radio: scheme that Sonos accepts for live streams.
        await _run(self._device.play_uri, uri, title=title, force_radio=True)

    async def play(self) -> None:
        await _run(self._device.play)

    async def pause(self) -> None:
        await _run(self._device.pause)

    async def stop(self) -> None:
        await _run(self._device.stop)

    # ------------------------------------------------------------------
    # Volume / mute
    # ------------------------------------------------------------------

    async def set_volume(self, level: int) -> None:
        clamped = max(0, min(100, level))
        await _run(lambda: setattr(self._device, "volume", clamped))

    async def get_volume(self) -> int:
        return int(await _run(lambda: self._device.volume))

    async def set_mute(self, muted: bool) -> None:
        await _run(lambda: setattr(self._device, "mute", muted))

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    async def get_transport_state(self) -> str:
        info = await _run(self._device.get_current_transport_info)
        return info.get("current_transport_state", "STOPPED")

    @property
    def player_name(self) -> str:
        return _wrap_connection_errors(
            self._device.ip_address, lambda: self._device.player_name
        )

    @property
    def ip_address(self) -> str:
        return self._device.ip_address
