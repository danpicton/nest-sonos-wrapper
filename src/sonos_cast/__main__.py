"""CLI entry point: sonos-cast --help"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys

from sonos_cast.cast_server import CastServer, CAST_PORT
from sonos_cast.mdns import CastAdvertiser
from sonos_cast.sonos_controller import SonosController, SonosNotFoundError


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _device_id(ip: str) -> str:
    # Derive a stable pseudo-MAC from the host IP so the mDNS id is consistent.
    octets = [int(x) for x in ip.split(".")]
    return "".join(f"{o:02x}" for o in [0xDE, 0xCA, 0xFF] + octets[-3:])


async def _run(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("sonos_cast")

    log.info("Connecting to Sonos…")
    try:
        if args.sonos_ip:
            sonos = SonosController.from_ip(args.sonos_ip)
        else:
            sonos = SonosController.from_name(args.sonos_name)
    except SonosNotFoundError as exc:
        log.error("%s", exc)
        sys.exit(1)
    log.info("Sonos: %s @ %s", sonos.player_name, sonos.ip_address)

    host_ip = args.host_ip or _local_ip()
    device_id = _device_id(host_ip)

    server = CastServer(
        sonos_controller=sonos,
        device_name=args.name,
        port=args.port,
    )
    await server.start()
    log.info("Cast receiver listening on %s:%d", host_ip, server.port)

    advertiser = CastAdvertiser(
        friendly_name=args.name,
        device_id=device_id,
        port=server.port,
        host_ip=host_ip,
    )
    advertiser.start()
    log.info(
        "mDNS advertised as '%s' — open Cast in Chrome or Android to see it",
        args.name,
    )

    try:
        await asyncio.Event().wait()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        advertiser.stop()
        await server.stop()
        log.info("Stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expose a Sonos S2 speaker as a Google Cast audio receiver."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sonos-ip", metavar="IP", help="Sonos device IP address")
    group.add_argument(
        "--sonos-name", metavar="NAME", help="Sonos player name (e.g. 'Living Room')"
    )
    parser.add_argument(
        "--name", default="Sonos Cast", help="Friendly name shown in Cast menus"
    )
    parser.add_argument(
        "--port", type=int, default=CAST_PORT, help=f"TCP port (default {CAST_PORT})"
    )
    parser.add_argument(
        "--host-ip", metavar="IP", help="Override the local IP advertised via mDNS"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
