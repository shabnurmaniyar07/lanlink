import socket
import time

from zeroconf import ServiceInfo

from lanlink.discovery import SERVICE_TYPE, DiscoveryBrowser, NearbyDevice, device_from_service_info


def test_parses_nearby_device_from_mdns_service_info() -> None:
    info = ServiceInfo(
        SERVICE_TYPE,
        f"Office PC.{SERVICE_TYPE}",
        addresses=[socket.inet_aton("192.168.1.50")],
        port=8765,
        properties={b"id": b"device-123", b"name": b"Office PC", b"api": b"v1"},
    )

    device = device_from_service_info(info)

    assert device is not None
    assert device.id == "device-123"
    assert device.name == "Office PC"
    assert device.url == "http://192.168.1.50:8765"


def test_discovery_browser_hides_stale_devices() -> None:
    browser = DiscoveryBrowser(stale_after_seconds=1)
    browser._devices["old"] = NearbyDevice(
        id="old-device",
        name="Old PC",
        host="192.168.1.51",
        port=8765,
        api="v1",
        service_name="old",
        last_seen=time.time() - 5,
    )

    assert browser.devices() == []
