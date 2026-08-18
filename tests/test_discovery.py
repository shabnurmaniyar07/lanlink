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
    assert device.url == "https://192.168.1.50:8765", "TLS is the default scheme"


def test_a_peer_may_advertise_plain_http() -> None:
    info = ServiceInfo(
        SERVICE_TYPE,
        f"Legacy PC.{SERVICE_TYPE}",
        addresses=[socket.inet_aton("192.168.1.60")],
        port=8765,
        properties={b"id": b"device-legacy", b"name": b"Legacy PC", b"scheme": b"http"},
    )
    device = device_from_service_info(info)
    assert device is not None
    assert device.url == "http://192.168.1.60:8765"


def test_fingerprint_and_platform_travel_in_the_txt_record() -> None:
    info = ServiceInfo(
        SERVICE_TYPE,
        f"Phone.{SERVICE_TYPE}",
        addresses=[socket.inet_aton("192.168.1.70")],
        port=8765,
        properties={
            b"id": b"device-phone",
            b"name": b"Phone",
            b"platform": b"Android",
            b"fp": b"abcdef0123456789",
        },
    )
    device = device_from_service_info(info)
    assert device is not None
    assert device.platform == "Android"
    assert device.fingerprint == "abcdef0123456789"


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
