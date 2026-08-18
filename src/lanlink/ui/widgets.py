"""Small reusable widgets: drop-aware view, breadcrumb bar, progress delegate."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QStyleOptionViewItem,
    QTreeView,
    QWidget,
)


def open_local_file(path: Path) -> None:
    """Hand a *local file* to the OS default handler.

    Deliberately not Qt's URL-opening helper: LanLink must never hand a URL to
    a web browser. This only ever receives a path on this machine.
    """
    target = str(Path(path).resolve())
    if sys.platform == "win32":
        os.startfile(target)  # noqa: S606 - local path, no shell
    elif sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", target])  # noqa: S603
    else:
        subprocess.Popen(["xdg-open", target])  # noqa: S603, S607


class DropTreeView(QTreeView):
    """A tree view that accepts dropped local files for upload."""

    filesDropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeView.DragDropMode.DropOnly)
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(True)
        self.setUniformRowHeights(True)
        self._drops_enabled = True

    def set_drops_enabled(self, enabled: bool) -> None:
        self._drops_enabled = enabled
        self.setAcceptDrops(enabled)

    def _has_files(self, event: QDropEvent | QDragEnterEvent | QDragMoveEvent) -> bool:
        return self._drops_enabled and event.mimeData().hasUrls()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._has_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._has_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if not self._has_files(event):
            event.ignore()
            return
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile() and Path(url.toLocalFile()).is_file()
        ]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class ProgressDelegate(QStyledItemDelegate):
    """Draws a real progress bar in the transfer table's progress column."""

    def __init__(self, column: int, role: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.column = column
        self.role = role

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if index.column() != self.column:
            super().paint(painter, option, index)
            return
        fraction = index.data(self.role)
        if fraction is None:
            super().paint(painter, option, index)
            return
        bar = QStyleOptionProgressBar()
        bar.rect = QRect(option.rect).adjusted(3, 5, -3, -5)
        bar.minimum = 0
        bar.maximum = 100
        bar.progress = int(float(fraction) * 100)
        bar.text = f"{bar.progress}%"
        bar.textVisible = True
        QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, bar, painter)


class Breadcrumb(QWidget):
    """Clickable path trail: Device / Share / folder / subfolder."""

    segmentClicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._buttons: list[QPushButton] = []

    def set_segments(self, segments: list[str]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # setParent(None) detaches it now; deleteLater() alone defers to the
                # next event-loop pass and the stale segment stays painted on top.
                widget.setParent(None)
                widget.deleteLater()
        self._buttons = []

        for position, label in enumerate(segments):
            if position:
                separator = QPushButton("›")
                separator.setFlat(True)
                separator.setEnabled(False)
                separator.setMaximumWidth(18)
                self._layout.addWidget(separator)
            button = QPushButton(label)
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if position == len(segments) - 1:
                button.setStyleSheet("font-weight: 600;")
            button.clicked.connect(lambda _=False, index=position: self.segmentClicked.emit(index))
            self._layout.addWidget(button)
            self._buttons.append(button)
        self._layout.addStretch()

    def segment_count(self) -> int:
        return len(self._buttons)
