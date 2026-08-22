"""Phase 2: per-share read / write / delete permissions."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lanlink.state import ALL_PERMISSIONS, DEFAULT_PERMISSIONS, HubState


def share_of(state: HubState) -> str:
    return next(iter(state.shares))


def test_new_shares_are_read_write_but_not_delete(state: HubState) -> None:
    share = next(iter(state.shares.values()))
    assert share.permissions == DEFAULT_PERMISSIONS == "rw"
    assert share.allows("r") and share.allows("w")
    assert not share.allows("d"), "delete must be opt-in"


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("rwd", "rwd"),
        ("r", "r"),
        ("rw", "rw"),
        ("", "r"),
        ("d", "rd"),
        ("RWD", "rwd"),
        ("rwdx!", "rwd"),
        ("wd", "rwd"),
    ],
)
def test_permissions_are_normalised(state: HubState, requested: str, expected: str) -> None:
    share = state.set_share_permissions(share_of(state), requested)
    assert share is not None
    assert share.permissions == expected


def test_permissions_survive_a_restart(state: HubState, tmp_path: Path) -> None:
    state.set_share_permissions(share_of(state), "r")
    reloaded = HubState(tmp_path / "settings.json")
    assert next(iter(reloaded.shares.values())).permissions == "r"


def test_read_only_share_blocks_every_write(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    (share_root / "existing.txt").write_text("x", encoding="utf-8")
    share_id = share_of(state)
    state.set_share_permissions(share_id, "r")

    upload = client.post(f"/v1/uploads/{share_id}", headers=auth, files={"file": ("a.txt", b"x")})
    assert upload.status_code == 403
    assert (
        client.put(f"/v1/files/{share_id}", headers=auth, params={"name": "a.txt"}, content=b"x").status_code
        == 403
    )
    assert (
        client.post(
            f"/v1/shares/{share_id}/folders", headers=auth, json={"path": "", "name": "New"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/v1/shares/{share_id}/rename", headers=auth, json={"path": "existing.txt", "new_name": "b.txt"}
        ).status_code
        == 403
    )
    assert (share_root / "existing.txt").exists()


def test_read_only_share_still_allows_reading(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    (share_root / "readable.txt").write_text("hello", encoding="utf-8")
    share_id = share_of(state)
    state.set_share_permissions(share_id, "r")

    assert client.get(f"/v1/shares/{share_id}/list", headers=auth).status_code == 200
    assert (
        client.get(f"/v1/files/{share_id}", headers=auth, params={"path": "readable.txt"}).content == b"hello"
    )
    assert (
        client.get(
            f"/v1/shares/{share_id}/properties", headers=auth, params={"path": "readable.txt"}
        ).status_code
        == 200
    )


def test_delete_requires_the_delete_flag(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    (share_root / "target.txt").write_text("x", encoding="utf-8")
    share_id = share_of(state)

    denied = client.request(
        "DELETE", f"/v1/shares/{share_id}/entries", headers=auth, params={"path": "target.txt"}
    )
    assert denied.status_code == 403
    assert (share_root / "target.txt").exists()

    state.set_share_permissions(share_id, ALL_PERMISSIONS)
    allowed = client.request(
        "DELETE", f"/v1/shares/{share_id}/entries", headers=auth, params={"path": "target.txt"}
    )
    assert allowed.status_code == 200
    assert not (share_root / "target.txt").exists()


def test_move_out_of_a_no_delete_share_is_refused(
    client: TestClient, state: HubState, auth: dict, share_root: Path, tmp_path: Path
) -> None:
    (share_root / "asset.txt").write_text("payload", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    destination_share = state.add_share(destination, "Destination")

    response = client.post(
        "/v1/operations",
        headers=auth,
        json={
            "source_share_id": share_of(state),
            "source_path": "asset.txt",
            "destination_share_id": destination_share.id,
            "destination_path": "",
            "operation": "move",
        },
    )
    assert response.status_code == 403
    assert (share_root / "asset.txt").exists(), "a refused move must not touch the source"


def test_copy_out_of_a_no_delete_share_is_allowed(
    client: TestClient, state: HubState, auth: dict, share_root: Path, tmp_path: Path
) -> None:
    (share_root / "asset.txt").write_text("payload", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    destination_share = state.add_share(destination, "Destination")

    response = client.post(
        "/v1/operations",
        headers=auth,
        json={
            "source_share_id": share_of(state),
            "source_path": "asset.txt",
            "destination_share_id": destination_share.id,
            "destination_path": "",
            "operation": "copy",
        },
    )
    assert response.status_code == 200
    assert (destination / "asset.txt").read_text(encoding="utf-8") == "payload"
    assert (share_root / "asset.txt").exists()


def test_permissions_are_reported_to_clients(client: TestClient, state: HubState, auth: dict) -> None:
    state.set_share_permissions(share_of(state), "r")
    share = client.get("/v1/shares", headers=auth).json()["shares"][0]
    assert share["permissions"] == "r"
