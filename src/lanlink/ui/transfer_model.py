"""Model/view backing for the transfer queue and the history page."""

from __future__ import annotations

import time

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPersistentModelIndex, Qt

from ..transfers import Transfer, TransferStatus
from .browser_model import format_size, format_time

COLUMNS = ("File", "From", "To", "Size", "Progress", "Speed", "ETA", "Status")

STATUS_LABEL = {
    TransferStatus.QUEUED: "Queued",
    TransferStatus.RUNNING: "Transferring",
    TransferStatus.PAUSED: "Paused",
    TransferStatus.COMPLETED: "Completed",
    TransferStatus.FAILED: "Failed",
    TransferStatus.CANCELLED: "Cancelled",
}


def format_rate(rate: float) -> str:
    if rate <= 0:
        return ""
    return f"{format_size(int(rate))}/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


class TransferTableModel(QAbstractTableModel):
    TransferRole = int(Qt.ItemDataRole.UserRole) + 1
    ProgressRole = int(Qt.ItemDataRole.UserRole) + 2

    def __init__(self, parent: QObject | None = None, history: bool = False) -> None:
        super().__init__(parent)
        self._rows: list[Transfer] = []
        self._history = history

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(COLUMNS)

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if self._history and section == 6:
                return "Finished"
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        transfer = self._rows[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return transfer.filename
            if column == 1:
                return transfer.source
            if column == 2:
                return transfer.destination
            if column == 3:
                return format_size(transfer.size)
            if column == 4:
                if transfer.status is TransferStatus.COMPLETED:
                    return "100%"
                return f"{transfer.progress * 100:.0f}%" if transfer.size else "—"
            if column == 5:
                return format_rate(transfer.rate)
            if column == 6:
                if self._history:
                    return format_time(transfer.finished_at)
                return format_eta(transfer.eta_seconds)
            if column == 7:
                return transfer.error or STATUS_LABEL[transfer.status]
        elif role == self.TransferRole:
            return transfer
        elif role == self.ProgressRole:
            return transfer.progress
        elif role == Qt.ItemDataRole.ToolTipRole and transfer.error:
            return transfer.error
        return None

    def set_transfers(self, transfers: list[Transfer]) -> None:
        """Update in place when the shape is unchanged so selection survives."""
        same_shape = len(transfers) == len(self._rows) and all(
            new.id == old.id for new, old in zip(transfers, self._rows, strict=False)
        )
        if same_shape:
            self._rows = transfers
            if transfers:
                top = self.index(0, 0)
                bottom = self.index(len(transfers) - 1, len(COLUMNS) - 1)
                self.dataChanged.emit(top, bottom)
            return
        self.beginResetModel()
        self._rows = transfers
        self.endResetModel()

    def transfer_at(self, row: int) -> Transfer | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


def summarise(transfers: list[Transfer]) -> str:
    """One-line status for the sidebar badge and the tray tooltip."""
    active = [item for item in transfers if item.is_active]
    if not active:
        return "No transfers running"
    running = [item for item in active if item.status is TransferStatus.RUNNING]
    total = sum(item.size or 0 for item in running)
    done = sum(item.transferred for item in running)
    percent = f" — {done / total * 100:.0f}%" if total else ""
    word = "file" if len(active) == 1 else "files"
    return f"{len(active)} {word} transferring{percent}"


def elapsed_label(transfer: Transfer) -> str:
    if not transfer.started_at:
        return ""
    end = transfer.finished_at or time.time()
    return format_eta(end - transfer.started_at)
