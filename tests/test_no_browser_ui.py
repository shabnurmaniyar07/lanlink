"""LanLink must never serve a user interface. The transport is HTTP; the UI is native."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import lanlink

PACKAGE_ROOT = Path(lanlink.__file__).parent


def test_no_static_directory_ships_with_the_package() -> None:
    assert not (PACKAGE_ROOT / "static").exists()


def test_root_serves_no_html(client: TestClient) -> None:
    assert client.get("/").status_code == 404


def test_static_mount_is_gone(client: TestClient) -> None:
    assert client.get("/ui").status_code == 404
    assert client.get("/ui/index.html").status_code == 404


def test_no_endpoint_returns_html(client: TestClient, auth: dict[str, str]) -> None:
    for path in ["/health", "/v1/device", "/v1/shares"]:
        response = client.get(path, headers=auth)
        assert "text/html" not in response.headers.get("content-type", "")


def test_source_never_launches_a_web_browser() -> None:
    banned = ("QDesktopServices", "webbrowser", "QWebEngineView", "StaticFiles")
    for module in PACKAGE_ROOT.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        for term in banned:
            assert term not in source, f"{module.name} must not reference {term}"


def test_health_and_device_endpoints_are_json(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    device = client.get("/v1/device")
    assert device.status_code == 200
    payload = device.json()["device"]
    assert {"id", "name", "hostname", "platform", "version"} <= set(payload)
    assert device.json()["pairing_armed"] is False
