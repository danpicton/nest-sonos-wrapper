"""Sonos S2 playback control via SoCo."""
from __future__ import annotations

from typing import Optional

import soco
from soco import SoCo


class SonosNotFoundError(Exception):
    pass


class SonosController:
    def __init__(self, soco_device: SoCo) -> None:
        self._device = soco_device

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_ip(cls, ip: str) -> "SonosController":
        return cls(SoCo(ip))

    @classmethod
    def from_name(cls, name: str) -> "SonosController":
        devices = soco.discover() or set()
        for dev in devices:
            if dev.player_name == name:
                return cls(dev)
        raise SonosNotFoundError(f"No Sonos device named {name!r} found on the network")

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def play_uri(self, uri: str, title: str = "Cast") -> None:
        self._device.play_uri(uri, title=title)

    def play(self) -> None:
        self._device.play()

    def pause(self) -> None:
        self._device.pause()

    def stop(self) -> None:
        self._device.stop()

    # ------------------------------------------------------------------
    # Volume / mute
    # ------------------------------------------------------------------

    def set_volume(self, level: int) -> None:
        self._device.volume = max(0, min(100, level))

    def get_volume(self) -> int:
        return int(self._device.volume)

    def set_mute(self, muted: bool) -> None:
        self._device.mute = muted

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def get_transport_state(self) -> str:
        info = self._device.get_current_transport_info()
        return info.get("current_transport_state", "STOPPED")

    @property
    def player_name(self) -> str:
        return self._device.player_name

    @property
    def ip_address(self) -> str:
        return self._device.ip_address
