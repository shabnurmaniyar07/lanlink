from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .discovery import DiscoveryService, local_ipv4_address_strings
from .state import HubState


def reachable_address(port: int) -> str:
    """Show a likely LAN URL without requiring an internet connection."""
    addresses = local_ipv4_address_strings()
    host = addresses[0] if addresses else "127.0.0.1"
    return f"http://{host}:{port}"


class LocalService:
    """Runs the paired-file API and local-network discovery in background threads."""

    def __init__(self, state: HubState, port: int = 8765) -> None:
        self.state = state
        self.port = port
        self.config = uvicorn.Config(create_app(state), host="0.0.0.0", port=port, log_level="warning")
        self.server = uvicorn.Server(self.config)
        self.thread: threading.Thread | None = None
        self.discovery = DiscoveryService(state, port)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.server.run, name="lanlink-api", daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.server.started:
            self.discovery.start()

    def stop(self) -> None:
        self.discovery.stop()
        self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=4)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local LanLink sharing node.")
    parser.add_argument("--share", action="append", default=[], help="Folder to share (repeatable)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    state = HubState()
    for folder in args.share:
        state.add_share(Path(folder))
    service = LocalService(state, args.port)
    service.start()
    code, expires_at = state.pairing_code()
    print(f"LanLink Hub is ready at {reachable_address(args.port)}")
    print(f"Pairing code: {code} (valid for {int(expires_at - time.time())} seconds)")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
            state.pairing_code()  # rotates an expired code before it is next displayed
    except KeyboardInterrupt:
        service.stop()


if __name__ == "__main__":
    main()
