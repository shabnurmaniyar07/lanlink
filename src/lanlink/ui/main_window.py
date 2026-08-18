"""The LanLink window: one native app, six pages, no browser anywhere."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..client import LanLinkClient
from ..crypto import fetch_peer_certificate, fingerprint_of_pem, secrets_are_protected, short_fingerprint
from ..discovery import DiscoveryBrowser, local_ipv4_address_strings
from ..invite import InvalidInvite, Invite, parse_invite
from ..server import LocalService
from ..state import ALL_PERMISSIONS, HubState, RemoteDevice
from ..transfers import (
    TransferManager,
    TransferStatus,
    download_folder_runner,
    download_runner,
    relay_folder_runner,
    relay_runner,
    upload_folder_runner,
    upload_runner,
)
from .browser_model import EntryFilterProxy, RemoteEntryModel, format_size, format_time
from .devices import DeviceListModel, DeviceStatus, HealthTracker, UnifiedDevice, merge_devices
from .jobs import JobRunner
from .pairing import PairingApproval
from .qrcode import QrLabel
from .transfer_model import TransferTableModel, summarise
from .widgets import Breadcrumb, DropTreeView, ProgressDelegate, open_local_file

PAGES = ["My Device", "Devices", "Transfers", "Shared Folders", "History", "Settings"]
PAGE_MY_DEVICE, PAGE_DEVICES, PAGE_TRANSFERS, PAGE_SHARES, PAGE_HISTORY, PAGE_SETTINGS = range(6)
PAGE_BROWSER = 6

PERMISSION_CHOICES = [("r", "Read only"), ("rw", "Read + write"), (ALL_PERMISSIONS, "Read + write + delete")]


class TransferBridge(QObject):
    """Turns TransferManager callbacks into a Qt signal.

    The manager calls back from worker threads; emitting a signal on a QObject
    owned by the GUI thread makes Qt queue the delivery, so the UI updates the
    moment a transfer moves instead of waiting for a poll.
    """

    changed = Signal()

    def notify(self, _transfer: object) -> None:
        self.changed.emit()


class DestinationDialog(QDialog):
    """Pick a paired device and one of its shares as a copy/move target."""

    def __init__(self, devices: list[UnifiedDevice], load_shares, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a destination")
        self.setMinimumWidth(420)
        self._load_shares = load_shares
        self.selected_device: UnifiedDevice | None = None
        self.selected_share: dict | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.device_box = QComboBox()
        for device in devices:
            self.device_box.addItem(f"{device.badge}  {device.name}", device)
        self.share_box = QComboBox()
        form.addRow("Device:", self.device_box)
        form.addRow("Shared folder:", self.share_box)
        layout.addLayout(form)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)

        self.device_box.currentIndexChanged.connect(self._reload_shares)
        if devices:
            self._reload_shares()

    def _reload_shares(self) -> None:
        self.share_box.clear()
        self.ok_button.setEnabled(False)
        device = self.device_box.currentData()
        if device is None:
            return
        self.status.setText(f"Loading shared folders on {device.name}…")
        self._load_shares(device, self._shares_ready, self._shares_failed)

    def _shares_ready(self, shares: list[dict]) -> None:
        writable = [
            share
            for share in shares
            if "w" in share.get("permissions", "") and share.get("available")
        ]
        self.share_box.clear()
        for share in writable:
            self.share_box.addItem(share["name"], share)
        if writable:
            self.status.setText("")
            self.ok_button.setEnabled(True)
        else:
            self.status.setText("This device has no writable shared folder.")

    def _shares_failed(self, message: str) -> None:
        self.status.setText(message)

    def accept(self) -> None:
        self.selected_device = self.device_box.currentData()
        self.selected_share = self.share_box.currentData()
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, state: HubState | None = None) -> None:
        super().__init__()
        self.state = state or HubState()
        self.runner = JobRunner()
        self.approval = PairingApproval()
        self.state.approval_callback = self.approval.request
        self.service = LocalService(self.state)
        self.discovery = DiscoveryBrowser(local_device_id=self.state.device_id)
        self.bridge = TransferBridge()
        self.transfers = TransferManager(workers=3, on_change=self.bridge.notify)
        self.health = HealthTracker()
        self._clients: dict[str, LanLinkClient] = {}
        self._probing: set[str] = set()

        self._invite: Invite | None = None
        self._addresses = local_ipv4_address_strings()
        self.current_device: UnifiedDevice | None = None
        self.current_share: dict | None = None
        self.current_path = ""

        self.setWindowTitle("LanLink")
        self.resize(1180, 760)
        self._build()

        if not self.service.start():
            QMessageBox.warning(
                self,
                "LanLink could not start",
                self.service.last_error or "The local network service did not start.",
            )
        self.discovery.start()

        self.bridge.changed.connect(self.refresh_transfers)
        self.refresh_all()
        self._start_timers()

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.sidebar = QListWidget()
        self.sidebar.setMaximumWidth(230)
        self.sidebar.setMinimumWidth(185)
        self.sidebar.setStyleSheet(
            "QListWidget { border: none; background: #f2f4f8; padding-top: 8px; font-size: 14px; }"
            "QListWidget::item { padding: 11px 14px; border-radius: 6px; margin: 2px 6px; }"
            "QListWidget::item:selected { background: #2457d6; color: white; }"
            "QListWidget::item:hover:!selected { background: #e2e7f1; }"
        )
        for label in PAGES:
            self.sidebar.addItem(QListWidgetItem(label))
        self.sidebar.currentRowChanged.connect(self._sidebar_changed)
        splitter.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_my_device())
        self.pages.addWidget(self._build_devices())
        self.pages.addWidget(self._build_transfers())
        self.pages.addWidget(self._build_shares())
        self.pages.addWidget(self._build_history())
        self.pages.addWidget(self._build_settings())
        self.pages.addWidget(self._build_browser())
        splitter.addWidget(self.pages)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)
        self.sidebar.setCurrentRow(PAGE_DEVICES)
        self.status_line = self.statusBar()
        self.status_line.showMessage("Ready")

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel(f"<h2 style='margin-bottom:2px'>{title}</h2>")
        layout.addWidget(heading)
        caption = QLabel(subtitle)
        caption.setWordWrap(True)
        caption.setStyleSheet("color: #5c6473;")
        layout.addWidget(caption)
        return page, layout

    def _build_my_device(self) -> QWidget:
        page, layout = self._page("My Device", "How other LanLink devices see this computer.")

        form = QFormLayout()
        self.my_name = QLabel()
        self.my_address = QLabel()
        self.my_status = QLabel()
        self.my_id = QLabel()
        self.my_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.my_fingerprint = QLabel()
        self.my_fingerprint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.my_fingerprint.setStyleSheet("font-family: monospace;")
        form.addRow("Name:", self.my_name)
        form.addRow("Status:", self.my_status)
        form.addRow("Address:", self.my_address)
        form.addRow("Device id:", self.my_id)
        form.addRow("Certificate:", self.my_fingerprint)
        layout.addLayout(form)

        layout.addSpacing(10)
        layout.addWidget(QLabel("<b>Pairing</b>"))
        self.pairing_state = QLabel()
        self.pairing_state.setWordWrap(True)
        layout.addWidget(self.pairing_state)
        self.pairing_code = QLabel()
        self.pairing_code.setStyleSheet("font-size: 30px; font-weight: 700; letter-spacing: 3px;")
        self.pairing_code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.pairing_code)

        row = QHBoxLayout()
        self.pairing_button = QPushButton("Allow a device to pair")
        self.pairing_button.clicked.connect(self.toggle_pairing)
        self.copy_invite_button = QPushButton("Copy invite link")
        self.copy_invite_button.clicked.connect(self.copy_invite)
        self.copy_invite_button.setEnabled(False)
        row.addWidget(self.pairing_button)
        row.addWidget(self.copy_invite_button)
        row.addStretch()
        layout.addLayout(row)

        qr_row = QHBoxLayout()
        self.qr_label = QrLabel()
        qr_row.addWidget(self.qr_label)
        qr_hint = QLabel(
            "Scan this with LanLink on a phone, or press <b>Copy invite link</b> and paste it "
            "into the other computer's Devices page. The invite carries this device's "
            "certificate, so the other side pins the right identity."
        )
        qr_hint.setWordWrap(True)
        qr_hint.setStyleSheet("color: #5c6473;")
        qr_row.addWidget(qr_hint, 1)
        layout.addLayout(qr_row)
        layout.addStretch()
        return page

    def _build_devices(self) -> QWidget:
        page, layout = self._page(
            "Devices", "Every LanLink device on this network. Double-click one to browse it."
        )

        row = QHBoxLayout()
        self.pair_button = QPushButton("Pair with selected device")
        self.pair_button.clicked.connect(self.pair_selected_device)
        self.open_button = QPushButton("Open selected device")
        self.open_button.clicked.connect(self.open_selected_device)
        self.forget_button = QPushButton("Forget selected device")
        self.forget_button.clicked.connect(self.forget_selected_device)
        row.addWidget(self.open_button)
        row.addWidget(self.pair_button)
        row.addStretch()
        row.addWidget(self.forget_button)
        layout.addLayout(row)

        self.device_model = DeviceListModel(self)
        self.device_view = QTreeView()
        self.device_view.setModel(self.device_model)
        self.device_view.setHeaderHidden(True)
        self.device_view.setRootIsDecorated(False)
        self.device_view.setAlternatingRowColors(True)
        self.device_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.device_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.device_view.setStyleSheet("QTreeView::item { padding: 8px 6px; }")
        self.device_view.doubleClicked.connect(lambda _: self.open_selected_device())
        layout.addWidget(self.device_view)

        self.devices_hint = QLabel()
        self.devices_hint.setWordWrap(True)
        self.devices_hint.setStyleSheet("color: #5c6473;")
        layout.addWidget(self.devices_hint)

        manual = QHBoxLayout()
        self.manual_address = QLineEdit()
        self.manual_address.setPlaceholderText(
            "Paste an invite link, or type an address such as 192.168.1.20:8765"
        )
        manual_button = QPushButton("Use invite / address")
        manual_button.clicked.connect(self.pair_manual_address)
        manual.addWidget(self.manual_address)
        manual.addWidget(manual_button)
        layout.addLayout(manual)
        return page

    def _build_browser(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        top = QHBoxLayout()
        self.back_button = QPushButton("← Back")
        self.back_button.clicked.connect(self.navigate_back)
        self.devices_button = QPushButton("All devices")
        self.devices_button.clicked.connect(lambda: self.sidebar.setCurrentRow(PAGE_DEVICES))
        top.addWidget(self.back_button)
        top.addWidget(self.devices_button)
        top.addStretch()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search this folder…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMaximumWidth(260)
        top.addWidget(self.search_box)
        layout.addLayout(top)

        self.breadcrumb = Breadcrumb()
        self.breadcrumb.segmentClicked.connect(self.navigate_to_segment)
        layout.addWidget(self.breadcrumb)

        actions = QHBoxLayout()
        for label, slot in [
            ("Download", self.download_selection),
            ("Upload here", self.upload_into_current),
            ("New folder", self.create_remote_folder),
            ("Copy to…", lambda: self.transfer_selection(move=False)),
            ("Move to…", lambda: self.transfer_selection(move=True)),
        ]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.reload_current_folder)
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)

        self.entry_model = RemoteEntryModel(self)
        self.entry_proxy = EntryFilterProxy(self)
        self.entry_proxy.setSourceModel(self.entry_model)

        self.entry_view = DropTreeView()
        self.entry_view.setModel(self.entry_proxy)
        self.entry_view.doubleClicked.connect(self.activate_entry)
        self.entry_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.entry_view.customContextMenuRequested.connect(self.show_entry_menu)
        self.entry_view.filesDropped.connect(self.upload_paths)
        self.entry_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.search_box.textChanged.connect(self.entry_proxy.set_search)
        header = self.entry_view.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.entry_view.setStyleSheet("QTreeView::item { padding: 3px 2px; }")
        layout.addWidget(self.entry_view)

        self.browser_status = QLabel("")
        self.browser_status.setWordWrap(True)
        self.browser_status.setStyleSheet("color: #5c6473;")
        layout.addWidget(self.browser_status)
        return page

    def _build_transfers(self) -> QWidget:
        page, layout = self._page("Transfers", "Files moving between this device and your other devices.")

        row = QHBoxLayout()
        for label, slot in [
            ("Pause", self.pause_selected_transfer),
            ("Resume", self.resume_selected_transfer),
            ("Cancel", self.cancel_selected_transfer),
            ("Retry", self.retry_selected_transfer),
        ]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch()
        pause_all = QPushButton("Pause all")
        pause_all.clicked.connect(self.transfers.pause_all)
        resume_all = QPushButton("Resume all")
        resume_all.clicked.connect(self.transfers.resume_all)
        row.addWidget(pause_all)
        row.addWidget(resume_all)
        layout.addLayout(row)

        self.transfer_model = TransferTableModel(self)
        self.transfer_view = QTreeView()
        self.transfer_view.setModel(self.transfer_model)
        self.transfer_view.setRootIsDecorated(False)
        self.transfer_view.setAlternatingRowColors(True)
        self.transfer_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.transfer_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.transfer_view.setItemDelegate(
            ProgressDelegate(4, TransferTableModel.ProgressRole, self.transfer_view)
        )
        transfer_header = self.transfer_view.header()
        transfer_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 8):
            transfer_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.transfer_view.setStyleSheet("QTreeView::item { padding: 4px 2px; }")
        layout.addWidget(self.transfer_view)

        self.transfer_summary = QLabel("No transfers running")
        layout.addWidget(self.transfer_summary)
        return page

    def _build_shares(self) -> QWidget:
        page, layout = self._page(
            "Shared Folders", "Only these folders are reachable. Everything else stays private."
        )

        row = QHBoxLayout()
        add = QPushButton("Add shared folder")
        add.clicked.connect(self.add_share)
        remove = QPushButton("Stop sharing")
        remove.clicked.connect(self.remove_share)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        row.addWidget(QLabel("Access:"))
        self.permission_box = QComboBox()
        for value, label in PERMISSION_CHOICES:
            self.permission_box.addItem(label, value)
        self.permission_box.setEnabled(False)
        self.permission_box.currentIndexChanged.connect(self.apply_share_permissions)
        row.addWidget(self.permission_box)
        layout.addLayout(row)

        self.share_list = QListWidget()
        self.share_list.setStyleSheet("QListWidget::item { padding: 8px 6px; }")
        self.share_list.currentRowChanged.connect(self.share_selection_changed)
        layout.addWidget(self.share_list)
        return page

    def _build_history(self) -> QWidget:
        page, layout = self._page("History", "Finished, failed and cancelled transfers from this session.")

        row = QHBoxLayout()
        retry = QPushButton("Retry selected")
        retry.clicked.connect(self.retry_history_item)
        clear = QPushButton("Clear history")
        clear.clicked.connect(self.clear_history)
        row.addWidget(retry)
        row.addStretch()
        row.addWidget(clear)
        layout.addLayout(row)

        self.history_model = TransferTableModel(self, history=True)
        self.history_view = QTreeView()
        self.history_view.setModel(self.history_model)
        self.history_view.setRootIsDecorated(False)
        self.history_view.setAlternatingRowColors(True)
        self.history_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        history_header = self.history_view.header()
        history_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 8):
            history_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.history_view)
        return page

    def _build_settings(self) -> QWidget:
        page, layout = self._page("Settings", "These apply to this installation of LanLink.")

        form = QFormLayout()
        self.setting_name = QLineEdit(self.state.device_name)
        form.addRow("Device name:", self.setting_name)

        self.setting_limit = QSpinBox()
        self.setting_limit.setRange(0, 1_000_000)
        self.setting_limit.setSuffix(" MB")
        self.setting_limit.setSpecialValueText("No limit")
        self.setting_limit.setValue(int(self.state.max_upload_bytes / (1024 * 1024)))
        form.addRow("Maximum upload size:", self.setting_limit)

        self.setting_bind_all = QCheckBox(
            "Listen on every network adapter (including VPN and public adapters)"
        )
        self.setting_bind_all.setChecked(self.state.bind_all_interfaces)
        form.addRow("Network:", self.setting_bind_all)

        self.setting_tls = QCheckBox("Encrypt connections with TLS and pinned certificates")
        self.setting_tls.setChecked(self.state.use_tls)
        form.addRow("Security:", self.setting_tls)

        self.setting_verify = QCheckBox("Verify every transfer with a SHA-256 checksum")
        self.setting_verify.setChecked(True)
        self.setting_verify.setEnabled(False)
        self.setting_verify.setToolTip("Always on: LanLink will not publish a file that fails its checksum.")
        form.addRow("Transfers:", self.setting_verify)
        layout.addLayout(form)

        note = QLabel(
            "Each device has its own certificate. Peers pin it when they pair, so an "
            "attacker who takes over the address cannot impersonate a paired device. "
            "Turning TLS off is for troubleshooting on a network you fully control."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #5c6473;")
        layout.addWidget(note)

        row = QHBoxLayout()
        save = QPushButton("Save settings")
        save.clicked.connect(self.save_settings)
        row.addWidget(save)
        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()
        return page

    # ------------------------------------------------------------------ timers

    def _start_timers(self) -> None:
        self.fast_timer = QTimer(self)
        self.fast_timer.timeout.connect(self._tick_fast)
        self.fast_timer.start(400)

        self.slow_timer = QTimer(self)
        self.slow_timer.timeout.connect(self._tick_slow)
        self.slow_timer.start(3000)

    def _tick_fast(self) -> None:
        self.refresh_pairing_panel()
        self.refresh_transfers()
        self._check_pairing_request()

    def _tick_slow(self) -> None:
        self.health.expire()
        self._check_network_change()
        self.refresh_devices()
        self.probe_devices()

    def _check_network_change(self) -> None:
        """Rebind after a Wi-Fi switch, a sleep/wake, or a new DHCP lease."""
        addresses = local_ipv4_address_strings()
        if addresses == self._addresses:
            return
        self._addresses = addresses
        self.status_line.showMessage("Network changed — reconnecting LanLink…", 6000)

        def rebind() -> bool:
            return self.service.restart()

        def rebound(ok: object) -> None:
            self._addresses = local_ipv4_address_strings()
            for client in self._clients.values():
                client.close()
            self._clients.clear()
            self.health.statuses.clear()
            self.health.errors.clear()
            self.discovery.stop()
            self.discovery.start()
            self.refresh_my_device()
            self.refresh_devices()
            self.status_line.showMessage(
                f"Reconnected on {self.service.url}" if ok else
                f"Could not rebind: {self.service.last_error}",
                8000,
            )

        self.runner.run(rebind, rebound, self._show_error)

    # ------------------------------------------------------------------ shared

    def _client_for(self, device: UnifiedDevice | RemoteDevice) -> LanLinkClient:
        remote = self.state.get_remote_device(device.id)
        address = getattr(device, "address", "") or (remote.base_url if remote else "")
        token = remote.token if remote else None
        certificate = remote.certificate if remote else None
        client = self._clients.get(device.id)
        stale = (
            client is None
            or client.base_url != address.rstrip("/")
            or client.token != token
            or client.peer_certificate != certificate
        )
        if stale:
            if client is not None:
                client.close()
            client = LanLinkClient(address, token=token, peer_certificate=certificate)
            self._clients[device.id] = client
        assert client is not None
        return client

    def _show_error(self, message: str) -> None:
        self.status_line.showMessage(message, 8000)

    def _warn(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def _sidebar_changed(self, row: int) -> None:
        if 0 <= row < len(PAGES):
            self.pages.setCurrentIndex(row)
            if row == PAGE_SHARES:
                self.refresh_shares()
            elif row == PAGE_HISTORY:
                self.refresh_transfers()

    def refresh_all(self) -> None:
        self.refresh_my_device()
        self.refresh_pairing_panel()
        self.refresh_devices()
        self.refresh_shares()
        self.refresh_transfers()

    # -------------------------------------------------------------- my device

    def refresh_my_device(self) -> None:
        device = self.state.public_device()
        self.my_name.setText(f"{device['name']}  ({device['hostname']})")
        self.my_address.setText(f"{self.service.url}   — same Wi-Fi, Ethernet or hotspot only")
        running = self.service.thread is not None and self.service.thread.is_alive()
        self.my_status.setText("\U0001f7e2  Online and sharing" if running else "\U0001f534  Not running")
        self.my_id.setText(device["id"])
        certificate = self.service.certificate
        if certificate is not None:
            protection = "protected by Windows" if secrets_are_protected() else "owner-only file"
            self.my_fingerprint.setText(f"{certificate.short_fingerprint}   (keys {protection})")
        else:
            self.my_fingerprint.setText("TLS is off — this device is serving plain HTTP.")

    def refresh_pairing_panel(self) -> None:
        current = self.state.pairing_code()
        if current is None:
            self.pairing_code.setText("————")
            self.pairing_state.setText(
                "Pairing is off. Nobody can pair with this device until you switch it on."
            )
            self.pairing_button.setText("Allow a device to pair")
            self.copy_invite_button.setEnabled(False)
            self.qr_label.clear_code("Pairing is off")
            self._invite = None
            return
        code, expires_at = current
        remaining = max(0, int(expires_at - time.time()))
        self.pairing_code.setText(code)
        self.pairing_state.setText(
            f"Enter this code on the other device within {remaining // 60}:{remaining % 60:02d}. "
            "It works once, then pairing switches off again."
        )
        self.pairing_button.setText("Stop allowing pairing")
        self.copy_invite_button.setEnabled(True)

        invite = self.current_invite(code)
        if self._invite is None or self._invite.to_url() != invite.to_url():
            self._invite = invite
            self.qr_label.set_payload(invite.to_url())

    def current_invite(self, code: str) -> Invite:
        addresses = local_ipv4_address_strings()
        host = self.service.host if self.service.host not in {"0.0.0.0", "::"} else ""
        if not host:
            host = addresses[0] if addresses else "127.0.0.1"
        certificate = self.service.certificate
        return Invite(
            host=host,
            port=self.service.port,
            code=code,
            device_id=self.state.device_id,
            name=self.state.device_name,
            fingerprint=certificate.fingerprint if certificate else "",
            scheme=self.service.scheme,
        )

    def copy_invite(self) -> None:
        current = self.state.pairing_code()
        if current is None:
            return
        from PySide6.QtWidgets import QApplication as _App

        invite = self.current_invite(current[0])
        clipboard = _App.clipboard()
        if clipboard is not None:
            clipboard.setText(invite.to_url())
        self.status_line.showMessage(
            "Invite link copied. Paste it into the other device's Devices page.", 8000
        )

    def toggle_pairing(self) -> None:
        if self.state.pairing_armed:
            self.state.cancel_pairing()
        else:
            self.state.start_pairing()
        self.refresh_pairing_panel()

    def _check_pairing_request(self) -> None:
        pending = self.approval.take()
        if pending is None:
            return
        answer = QMessageBox.question(
            self,
            "Allow this device?",
            f"“{pending.client_name}” entered the correct pairing code.\n\n"
            f"Device id: {pending.client_id}\n\nGive it access to your shared folders?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        pending.answer(answer == QMessageBox.StandardButton.Yes)
        self.refresh_devices()

    # ---------------------------------------------------------------- devices

    def refresh_devices(self) -> None:
        devices = merge_devices(
            self.discovery.devices(),
            self.state.remote_devices_snapshot(),
            self.state.paired_devices_snapshot(),
            health=self.health.statuses,
            errors=self.health.errors,
        )
        selected = self._selected_device()
        self.device_model.set_devices(devices)
        if selected:
            row = self.device_model.row_of(selected.id)
            if row >= 0:
                self.device_view.setCurrentIndex(self.device_model.index(row, 0))

        if not devices:
            hint = (
                "No devices yet. Start LanLink on another computer on the same network, "
                "or add one by address below."
            )
        else:
            online = sum(1 for device in devices if device.status is DeviceStatus.ONLINE)
            hint = f"{len(devices)} device(s) known, {online} online."
        if not self.discovery.zeroconf:
            hint = f"{hint}  Discovery is not running: {self.discovery.last_error or 'unknown reason'}"
        self.devices_hint.setText(hint)

    def probe_devices(self) -> None:
        """Ask each known address for /v1/device on a worker thread."""
        for device in self.device_model.devices():
            if not device.address or device.id in self._probing:
                continue
            self._probing.add(device.id)
            self.health.mark_connecting(device.id)
            address, device_id = device.address, device.id

            remote = self.state.get_remote_device(device_id)
            pin = remote.certificate if remote else None

            def check(url: str = address, certificate: str | None = pin) -> dict:
                probe = LanLinkClient(url, peer_certificate=certificate, timeout=5)
                try:
                    return probe.device_info()
                finally:
                    probe.close()

            def ok(_result: object, key: str = device_id) -> None:
                self._probe_ok(key)

            def failed(message: str, key: str = device_id) -> None:
                self._probe_failed(key, message)

            self.runner.run(check, ok, failed)

    def _probe_ok(self, device_id: str) -> None:
        self._probing.discard(device_id)
        self.health.mark_online(device_id)

    def _probe_failed(self, device_id: str, message: str) -> None:
        self._probing.discard(device_id)
        lowered = message.lower()
        if "certificate" in lowered or "ssl" in lowered or "verify" in lowered:
            # A pinned certificate that stopped matching is not "offline" — say so.
            self.health.mark_error(device_id, "Certificate mismatch — refusing to connect")
        else:
            self.health.mark_offline(device_id)

    def _selected_device(self) -> UnifiedDevice | None:
        index = self.device_view.currentIndex()
        if not index.isValid():
            return None
        return self.device_model.device_at(index.row())

    def pair_selected_device(self) -> None:
        device = self._selected_device()
        if device is None:
            self._warn("No device selected", "Select a device in the list first.")
            return
        if not device.address:
            self._warn("No address", f"LanLink does not know an address for {device.name} yet.")
            return
        try:
            invite = parse_invite(device.address)
        except InvalidInvite as error:
            self._warn("Bad address", str(error))
            return
        invite.fingerprint = device.fingerprint
        invite.name = device.name
        self._pair_with(invite)

    def pair_manual_address(self) -> None:
        try:
            invite = parse_invite(self.manual_address.text())
        except InvalidInvite as error:
            self._warn("Cannot use that", str(error))
            return
        self._pair_with(invite)

    def _pair_with(self, invite: Invite) -> None:
        code = invite.code
        if not code:
            entered, accepted = QInputDialog.getText(
                self,
                "Pair with device",
                "Switch on pairing on the other device, then enter its 8-digit code."
                f"\n\n{invite.base_url}",
            )
            if not accepted or not entered.strip():
                return
            code = entered.strip()

        host, port, scheme = invite.host, invite.port, invite.scheme
        expected = invite.fingerprint
        address = invite.base_url
        device_name = self.state.device_name
        device_id = self.state.device_id

        def do_pair() -> dict:
            certificate = ""
            if scheme == "https":
                certificate = fetch_peer_certificate(host, port)
                actual = fingerprint_of_pem(certificate)
                # A truncated fingerprint from mDNS still narrows identity usefully.
                if expected and not actual.startswith(expected.lower()):
                    raise RuntimeError(
                        "The certificate this device presented does not match the one it "
                        "advertised. Do not continue on this network."
                    )
            client = LanLinkClient(address, peer_certificate=certificate or None)
            try:
                result = client.pair(device_name, code, client_id=device_id)
            finally:
                client.close()
            result["certificate"] = certificate
            return result

        self.status_line.showMessage(f"Pairing with {address}…")
        self.runner.run(
            do_pair,
            lambda result: self._pair_ok(address, result),
            lambda message: self._warn("Pairing failed", message),
        )

    def _pair_ok(self, address: str, result: dict) -> None:
        device = result["device"]
        certificate = result.get("certificate", "")
        fingerprint = fingerprint_of_pem(certificate) if certificate else ""
        saved = self.state.upsert_remote_device(
            device["id"], device["name"], address, result["token"], certificate, fingerprint
        )
        self.manual_address.clear()
        self._clients.pop(saved.id, None)
        if fingerprint:
            QMessageBox.information(
                self,
                "Paired",
                f"Paired with {saved.name}.\n\nIts certificate fingerprint is:\n"
                f"{short_fingerprint(fingerprint)}\n\n"
                "Check it matches the fingerprint shown on that device's My Device page. "
                "LanLink will refuse to connect if it ever changes.",
            )
        self.status_line.showMessage(f"Paired with {saved.name}", 6000)
        self.refresh_devices()

    def forget_selected_device(self) -> None:
        device = self._selected_device()
        if device is None or not device.paired_out:
            self._warn("Nothing to forget", "Select a device you have paired with.")
            return
        if (
            QMessageBox.question(self, "Forget device", f"Remove the pairing with {device.name}?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.state.remove_remote_device(device.id)
        client = self._clients.pop(device.id, None)
        if client:
            client.close()
        self.refresh_devices()

    def open_selected_device(self) -> None:
        device = self._selected_device()
        if device is None:
            self._warn("No device selected", "Select a device in the list first.")
            return
        if not device.paired_out:
            self._warn(
                "Not paired yet",
                f"Pair with {device.name} before browsing it. "
                "Switch on pairing there, then choose Pair with selected device.",
            )
            return
        self.open_device(device)

    # ---------------------------------------------------------------- browser

    def open_device(self, device: UnifiedDevice) -> None:
        self.current_device = device
        self.current_share = None
        self.current_path = ""
        self.sidebar.setCurrentRow(PAGE_DEVICES)
        self.pages.setCurrentIndex(PAGE_BROWSER)
        self.search_box.clear()
        self.browser_status.setText(f"Loading shared folders on {device.name}…")
        self.entry_model.set_entries([])
        self._update_breadcrumb()

        client = self._client_for(device)
        self.runner.run(client.shares, self._shares_loaded, self._browser_failed)

    def _shares_loaded(self, shares: list[dict]) -> None:
        entries = [
            {
                "kind": "share",
                "name": share["name"],
                "share_id": share["id"],
                "path": "",
                "size": None,
                "permissions": share.get("permissions", "r"),
                "available": share.get("available", True),
            }
            for share in shares
        ]
        self.entry_model.set_entries(entries)
        self.entry_view.set_drops_enabled(False)
        name = self.current_device.name if self.current_device else "device"
        self.browser_status.setText(
            f"{len(entries)} shared folder(s) on {name}. Open one to see its files."
            if entries
            else f"{name} is not sharing any folders yet."
        )
        self._update_breadcrumb()

    def load_folder(self, share: dict, path: str) -> None:
        if self.current_device is None:
            return
        self.current_share = share
        self.current_path = path
        self.search_box.clear()
        self.browser_status.setText("Loading…")
        client = self._client_for(self.current_device)
        share_id = share["share_id"]
        self.runner.run(
            lambda: client.list_folder(share_id, path), self._folder_loaded, self._browser_failed
        )

    def _folder_loaded(self, entries: list[dict]) -> None:
        self.entry_model.set_entries(entries)
        writable = "w" in (self.current_share or {}).get("permissions", "")
        self.entry_view.set_drops_enabled(writable)
        files = sum(1 for entry in entries if entry.get("kind") == "file")
        folders = len(entries) - files
        hint = f"{folders} folder(s), {files} file(s)."
        if writable:
            hint = f"{hint}  Drag files here to upload them."
        else:
            hint = f"{hint}  This shared folder is read-only."
        self.browser_status.setText(hint)
        self._update_breadcrumb()

    def _browser_failed(self, message: str) -> None:
        self.browser_status.setText(message)
        self.status_line.showMessage(message, 8000)

    def reload_current_folder(self) -> None:
        if self.current_device is None:
            return
        if self.current_share is None:
            self.open_device(self.current_device)
        else:
            self.load_folder(self.current_share, self.current_path)

    def _update_breadcrumb(self) -> None:
        segments = [self.current_device.name if self.current_device else "Device"]
        if self.current_share:
            segments.append(self.current_share["name"])
            segments.extend(part for part in self.current_path.split("/") if part)
        self.breadcrumb.set_segments(segments)

    def navigate_to_segment(self, index: int) -> None:
        if self.current_device is None:
            return
        if index == 0:
            self.open_device(self.current_device)
            return
        if self.current_share is None:
            return
        parts = [part for part in self.current_path.split("/") if part]
        self.load_folder(self.current_share, "/".join(parts[: index - 1]))

    def navigate_back(self) -> None:
        if self.current_device is None:
            self.sidebar.setCurrentRow(PAGE_DEVICES)
            return
        if self.current_share is None:
            self.sidebar.setCurrentRow(PAGE_DEVICES)
            return
        parts = [part for part in self.current_path.split("/") if part]
        if not parts:
            self.open_device(self.current_device)
        else:
            self.load_folder(self.current_share, "/".join(parts[:-1]))

    def _selected_entries(self) -> list[dict]:
        rows = {index.row() for index in self.entry_view.selectionModel().selectedRows()}
        entries = [self.entry_proxy.entry_at(row) for row in sorted(rows)]
        return [entry for entry in entries if entry]

    def _selected_entry(self) -> dict | None:
        entries = self._selected_entries()
        return entries[0] if entries else None

    def activate_entry(self) -> None:
        entry = self._selected_entry()
        if not entry:
            return
        kind = entry.get("kind")
        if kind == "share":
            if not entry.get("available", True):
                self._warn("Unavailable", "That shared folder is not available on the other device.")
                return
            self.load_folder(entry, "")
        elif kind == "folder" and self.current_share:
            self.load_folder(self.current_share, entry["path"])
        else:
            self.open_entry()

    def show_entry_menu(self, position) -> None:
        entry = self._selected_entry()
        if not entry or self.current_device is None:
            return
        writable = "w" in (self.current_share or {}).get("permissions", "")
        deletable = "d" in (self.current_share or {}).get("permissions", "")
        is_share = entry.get("kind") == "share"

        menu = QMenu(self)
        menu.addAction("Open", self.activate_entry)
        download = menu.addAction("Download…", self.download_selection)
        download.setEnabled(not is_share)
        upload_folder = menu.addAction("Upload folder…", self.upload_folder_into_current)
        upload_folder.setEnabled(writable and not is_share)
        menu.addSeparator()
        upload = menu.addAction("Upload here…", self.upload_into_current)
        upload.setEnabled(writable and not is_share)
        folder = menu.addAction("New folder…", self.create_remote_folder)
        folder.setEnabled(writable and not is_share)
        menu.addSeparator()
        copy = menu.addAction("Copy to…", lambda: self.transfer_selection(move=False))
        copy.setEnabled(not is_share)
        move = menu.addAction("Move to…", lambda: self.transfer_selection(move=True))
        move.setEnabled(deletable and not is_share)
        menu.addSeparator()
        rename = menu.addAction("Rename…", self.rename_selection)
        rename.setEnabled(writable and not is_share)
        delete = menu.addAction("Delete", self.delete_selection)
        delete.setEnabled(deletable and not is_share)
        menu.addSeparator()
        menu.addAction("Properties", self.show_properties)
        menu.exec(self.entry_view.viewport().mapToGlobal(position))

    # ------------------------------------------------------------- operations

    def open_entry(self) -> None:
        """Download to a temporary file, then hand it to the OS default handler."""
        entry = self._selected_entry()
        device, share = self.current_device, self.current_share
        if not entry or entry.get("kind") != "file" or not share or device is None:
            return
        destination = Path(tempfile.mkdtemp(prefix="lanlink-")) / entry["name"]
        client = self._client_for(device)
        share_id = share["share_id"]
        transfer = self.transfers.submit(
            kind="open",
            filename=entry["name"],
            source=device.name,
            destination="Open locally",
            size=entry.get("size"),
            runner=download_runner(self.transfers, client, share_id, entry["path"], destination),
        )
        self._after_transfer(transfer, lambda: open_local_file(destination))

    def download_selection(self) -> None:
        entries = [entry for entry in self._selected_entries() if entry.get("kind") in {"file", "folder"}]
        device, share = self.current_device, self.current_share
        if not entries or not share or device is None:
            self._warn("Nothing to download", "Select one or more files or folders first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Save to")
        if not folder:
            return
        client = self._client_for(device)
        share_id = share["share_id"]
        for entry in entries:
            destination = Path(folder) / entry["name"]
            is_folder = entry.get("kind") == "folder"
            if destination.exists() and not is_folder:
                self._show_error(f"{entry['name']} already exists in that folder; skipped.")
                continue
            runner = (
                download_folder_runner(self.transfers, client, share_id, entry["path"], destination)
                if is_folder
                else download_runner(self.transfers, client, share_id, entry["path"], destination)
            )
            self.transfers.submit(
                kind="download-folder" if is_folder else "download",
                filename=entry["name"],
                source=f"{device.name}/{share['name']}",
                destination=folder,
                size=entry.get("size"),
                runner=runner,
            )
        self.sidebar.setCurrentRow(PAGE_TRANSFERS)

    def upload_folder_into_current(self) -> None:
        if not self.current_share:
            self._warn("Open a folder first", "Open a shared folder before uploading.")
            return
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder to upload")
        if chosen:
            self.upload_paths([Path(chosen)])

    def upload_into_current(self) -> None:
        if not self.current_share:
            self._warn("Open a folder first", "Open a shared folder before uploading.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files to upload")
        if paths:
            self.upload_paths([Path(path) for path in paths])

    def upload_paths(self, paths: list[Path]) -> None:
        if not self.current_share or self.current_device is None:
            self._warn("Open a folder first", "Open a shared folder before uploading.")
            return
        if "w" not in self.current_share.get("permissions", ""):
            self._warn("Read-only", "That shared folder does not accept uploads.")
            return
        client = self._client_for(self.current_device)
        share_id = self.current_share["share_id"]
        for path in paths:
            is_folder = path.is_dir()
            runner = (
                upload_folder_runner(self.transfers, client, share_id, self.current_path, path)
                if is_folder
                else upload_runner(self.transfers, client, share_id, self.current_path, path)
            )
            self.transfers.submit(
                kind="upload-folder" if is_folder else "upload",
                filename=path.name + ("/" if is_folder else ""),
                source=str(path.parent),
                destination=f"{self.current_device.name}/{self.current_share['name']}",
                size=None if is_folder else path.stat().st_size,
                runner=runner,
            )
        self.sidebar.setCurrentRow(PAGE_TRANSFERS)
        QTimer.singleShot(1200, self.reload_current_folder)

    def create_remote_folder(self) -> None:
        if not self.current_share or self.current_device is None:
            self._warn("Open a folder first", "Open a shared folder before adding a new one.")
            return
        name, accepted = QInputDialog.getText(self, "New folder", "Folder name:")
        if not accepted or not name.strip():
            return
        client = self._client_for(self.current_device)
        share_id = self.current_share["share_id"]
        path, new_name = self.current_path, name.strip()
        self.runner.run(
            lambda: client.create_folder(share_id, path, new_name),
            lambda _: self.reload_current_folder(),
            lambda message: self._warn("Could not create the folder", message),
        )

    def rename_selection(self) -> None:
        entry = self._selected_entry()
        if not entry or not self.current_share or self.current_device is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename", "New name:", text=str(entry.get("name", ""))
        )
        if not accepted or not name.strip():
            return
        client = self._client_for(self.current_device)
        share_id = self.current_share["share_id"]
        path, new_name = entry["path"], name.strip()
        self.runner.run(
            lambda: client.rename(share_id, path, new_name),
            lambda _: self.reload_current_folder(),
            lambda message: self._warn("Could not rename", message),
        )

    def delete_selection(self) -> None:
        entries = self._selected_entries()
        if not entries or not self.current_share or self.current_device is None:
            return
        names = ", ".join(entry["name"] for entry in entries[:5])
        extra = "" if len(entries) <= 5 else f" and {len(entries) - 5} more"
        folders = [entry for entry in entries if entry.get("kind") == "folder"]
        question = (
            f"Delete {names}{extra} from {self.current_device.name}?\n\n"
            "This cannot be undone from LanLink."
        )
        if folders:
            question += "\n\nFolders will be deleted with everything inside them."
        answer = QMessageBox.question(
            self,
            "Delete",
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        client = self._client_for(self.current_device)
        share_id = self.current_share["share_id"]
        for entry in entries:

            def remove(path: str = entry["path"], folder: bool = entry.get("kind") == "folder") -> dict:
                return client.delete(share_id, path, recursive=folder)

            self.runner.run(
                remove,
                lambda _result: self.reload_current_folder(),
                lambda message: self._warn("Could not delete", message),
            )

    def show_properties(self) -> None:
        entry = self._selected_entry()
        if not entry or self.current_device is None:
            return
        if entry.get("kind") == "share":
            self._properties_dialog(
                entry["name"],
                {
                    "Type": "Shared folder",
                    "Device": self.current_device.name,
                    "Access": entry.get("permissions", ""),
                    "Available": "Yes" if entry.get("available", True) else "No",
                },
            )
            return
        if not self.current_share:
            return
        client = self._client_for(self.current_device)
        share_id, path = self.current_share["share_id"], entry["path"]
        self.runner.run(
            lambda: client.properties(share_id, path),
            self._properties_loaded,
            lambda message: self._warn("Could not read properties", message),
        )

    def _properties_loaded(self, detail: dict) -> None:
        rows = {
            "Type": (
                "Folder"
                if detail["kind"] == "folder"
                else f"File ({detail.get('extension') or 'no extension'})"
            ),
            "Location": f"{detail.get('share', '')}/{detail.get('path', '')}".rstrip("/"),
            "Size": format_size(detail.get("size")) or "—",
            "Modified": format_time(detail.get("modified_at")),
            "Created": format_time(detail.get("created_at")),
            "Read-only": "Yes" if detail.get("read_only") else "No",
        }
        counts = detail.get("item_count")
        if counts:
            rows["Contains"] = f"{counts['folders']} folder(s), {counts['files']} file(s)"
        self._properties_dialog(detail.get("name", "Properties"), rows)

    def _properties_dialog(self, title: str, rows: dict[str, str]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Properties — {title}")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        for label, value in rows.items():
            field = QLabel(str(value))
            field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            field.setWordWrap(True)
            form.addRow(f"{label}:", field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def transfer_selection(self, move: bool) -> None:
        entries = [
            entry for entry in self._selected_entries() if entry.get("kind") in {"file", "folder"}
        ]
        device, share = self.current_device, self.current_share
        if not entries or not share or device is None:
            self._warn("Nothing selected", "Select one or more files or folders first.")
            return
        targets = [
            candidate
            for candidate in self.device_model.devices()
            if candidate.is_browsable and candidate.id != device.id
        ]
        if not targets:
            self._warn(
                "No destination",
                "Pair with another device first — a copy needs somewhere to go.",
            )
            return

        dialog = DestinationDialog(targets, self._load_shares_for, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_share:
            return

        destination_device = dialog.selected_device
        destination_share = dialog.selected_share
        if destination_device is None:
            return
        source_client = self._client_for(device)
        destination_client = self._client_for(destination_device)
        share_id = share["share_id"]

        for entry in entries:
            is_folder = entry.get("kind") == "folder"
            relay = relay_folder_runner if is_folder else relay_runner
            self.transfers.submit(
                kind=("remote-move" if move else "remote-copy") + ("-folder" if is_folder else ""),
                filename=entry["name"] + ("/" if is_folder else ""),
                source=f"{device.name}/{share['name']}",
                destination=f"{destination_device.name}/{destination_share['name']}",
                size=entry.get("size"),
                runner=relay(
                    self.transfers,
                    source_client,
                    share_id,
                    entry["path"],
                    destination_client,
                    destination_share["id"],
                    "",
                    entry["name"],
                    delete_source=move,
                ),
            )
        self.sidebar.setCurrentRow(PAGE_TRANSFERS)
        if move:
            QTimer.singleShot(1500, self.reload_current_folder)

    def _load_shares_for(self, device: UnifiedDevice, on_ok, on_error) -> None:
        client = self._client_for(device)
        self.runner.run(client.shares, on_ok, on_error)

    def _after_transfer(self, transfer, action) -> None:
        """Poll a single transfer and run an action once it completes."""

        def check() -> None:
            if transfer.status is TransferStatus.COMPLETED:
                action()
            elif transfer.is_active:
                QTimer.singleShot(300, check)
            elif transfer.error:
                self._show_error(transfer.error)

        QTimer.singleShot(300, check)

    # -------------------------------------------------------------- transfers

    def refresh_transfers(self) -> None:
        snapshot = self.transfers.snapshot()
        self.transfer_model.set_transfers([item for item in snapshot if item.is_active])
        self.history_model.set_transfers([item for item in snapshot if not item.is_active])
        self.transfer_summary.setText(summarise(snapshot))
        active = len([item for item in snapshot if item.is_active])
        label = "Transfers" if not active else f"Transfers ({active})"
        self.sidebar.item(PAGE_TRANSFERS).setText(label)

    def _selected_transfer(self, view: QTreeView, model: TransferTableModel):
        index = view.currentIndex()
        if not index.isValid():
            return None
        return model.transfer_at(index.row())

    def pause_selected_transfer(self) -> None:
        transfer = self._selected_transfer(self.transfer_view, self.transfer_model)
        if transfer:
            self.transfers.pause(transfer.id)

    def resume_selected_transfer(self) -> None:
        transfer = self._selected_transfer(self.transfer_view, self.transfer_model)
        if transfer:
            self.transfers.resume(transfer.id)

    def cancel_selected_transfer(self) -> None:
        transfer = self._selected_transfer(self.transfer_view, self.transfer_model)
        if transfer:
            self.transfers.cancel(transfer.id)

    def retry_selected_transfer(self) -> None:
        transfer = self._selected_transfer(self.transfer_view, self.transfer_model)
        if transfer:
            self.transfers.retry(transfer.id)

    def retry_history_item(self) -> None:
        transfer = self._selected_transfer(self.history_view, self.history_model)
        if transfer:
            self.transfers.retry(transfer.id)
            self.sidebar.setCurrentRow(PAGE_TRANSFERS)

    def clear_history(self) -> None:
        self.transfers.clear_history()
        self.refresh_transfers()

    # ----------------------------------------------------------------- shares

    def refresh_shares(self) -> None:
        row = self.share_list.currentRow()
        self.share_list.blockSignals(True)
        self.share_list.clear()
        for share in self.state.shares.values():
            label = dict(PERMISSION_CHOICES).get(share.permissions, share.permissions)
            suffix = "" if share.available else "   — unavailable right now"
            item = QListWidgetItem(f"\U0001f4c1  {share.name}   [{label}]\n      {share.path}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, share.id)
            self.share_list.addItem(item)
        self.share_list.blockSignals(False)
        if 0 <= row < self.share_list.count():
            self.share_list.setCurrentRow(row)

    def _selected_share_id(self) -> str | None:
        item = self.share_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def share_selection_changed(self, _row: int) -> None:
        share_id = self._selected_share_id()
        share = self.state.get_share(share_id) if share_id else None
        self.permission_box.setEnabled(share is not None)
        if share is None:
            return
        self.permission_box.blockSignals(True)
        index = self.permission_box.findData(share.permissions)
        self.permission_box.setCurrentIndex(index if index >= 0 else 1)
        self.permission_box.blockSignals(False)

    def apply_share_permissions(self) -> None:
        share_id = self._selected_share_id()
        if not share_id:
            return
        self.state.set_share_permissions(share_id, self.permission_box.currentData())
        self.refresh_shares()

    def add_share(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to share")
        if not folder:
            return
        try:
            self.state.add_share(Path(folder))
        except ValueError as error:
            self._warn("Cannot share folder", str(error))
        self.refresh_shares()

    def remove_share(self) -> None:
        share_id = self._selected_share_id()
        if not share_id:
            return
        share = self.state.get_share(share_id)
        if share and QMessageBox.question(
            self, "Stop sharing", f"Stop sharing {share.name}? The folder itself is not touched."
        ) == QMessageBox.StandardButton.Yes:
            self.state.remove_share(share_id)
            self.refresh_shares()

    # --------------------------------------------------------------- settings

    def save_settings(self) -> None:
        self.state.set_device_name(self.setting_name.text())
        self.state.max_upload_bytes = self.setting_limit.value() * 1024 * 1024
        self.state.bind_all_interfaces = self.setting_bind_all.isChecked()
        tls_changed = self.state.use_tls != self.setting_tls.isChecked()
        self.state.use_tls = self.setting_tls.isChecked()
        self.state._save()
        if tls_changed:
            QMessageBox.information(
                self,
                "Restart needed",
                "The connection security change applies when LanLink restarts. "
                "Devices paired under the old setting will need to pair again.",
            )
        self.refresh_my_device()
        self.status_line.showMessage(
            "Settings saved. Network changes apply the next time LanLink starts.", 8000
        )

    # ---------------------------------------------------------------- closing

    def closeEvent(self, event) -> None:  # noqa: N802
        active = self.transfers.active()
        if active and QMessageBox.question(
            self,
            "Transfers running",
            f"{len(active)} transfer(s) are still running. Quit and cancel them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

        self.fast_timer.stop()
        self.slow_timer.stop()
        self.transfers.shutdown()
        self.runner.wait(2000)
        for client in self._clients.values():
            client.close()
        self.discovery.stop()
        self.service.stop()
        event.accept()
