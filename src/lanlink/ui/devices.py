"""One device identity, however it was learned about.

Before Phase 3 the same physical machine could appear three times: once from
mDNS, once in "paired devices", once in "remote devices". They are merged here
by ``device_id`` so an IP change never splits a device in two.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QPersistentModelIndex, Qt

from ..discovery import NearbyDevice
from ..state import PairedDevice, RemoteDevice


class DeviceStatus(StrEnum):
    ONLINE = "online"
    CONNECTING = "connecting"
    OFFLINE = "offline"
    ERROR = "error"


STATUS_BADGE = {
    DeviceStatus.ONLINE: "\U0001f7e2",
    DeviceStatus.CONNECTING: "\U0001f7e1",
    DeviceStatus.OFFLINE: "⚪",
    DeviceStatus.ERROR: "\U0001f534",
}
STATUS_TEXT = {
    DeviceStatus.ONLINE: "Online",
    DeviceStatus.CONNECTING: "Connecting",
    DeviceStatus.OFFLINE: "Offline",
    DeviceStatus.ERROR: "Error",
}


@dataclass
class UnifiedDevice:
    id: str
    name: str
    address: str = ""
    platform: str = ""
    version: str = ""
    discovered: bool = False
    paired_out: bool = False  # we hold a token for them: we can browse them
    paired_in: bool = False  # they hold a token for us: they can browse us
    status: DeviceStatus = DeviceStatus.OFFLINE
    last_seen: float | None = None
    error: str = ""

    @property
    def badge(self) -> str:
        return STATUS_BADGE[self.status]

    @property
    def is_browsable(self) -> bool:
        return self.paired_out and bool(self.address)

    @property
    def kind_icon(self) -> str:
        return "\U0001f4f1" if self.platform.lower() == "android" else "\U0001f4bb"

    @property
    def detail(self) -> str:
        if self.error:
            return self.error
        parts = [STATUS_TEXT[self.status]]
        if self.address:
            parts.append(self.address)
        if not self.paired_out:
            parts.append("not paired yet" if self.discovered else "no address known")
        if self.paired_in and not self.paired_out:
            parts.append("this device can reach you")
        return "  •  ".join(parts)


def merge_devices(
    nearby: list[NearbyDevice],
    remotes: list[RemoteDevice],
    paired: list[PairedDevice],
    health: dict[str, DeviceStatus] | None = None,
    errors: dict[str, str] | None = None,
) -> list[UnifiedDevice]:
    """Fold the three sources into one list keyed by device id."""
    health = health or {}
    errors = errors or {}
    merged: dict[str, UnifiedDevice] = {}

    for remote in remotes:
        merged[remote.id] = UnifiedDevice(
            id=remote.id, name=remote.name, address=remote.base_url, paired_out=True
        )

    for device in nearby:
        entry = merged.get(device.id)
        if entry is None:
            entry = UnifiedDevice(id=device.id, name=device.name)
            merged[device.id] = entry
        entry.discovered = True
        entry.last_seen = device.last_seen
        # mDNS carries the live address, so it wins over the stored one after a
        # DHCP lease change.
        entry.address = device.url
        entry.name = device.name or entry.name
        entry.platform = getattr(device, "platform", "") or entry.platform
        entry.version = getattr(device, "version", "") or entry.version

    for known in paired:
        entry = merged.get(known.id)
        if entry is None:
            entry = UnifiedDevice(id=known.id, name=known.name)
            merged[known.id] = entry
        entry.paired_in = True
        entry.last_seen = max(entry.last_seen or 0.0, known.last_seen) or entry.last_seen

    for unified in merged.values():
        unified.error = errors.get(unified.id, "")
        if unified.id in health:
            unified.status = health[unified.id]
        elif unified.discovered:
            unified.status = DeviceStatus.CONNECTING
        else:
            unified.status = DeviceStatus.OFFLINE

    order = {
        DeviceStatus.ONLINE: 0,
        DeviceStatus.CONNECTING: 1,
        DeviceStatus.ERROR: 2,
        DeviceStatus.OFFLINE: 3,
    }
    return sorted(merged.values(), key=lambda item: (order[item.status], item.name.lower()))


class DeviceListModel(QAbstractListModel):
    """Model/view backing for the device list. No QTableWidget anywhere."""

    DeviceRole = int(Qt.ItemDataRole.UserRole) + 1
    StatusRole = int(Qt.ItemDataRole.UserRole) + 2

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._devices: list[UnifiedDevice] = []

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._devices)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._devices):
            return None
        device = self._devices[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{device.kind_icon}  {device.name}   {device.badge}\n     {device.detail}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{device.name}\n{device.address or 'no address'}\nDevice id: {device.id}"
        if role == self.DeviceRole:
            return device
        if role == self.StatusRole:
            return device.status
        return None

    def set_devices(self, devices: list[UnifiedDevice]) -> None:
        """Replace the whole list. Selection is preserved by the view via ids."""
        self.beginResetModel()
        self._devices = devices
        self.endResetModel()

    def device_at(self, row: int) -> UnifiedDevice | None:
        if 0 <= row < len(self._devices):
            return self._devices[row]
        return None

    def row_of(self, device_id: str) -> int:
        for row, device in enumerate(self._devices):
            if device.id == device_id:
                return row
        return -1

    def devices(self) -> list[UnifiedDevice]:
        return list(self._devices)


@dataclass
class HealthTracker:
    """Remembers the last probe result per device so the badge does not flicker."""

    statuses: dict[str, DeviceStatus] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    checked_at: dict[str, float] = field(default_factory=dict)

    def mark_online(self, device_id: str) -> None:
        self.statuses[device_id] = DeviceStatus.ONLINE
        self.errors.pop(device_id, None)
        self.checked_at[device_id] = time.time()

    def mark_error(self, device_id: str, message: str) -> None:
        self.statuses[device_id] = DeviceStatus.ERROR
        self.errors[device_id] = message
        self.checked_at[device_id] = time.time()

    def mark_offline(self, device_id: str) -> None:
        self.statuses[device_id] = DeviceStatus.OFFLINE
        self.errors.pop(device_id, None)
        self.checked_at[device_id] = time.time()

    def mark_connecting(self, device_id: str) -> None:
        self.statuses.setdefault(device_id, DeviceStatus.CONNECTING)

    def expire(self, older_than_seconds: float = 30.0) -> None:
        """Drop stale results so a sleeping laptop stops showing as online."""
        cutoff = time.time() - older_than_seconds
        for device_id, checked in list(self.checked_at.items()):
            if checked < cutoff:
                self.statuses[device_id] = DeviceStatus.OFFLINE
                self.errors.pop(device_id, None)
