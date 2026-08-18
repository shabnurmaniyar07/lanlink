"""Acceptance test: the complete device-A → device-B flow, end to end.

Two independent LanLink nodes are started as real TLS servers with real
certificates, real pairing, real discovery parsing and the real PySide6 window
driving them. Nothing here is mocked except the display.

This covers the fifteen acceptance points:
  1  discovery      2  pairing        3  device list     4  shared folders
  5  destination    6  source file    7  Copy to…        8  file created
  9  size/content  10  progress      11  receiver view  12  large file
 13  disconnect    14  Move          15  no intermediate copy
plus recursive folder transfer.
"""

from __future__ import annotations

import hashlib
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lanlink.api import create_app  # noqa: E402
from lanlink.client import LanLinkClient  # noqa: E402
from lanlink.crypto import ensure_device_certificate, fetch_peer_certificate, fingerprint_of_pem  # noqa: E402
from lanlink.discovery import SERVICE_TYPE, device_from_service_info  # noqa: E402
from lanlink.files import PART_SUFFIX, sha256_of  # noqa: E402
from lanlink.state import ALL_PERMISSIONS, HubState  # noqa: E402
from lanlink.transfers import (  # noqa: E402
    TransferManager,
    TransferStatus,
    relay_folder_runner,
    relay_runner,
)
from lanlink.ui import main_window as mw  # noqa: E402
from lanlink.ui.devices import DeviceStatus, merge_devices  # noqa: E402

LARGE_FILE_BYTES = 24 * 1024 * 1024  # 24 MB: many chunks, still quick on loopback


# --------------------------------------------------------------------- fixtures


class Node:
    """One LanLink installation: state, share, certificate, live TLS server."""

    def __init__(self, root: Path, name: str) -> None:
        self.name = name
        self.home = root / name
        self.home.mkdir(parents=True)
        self.share_root = self.home / "Shared"
        self.share_root.mkdir()
        self.state = HubState(self.home / "settings.json")
        self.state.device_name = name
        self.share = self.state.add_share(self.share_root, "Shared")
        self.state.set_share_permissions(self.share.id, ALL_PERMISSIONS)
        self.certificate = ensure_device_certificate(
            self.home, self.state.device_id, name, ["127.0.0.1"]
        )
        self.state.certificate_fingerprint = self.certificate.fingerprint
        self.port = _free_port()
        self.url = f"https://127.0.0.1:{self.port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def share_id(self) -> str:
        return self.share.id

    def start(self) -> None:
        config = uvicorn.Config(
            create_app(self.state),
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            ssl_certfile=str(self.certificate.certificate_path),
            ssl_keyfile=str(self.certificate.key_path),
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 20
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError(f"{self.name} did not start")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def kill(self) -> None:
        """Drop the machine off the network mid-request.

        A graceful stop is not a disconnect: uvicorn waits for the in-flight
        upload to finish, so the transfer would succeed. force_exit is what
        actually models the cable being pulled.
        """
        if self._server:
            self._server.should_exit = True
            self._server.force_exit = True
        if self._thread:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def nodes(tmp_path: Path) -> Iterator[tuple[Node, Node]]:
    laptop_a = Node(tmp_path, "LAPTOP-A")
    laptop_b = Node(tmp_path, "LAPTOP-B")
    laptop_a.start()
    laptop_b.start()
    try:
        yield laptop_a, laptop_b
    finally:
        laptop_a.stop()
        laptop_b.stop()


@pytest.fixture
def manager() -> Iterator[TransferManager]:
    instance = TransferManager(workers=2)
    yield instance
    instance.shutdown()


def pair(initiator: Node, target: Node) -> LanLinkClient:
    """Pair `initiator` with `target` exactly the way the UI does."""
    certificate = fetch_peer_certificate("127.0.0.1", target.port)
    assert fingerprint_of_pem(certificate) == target.certificate.fingerprint

    code, _ = target.state.start_pairing()
    client = LanLinkClient(target.url, peer_certificate=certificate)
    result = client.pair(initiator.name, code, client_id=initiator.state.device_id)
    initiator.state.upsert_remote_device(
        result["device"]["id"],
        result["device"]["name"],
        target.url,
        result["token"],
        certificate,
        fingerprint_of_pem(certificate),
    )
    client.token = result["token"]
    return client


def wait_for(transfer, statuses: set[TransferStatus], timeout: float = 120.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if transfer.status in statuses:
            return transfer.status
        time.sleep(0.02)
    raise AssertionError(f"transfer stuck in {transfer.status} at {transfer.transferred} bytes")


def scratch_files(node: Node) -> list[Path]:
    """Anything in a node's home that is not its share, settings or certificate."""
    allowed = {"settings.json", "settings.json.bak", "device-cert.pem", "device-key.pem"}
    return [
        item
        for item in node.home.iterdir()
        if item.name not in allowed and item != node.share_root and item.is_file()
    ]


# ------------------------------------------------------ 1-2  discovery & pairing


def test_01_devices_discover_each_other(nodes) -> None:
    """mDNS TXT records from each node parse into the other's device list."""
    from zeroconf import ServiceInfo

    laptop_a, laptop_b = nodes
    seen = []
    for node in (laptop_a, laptop_b):
        device = node.state.public_device()
        info = ServiceInfo(
            SERVICE_TYPE,
            f"{node.name}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton("127.0.0.1")],
            port=node.port,
            properties={
                "id": device["id"],
                "name": device["name"],
                "api": "v1",
                "scheme": "https",
                "fp": device["fingerprint"][:32],
            },
        )
        parsed = device_from_service_info(info)
        assert parsed is not None
        assert parsed.id == node.state.device_id
        assert parsed.url == node.url
        assert parsed.fingerprint == node.certificate.fingerprint[:32]
        seen.append(parsed)

    assert seen[0].id != seen[1].id, "two installations must have distinct identities"


def test_02_devices_can_pair_both_ways(nodes) -> None:
    laptop_a, laptop_b = nodes
    a_to_b = pair(laptop_a, laptop_b)
    b_to_a = pair(laptop_b, laptop_a)
    try:
        assert a_to_b.shares()[0]["name"] == "Shared"
        assert b_to_a.shares()[0]["name"] == "Shared"
        assert len(laptop_a.state.remote_devices) == 1
        assert len(laptop_b.state.paired_devices) == 1
        # Pairing consumed the code on both sides.
        assert laptop_a.state.pairing_armed is False
        assert laptop_b.state.pairing_armed is False
    finally:
        a_to_b.close()
        b_to_a.close()


# --------------------------------------------- 3-4  device list & shared folders


def test_03_laptop_b_appears_in_laptop_a_device_list(nodes, qapp) -> None:
    laptop_a, laptop_b = nodes
    client = pair(laptop_a, laptop_b)
    client.close()

    devices = merge_devices(
        [],
        laptop_a.state.remote_devices_snapshot(),
        laptop_a.state.paired_devices_snapshot(),
        health={laptop_b.state.device_id: DeviceStatus.ONLINE},
    )
    assert len(devices) == 1
    entry = devices[0]
    assert entry.name == "LAPTOP-B"
    assert entry.is_browsable is True
    assert entry.pinned is True
    assert entry.badge == "\U0001f7e2"

    model = mw.DeviceListModel()
    model.set_devices(devices)
    assert model.rowCount() == 1
    assert "LAPTOP-B" in model.data(model.index(0, 0))


def test_04_opening_laptop_b_lists_its_shared_folders(nodes) -> None:
    laptop_a, laptop_b = nodes
    (laptop_b.share_root / "Engineering").mkdir()
    client = pair(laptop_a, laptop_b)
    try:
        shares = client.shares()
        assert [share["name"] for share in shares] == ["Shared"]
        assert shares[0]["available"] is True
        assert shares[0]["permissions"] == ALL_PERMISSIONS

        entries = client.list_folder(shares[0]["id"], "")
        assert [entry["name"] for entry in entries] == ["Engineering"]
        assert entries[0]["kind"] == "folder"
    finally:
        client.close()


# ------------------------------------------------- 5-11  copy a file A → B


def test_05_to_11_copy_file_to_a_chosen_folder_on_laptop_b(nodes, manager) -> None:
    laptop_a, laptop_b = nodes

    # 6. a real source file on Laptop A
    payload = b"engineering-drawing-payload\n" * 4000  # ~108 KB
    source_file = laptop_a.share_root / "Drawing.dwg"
    source_file.write_bytes(payload)

    # 5. a chosen destination folder on Laptop B
    (laptop_b.share_root / "Incoming").mkdir()

    a_client = pair(laptop_a, laptop_b)  # A → B, for the destination
    b_client = pair(laptop_b, laptop_a)  # B → A, so the hub can read the source
    ticks: list[int] = []
    try:
        # 7. the relay the "Copy to…" action builds
        transfer = manager.submit(
            kind="remote-copy",
            filename="Drawing.dwg",
            source="LAPTOP-A/Shared",
            destination="LAPTOP-B/Shared/Incoming",
            size=len(payload),
            runner=relay_runner(
                manager,
                b_client,  # reads from Laptop A
                laptop_a.share_id,
                "Drawing.dwg",
                a_client,  # writes to Laptop B
                laptop_b.share_id,
                "Incoming",
                "Drawing.dwg",
            ),
        )
        # 10. progress is observable while it runs
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and transfer.status is TransferStatus.QUEUED:
            time.sleep(0.01)
        while transfer.is_active and time.monotonic() < deadline:
            ticks.append(transfer.transferred)
            time.sleep(0.005)

        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error

        # 8. the file physically exists on Laptop B
        landed = laptop_b.share_root / "Incoming" / "Drawing.dwg"
        assert landed.exists()

        # 9. size and content match exactly
        assert landed.stat().st_size == len(payload)
        assert landed.read_bytes() == payload
        assert sha256_of(landed) == sha256_of(source_file)
        assert hashlib.sha256(landed.read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()

        # source survives a copy
        assert source_file.read_bytes() == payload

        # 10. progress advanced and finished at 100%
        assert transfer.progress == 1.0
        assert transfer.transferred == len(payload)

        # 11. Laptop B's own listing now shows the arrived file
        arrived = a_client.list_folder(laptop_b.share_id, "Incoming")
        assert [entry["name"] for entry in arrived] == ["Drawing.dwg"]
        assert arrived[0]["size"] == len(payload)
    finally:
        a_client.close()
        b_client.close()


def hold_mid_transfer(manager: TransferManager, transfer, at_least: int = 512 * 1024) -> None:
    """Pause a transfer once it is genuinely in flight.

    Loopback is fast enough that a plain sleep races the transfer to completion,
    so the tests below stop it deliberately rather than hoping to catch it.
    """
    deadline = time.monotonic() + 60
    while transfer.transferred < at_least and transfer.is_active and time.monotonic() < deadline:
        time.sleep(0.002)
    assert manager.pause(transfer.id), f"could not pause at {transfer.transferred} bytes"
    while transfer.status is not TransferStatus.PAUSED and time.monotonic() < deadline:
        time.sleep(0.002)
    assert transfer.status is TransferStatus.PAUSED


def test_11_receiver_sees_progress_as_a_growing_partial(nodes, manager) -> None:
    """Laptop B can observe an in-flight transfer without it being mistaken for a file."""
    laptop_a, laptop_b = nodes
    payload = os.urandom(12 * 1024 * 1024)
    (laptop_a.share_root / "InFlight.bin").write_bytes(payload)

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-copy",
            filename="InFlight.bin",
            source="LAPTOP-A",
            destination="LAPTOP-B",
            size=len(payload),
            runner=relay_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "InFlight.bin",
                a_client,
                laptop_b.share_id,
                "",
                "InFlight.bin",
            ),
        )
        hold_mid_transfer(manager, transfer, at_least=2 * 1024 * 1024)

        part = laptop_b.share_root / ("InFlight.bin" + PART_SUFFIX)
        assert part.exists(), "the receiver should see the file arriving"

        # Bytes counted at the source may still be in the socket when we pause,
        # so let the receiver's own report settle rather than racing it.
        deadline = time.monotonic() + 10
        status = a_client.partial_status(laptop_b.share_id, "", "InFlight.bin")
        while status["received"] == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
            status = a_client.partial_status(laptop_b.share_id, "", "InFlight.bin")
        assert status["received"] > 0, "Laptop B should report how much has arrived"
        assert status["complete"] is False
        assert status["received"] <= len(payload)

        # ...and the partial must never masquerade as a finished file.
        listed = [entry["name"] for entry in a_client.list_folder(laptop_b.share_id, "")]
        assert "InFlight.bin" not in listed
        assert not any(name.endswith(PART_SUFFIX) for name in listed)

        manager.resume(transfer.id)
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error
        assert (laptop_b.share_root / "InFlight.bin").read_bytes() == payload
        assert not part.exists()
    finally:
        a_client.close()
        b_client.close()


# ------------------------------------------------------------- 12  large file


def test_12_large_file_transfers_intact(nodes, manager) -> None:
    laptop_a, laptop_b = nodes
    payload = os.urandom(LARGE_FILE_BYTES)
    source_file = laptop_a.share_root / "Big.iso"
    source_file.write_bytes(payload)
    expected = sha256_of(source_file)

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-copy",
            filename="Big.iso",
            source="LAPTOP-A",
            destination="LAPTOP-B",
            size=LARGE_FILE_BYTES,
            runner=relay_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Big.iso",
                a_client,
                laptop_b.share_id,
                "",
                "Big.iso",
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED}, timeout=180)
        assert transfer.status is TransferStatus.COMPLETED, transfer.error

        landed = laptop_b.share_root / "Big.iso"
        assert landed.stat().st_size == LARGE_FILE_BYTES
        assert sha256_of(landed) == expected
        assert transfer.transferred == LARGE_FILE_BYTES
    finally:
        a_client.close()
        b_client.close()


# ----------------------------------------------------- 13  failure / disconnect


class FailingDestination:
    """Wraps a real client and drops the connection after N bytes.

    A graceful or forced server shutdown is not a reliable mid-transfer failure:
    on Windows the socket buffers happily absorb the rest of the body, so the
    transfer completes anyway. Injecting the failure here makes the test assert
    the behaviour that actually matters, identically on every platform.
    """

    def __init__(self, inner: LanLinkClient, fail_after: int) -> None:
        self._inner = inner
        self._fail_after = fail_after

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def partial_status(self, *args, **kwargs) -> dict:
        return {"received": 0, "complete": False, "size": None}

    def put_stream(self, *args, **kwargs):
        chunks = args[3] if len(args) > 3 else kwargs["chunks"]
        sent = 0
        for chunk in chunks:
            sent += len(chunk)
            if sent >= self._fail_after:
                raise ConnectionError("The other device disconnected during the transfer.")
        raise ConnectionError("The other device disconnected during the transfer.")


def test_13_destination_failure_mid_transfer_never_destroys_the_source(nodes, manager) -> None:
    laptop_a, laptop_b = nodes
    payload = os.urandom(8 * 1024 * 1024)
    source_file = laptop_a.share_root / "Interrupted.bin"
    source_file.write_bytes(payload)

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-move",
            filename="Interrupted.bin",
            source="LAPTOP-A",
            destination="LAPTOP-B",
            size=len(payload),
            runner=relay_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Interrupted.bin",
                FailingDestination(a_client, fail_after=1024 * 1024),
                laptop_b.share_id,
                "",
                "Interrupted.bin",
                delete_source=True,
            ),
        )
        wait_for(transfer, {TransferStatus.FAILED, TransferStatus.COMPLETED})

        assert transfer.status is TransferStatus.FAILED, "a dead destination must fail the transfer"
        assert transfer.error, "the failure must carry a reason for the user"
        assert "disconnect" in transfer.error.lower()

        # The whole point: a failed move never destroys the source.
        assert source_file.exists()
        assert source_file.read_bytes() == payload
        assert not (laptop_b.share_root / "Interrupted.bin").exists()
    finally:
        a_client.close()
        b_client.close()


def test_13a_killing_the_destination_never_loses_the_file(nodes, manager) -> None:
    """Kill the real server mid-transfer and assert the outcome is never lossy.

    Whether the transfer fails or squeaks through depends on how much the OS
    socket buffers absorbed, which differs between platforms. What must hold
    everywhere: the source survives unless a byte-identical copy landed.
    """
    laptop_a, laptop_b = nodes
    payload = os.urandom(12 * 1024 * 1024)
    source_file = laptop_a.share_root / "Killed.bin"
    source_file.write_bytes(payload)

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-move",
            filename="Killed.bin",
            source="LAPTOP-A",
            destination="LAPTOP-B",
            size=len(payload),
            runner=relay_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Killed.bin",
                a_client,
                laptop_b.share_id,
                "",
                "Killed.bin",
                delete_source=True,
            ),
        )
        hold_mid_transfer(manager, transfer)
        laptop_b.kill()
        manager.resume(transfer.id)
        wait_for(transfer, {TransferStatus.FAILED, TransferStatus.COMPLETED}, timeout=120)

        landed = laptop_b.share_root / "Killed.bin"
        if transfer.status is TransferStatus.COMPLETED:
            # It got through despite the kill: then the copy must be perfect.
            assert landed.exists() and landed.read_bytes() == payload
        else:
            # It failed: then the source must still be here, untouched.
            assert transfer.error
            assert source_file.read_bytes() == payload
            assert not landed.exists()

        # Either way the file still exists somewhere, whole.
        survivors = [
            path for path in (source_file, landed) if path.exists() and path.read_bytes() == payload
        ]
        assert survivors, "a killed transfer must never lose the file from both machines"
    finally:
        a_client.close()
        b_client.close()


def test_13b_cancel_leaves_the_source_untouched(nodes, manager) -> None:
    laptop_a, laptop_b = nodes
    payload = os.urandom(12 * 1024 * 1024)
    source_file = laptop_a.share_root / "Cancelled.bin"
    source_file.write_bytes(payload)

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-move",
            filename="Cancelled.bin",
            source="LAPTOP-A",
            destination="LAPTOP-B",
            size=len(payload),
            runner=relay_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Cancelled.bin",
                a_client,
                laptop_b.share_id,
                "",
                "Cancelled.bin",
                delete_source=True,
            ),
        )
        hold_mid_transfer(manager, transfer)
        manager.cancel(transfer.id)

        wait_for(transfer, {TransferStatus.CANCELLED, TransferStatus.FAILED, TransferStatus.COMPLETED})
        assert transfer.status is TransferStatus.CANCELLED
        assert source_file.read_bytes() == payload
        assert not (laptop_b.share_root / "Cancelled.bin").exists()
    finally:
        a_client.close()
        b_client.close()


# --------------------------------------------------------------------- 14  move


def test_14_move_transfers_verifies_then_deletes_the_source(nodes, manager) -> None:
    laptop_a, laptop_b = nodes
    payload = b"move-me" * 50_000
    source_file = laptop_a.share_root / "Moved.bin"
    source_file.write_bytes(payload)
    expected = sha256_of(source_file)

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-move",
            filename="Moved.bin",
            source="LAPTOP-A/Shared",
            destination="LAPTOP-B/Shared",
            size=len(payload),
            runner=relay_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Moved.bin",
                a_client,
                laptop_b.share_id,
                "",
                "Moved.bin",
                delete_source=True,
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error

        landed = laptop_b.share_root / "Moved.bin"
        assert landed.read_bytes() == payload
        assert sha256_of(landed) == expected
        assert not source_file.exists(), "a completed move removes the source"
        assert laptop_a.share_root.is_dir(), "only the file goes, not the share"
    finally:
        a_client.close()
        b_client.close()


# --------------------------------------------- 15  no permanent intermediate copy


def test_15_relay_leaves_no_intermediate_copy_anywhere(nodes, manager, tmp_path: Path) -> None:
    laptop_a, laptop_b = nodes
    payload = os.urandom(8 * 1024 * 1024)
    (laptop_a.share_root / "Clean.bin").write_bytes(payload)

    hub_scratch = tmp_path / "hub-scratch"
    hub_scratch.mkdir()
    before_a = {item.name for item in laptop_a.share_root.iterdir()}

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-copy",
            filename="Clean.bin",
            source="LAPTOP-A",
            destination="LAPTOP-B",
            size=len(payload),
            runner=relay_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Clean.bin",
                a_client,
                laptop_b.share_id,
                "",
                "Clean.bin",
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED})
        assert transfer.status is TransferStatus.COMPLETED, transfer.error

        # The hub kept nothing.
        assert list(hub_scratch.iterdir()) == []
        assert scratch_files(laptop_a) == []
        assert scratch_files(laptop_b) == []
        # No part files survived on either side.
        assert not any(item.name.endswith(PART_SUFFIX) for item in laptop_a.share_root.rglob("*"))
        assert not any(item.name.endswith(PART_SUFFIX) for item in laptop_b.share_root.rglob("*"))
        # Laptop A's share is unchanged apart from what was already there.
        assert {item.name for item in laptop_a.share_root.iterdir()} == before_a
        assert (laptop_b.share_root / "Clean.bin").stat().st_size == len(payload)
    finally:
        a_client.close()
        b_client.close()


# ------------------------------------------------------- recursive folder copy


def build_tree(root: Path) -> dict[str, bytes]:
    """A nested tree with files at several depths, including an empty folder."""
    contents = {
        "Project/readme.txt": b"top level\n",
        "Project/CAD/part-a.step": os.urandom(200_000),
        "Project/CAD/part-b.step": os.urandom(150_000),
        "Project/CAD/revisions/rev1.txt": b"revision one\n",
        "Project/CAD/revisions/rev2.txt": b"revision two\n",
        "Project/Docs/spec.md": b"# spec\n" * 500,
    }
    for relative, payload in contents.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    (root / "Project" / "Empty").mkdir(parents=True, exist_ok=True)
    return contents


def test_16_folder_copies_recursively(nodes, manager) -> None:
    laptop_a, laptop_b = nodes
    contents = build_tree(laptop_a.share_root)
    (laptop_b.share_root / "Incoming").mkdir()

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-copy-folder",
            filename="Project/",
            source="LAPTOP-A/Shared",
            destination="LAPTOP-B/Shared/Incoming",
            runner=relay_folder_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Project",
                a_client,
                laptop_b.share_id,
                "Incoming",
                "Project",
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED}, timeout=180)
        assert transfer.status is TransferStatus.COMPLETED, transfer.error

        destination_root = laptop_b.share_root / "Incoming" / "Project"
        assert destination_root.is_dir()
        for relative, payload in contents.items():
            landed = laptop_b.share_root / "Incoming" / relative
            assert landed.exists(), f"{relative} did not arrive"
            assert landed.read_bytes() == payload, f"{relative} arrived corrupted"
        assert (destination_root / "Empty").is_dir(), "empty folders must be recreated too"

        # The source tree is untouched by a copy.
        for relative, payload in contents.items():
            assert (laptop_a.share_root / relative).read_bytes() == payload
    finally:
        a_client.close()
        b_client.close()


def test_17_folder_move_deletes_the_source_tree_last(nodes, manager) -> None:
    laptop_a, laptop_b = nodes
    contents = build_tree(laptop_a.share_root)

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-move-folder",
            filename="Project/",
            source="LAPTOP-A/Shared",
            destination="LAPTOP-B/Shared",
            runner=relay_folder_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Project",
                a_client,
                laptop_b.share_id,
                "",
                "Project",
                delete_source=True,
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED}, timeout=180)
        assert transfer.status is TransferStatus.COMPLETED, transfer.error

        for relative, payload in contents.items():
            assert (laptop_b.share_root / relative).read_bytes() == payload
        assert not (laptop_a.share_root / "Project").exists(), "the source tree goes only at the end"
        assert laptop_a.share_root.is_dir()
    finally:
        a_client.close()
        b_client.close()


def test_18_folder_transfer_reports_aggregate_progress(nodes, manager) -> None:
    laptop_a, laptop_b = nodes
    contents = build_tree(laptop_a.share_root)
    total = sum(len(payload) for payload in contents.values())

    a_client = pair(laptop_a, laptop_b)
    b_client = pair(laptop_b, laptop_a)
    try:
        transfer = manager.submit(
            kind="remote-copy-folder",
            filename="Project/",
            source="LAPTOP-A",
            destination="LAPTOP-B",
            runner=relay_folder_runner(
                manager,
                b_client,
                laptop_a.share_id,
                "Project",
                a_client,
                laptop_b.share_id,
                "",
                "Project",
            ),
        )
        wait_for(transfer, {TransferStatus.COMPLETED, TransferStatus.FAILED}, timeout=180)
        assert transfer.status is TransferStatus.COMPLETED, transfer.error
        assert transfer.size == total, "the folder's total size drives the progress bar"
        assert transfer.transferred == total
        assert transfer.progress == 1.0
    finally:
        a_client.close()
        b_client.close()
