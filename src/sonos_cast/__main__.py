"""CLI entry point: sonos-cast --help"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys

from sonos_cast.cast_server import CastServer, CAST_PORT
from sonos_cast.mdns import CastAdvertiser, CAST_SERVICE_TYPE
from sonos_cast.sonos_controller import SonosController, SonosNotFoundError


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _device_id(ip: str) -> str:
    # 32-char hex UUID required by the Cast SDK (16 bytes: fixed prefix + all 4 IP octets padded).
    octets = [int(x) for x in ip.split(".")]
    raw = bytes([0xDE, 0xCA, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00,
                 0x00, 0x00, 0x00, 0x00] + octets)
    return raw.hex()


# ---------------------------------------------------------------------------
# Browse subcommand — diagnostic: list Cast devices visible on the LAN
# ---------------------------------------------------------------------------

async def _browse(timeout: float = 5.0) -> None:
    import socket as _socket
    from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

    found: list[str] = []

    def _on_change(zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name)
        if info:
            addrs = ", ".join(_socket.inet_ntoa(a) for a in info.addresses)
            props = {k.decode(): v.decode() if isinstance(v, bytes) else v
                     for k, v in info.properties.items()}
            fn = props.get("fn", "(unknown)")
            found.append(f"  {fn!r:30s}  {addrs}:{info.port}  id={props.get('id','?')}")
        else:
            found.append(f"  {name}")

    zc = Zeroconf()
    browser = ServiceBrowser(zc, CAST_SERVICE_TYPE, handlers=[_on_change])
    print(f"Scanning for {CAST_SERVICE_TYPE} for {timeout:.0f}s …")
    await asyncio.sleep(timeout)
    zc.close()

    if found:
        print(f"Found {len(found)} Cast device(s):")
        for line in found:
            print(line)
    else:
        print("No Cast devices found. Check that the service is running and on the same subnet.")


# ---------------------------------------------------------------------------
# Main server run
# ---------------------------------------------------------------------------

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
        device_id=device_id,
    )
    await server.start()
    log.info("Cast receiver listening on %s:%d", host_ip, server.port)

    advertiser = CastAdvertiser(
        friendly_name=args.name,
        device_id=device_id,
        port=server.port,
        host_ip=host_ip,
    )
    await advertiser.start()
    log.info(
        "mDNS advertised as '%s' — open Cast in Chrome or Android to see it",
        args.name,
    )

    try:
        await asyncio.Event().wait()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await advertiser.stop()
        await server.stop()
        log.info("Stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expose a Sonos S2 speaker as a Google Cast audio receiver."
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- run (default) ---
    run_p = subparsers.add_parser("run", help="Start the Cast receiver (default)")
    group = run_p.add_mutually_exclusive_group(required=True)
    group.add_argument("--sonos-ip", metavar="IP", help="Sonos device IP address")
    group.add_argument("--sonos-name", metavar="NAME", help="Sonos player name")
    run_p.add_argument("--name", default="Sonos Cast", help="Friendly name in Cast menus")
    run_p.add_argument("--port", type=int, default=CAST_PORT, help=f"TCP port (default {CAST_PORT})")
    run_p.add_argument("--host-ip", metavar="IP", help="Override the local IP advertised via mDNS")
    run_p.add_argument("-v", "--verbose", action="store_true")

    # Keep flat (no subcommand) form working for backward compatibility:
    # sonos-cast --sonos-ip x.x.x.x --name "..."
    parser.add_argument("--sonos-ip", metavar="IP")
    parser.add_argument("--sonos-name", metavar="NAME")
    parser.add_argument("--name", default="Sonos Cast")
    parser.add_argument("--port", type=int, default=CAST_PORT)
    parser.add_argument("--host-ip", metavar="IP")
    parser.add_argument("-v", "--verbose", action="store_true")

    # --- browse ---
    browse_p = subparsers.add_parser(
        "browse", help="Scan the LAN for Cast devices and print what's visible"
    )
    browse_p.add_argument(
        "--timeout", type=float, default=5.0, metavar="SEC",
        help="How long to listen (default 5s)"
    )

    args = parser.parse_args()

    if args.command == "browse":
        try:
            asyncio.run(_browse(args.timeout))
        except KeyboardInterrupt:
            pass
        return

    # run (explicit subcommand or flat form)
    if not args.sonos_ip and not args.sonos_name:
        parser.error("one of --sonos-ip or --sonos-name is required")

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
