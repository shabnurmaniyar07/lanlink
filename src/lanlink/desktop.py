from __future__ import annotations

import sys
import time
from pathlib import Path

from httpx import HTTPError
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .client import LanLinkClient
from .discovery import DiscoveryBrowser
from .server import LocalService
from .state import HubState, RemoteDevice, SettingsCorruptError

PERMISSION_LABELS = {
    "r": "Read only",
    "rw": "Read + write",
    "rwd": "Read + write + delete",
}


class HubWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.state = HubState()
        self.service = LocalService(self.state)
        self.discovery_browser = DiscoveryBrowser(local_device_id=self.state.device_id)
        self.remote_current_device_id: str | None = None
        self.remote_current_share_id: str | None = None
        self.remote_current_path = ""
        self.remote_current_share_name = ""
        self.remote_items: list[dict] = []
        self.setWindowTitle("LanLink Hub")
        self.resize(940, 720)

        root = QWidget()
        layout = QVBoxLayout(root)
        header = QLabel(
            "<h2>LanLink Hub</h2><p>Share only the folders you choose with "
            "paired devices on this network.</p>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        details = QFormLayout()
        self.address = QLabel()
        self.code = QLabel()
        self.code.setStyleSheet("font-size: 20px; font-weight: 600;")
        details.addRow("This device is reachable at:", self.address)
        details.addRow("Pairing code:", self.code)
        layout.addLayout(details)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_shares_tab(), "Shared folders")
        self.tabs.addTab(self._build_devices_tab(), "Devices")
        self.remote_tab_index = self.tabs.addTab(self._build_remote_tab(), "Remote browser")
        layout.addWidget(self.tabs)
        self.setCentralWidget(root)

        if not self.service.start():
            QMessageBox.warning(
                self,
                "LanLink could not start",
                self.service.last_error or "The local network service did not start.",
            )
        self.discovery_browser.start()
        self.refresh()

        self.code_timer = QTimer(self)
        self.code_timer.timeout.connect(self.refresh_code)
        self.code_timer.start(1_000)

        self.devices_timer = QTimer(self)
        self.devices_timer.timeout.connect(self.refresh_devices)
        self.devices_timer.start(2_000)

    def _build_shares_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        button_row = QHBoxLayout()
        self.rotate_button = QPushButton("Allow a device to pair")
        self.rotate_button.clicked.connect(self.toggle_pairing)
        self.add_button = QPushButton("Add shared folder")
        self.add_button.clicked.connect(self.add_folder)
        self.remove_button = QPushButton("Stop sharing selected folder")
        self.remove_button.clicked.connect(self.remove_selected)
        self.permissions_button = QPushButton("Change permissions")
        self.permissions_button.clicked.connect(self.cycle_permissions)
        button_row.addWidget(self.rotate_button)
        button_row.addStretch()
        button_row.addWidget(self.permissions_button)
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.remove_button)
        layout.addLayout(button_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Shared name", "Access", "Folder"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        return tab

    def _build_devices_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        paired_row = QHBoxLayout()
        paired_row.addWidget(QLabel("<b>Paired devices</b>"))
        paired_row.addStretch()
        self.revoke_button = QPushButton("Revoke selected device")
        self.revoke_button.clicked.connect(self.revoke_selected_pairing)
        paired_row.addWidget(self.revoke_button)
        layout.addLayout(paired_row)

        self.paired_status = QLabel()
        self.paired_status.setWordWrap(True)
        layout.addWidget(self.paired_status)

        self.paired_table = QTableWidget(0, 3)
        self.paired_table.setHorizontalHeaderLabels(["Device", "Client ID", "Paired"])
        self.paired_table.horizontalHeader().setStretchLastSection(True)
        self.paired_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.paired_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.paired_table)

        nearby_row = QHBoxLayout()
        nearby_row.addWidget(QLabel("<b>Nearby LanLink devices</b>"))
        nearby_row.addStretch()
        self.use_nearby_button = QPushButton("Use in remote browser")
        self.use_nearby_button.clicked.connect(self.use_selected_nearby_for_remote)
        nearby_row.addWidget(self.use_nearby_button)
        self.open_device_button = QPushButton("Open selected device")
        self.open_device_button.clicked.connect(self.open_selected_device)
        nearby_row.addWidget(self.open_device_button)
        layout.addLayout(nearby_row)

        self.nearby_status = QLabel()
        self.nearby_status.setWordWrap(True)
        layout.addWidget(self.nearby_status)

        self.nearby_table = QTableWidget(0, 3)
        self.nearby_table.setHorizontalHeaderLabels(["Device", "Address", "Status"])
        self.nearby_table.horizontalHeader().setStretchLastSection(True)
        self.nearby_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.nearby_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.nearby_table.itemDoubleClicked.connect(lambda _: self.open_selected_device())
        layout.addWidget(self.nearby_table)
        return tab

    def _build_remote_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        pair_details = QFormLayout()
        self.remote_url_input = QLineEdit()
        self.remote_url_input.setPlaceholderText("http://192.168.1.20:8765")
        self.remote_pair_code_input = QLineEdit()
        self.remote_pair_code_input.setPlaceholderText("8-digit code from the other computer")
        pair_details.addRow("Remote address:", self.remote_url_input)
        pair_details.addRow("Pairing code:", self.remote_pair_code_input)
        layout.addLayout(pair_details)

        pair_row = QHBoxLayout()
        self.pair_remote_button = QPushButton("Pair remote device")
        self.pair_remote_button.clicked.connect(self.pair_remote_device)
        self.browse_remote_button = QPushButton("Browse selected remote")
        self.browse_remote_button.clicked.connect(self.browse_selected_remote)
        self.remove_remote_button = QPushButton("Forget selected remote")
        self.remove_remote_button.clicked.connect(self.remove_selected_remote)
        pair_row.addWidget(self.pair_remote_button)
        pair_row.addStretch()
        pair_row.addWidget(self.browse_remote_button)
        pair_row.addWidget(self.remove_remote_button)
        layout.addLayout(pair_row)

        self.remote_status = QLabel("Pair with another LanLink computer to browse its shared folders here.")
        self.remote_status.setWordWrap(True)
        layout.addWidget(self.remote_status)

        self.remote_devices_table = QTableWidget(0, 3)
        self.remote_devices_table.setHorizontalHeaderLabels(["Remote device", "Address", "Paired"])
        self.remote_devices_table.horizontalHeader().setStretchLastSection(True)
        self.remote_devices_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.remote_devices_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.remote_devices_table.itemDoubleClicked.connect(lambda _: self.browse_selected_remote())
        layout.addWidget(self.remote_devices_table)

        browser_row = QHBoxLayout()
        self.remote_back_button = QPushButton("Back")
        self.remote_back_button.clicked.connect(self.remote_back)
        self.download_remote_button = QPushButton("Download selected file")
        self.download_remote_button.clicked.connect(self.download_selected_remote_file)
        browser_row.addWidget(QLabel("<b>Remote files</b>"))
        browser_row.addStretch()
        browser_row.addWidget(self.remote_back_button)
        browser_row.addWidget(self.download_remote_button)
        layout.addLayout(browser_row)

        self.remote_items_table = QTableWidget(0, 3)
        self.remote_items_table.setHorizontalHeaderLabels(["Name", "Type", "Size"])
        self.remote_items_table.horizontalHeader().setStretchLastSection(True)
        self.remote_items_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.remote_items_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.remote_items_table.itemDoubleClicked.connect(self.open_remote_item)
        layout.addWidget(self.remote_items_table)
        return tab

    def refresh(self) -> None:
        self.address.setText(f"{self.service.url}  (same Wi-Fi or hotspot only)")
        self.refresh_code()
        self.refresh_shares()
        self.refresh_devices()
        self.refresh_remote_devices_table()

    def refresh_devices(self) -> None:
        self.refresh_pairings()
        self.refresh_nearby_devices()

    def refresh_shares(self) -> None:
        shares = list(self.state.shares.values())
        self.table.setRowCount(len(shares))
        for row, share in enumerate(shares):
            name = QTableWidgetItem(share.name)
            name.setData(Qt.ItemDataRole.UserRole, share.id)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(PERMISSION_LABELS[share.permissions]))
            folder = QTableWidgetItem(share.path)
            if not share.available:
                folder.setText(f"{share.path}   (unavailable right now)")
            self.table.setItem(row, 2, folder)

    def refresh_pairings(self) -> None:
        devices = self.state.paired_devices_snapshot()
        self.paired_status.setText(
            f"{len(devices)} paired device{'s' if len(devices) != 1 else ''} can access your shared folders."
            if devices
            else "No paired phones or computers yet."
        )
        self.paired_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            name = QTableWidgetItem(device.name or "Unnamed device")
            name.setData(Qt.ItemDataRole.UserRole, device.id)
            paired_at = time.strftime("%Y-%m-%d %H:%M", time.localtime(device.paired_at))
            self.paired_table.setItem(row, 0, name)
            self.paired_table.setItem(row, 1, QTableWidgetItem(device.id))
            self.paired_table.setItem(row, 2, QTableWidgetItem(paired_at))

    def refresh_nearby_devices(self) -> None:
        if not self.discovery_browser.zeroconf:
            detail = self.discovery_browser.last_error or "Nearby discovery is not running."
            self.nearby_status.setText(detail)
            self.nearby_table.setRowCount(0)
            return

        devices = self.discovery_browser.devices()
        self.nearby_status.setText(
            f"{len(devices)} nearby LanLink device{'s' if len(devices) != 1 else ''} found."
            if devices
            else "No other LanLink devices found yet. Start LanLink on another\n"
            "computer on the same Wi-Fi or hotspot."
        )
        self.nearby_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            name = QTableWidgetItem(device.name)
            name.setData(Qt.ItemDataRole.UserRole, device.url)
            self.nearby_table.setItem(row, 0, name)
            self.nearby_table.setItem(row, 1, QTableWidgetItem(device.url))
            self.nearby_table.setItem(row, 2, QTableWidgetItem("Ready to pair/open"))

    def refresh_remote_devices_table(self) -> None:
        devices = self.state.remote_devices_snapshot()
        self.remote_devices_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            name = QTableWidgetItem(device.name)
            name.setData(Qt.ItemDataRole.UserRole, device.id)
            paired_at = time.strftime("%Y-%m-%d %H:%M", time.localtime(device.paired_at))
            self.remote_devices_table.setItem(row, 0, name)
            self.remote_devices_table.setItem(row, 1, QTableWidgetItem(device.base_url))
            self.remote_devices_table.setItem(row, 2, QTableWidgetItem(paired_at))

    def _selected_nearby_url(self) -> str | None:
        row = self.nearby_table.currentRow()
        if row < 0:
            return None
        item = self.nearby_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selected_remote_device(self) -> RemoteDevice | None:
        row = self.remote_devices_table.currentRow()
        if row < 0:
            return None
        item = self.remote_devices_table.item(row, 0)
        device_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self.state.get_remote_device(device_id) if device_id else None

    def _normalize_remote_url(self, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if "://" not in value:
            value = f"http://{value}"
        return value.rstrip("/")

    def _remote_client(self, device: RemoteDevice) -> LanLinkClient:
        return LanLinkClient(device.base_url, token=device.token)

    def _remote_parent_path(self) -> str:
        parts = [part for part in self.remote_current_path.split("/") if part]
        return "/".join(parts[:-1])

    def _format_size(self, size: int | None) -> str:
        if size is None:
            return ""
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        unit = units[0]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                break
            value /= 1024
        return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"

    def _set_remote_items(self, items: list[dict]) -> None:
        self.remote_items = items
        self.remote_items_table.setRowCount(len(items))
        for row, item in enumerate(items):
            name = QTableWidgetItem(item["name"])
            name.setData(Qt.ItemDataRole.UserRole, row)
            self.remote_items_table.setItem(row, 0, name)
            self.remote_items_table.setItem(row, 1, QTableWidgetItem(item["kind"]))
            self.remote_items_table.setItem(row, 2, QTableWidgetItem(self._format_size(item.get("size"))))

    def use_selected_nearby_for_remote(self) -> None:
        url = self._selected_nearby_url()
        if not url:
            QMessageBox.information(self, "No device selected", "Select a nearby LanLink device first.")
            return
        self.remote_url_input.setText(url)
        self.tabs.setCurrentIndex(self.remote_tab_index)

    def pair_remote_device(self) -> None:
        base_url = self._normalize_remote_url(self.remote_url_input.text())
        pair_code = self.remote_pair_code_input.text().strip()
        if not base_url or not pair_code:
            QMessageBox.information(self, "Pair remote device", "Enter the remote address and pairing code.")
            return

        client = LanLinkClient(base_url)
        try:
            result = client.pair(self.state.device_name, pair_code, client_id=self.state.device_id)
        except HTTPError as error:
            QMessageBox.warning(self, "Pairing failed", str(error))
            return
        finally:
            client.close()

        token = result["token"]
        device = result["device"]
        saved = self.state.upsert_remote_device(device["id"], device["name"], base_url, token)
        self.remote_pair_code_input.clear()
        self.refresh_remote_devices_table()
        self.remote_status.setText(f"Paired with {saved.name}. Select it and choose Browse selected remote.")

    def browse_selected_remote(self) -> None:
        device = self._selected_remote_device()
        if not device:
            QMessageBox.information(self, "No remote selected", "Select a paired remote device first.")
            return
        self.load_remote_root(device)

    def remove_selected_remote(self) -> None:
        device = self._selected_remote_device()
        if not device:
            return
        if self.state.remove_remote_device(device.id):
            if self.remote_current_device_id == device.id:
                self.remote_current_device_id = None
                self.remote_current_share_id = None
                self.remote_current_path = ""
                self.remote_current_share_name = ""
                self._set_remote_items([])
            self.refresh_remote_devices_table()
            self.remote_status.setText(f"Forgot {device.name}.")

    def load_remote_root(self, device: RemoteDevice) -> None:
        client = self._remote_client(device)
        try:
            shares = client.shares()
        except HTTPError as error:
            QMessageBox.warning(self, "Cannot browse remote", str(error))
            return
        finally:
            client.close()

        self.remote_current_device_id = device.id
        self.remote_current_share_id = None
        self.remote_current_path = ""
        self.remote_current_share_name = ""
        self._set_remote_items(
            [
                {"kind": "share", "name": share["name"], "share_id": share["id"], "path": "", "size": None}
                for share in shares
            ]
        )
        self.remote_status.setText(f"Browsing {device.name}. Open a shared folder to see files.")

    def load_remote_folder(self, share_id: str, path: str, share_name: str | None = None) -> None:
        if not self.remote_current_device_id:
            return
        device = self.state.get_remote_device(self.remote_current_device_id)
        if not device:
            return

        client = self._remote_client(device)
        try:
            entries = client.list_folder(share_id, path)
        except HTTPError as error:
            QMessageBox.warning(self, "Cannot open folder", str(error))
            return
        finally:
            client.close()

        self.remote_current_share_id = share_id
        self.remote_current_path = path
        if share_name:
            self.remote_current_share_name = share_name
        self._set_remote_items(entries)
        location = self.remote_current_share_name
        if path:
            location = f"{location}/{path}"
        self.remote_status.setText(f"Browsing {device.name}: {location}")

    def open_remote_item(self, item: QTableWidgetItem) -> None:
        index = self.remote_items_table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        remote_item = self.remote_items[index]
        if remote_item["kind"] == "share":
            self.load_remote_folder(remote_item["share_id"], "", share_name=remote_item["name"])
        elif remote_item["kind"] == "folder":
            if self.remote_current_share_id:
                self.load_remote_folder(self.remote_current_share_id, remote_item["path"])
        elif remote_item["kind"] == "file":
            self.download_selected_remote_file()

    def remote_back(self) -> None:
        if not self.remote_current_device_id:
            return
        device = self.state.get_remote_device(self.remote_current_device_id)
        if not device:
            return
        if not self.remote_current_share_id:
            self.load_remote_root(device)
            return
        if not self.remote_current_path:
            self.load_remote_root(device)
            return
        self.load_remote_folder(self.remote_current_share_id, self._remote_parent_path())

    def download_selected_remote_file(self) -> None:
        row = self.remote_items_table.currentRow()
        if row < 0 or not self.remote_current_share_id or not self.remote_current_device_id:
            return
        index = self.remote_items_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        item = self.remote_items[index]
        if item["kind"] != "file":
            QMessageBox.information(self, "Download file", "Select a file to download.")
            return

        destination, _ = QFileDialog.getSaveFileName(self, "Save remote file", item["name"])
        if not destination:
            return
        target = Path(destination)
        if target.exists():
            QMessageBox.warning(
                self, "File exists", "Choose a new filename. LanLink will not overwrite files."
            )
            return

        device = self.state.get_remote_device(self.remote_current_device_id)
        if not device:
            return
        client = self._remote_client(device)
        try:
            client.download(self.remote_current_share_id, item["path"], target)
        except (HTTPError, OSError) as error:
            QMessageBox.warning(self, "Download failed", str(error))
            return
        finally:
            client.close()
        self.remote_status.setText(f"Downloaded {item['name']} to {target}.")

    def refresh_code(self) -> None:
        """No code exists unless the local owner has switched pairing mode on."""
        current = self.state.pairing_code()
        if current is None:
            self.code.setText("Off — nobody can pair with this device.")
            self.rotate_button.setText("Allow a device to pair")
            return
        code, expires_at = current
        seconds = max(0, int(expires_at - time.time()))
        self.code.setText(f"{code}  (expires in {seconds // 60}:{seconds % 60:02d}, one device)")
        self.rotate_button.setText("Stop allowing pairing")

    def toggle_pairing(self) -> None:
        if self.state.pairing_armed:
            self.state.cancel_pairing()
        else:
            self.state.start_pairing()
        self.refresh_code()

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to share")
        if not folder:
            return
        try:
            self.state.add_share(Path(folder))
        except ValueError as error:
            QMessageBox.warning(self, "Cannot share folder", str(error))
        self.refresh()

    def cycle_permissions(self) -> None:
        """Step a share through read-only → read+write → read+write+delete."""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "No folder selected", "Select a shared folder first.")
            return
        item = self.table.item(row, 0)
        share = self.state.get_share(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if not share:
            return
        order = list(PERMISSION_LABELS)
        following = order[(order.index(share.permissions) + 1) % len(order)]
        self.state.set_share_permissions(share.id, following)
        self.refresh_shares()

    def remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item and self.state.remove_share(item.data(Qt.ItemDataRole.UserRole)):
            self.refresh()

    def revoke_selected_pairing(self) -> None:
        row = self.paired_table.currentRow()
        if row < 0:
            return
        item = self.paired_table.item(row, 0)
        if item and self.state.revoke(item.data(Qt.ItemDataRole.UserRole)):
            self.refresh_pairings()

    def open_selected_device(self) -> None:
        """Open the device inside LanLink. Never hands off to a web browser."""
        url = self._selected_nearby_url()
        if not url:
            QMessageBox.information(self, "No device selected", "Select a nearby LanLink device first.")
            return
        existing = next(
            (d for d in self.state.remote_devices_snapshot() if d.base_url == url.rstrip("/")),
            None,
        )
        if existing:
            self.tabs.setCurrentIndex(self.remote_tab_index)
            self.load_remote_root(existing)
            return
        self.remote_url_input.setText(url)
        self.tabs.setCurrentIndex(self.remote_tab_index)
        self.remote_status.setText(
            f"{url} is not paired yet. Enable pairing mode on that device, then enter its code here."
        )

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.discovery_browser.stop()
        self.service.stop()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    try:
        window = HubWindow()
    except SettingsCorruptError as error:
        # Never silently mint a new device identity: every peer's pairing depends on it.
        QMessageBox.critical(None, "LanLink settings problem", str(error))
        sys.exit(1)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
