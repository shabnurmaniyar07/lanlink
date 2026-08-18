from __future__ import annotations

import contextlib
import ipaddress
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

from .state import HubState

SERVICE_TYPE = "_lanlink._tcp.local."

_shared_lock = threading.RLock()
_shared_zeroconf: Zeroconf | None = None
_shared_users = 0


def acquire_zeroconf() -> Zeroconf:
    """One Zeroconf instance per process: advertising and browsing share it.

    Two instances mean two sets of multicast sockets, two Windows firewall
    prompts, and avoidable multicast-group contention.
    """
    global _shared_zeroconf, _shared_users
    with _shared_lock:
        if _shared_zeroconf is None:
            _shared_zeroconf = Zeroconf()
        _shared_users += 1
        return _shared_zeroconf


def release_zeroconf() -> None:
    global _shared_zeroconf, _shared_users
    with _shared_lock:
        if _shared_users <= 0:
            return
        _shared_users -= 1
        if _shared_users == 0 and _shared_zeroconf is not None:
            _shared_zeroconf.close()
            _shared_zeroconf = None


@dataclass
class NearbyDevice:
    id: str
    name: str
    host: str
    port: int
    api: str
    service_name: str
    last_seen: float

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def local_ipv4_address_strings() -> list[str]:
    """Best-effort LAN addresses; loopback and link-local addresses are excluded."""
    addresses: list[str] = []

    def add_address(address: str) -> None:
        if address not in addresses:
            addresses.append(address)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            add_address(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add_address(str(info[4][0]))
    except socket.gaierror:
        pass

    usable: list[str] = []
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.is_loopback or parsed.is_link_local:
            continue
        usable.append(address)
    return usable


def local_ipv4_addresses() -> list[bytes]:
    return [socket.inet_aton(address) for address in local_ipv4_address_strings()]


def _decode_properties(properties: dict[Any, Any]) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for raw_key, raw_value in properties.items():
        key = raw_key.decode("utf-8", "replace") if isinstance(raw_key, bytes) else str(raw_key)
        if raw_value is None:
            value = ""
        elif isinstance(raw_value, bytes):
            value = raw_value.decode("utf-8", "replace")
        else:
            value = str(raw_value)
        decoded[key] = value
    return decoded


def _service_display_name(service_name: str) -> str:
    if service_name.endswith(SERVICE_TYPE):
        service_name = service_name[: -len(SERVICE_TYPE)]
    return service_name.rstrip(".") or "LanLink device"


def _first_ipv4_address(info: ServiceInfo) -> str | None:
    for address in info.addresses:
        if len(address) == 4:
            return socket.inet_ntoa(address)
    return None


def device_from_service_info(info: ServiceInfo) -> NearbyDevice | None:
    host = _first_ipv4_address(info)
    if not host or not info.port:
        return None
    properties = _decode_properties(info.properties)
    device_id = properties.get("id", "").strip()
    if not device_id:
        return None
    return NearbyDevice(
        id=device_id,
        name=properties.get("name") or _service_display_name(info.name),
        host=host,
        port=info.port,
        api=properties.get("api") or "v1",
        service_name=info.name,
        last_seen=time.time(),
    )


class DiscoveryService:
    """Advertises this node with mDNS. Pairing still requires a visible short code."""

    def __init__(self, state: HubState, port: int) -> None:
        self.state = state
        self.port = port
        self.zeroconf: Zeroconf | None = None
        self.info: ServiceInfo | None = None
        self.last_error: str | None = None

    def start(self) -> bool:
        addresses = local_ipv4_addresses()
        if not addresses:
            self.last_error = "No LAN address available for mDNS advertisement."
            return False
        device = self.state.public_device()
        self.info = ServiceInfo(
            SERVICE_TYPE,
            name=f"{device['name']}.{SERVICE_TYPE}",
            addresses=addresses,
            port=self.port,
            properties={
                "id": device["id"],
                "name": device["name"],
                "api": "v1",
                "platform": device.get("platform", ""),
                "version": device.get("version", ""),
            },
            server=f"lanlink-{device['id'][:8]}.local.",
        )
        try:
            self.zeroconf = acquire_zeroconf()
            self.zeroconf.register_service(self.info, allow_name_change=True)
        except Exception as error:
            self.last_error = str(error)
            self.stop()
            return False
        self.last_error = None
        return True

    def stop(self) -> None:
        if self.zeroconf and self.info:
            with contextlib.suppress(Exception):
                self.zeroconf.unregister_service(self.info)
        if self.zeroconf:
            release_zeroconf()
        self.zeroconf = None
        self.info = None


class _DiscoveryListener(ServiceListener):
    def __init__(self, owner: DiscoveryBrowser) -> None:
        self.owner = owner

    def add_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self.owner.update_service(zeroconf, service_type, name)

    def update_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self.owner.update_service(zeroconf, service_type, name)

    def remove_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        self.owner.remove_service(name)


class DiscoveryBrowser:
    """Browses for other LanLink nodes advertised on the local network."""

    def __init__(self, local_device_id: str | None = None, stale_after_seconds: int = 20) -> None:
        self.local_device_id = local_device_id
        self.stale_after_seconds = stale_after_seconds
        self.zeroconf: Zeroconf | None = None
        self.browser: ServiceBrowser | None = None
        self.listener: _DiscoveryListener | None = None
        self.last_error: str | None = None
        self._lock = threading.RLock()
        self._devices: dict[str, NearbyDevice] = {}

    def start(self) -> bool:
        if self.zeroconf:
            return True
        try:
            self.zeroconf = acquire_zeroconf()
            self.listener = _DiscoveryListener(self)
            self.browser = ServiceBrowser(self.zeroconf, SERVICE_TYPE, listener=self.listener)
        except Exception as error:
            self.last_error = str(error)
            self.stop()
            return False
        self.last_error = None
        return True

    def stop(self) -> None:
        if self.browser:
            self.browser.cancel()
        if self.zeroconf:
            release_zeroconf()
        self.browser = None
        self.listener = None
        self.zeroconf = None
        with self._lock:
            self._devices.clear()

    def devices(self) -> list[NearbyDevice]:
        now = time.time()
        with self._lock:
            stale_names = [
                service_name
                for service_name, device in self._devices.items()
                if now - device.last_seen > self.stale_after_seconds
            ]
            for service_name in stale_names:
                del self._devices[service_name]
            return sorted(self._devices.values(), key=lambda device: (device.name.lower(), device.host))

    def update_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name, timeout=1_000)
        if not info:
            return
        device = device_from_service_info(info)
        if not device or device.id == self.local_device_id:
            return
        with self._lock:
            self._devices[name] = device

    def remove_service(self, name: str) -> None:
        with self._lock:
            self._devices.pop(name, None)
