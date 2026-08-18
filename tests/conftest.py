from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lanlink.api import create_app
from lanlink.state import HubState


@pytest.fixture
def state(tmp_path: Path) -> HubState:
    hub = HubState(tmp_path / "settings.json")
    share_root = tmp_path / "shared"
    share_root.mkdir()
    hub.add_share(share_root, "Demo")
    return hub


@pytest.fixture
def share_root(state: HubState) -> Path:
    return Path(next(iter(state.shares.values())).path)


@pytest.fixture
def client(state: HubState) -> TestClient:
    return TestClient(create_app(state), raise_server_exceptions=False)


@pytest.fixture
def pair_device(client: TestClient, state: HubState):
    """Arm pairing and pair one device, returning its token."""

    def _pair(client_id: str = "client-12345678", name: str = "Test device") -> str:
        code, _ = state.start_pairing()
        response = client.post(
            "/v1/pair", json={"client_id": client_id, "client_name": name, "pair_code": code}
        )
        assert response.status_code == 200, response.text
        return response.json()["token"]

    return _pair


@pytest.fixture
def token(pair_device) -> str:
    return pair_device()


@pytest.fixture
def auth(token: str) -> dict[str, str]:
    return {"X-LanLink-Token": token}
