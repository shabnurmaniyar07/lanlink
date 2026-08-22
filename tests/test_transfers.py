"""Phase 2: transfer queue and hub-mediated node-to-node relay.

The relay test runs two real uvicorn nodes on loopback, so the streaming path is
exercised end to end rather than through an in-process shim.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from lanlink.api import create_app
from lanlink.client import LanLinkClient
from lanlink.state import ALL_PERMISSIONS, HubState
from lanlink.transfers import (
    TransferManager,
    TransferStatus,
    download_runner,
    relay_runner,
    upload_runner,
)


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
    deadline = time.monotonic() + 10
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


def token_for(state: HubState, client_id: str = "client-hub00001") -> str:
    code, _ = state.start_pairing()
    result = state.pair(client_id, "Hub", code, source="127.0.0.1")
    assert result.ok and result.token
    return result.token


@pytest.fixture
def manager() -> Iterator[TransferManager]:
    instance = TransferManager(workers=2)
    yield instance
    instance.shutdown()


def wait_for(transfer, statuses: set[TransferStatus], timeout: float = 15.0) -> TransferStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if transfer.status in statuses:
            return transfer.status
        time.sleep(0.02)
    raise AssertionError(f"transfer stuck in {transfer.status}")


def test_queue_reports_progress_and_completes(manager: TransferManager) -> None:
    def run(transfer, control):
        transfer.size = 100
        for _ in range(10):
            manager.advance(transfer, control, 10)

    transfer = manager.submit(
        kind="test", filename="a.bin", source="A", destination="B", runner=run, size=100
    )
    wait_for(transfer, {TransferStatus.COMPLETED})
    assert transfer.transferred == 100
    assert transfer.progress == 1.0
    assert transfer.finished_at is not None


def test_failure_is_recorded_and_retryable(manager: TransferManager) -> None:
    attempts = {"count": 0}

    def run(transfer, control):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("network dropped")
        manager.advance(transfer, control, 10)

    transfer = manager.submit(kind="test", filename="a.bin", source="A", destination="B", runner=run, size=10)
    wait_for(transfer, {TransferStatus.FAILED})
    assert transfer.error == "network dropped"
    assert transfer in manager.history()

    manager.retry(transfer.id)
    wait_for(transfer, {TransferStatus.COMPLETED})
    assert transfer.error == ""
    assert transfer.transferred == 10


def test_cancel_stops_a_running_transfer(manager: TransferManager) -> None:
    started = threading.Event()

    def run(transfer, control):
        transfer.size = 10_000
        started.set()
        for _ in range(10_000):
            manager.advance(transfer, control, 1)
            time.sleep(0.001)

    transfer = manager.submit(
        kind="test", filename="big.bin", source="A", destination="B", runner=run, size=10_000
    )
    assert started.wait(5)
    assert manager.cancel(transfer.id) is True
    wait_for(transfer, {TransferStatus.CANCELLED})
    assert transfer.transferred < 10_000


def test_pause_and_resume(manager: TransferManager) -> None:
    started = threading.Event()

    def run(transfer, control):
        transfer.size = 200
        started.set()
        for _ in range(200):
            manager.advance(transfer, control, 1)
            time.sleep(0.002)

    transfer = manager.submit(
        kind="test", filename="p.bin", source="A", destination="B", runner=run, size=200
    )
    assert started.wait(5)
    assert manager.pause(transfer.id) is True
    time.sleep(0.1)
    paused_at = transfer.transferred
    time.sleep(0.1)
    assert transfer.transferred == paused_at, "a paused transfer must stop moving"

    assert manager.resume(transfer.id) is True
    wait_for(transfer, {TransferStatus.COMPLETED})
    assert transfer.transferred == 200


def test_history_separates_finished_transfers(manager: TransferManager) -> None:
    transfer = manager.submit(
        kind="test",
        filename="a.bin",
        source="A",
        destination="B",
        runner=lambda t, c: manager.advance(t, c, 1),
        size=1,
    )
    wait_for(transfer, {TransferStatus.COMPLETED})
    assert manager.active() == []
    assert [item.id for item in manager.history()] == [transfer.id]
    manager.clear_history()
    assert manager.snapshot() == []


def test_download_and_upload_against_a_real_node(manager: TransferManager, tmp_path: Path) -> None:
    state, root, share_id = build_node(tmp_path, "alpha")
    payload = b"lanlink" * 20_000  # ~140 KB, several chunks
    (root / "source.bin").write_bytes(payload)
    token = token_for(state)

    with run_node(state) as url:
        client = LanLinkClient(url, token=token)
        destination = tmp_path / "downloaded.bin"
        transfer = manager.submit(
            kind="download",
            filename="source.bin",
            source="alpha",
            destination=str(destination),
            runner=download_runner(manager, client, share_id, "source.bin", destination),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error
        assert destination.read_bytes() == payload
        assert transfer.transferred == len(payload)

        upload = manager.submit(
            kind="upload",
            filename="downloaded.bin",
            source=str(destination),
            destination="alpha",
            runner=upload_runner(manager, client, share_id, "", destination),
        )
        wait_for(upload, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert upload.status is TransferStatus.COMPLETED, upload.error
        assert (root / "downloaded.bin").read_bytes() == payload
        client.close()


def test_relay_copies_between_two_nodes_without_touching_the_hub(
    manager: TransferManager, tmp_path: Path
) -> None:
    source_state, source_root, source_share = build_node(tmp_path, "source")
    dest_state, dest_root, dest_share = build_node(tmp_path, "dest")
    payload = b"engineering-drawing" * 10_000  # ~190 KB
    (source_root / "drawing.dwg").write_bytes(payload)

    hub_scratch = tmp_path / "hub"
    hub_scratch.mkdir()

    with run_node(source_state) as source_url, run_node(dest_state) as dest_url:
        source_client = LanLinkClient(source_url, token=token_for(source_state))
        dest_client = LanLinkClient(dest_url, token=token_for(dest_state))

        transfer = manager.submit(
            kind="remote-copy",
            filename="drawing.dwg",
            source="source",
            destination="dest",
            runner=relay_runner(
                manager,
                source_client,
                source_share,
                "drawing.dwg",
                dest_client,
                dest_share,
                "",
                "drawing.dwg",
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error

        assert (dest_root / "drawing.dwg").read_bytes() == payload
        assert (source_root / "drawing.dwg").exists(), "copy must leave the source in place"
        assert list(hub_scratch.iterdir()) == [], "the hub must not stage a second copy"
        assert transfer.transferred == len(payload)

        source_client.close()
        dest_client.close()


def test_relay_move_deletes_the_source_only_after_verification(
    manager: TransferManager, tmp_path: Path
) -> None:
    source_state, source_root, source_share = build_node(tmp_path, "source")
    dest_state, dest_root, dest_share = build_node(tmp_path, "dest")
    payload = b"payload" * 5_000
    (source_root / "moved.bin").write_bytes(payload)

    with run_node(source_state) as source_url, run_node(dest_state) as dest_url:
        source_client = LanLinkClient(source_url, token=token_for(source_state))
        dest_client = LanLinkClient(dest_url, token=token_for(dest_state))

        transfer = manager.submit(
            kind="remote-move",
            filename="moved.bin",
            source="source",
            destination="dest",
            runner=relay_runner(
                manager,
                source_client,
                source_share,
                "moved.bin",
                dest_client,
                dest_share,
                "",
                "moved.bin",
                delete_source=True,
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error
        assert (dest_root / "moved.bin").read_bytes() == payload
        assert not (source_root / "moved.bin").exists()

        source_client.close()
        dest_client.close()


def test_relay_leaves_the_source_when_the_destination_refuses(
    manager: TransferManager, tmp_path: Path
) -> None:
    source_state, source_root, source_share = build_node(tmp_path, "source")
    dest_state, dest_root, dest_share = build_node(tmp_path, "dest")
    (source_root / "keep.bin").write_bytes(b"important")
    dest_state.set_share_permissions(dest_share, "r")  # destination is read-only

    with run_node(source_state) as source_url, run_node(dest_state) as dest_url:
        source_client = LanLinkClient(source_url, token=token_for(source_state))
        dest_client = LanLinkClient(dest_url, token=token_for(dest_state))

        transfer = manager.submit(
            kind="remote-move",
            filename="keep.bin",
            source="source",
            destination="dest",
            runner=relay_runner(
                manager,
                source_client,
                source_share,
                "keep.bin",
                dest_client,
                dest_share,
                "",
                "keep.bin",
                delete_source=True,
            ),
        )
        wait_for(transfer, {TransferStatus.FAILED, TransferStatus.COMPLETED})
        assert transfer.status is TransferStatus.FAILED
        assert (source_root / "keep.bin").exists(), "a failed move must never delete the source"
        assert not (dest_root / "keep.bin").exists()

        source_client.close()
        dest_client.close()
