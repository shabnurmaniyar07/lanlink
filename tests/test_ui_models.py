"""Phase 3: Qt model/view layer, exercised headlessly (offscreen platform)."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lanlink.discovery import NearbyDevice  # noqa: E402
from lanlink.state import PairedDevice, RemoteDevice  # noqa: E402
from lanlink.transfers import Transfer, TransferStatus  # noqa: E402
from lanlink.ui.browser_model import (  # noqa: E402
    EntryFilterProxy,
    RemoteEntryModel,
    format_size,
    format_time,
)
from lanlink.ui.devices import (  # noqa: E402
    DeviceListModel,
    DeviceStatus,
    HealthTracker,
    merge_devices,
)
from lanlink.ui.jobs import JobRunner  # noqa: E402
from lanlink.ui.pairing import PairingApproval  # noqa: E402
from lanlink.ui.transfer_model import TransferTableModel, format_eta, format_rate, summarise  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ------------------------------------------------------------------ device merge


def nearby(device_id: str, name: str, host: str = "192.168.1.50") -> NearbyDevice:
    return NearbyDevice(
        id=device_id,
        name=name,
        host=host,
        port=8765,
        api="v1",
        service_name=f"{name}._lanlink._tcp.local.",
        last_seen=time.time(),
    )


def remote(device_id: str, name: str, url: str = "https://192.168.1.50:8765") -> RemoteDevice:
    return RemoteDevice(id=device_id, name=name, base_url=url, token="t", paired_at=1.0)


def paired(device_id: str, name: str) -> PairedDevice:
    return PairedDevice(id=device_id, name=name, token_hash="h", paired_at=1.0, last_seen=2.0)


def test_one_device_from_three_sources_is_one_row() -> None:
    devices = merge_devices(
        [nearby("dev-1", "Office PC")],
        [remote("dev-1", "Office PC")],
        [paired("dev-1", "Office PC")],
    )
    assert len(devices) == 1
    device = devices[0]
    assert device.discovered and device.paired_out and device.paired_in


def test_distinct_devices_stay_separate() -> None:
    devices = merge_devices(
        [nearby("dev-1", "Office PC"), nearby("dev-2", "Design PC", "192.168.1.51")],
        [remote("dev-3", "Laptop", "https://192.168.1.52:8765")],
        [],
    )
    assert {device.id for device in devices} == {"dev-1", "dev-2", "dev-3"}


def test_ip_change_updates_address_without_splitting_identity() -> None:
    devices = merge_devices(
        [nearby("dev-1", "Office PC", "192.168.1.99")],
        [remote("dev-1", "Office PC", "https://192.168.1.50:8765")],
        [],
    )
    assert len(devices) == 1
    assert devices[0].address == "https://192.168.1.99:8765", "the mDNS address must win after a DHCP change"


def test_paired_only_device_is_offline_without_discovery() -> None:
    devices = merge_devices([], [remote("dev-1", "Office PC")], [])
    assert devices[0].status is DeviceStatus.OFFLINE
    assert devices[0].badge == "⚪"


def test_discovered_device_starts_connecting() -> None:
    devices = merge_devices([nearby("dev-1", "Office PC")], [], [])
    assert devices[0].status is DeviceStatus.CONNECTING


def test_health_result_drives_the_badge() -> None:
    health = {"dev-1": DeviceStatus.ONLINE, "dev-2": DeviceStatus.ERROR}
    devices = merge_devices(
        [nearby("dev-1", "A"), nearby("dev-2", "B", "192.168.1.51")],
        [],
        [],
        health=health,
        errors={"dev-2": "connection refused"},
    )
    by_id = {device.id: device for device in devices}
    assert by_id["dev-1"].badge == "\U0001f7e2"
    assert by_id["dev-2"].badge == "\U0001f534"
    assert "connection refused" in by_id["dev-2"].detail


def test_online_devices_sort_first() -> None:
    devices = merge_devices(
        [nearby("dev-1", "Zebra"), nearby("dev-2", "Alpha", "192.168.1.51")],
        [],
        [],
        health={"dev-1": DeviceStatus.ONLINE, "dev-2": DeviceStatus.OFFLINE},
    )
    assert [device.name for device in devices] == ["Zebra", "Alpha"]


def test_only_paired_devices_are_browsable() -> None:
    devices = merge_devices([nearby("dev-1", "A")], [remote("dev-2", "B")], [])
    by_id = {device.id: device for device in devices}
    assert by_id["dev-1"].is_browsable is False, "an unpaired device must not be browsable"
    assert by_id["dev-2"].is_browsable is True


def test_health_tracker_expires_stale_results() -> None:
    tracker = HealthTracker()
    tracker.mark_online("dev-1")
    assert tracker.statuses["dev-1"] is DeviceStatus.ONLINE
    tracker.checked_at["dev-1"] = time.time() - 120
    tracker.expire(older_than_seconds=30)
    assert tracker.statuses["dev-1"] is DeviceStatus.OFFLINE


def test_device_list_model(qapp) -> None:
    model = DeviceListModel()
    model.set_devices(merge_devices([nearby("dev-1", "Office PC")], [], []))
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert "Office PC" in model.data(index, Qt.ItemDataRole.DisplayRole)
    assert model.data(index, DeviceListModel.DeviceRole).id == "dev-1"
    assert model.row_of("dev-1") == 0
    assert model.row_of("missing") == -1


# ------------------------------------------------------------------ file browser


ENTRIES = [
    {"name": "notes.txt", "kind": "file", "path": "notes.txt", "size": 120, "modified_at": 1000.0},
    {"name": "Projects", "kind": "folder", "path": "Projects", "size": None, "modified_at": 900.0},
    {"name": "archive.zip", "kind": "file", "path": "archive.zip", "size": 4096, "modified_at": 1100.0},
]


def test_entry_model_columns(qapp) -> None:
    model = RemoteEntryModel()
    model.set_entries(ENTRIES)
    assert model.rowCount() == 3
    assert model.columnCount() == 4
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "notes.txt"
    assert model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole) == "120 B"
    assert model.data(model.index(0, 2), Qt.ItemDataRole.DisplayRole) == "Text Document"
    assert model.data(model.index(1, 2), Qt.ItemDataRole.DisplayRole) == "Folder"
    assert model.data(model.index(2, 2), Qt.ItemDataRole.DisplayRole) == "ZIP Archive"


def test_folders_sort_above_files_in_both_directions(qapp) -> None:
    model = RemoteEntryModel()
    model.set_entries(ENTRIES)
    proxy = EntryFilterProxy()
    proxy.setSourceModel(model)

    proxy.sort(0, Qt.SortOrder.AscendingOrder)
    assert proxy.entry_at(0)["kind"] == "folder"

    proxy.sort(1, Qt.SortOrder.DescendingOrder)
    kinds = [proxy.entry_at(row)["kind"] for row in range(proxy.rowCount())]
    assert kinds[-1] == "folder", "descending puts the folder group last but keeps it grouped"


def test_search_filters_by_name(qapp) -> None:
    model = RemoteEntryModel()
    model.set_entries(ENTRIES)
    proxy = EntryFilterProxy()
    proxy.setSourceModel(model)

    proxy.set_search("zip")
    assert proxy.rowCount() == 1
    assert proxy.entry_at(0)["name"] == "archive.zip"

    proxy.set_search("PROJ")
    assert proxy.rowCount() == 1
    assert proxy.entry_at(0)["name"] == "Projects"

    proxy.set_search("")
    assert proxy.rowCount() == 3


@pytest.mark.parametrize(
    ("size", "expected"),
    [(None, ""), (0, "0 B"), (512, "512 B"), (1536, "1.5 KB"), (5 * 1024 * 1024, "5.0 MB")],
)
def test_format_size(size, expected) -> None:
    assert format_size(size) == expected


def test_format_time_handles_missing() -> None:
    assert format_time(None) == ""
    assert format_time(0) == ""
    assert len(format_time(1_700_000_000)) == 16


# -------------------------------------------------------------------- transfers


def make_transfer(**overrides) -> Transfer:
    values = {
        "id": "t1",
        "kind": "download",
        "filename": "drawing.dwg",
        "source": "Office PC/Projects",
        "destination": "C:/Downloads",
        "size": 1000,
        "transferred": 250,
        "status": TransferStatus.RUNNING,
    }
    values.update(overrides)
    return Transfer(**values)


def test_transfer_model_shows_progress_and_status(qapp) -> None:
    model = TransferTableModel()
    model.set_transfers([make_transfer(rate=100.0)])
    assert model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole) == "drawing.dwg"
    assert model.data(model.index(0, 3), Qt.ItemDataRole.DisplayRole) == "1000 B"
    assert model.data(model.index(0, 4), Qt.ItemDataRole.DisplayRole) == "25%"
    assert model.data(model.index(0, 7), Qt.ItemDataRole.DisplayRole) == "Transferring"
    assert model.data(model.index(0, 4), TransferTableModel.ProgressRole) == pytest.approx(0.25)


def test_transfer_model_surfaces_errors(qapp) -> None:
    model = TransferTableModel()
    model.set_transfers([make_transfer(status=TransferStatus.FAILED, error="connection reset")])
    assert model.data(model.index(0, 7), Qt.ItemDataRole.DisplayRole) == "connection reset"


def test_transfer_model_updates_in_place_when_shape_is_unchanged(qapp) -> None:
    model = TransferTableModel()
    transfer = make_transfer()
    model.set_transfers([transfer])

    resets = []
    model.modelAboutToBeReset.connect(lambda: resets.append(1))
    transfer.transferred = 900
    model.set_transfers([transfer])

    assert resets == [], "a progress tick must not reset the model and drop the selection"
    assert model.data(model.index(0, 4), Qt.ItemDataRole.DisplayRole) == "90%"


def test_history_model_shows_finish_time(qapp) -> None:
    model = TransferTableModel(history=True)
    model.set_transfers(
        [make_transfer(status=TransferStatus.COMPLETED, transferred=1000, finished_at=1_700_000_000)]
    )
    assert model.headerData(6, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Finished"
    assert model.data(model.index(0, 4), Qt.ItemDataRole.DisplayRole) == "100%"
    assert model.data(model.index(0, 6), Qt.ItemDataRole.DisplayRole) != ""


def test_summarise() -> None:
    assert summarise([]) == "No transfers running"
    assert summarise([make_transfer(status=TransferStatus.COMPLETED)]) == "No transfers running"
    text = summarise([make_transfer(), make_transfer(id="t2")])
    assert text.startswith("2 files transferring")


@pytest.mark.parametrize(("seconds", "expected"), [(None, ""), (5, "5s"), (75, "1m 15s"), (3700, "1h 01m")])
def test_format_eta(seconds, expected) -> None:
    assert format_eta(seconds) == expected


def test_format_rate() -> None:
    assert format_rate(0) == ""
    assert format_rate(2048) == "2.0 KB/s"


# ------------------------------------------------------------------- job runner


def test_jobs_run_off_the_calling_thread(qapp) -> None:
    import threading

    runner = JobRunner(max_threads=2)
    seen: dict[str, object] = {}
    done = threading.Event()

    def work() -> str:
        seen["worker"] = threading.current_thread().name
        return "value"

    job = runner.run(work)
    job.signals.finished.connect(lambda result: (seen.__setitem__("result", result), done.set()))
    assert runner.wait(5000)
    qapp.processEvents()
    assert seen["worker"] != threading.current_thread().name
    assert seen.get("result") == "value"


def test_job_failure_is_reported_as_text(qapp) -> None:
    runner = JobRunner(max_threads=1)
    captured: list[str] = []

    job = runner.run(lambda: (_ for _ in ()).throw(RuntimeError("node unreachable")))
    job.signals.failed.connect(captured.append)
    assert runner.wait(5000)
    qapp.processEvents()
    assert captured == ["node unreachable"]


# ---------------------------------------------------------------- pair approval


def test_pairing_approval_round_trip() -> None:
    import threading

    approval = PairingApproval(timeout=5)
    result: dict[str, bool] = {}

    thread = threading.Thread(target=lambda: result.__setitem__("ok", approval.request("client-1", "Phone")))
    thread.start()

    deadline = time.time() + 5
    pending = None
    while time.time() < deadline and pending is None:
        pending = approval.take()
        time.sleep(0.01)
    assert pending is not None
    assert pending.client_name == "Phone"

    pending.answer(True)
    thread.join(timeout=5)
    assert result["ok"] is True


def test_pairing_approval_times_out_to_refusal() -> None:
    approval = PairingApproval(timeout=0.05)
    assert approval.request("client-1", "Phone") is False


def test_pairing_approval_refuses_a_second_concurrent_request() -> None:
    import threading

    approval = PairingApproval(timeout=2)
    threading.Thread(target=lambda: approval.request("client-1", "First"), daemon=True).start()
    time.sleep(0.1)
    assert approval.request("client-2", "Second") is False
