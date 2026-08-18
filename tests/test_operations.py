"""Phase 2: rename, delete, mkdir and properties over the /v1 API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from lanlink.state import ALL_PERMISSIONS, HubState


def share_of(state: HubState) -> str:
    return next(iter(state.shares))


def allow_delete(state: HubState) -> None:
    state.set_share_permissions(share_of(state), ALL_PERMISSIONS)


def test_create_folder(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    response = client.post(
        f"/v1/shares/{share_of(state)}/folders", headers=auth, json={"path": "", "name": "Projects"}
    )
    assert response.status_code == 200
    assert (share_root / "Projects").is_dir()


def test_create_nested_folder(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    (share_root / "Projects").mkdir()
    response = client.post(
        f"/v1/shares/{share_of(state)}/folders",
        headers=auth,
        json={"path": "Projects", "name": "CAD"},
    )
    assert response.status_code == 200
    assert (share_root / "Projects" / "CAD").is_dir()


def test_create_folder_rejects_duplicate(
    client: TestClient,
    state: HubState,
    auth: dict,
    share_root: Path,
) -> None:
    (share_root / "Projects").mkdir()
    response = client.post(
        f"/v1/shares/{share_of(state)}/folders", headers=auth, json={"path": "", "name": "Projects"}
    )
    assert response.status_code == 409


def test_create_folder_rejects_unsafe_name(client: TestClient, state: HubState, auth: dict) -> None:
    for name in ["../escape", "..\\escape", "CON", "bad|name"]:
        response = client.post(
            f"/v1/shares/{share_of(state)}/folders", headers=auth, json={"path": "", "name": name}
        )
        assert response.status_code == 409, name


def test_rename_file(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    (share_root / "old.txt").write_text("data", encoding="utf-8")
    response = client.post(
        f"/v1/shares/{share_of(state)}/rename",
        headers=auth,
        json={"path": "old.txt", "new_name": "new.txt"},
    )
    assert response.status_code == 200
    assert not (share_root / "old.txt").exists()
    assert (share_root / "new.txt").read_text(encoding="utf-8") == "data"


def test_rename_folder(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    (share_root / "before").mkdir()
    response = client.post(
        f"/v1/shares/{share_of(state)}/rename",
        headers=auth,
        json={"path": "before", "new_name": "after"},
    )
    assert response.status_code == 200
    assert (share_root / "after").is_dir()


def test_rename_cannot_escape_the_share(
    client: TestClient,
    state: HubState,
    auth: dict,
    share_root: Path,
) -> None:
    (share_root / "file.txt").write_text("x", encoding="utf-8")
    response = client.post(
        f"/v1/shares/{share_of(state)}/rename",
        headers=auth,
        json={"path": "file.txt", "new_name": "../escaped.txt"},
    )
    assert response.status_code == 409
    assert (share_root / "file.txt").exists()
    assert not (share_root.parent / "escaped.txt").exists()


def test_rename_refuses_the_share_root(client: TestClient, state: HubState, auth: dict) -> None:
    response = client.post(
        f"/v1/shares/{share_of(state)}/rename", headers=auth, json={"path": ".", "new_name": "hijack"}
    )
    assert response.status_code == 409


def test_delete_file(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    allow_delete(state)
    (share_root / "gone.txt").write_text("x", encoding="utf-8")
    response = client.request(
        "DELETE", f"/v1/shares/{share_of(state)}/entries", headers=auth, params={"path": "gone.txt"}
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "file"
    assert not (share_root / "gone.txt").exists()


def test_delete_non_empty_folder_needs_recursive(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    allow_delete(state)
    folder = share_root / "full"
    folder.mkdir()
    (folder / "child.txt").write_text("x", encoding="utf-8")

    blocked = client.request(
        "DELETE", f"/v1/shares/{share_of(state)}/entries", headers=auth, params={"path": "full"}
    )
    assert blocked.status_code == 409
    assert folder.exists()

    allowed = client.request(
        "DELETE",
        f"/v1/shares/{share_of(state)}/entries",
        headers=auth,
        params={"path": "full", "recursive": True},
    )
    assert allowed.status_code == 200
    assert not folder.exists()


def test_delete_refuses_the_share_root(
    client: TestClient,
    state: HubState,
    auth: dict,
    share_root: Path,
) -> None:
    allow_delete(state)
    response = client.request(
        "DELETE", f"/v1/shares/{share_of(state)}/entries", headers=auth, params={"path": ""}
    )
    assert response.status_code == 409
    assert share_root.is_dir()


def test_delete_cannot_escape_the_share(
    client: TestClient,
    state: HubState,
    auth: dict,
    tmp_path: Path,
) -> None:
    allow_delete(state)
    victim = tmp_path / "outside.txt"
    victim.write_text("keep me", encoding="utf-8")
    response = client.request(
        "DELETE",
        f"/v1/shares/{share_of(state)}/entries",
        headers=auth,
        params={"path": "../outside.txt"},
    )
    assert response.status_code == 409
    assert victim.exists()


def test_file_properties(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    (share_root / "report.PDF").write_bytes(b"12345")
    response = client.get(
        f"/v1/shares/{share_of(state)}/properties", headers=auth, params={"path": "report.PDF"}
    )
    assert response.status_code == 200
    detail = response.json()
    assert detail["name"] == "report.PDF"
    assert detail["kind"] == "file"
    assert detail["size"] == 5
    assert detail["extension"] == ".pdf"
    assert detail["modified_at"] > 0


def test_folder_properties_count_children(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    (share_root / "mixed").mkdir()
    (share_root / "mixed" / "a.txt").write_text("a", encoding="utf-8")
    (share_root / "mixed" / "b.txt").write_text("b", encoding="utf-8")
    (share_root / "mixed" / "sub").mkdir()

    detail = client.get(
        f"/v1/shares/{share_of(state)}/properties", headers=auth, params={"path": "mixed"}
    ).json()
    assert detail["kind"] == "folder"
    assert detail["item_count"] == {"folders": 1, "files": 2}
    assert detail["size"] is None


def test_properties_of_missing_item(client: TestClient, state: HubState, auth: dict) -> None:
    response = client.get(
        f"/v1/shares/{share_of(state)}/properties", headers=auth, params={"path": "nope.txt"}
    )
    assert response.status_code == 404


def test_all_operations_require_pairing(client: TestClient, state: HubState) -> None:
    share_id = share_of(state)
    assert client.post(f"/v1/shares/{share_id}/folders", json={"path": "", "name": "x"}).status_code == 401
    assert (
        client.post(f"/v1/shares/{share_id}/rename", json={"path": "a", "new_name": "b"}).status_code == 401
    )
    assert client.request("DELETE", f"/v1/shares/{share_id}/entries", params={"path": "a"}).status_code == 401
    assert client.get(f"/v1/shares/{share_id}/properties").status_code == 401


def test_streaming_put_upload(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    response = client.put(
        f"/v1/files/{share_of(state)}",
        headers=auth,
        params={"path": "", "name": "streamed.bin"},
        content=b"0" * 4096,
    )
    assert response.status_code == 200
    assert response.json()["bytes"] == 4096
    assert (share_root / "streamed.bin").stat().st_size == 4096


def test_streaming_put_rejects_unsafe_name(client: TestClient, state: HubState, auth: dict) -> None:
    response = client.put(
        f"/v1/files/{share_of(state)}",
        headers=auth,
        params={"path": "", "name": "..\\..\\evil.txt"},
        content=b"x",
    )
    assert response.status_code == 404


def test_streaming_put_honours_size_limit(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    state.max_upload_bytes = 1024
    response = client.put(
        f"/v1/files/{share_of(state)}",
        headers=auth,
        params={"path": "", "name": "big.bin"},
        content=b"0" * 8192,
    )
    assert response.status_code == 413
    assert not (share_root / "big.bin").exists()
