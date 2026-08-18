from __future__ import annotations

from fastapi.testclient import TestClient

from lanlink.state import HubState


def test_pairing_is_required_for_file_api(client: TestClient, state: HubState) -> None:
    assert client.get("/v1/shares").status_code == 401

    code, _ = state.start_pairing()
    pair = client.post(
        "/v1/pair",
        json={"client_id": "client-12345678", "client_name": "Test phone", "pair_code": code},
    )
    assert pair.status_code == 200
    token = pair.json()["token"]

    response = client.get("/v1/shares", headers={"X-LanLink-Token": token})
    assert response.status_code == 200
    assert response.json()["shares"][0]["name"] == "Demo"
    assert client.get("/v1/shares/unknown/list", headers={"X-LanLink-Token": token}).status_code == 404


def test_shares_report_availability_and_permissions(client: TestClient, auth: dict[str, str]) -> None:
    share = client.get("/v1/shares", headers=auth).json()["shares"][0]
    assert share["available"] is True
    assert share["permissions"] == "rw"


def test_download_round_trip(client: TestClient, state: HubState, auth: dict[str, str]) -> None:
    share_id = next(iter(state.shares))
    upload = client.post(
        f"/v1/uploads/{share_id}", headers=auth, files={"file": ("note.txt", b"hello")}
    )
    assert upload.status_code == 200

    listing = client.get(f"/v1/shares/{share_id}/list", headers=auth).json()["entries"]
    assert [entry["name"] for entry in listing] == ["note.txt"]

    download = client.get(f"/v1/files/{share_id}", params={"path": "note.txt"}, headers=auth)
    assert download.status_code == 200
    assert download.content == b"hello"


def test_traversal_is_refused_over_http(client: TestClient, state: HubState, auth: dict) -> None:
    share_id = next(iter(state.shares))
    for path in ["../", "..%2Fsecret", "/etc/passwd", "..\\secret"]:
        response = client.get(f"/v1/shares/{share_id}/list", params={"path": path}, headers=auth)
        assert response.status_code == 404, path
