"""Model/view backing for the remote file browser."""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)

COLUMNS = ("Name", "Size", "Modified", "Type")
KIND_ICON = {"share": "\U0001f4c1", "folder": "\U0001f4c1", "file": "\U0001f4c4"}
KIND_LABEL = {"share": "Shared folder", "folder": "Folder", "file": "File"}


def format_size(size: int | None) -> str:
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_time(stamp: float | None) -> str:
    if not stamp:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))


class RemoteEntryModel(QAbstractTableModel):
    """Rows are shares, folders or files returned by the remote node."""

    EntryRole = int(Qt.ItemDataRole.UserRole) + 1
    SortRole = int(Qt.ItemDataRole.UserRole) + 2

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entries: list[dict] = []

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(COLUMNS)

    def headerData(  # noqa: N802
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        entry = self._entries[index.row()]
        column = index.column()
        kind = entry.get("kind", "file")

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return f"{KIND_ICON.get(kind, '')}  {entry.get('name', '')}"
            if column == 1:
                return format_size(entry.get("size"))
            if column == 2:
                return format_time(entry.get("modified_at"))
            if column == 3:
                return KIND_LABEL.get(kind, kind)
        elif role == self.SortRole:
            # Folders always sort above files, whichever column is active.
            group = 0 if kind in {"share", "folder"} else 1
            if column == 1:
                return (group, entry.get("size") or 0)
            if column == 2:
                return (group, entry.get("modified_at") or 0.0)
            if column == 3:
                return (group, kind)
            return (group, str(entry.get("name", "")).lower())
        elif role == self.EntryRole:
            return entry
        elif role == Qt.ItemDataRole.TextAlignmentRole and column == 1:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def set_entries(self, entries: list[dict]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def entry_at(self, row: int) -> dict | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def entries(self) -> list[dict]:
        return list(self._entries)


class EntryFilterProxy(QSortFilterProxyModel):
    """Name search plus folders-first sorting."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setSortRole(RemoteEntryModel.SortRole)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._needle = ""

    def set_search(self, text: str) -> None:
        self._needle = text.strip().lower()
        self.invalidate()

    def lessThan(  # noqa: N802
        self,
        left: QModelIndex | QPersistentModelIndex,
        right: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        """Compare the tuple sort keys in Python.

        Qt's default comparison cannot order a Python tuple, so folders would
        drift in among the files once the user clicked a column header.
        """
        left_key = left.data(RemoteEntryModel.SortRole)
        right_key = right.data(RemoteEntryModel.SortRole)
        if left_key is None or right_key is None:
            return False
        try:
            return bool(left_key < right_key)
        except TypeError:
            return str(left_key) < str(right_key)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex | QPersistentModelIndex) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        entry = model.entry_at(source_row) if isinstance(model, RemoteEntryModel) else None
        if entry is None:
            return True
        return self._needle in str(entry.get("name", "")).lower()

    def entry_at(self, proxy_row: int) -> dict | None:
        model = self.sourceModel()
        if not isinstance(model, RemoteEntryModel):
            return None
        source_index = self.mapToSource(self.index(proxy_row, 0))
        return model.entry_at(source_index.row())
