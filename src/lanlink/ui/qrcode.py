"""Paint a pairing invite as a QR code inside the app — no browser, no image files."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from ..invite import qr_matrix

QUIET_ZONE = 4


def render_qr(payload: str, pixels: int = 240) -> QPixmap:
    matrix = qr_matrix(payload)
    modules = len(matrix) + QUIET_ZONE * 2
    scale = max(1, pixels // modules)
    size = modules * scale

    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("black"))
    for row_index, row in enumerate(matrix):
        for column_index, filled in enumerate(row):
            if filled:
                painter.drawRect(
                    (column_index + QUIET_ZONE) * scale,
                    (row_index + QUIET_ZONE) * scale,
                    scale,
                    scale,
                )
    painter.end()
    return QPixmap.fromImage(image)


class QrLabel(QLabel):
    """Shows an invite QR, or a placeholder when pairing mode is off."""

    def __init__(self, parent: QWidget | None = None, pixels: int = 220) -> None:
        super().__init__(parent)
        self.pixels = pixels
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(pixels, pixels))
        self.setStyleSheet("border: 1px solid #dfe4ef; border-radius: 8px; background: white;")
        self.clear_code()

    def set_payload(self, payload: str) -> None:
        try:
            self.setPixmap(render_qr(payload, self.pixels))
            self.setText("")
        except Exception:  # noqa: BLE001 - a missing QR encoder must not break pairing
            self.clear_code("The code above still works if you type it in.")

    def clear_code(self, message: str = "Pairing is off") -> None:
        self.setPixmap(QPixmap())
        self.setText(message)
        self.setWordWrap(True)
        self.setStyleSheet(
            "border: 1px dashed #c7cede; border-radius: 8px; color: #8b93a4; background: #fafbfe;"
        )
