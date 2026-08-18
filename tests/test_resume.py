"""Phase 4: range downloads, resumable uploads, SHA-256 verification, invites."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from lanlink.api import create_app, parse_range
from lanlink.client import LanLinkClient
from lanlink.files import PART_SUFFIX, sha256_of
from lanlink.invite import InvalidInvite, Invite, parse_invite, qr_matrix
from lanlink.state import ALL_PERMISSIONS, HubState
from lanlink.transfers import (
    TransferManager,
    TransferStatus,
    download_runner,
    relay_runner,
    upload_runner,
)


def share_of(state: HubState) -> str:
    return next(iter(state.shares))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def run_node(state: HubState) -> Iterator[str]:
    port = free_port()
    config = uvicorn.Config(create_app(state), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("test node did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def build_node(tmp_path: Path, name: str) -> tuple[HubState, Path, str]:
    root = tmp_path / name
    root.mkdir()
    state = HubState(tmp_path / f"{name}.json")
    share = state.add_share(root, name.title())
    state.set_share_permissions(share.id, ALL_PERMISSIONS)
    return state, root, share.id


def token_for(state: HubState, client_id: str = "client-resume01") -> str:
    code, _ = state.start_pairing()
    result = state.pair(client_id, "Hub", code, source="127.0.0.1")
    assert result.ok and result.token
    return result.token


@pytest.fixture
def manager() -> Iterator[TransferManager]:
    instance = TransferManager(workers=2)
    yield instance
    instance.shutdown()


def wait_for(transfer, statuses: set[TransferStatus], timeout: float = 20.0) -> TransferStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if transfer.status in statuses:
            return transfer.status
        time.sleep(0.02)
    raise AssertionError(f"transfer stuck in {transfer.status}")


# ------------------------------------------------------------------ range parsing


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("bytes=0-", 0),
        ("bytes=1024-", 1024),
        ("bytes=1024-2048", 1024),
        ("BYTES=99-", 99),
        ("bytes=-500", None),
        ("bytes=0-10,20-30", None),
        ("items=5-", None),
        ("bytes=abc-", None),
        ("bytes=-1-", None),
    ],
)
def test_parse_range(header, expected) -> None:
    assert parse_range(header, 100_000) == expected


# ----------------------------------------------------------------- range download


def test_range_request_returns_partial_content(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    payload = bytes(range(256)) * 40  # 10 KB
    (share_root / "blob.bin").write_bytes(payload)
    share_id = share_of(state)

    whole = client.get(f"/v1/files/{share_id}", params={"path": "blob.bin"}, headers=auth)
    assert whole.status_code == 200
    assert whole.content == payload

    resumed = client.get(
        f"/v1/files/{share_id}",
        params={"path": "blob.bin"},
        headers={**auth, "Range": "bytes=4096-"},
    )
    assert resumed.status_code == 206
    assert resumed.headers["Content-Range"] == f"bytes 4096-{len(payload) - 1}/{len(payload)}"
    assert resumed.content == payload[4096:]


def test_range_past_the_end_is_refused(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    (share_root / "small.bin").write_bytes(b"0123456789")
    response = client.get(
        f"/v1/files/{share_of(state)}",
        params={"path": "small.bin"},
        headers={**auth, "Range": "bytes=99-"},
    )
    assert response.status_code == 416


def test_checksum_endpoint(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    (share_root / "data.bin").write_bytes(b"lanlink" * 100)
    response = client.get(
        f"/v1/shares/{share_of(state)}/checksum", params={"path": "data.bin"}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["sha256"] == sha256_of(share_root / "data.bin")


# ---------------------------------------------------------------- resumable upload


def test_upload_in_two_parts(client: TestClient, state: HubState, auth: dict, share_root: Path) -> None:
    share_id = share_of(state)
    payload = b"A" * 3000 + b"B" * 3000

    first = client.put(
        f"/v1/files/{share_id}",
        headers=auth,
        params={"name": "split.bin", "offset": 0, "finalize": False},
        content=payload[:3000],
    )
    assert first.status_code == 200
    assert first.json() == {"path": "split.bin", "received": 3000, "complete": False}
    assert not (share_root / "split.bin").exists(), "an unfinished upload must not appear yet"
    assert (share_root / ("split.bin" + PART_SUFFIX)).stat().st_size == 3000

    status = client.get(
        f"/v1/shares/{share_id}/partial", headers=auth, params={"name": "split.bin"}
    ).json()
    assert status == {"received": 3000, "complete": False, "size": None}

    second = client.put(
        f"/v1/files/{share_id}",
        headers=auth,
        params={"name": "split.bin", "offset": 3000, "finalize": True},
        content=payload[3000:],
    )
    assert second.status_code == 200
    assert (share_root / "split.bin").read_bytes() == payload
    assert not (share_root / ("split.bin" + PART_SUFFIX)).exists()


def test_partial_uploads_are_hidden_from_listings(client: TestClient, state: HubState, auth: dict) -> None:
    share_id = share_of(state)
    client.put(
        f"/v1/files/{share_id}",
        headers=auth,
        params={"name": "wip.bin", "offset": 0, "finalize": False},
        content=b"half",
    )
    names = [
        entry["name"] for entry in client.get(f"/v1/shares/{share_id}/list", headers=auth).json()["entries"]
    ]
    assert names == []


def test_upload_rejects_an_offset_beyond_what_was_received(
    client: TestClient, state: HubState, auth: dict
) -> None:
    share_id = share_of(state)
    client.put(
        f"/v1/files/{share_id}",
        headers=auth,
        params={"name": "gap.bin", "offset": 0, "finalize": False},
        content=b"12345",
    )
    response = client.put(
        f"/v1/files/{share_id}",
        headers=auth,
        params={"name": "gap.bin", "offset": 500, "finalize": True},
        content=b"tail",
    )
    assert response.status_code == 409
    assert response.headers["X-LanLink-Received"] == "5"


def test_a_bad_checksum_discards_the_upload(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    share_id = share_of(state)
    response = client.put(
        f"/v1/files/{share_id}",
        headers=auth,
        params={"name": "corrupt.bin", "offset": 0, "sha256": "00" * 32},
        content=b"the bytes that arrived",
    )
    assert response.status_code == 409
    assert "checksum" in response.json()["detail"].lower()
    assert not (share_root / "corrupt.bin").exists()
    assert not (share_root / ("corrupt.bin" + PART_SUFFIX)).exists()


def test_a_good_checksum_publishes_the_file(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    import hashlib

    payload = b"verified content"
    response = client.put(
        f"/v1/files/{share_of(state)}",
        headers=auth,
        params={"name": "good.bin", "offset": 0, "sha256": hashlib.sha256(payload).hexdigest()},
        content=payload,
    )
    assert response.status_code == 200
    assert (share_root / "good.bin").read_bytes() == payload


def test_resumed_upload_rewinds_when_the_sender_restarts(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    share_id = share_of(state)
    client.put(
        f"/v1/files/{share_id}",
        headers=auth,
        params={"name": "rewind.bin", "offset": 0, "finalize": False},
        content=b"XXXXXXXXXX",
    )
    client.put(
        f"/v1/files/{share_id}",
        headers=auth,
        params={"name": "rewind.bin", "offset": 4, "finalize": True},
        content=b"YYYY",
    )
    assert (share_root / "rewind.bin").read_bytes() == b"XXXXYYYY"


# ------------------------------------------------------- end-to-end resume + verify


def test_download_resumes_from_a_partial_file(manager: TransferManager, tmp_path: Path) -> None:
    state, root, share_id = build_node(tmp_path, "alpha")
    payload = bytes(range(256)) * 800  # ~200 KB
    (root / "big.bin").write_bytes(payload)
    token = token_for(state)

    destination = tmp_path / "big.bin"
    partial = destination.with_name(destination.name + PART_SUFFIX)
    partial.write_bytes(payload[:70_000])  # a transfer that died two-thirds of the way

    with run_node(state) as url:
        client = LanLinkClient(url, token=token)
        transfer = manager.submit(
            kind="download",
            filename="big.bin",
            source="alpha",
            destination=str(destination),
            runner=download_runner(manager, client, share_id, "big.bin", destination),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error
        assert destination.read_bytes() == payload
        assert not partial.exists()
        # Only the missing tail crossed the wire.
        assert transfer.transferred == len(payload)
        client.close()


def test_download_verifies_the_checksum(manager: TransferManager, tmp_path: Path, monkeypatch) -> None:
    state, root, share_id = build_node(tmp_path, "alpha")
    (root / "data.bin").write_bytes(b"honest bytes" * 500)
    token = token_for(state)
    destination = tmp_path / "data.bin"

    with run_node(state) as url:
        client = LanLinkClient(url, token=token)
        monkeypatch.setattr(client, "checksum", lambda *_a, **_k: "00" * 32)
        transfer = manager.submit(
            kind="download",
            filename="data.bin",
            source="alpha",
            destination=str(destination),
            runner=download_runner(manager, client, share_id, "data.bin", destination),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.FAILED
        assert "SHA-256" in transfer.error
        assert not destination.exists(), "a file that fails verification must not be published"
        client.close()


def test_upload_resumes_and_verifies(manager: TransferManager, tmp_path: Path) -> None:
    state, root, share_id = build_node(tmp_path, "alpha")
    payload = bytes(range(256)) * 600
    source = tmp_path / "upload.bin"
    source.write_bytes(payload)
    (root / ("upload.bin" + PART_SUFFIX)).write_bytes(payload[:50_000])
    token = token_for(state)

    with run_node(state) as url:
        client = LanLinkClient(url, token=token)
        transfer = manager.submit(
            kind="upload",
            filename="upload.bin",
            source=str(source),
            destination="alpha",
            runner=upload_runner(manager, client, share_id, "", source),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error
        assert (root / "upload.bin").read_bytes() == payload
        assert not (root / ("upload.bin" + PART_SUFFIX)).exists()
        client.close()


def test_relay_verifies_end_to_end(manager: TransferManager, tmp_path: Path) -> None:
    source_state, source_root, source_share = build_node(tmp_path, "source")
    dest_state, dest_root, dest_share = build_node(tmp_path, "dest")
    payload = b"drawing-data" * 20_000
    (source_root / "big.dwg").write_bytes(payload)

    with run_node(source_state) as source_url, run_node(dest_state) as dest_url:
        source_client = LanLinkClient(source_url, token=token_for(source_state))
        dest_client = LanLinkClient(dest_url, token=token_for(dest_state))
        transfer = manager.submit(
            kind="remote-copy",
            filename="big.dwg",
            source="source",
            destination="dest",
            runner=relay_runner(
                manager,
                source_client,
                source_share,
                "big.dwg",
                dest_client,
                dest_share,
                "",
                "big.dwg",
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error
        assert sha256_of(dest_root / "big.dwg") == sha256_of(source_root / "big.dwg")
        source_client.close()
        dest_client.close()


# -------------------------------------------------------------------- invites


def test_invite_round_trip() -> None:
    invite = Invite(
        host="192.168.1.20",
        port=8765,
        code="12345678",
        device_id="dev-1",
        name="Office PC",
        fingerprint="abc123",
    )
    parsed = parse_invite(invite.to_url())
    assert parsed.host == "192.168.1.20"
    assert parsed.port == 8765
    assert parsed.code == "12345678"
    assert parsed.device_id == "dev-1"
    assert parsed.name == "Office PC"
    assert parsed.fingerprint == "abc123"
    assert parsed.base_url == "https://192.168.1.20:8765"


def test_invite_url_is_a_lanlink_link() -> None:
    url = Invite(host="10.0.0.5", port=9000, code="87654321").to_url()
    assert url.startswith("lanlink://pair?")
    assert "code=87654321" in url


def test_invite_handles_a_name_with_spaces() -> None:
    invite = Invite(host="10.0.0.5", port=8765, code="1", name="Shab's Laptop")
    assert parse_invite(invite.to_url()).name == "Shab's Laptop"


@pytest.mark.parametrize(
    ("text", "host", "port", "scheme"),
    [
        ("192.168.1.20:8765", "192.168.1.20", 8765, "https"),
        ("192.168.1.20", "192.168.1.20", 8765, "https"),
        ("https://192.168.1.20:9000", "192.168.1.20", 9000, "https"),
        ("http://192.168.1.20:9000", "192.168.1.20", 9000, "http"),
    ],
)
def test_plain_addresses_still_work(text, host, port, scheme) -> None:
    invite = parse_invite(text)
    assert (invite.host, invite.port, invite.scheme) == (host, port, scheme)


@pytest.mark.parametrize("text", ["", "   ", "lanlink://open?host=x", "lanlink://pair?port=8765", "http://:9000"])
def test_bad_invites_are_rejected(text) -> None:
    with pytest.raises(InvalidInvite):
        parse_invite(text)


def test_qr_matrix_encodes_the_invite() -> None:
    matrix = qr_matrix(Invite(host="192.168.1.20", port=8765, code="12345678").to_url())
    assert len(matrix) >= 21
    assert all(len(row) == len(matrix) for row in matrix)
    # Finder patterns: the three corners are always dark.
    assert matrix[0][0] and matrix[0][-1] and matrix[-1][0]
