from pathlib import Path

from fastapi.testclient import TestClient

from lanlink.api import create_app
from lanlink.state import HubState


def test_pairing_is_required_for_file_api(tmp_path: Path) -> None:
    state = HubState(tmp_path / "settings.json")
    state.add_share(tmp_path, "Demo")
    app = create_app(state)
    client = TestClient(app)

    assert client.get("/v1/shares").status_code == 401
    code, _ = state.pairing_code()
    pair = client.post(
        "/v1/pair",
        json={"client_id": "client-12345678", "client_name": "Test phone", "pair_code": code},
    )
    assert pair.status_code == 200
    token = pair.json()["token"]

    response = client.get("/v1/shares", headers={"X-LanLink-Token": token})
    assert response.status_code == 200
    assert response.json()["shares"][0]["name"] == "Demo"
    assert client.get(
        "/v1/shares/unknown/list", headers={"X-LanLink-Token": token}
    ).status_code == 404
