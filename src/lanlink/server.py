from __future__ import annotations

import argparse
import socket
import threading
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .discovery import DiscoveryService, local_ipv4_address_strings
from .state import HubState

DEFAULT_PORT = 8765
PORT_ATTEMPTS = 10


def preferred_bind_host(bind_all: bool = False) -> str:
    """Bind the LAN address by default so VPN and public adapters stay untouched."""
    if bind_all:
        return "0.0.0.0"  # noqa: S104 - explicit opt-in only
    addresses = local_ipv4_address_strings()
    return addresses[0] if addresses else "127.0.0.1"


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(host: str, preferred: int = DEFAULT_PORT, attempts: int = PORT_ATTEMPTS) -> int:
    for offset in range(attempts):
        candidate = preferred + offset
        if port_is_free(host, candidate):
            return candidate
    raise OSError(f"No free port in {preferred}-{preferred + attempts - 1} on {host}.")


def reachable_address(port: int, host: str | None = None) -> str:
    """Show a likely LAN URL without requiring an internet connection."""
    if host and host not in {"0.0.0.0", "::"}:
        return f"http://{host}:{port}"
    addresses = local_ipv4_address_strings()
    return f"http://{addresses[0] if addresses else '127.0.0.1'}:{port}"


class LocalService:
    """Runs the paired-file API and local-network discovery in background threads."""

    def __init__(self, state: HubState, port: int = DEFAULT_PORT, host: str | None = None) -> None:
        self.state = state
        self.host = host or preferred_bind_host(state.bind_all_interfaces)
        self.port = port
        self.thread: threading.Thread | None = None
        self.discovery: DiscoveryService | None = None
        self.last_error: str | None = None
        self.server: uvicorn.Server | None = None

    def start(self) -> bool:
        if self.thread and self.thread.is_alive():
            return True
        try:
            self.port = choose_port(self.host, self.port)
        except OSError as error:
            self.last_error = str(error)
            return False

        config = uvicorn.Config(
            create_app(self.state), host=self.host, port=self.port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="lanlink-api", daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            if not self.thread.is_alive():
                self.last_error = "The LanLink network service stopped while starting."
                return False
            time.sleep(0.05)
        if not self.server.started:
            self.last_error = "The LanLink network service did not start in time."
            return False

        self.last_error = None
        self.discovery = DiscoveryService(self.state, self.port)
        self.discovery.start()
        return True

    @property
    def url(self) -> str:
        return reachable_address(self.port, self.host)

    def stop(self) -> None:
        if self.discovery:
            self.discovery.stop()
            self.discovery = None
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=4)
        self.thread = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local LanLink sharing node.")
    parser.add_argument("--share", action="append", default=[], help="Folder to share (repeatable)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=None, help="Bind address (default: this machine's LAN IP)")
    parser.add_argument(
        "--bind-all",
        action="store_true",
        help="Bind every interface. Off by default so VPN/public adapters stay unexposed.",
    )
    parser.add_argument("--pair", action="store_true", help="Arm pairing mode at startup")
    args = parser.parse_args()

    state = HubState()
    for folder in args.share:
        state.add_share(Path(folder))
    host = args.host or preferred_bind_host(args.bind_all or state.bind_all_interfaces)
    service = LocalService(state, args.port, host=host)
    if not service.start():
        print(f"LanLink could not start: {service.last_error}")
        return

    print(f"LanLink Hub is ready at {service.url}")
    if args.pair:
        code, expires_at = state.start_pairing()
        print(f"Pairing code: {code} (valid for {int(expires_at - time.time())} seconds, one use)")
    else:
        print("Pairing mode is off. Restart with --pair to allow a new device to pair.")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        service.stop()


if __name__ == "__main__":
    main()
