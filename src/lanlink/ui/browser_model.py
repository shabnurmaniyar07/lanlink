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
from PySide6.QtGui import QIcon

from ..filetypes import describe, icon_for

COLUMNS = ("Name", "Size", "Type", "Modified")


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
    IconRole = int(Qt.ItemDataRole.UserRole) + 3

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entries: list[dict] = []
        # path -> thumbnail, filled in as they are generated off-thread.
        self._thumbnails: dict[str, QIcon] = {}
        self.icons_enabled = True

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
        name = str(entry.get("name", ""))

        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return name
            if column == 1:
                return format_size(entry.get("size"))
            if column == 2:
                return describe(name, kind).label
            if column == 3:
                return format_time(entry.get("modified_at"))
        elif role == Qt.ItemDataRole.DecorationRole and column == 0 and self.icons_enabled:
            thumbnail = self._thumbnails.get(str(entry.get("path", "")))
            return thumbnail if thumbnail is not None else self._icon_for(entry)
        elif role == self.IconRole:
            return icon_for(name, kind)
        elif role == self.SortRole:
            # Folders always sort above files, whichever column is active.
            group = 0 if kind in {"share", "folder"} else 1
            if column == 1:
                return (group, entry.get("size") or 0)
            if column == 2:
                return (group, describe(name, kind).label.lower())
            if column == 3:
                return (group, entry.get("modified_at") or 0.0)
            return (group, name.lower())
        elif role == self.EntryRole:
            return entry
        elif role == Qt.ItemDataRole.ToolTipRole:
            return f"{name}\n{describe(name, kind).label}\n{format_size(entry.get('size'))}"
        elif role == Qt.ItemDataRole.TextAlignmentRole and column == 1:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _icon_for(self, entry: dict) -> QIcon | None:
        from .thumbnails import glyph_icon

        return glyph_icon(str(entry.get("name", "")), str(entry.get("kind", "file")))

    def set_thumbnail(self, path: str, icon: QIcon) -> None:
        """Swap a generic icon for a real preview once it has been generated."""
        self._thumbnails[path] = icon
        for row, entry in enumerate(self._entries):
            if entry.get("path") == path:
                position = self.index(row, 0)
                self.dataChanged.emit(position, position, [Qt.ItemDataRole.DecorationRole])
                break

    def thumbnail_count(self) -> int:
        return len(self._thumbnails)

    def set_entries(self, entries: list[dict]) -> None:
        self.beginResetModel()
        normalized = []
        for entry in entries:
            e = dict(entry)
            if "kind" not in e or not e["kind"]:
                e["kind"] = "folder" if e.get("is_dir") else "file"
            normalized.append(e)
        self._entries = normalized
        self._thumbnails.clear()
        self.endResetModel()

    def image_entries(self) -> list[dict]:
        """Rows worth generating a thumbnail for."""
        return [
            entry
            for entry in self._entries
            if (entry.get("kind") == "file" or not entry.get("is_dir"))
            and describe(str(entry.get("name", ""))).can_thumbnail
        ]

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

    def matches(self, entry: dict) -> bool:
        """Name *or* type, so 'step' finds STEP models as well as step*.* files."""
        if not self._needle:
            return True
        name = str(entry.get("name", "")).lower()
        label = describe(str(entry.get("name", "")), str(entry.get("kind", "file"))).label.lower()
        return self._needle in name or self._needle in label

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
        return True if entry is None else self.matches(entry)

    def entry_at(self, proxy_row: int) -> dict | None:
        model = self.sourceModel()
        if not isinstance(model, RemoteEntryModel):
            return None
        source_index = self.mapToSource(self.index(proxy_row, 0))
        return model.entry_at(source_index.row())
