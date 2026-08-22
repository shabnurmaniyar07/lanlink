"""Thumbnails for remote images, generated once and cached on disk.

A remote listing has no preview, and a 40 MP photo must not be pulled into the
UI to draw a 64-pixel square. So an image is staged like any other file, scaled
down once, and the small PNG is what the model hands to the view from then on.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QImageReader, QPainter, QPixmap

from ..filetypes import ICONS, Category, describe
from ..staging import RemoteFile, default_cache_root

THUMBNAIL_SIZE = 128
# Refuse to decode anything absurd; a corrupt header must not exhaust memory.
MAX_SOURCE_PIXELS = 80_000_000


def thumbnail_cache_root() -> Path:
    return default_cache_root() / "thumbnails"


class ThumbnailCache:
    """Disk-backed, with a small in-memory layer for the visible rows."""

    def __init__(self, root: Path | None = None, size: int = THUMBNAIL_SIZE) -> None:
        self.root = Path(root) if root else thumbnail_cache_root()
        self.size = size
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._memory: dict[str, QPixmap] = {}

    def key_for(self, remote: RemoteFile) -> str:
        return f"{remote.identity}-{self.size}"

    def path_for(self, remote: RemoteFile) -> Path:
        return self.root / f"{self.key_for(remote)}.png"

    def cached(self, remote: RemoteFile) -> QPixmap | None:
        key = self.key_for(remote)
        with self._lock:
            if key in self._memory:
                return self._memory[key]
        path = self.path_for(remote)
        if not path.is_file():
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        with self._lock:
            self._memory[key] = pixmap
        return pixmap

    def build(self, remote: RemoteFile, source: Path) -> QPixmap | None:
        """Scale a staged image down and remember the result.

        QImageReader is asked to scale during decode, so a huge photo is never
        fully materialised.
        """
        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid():
            if original.width() * original.height() > MAX_SOURCE_PIXELS:
                return None
            scaled = original.scaled(QSize(self.size, self.size), Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(scaled)

        image = reader.read()
        if image.isNull():
            return None
        if not original.isValid():
            image = image.scaled(
                QSize(self.size, self.size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        pixmap = self._square(QPixmap.fromImage(image))
        target = self.path_for(remote)
        target.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(str(target), "PNG")
        with self._lock:
            self._memory[self.key_for(remote)] = pixmap
        return pixmap

    def _square(self, pixmap: QPixmap) -> QPixmap:
        """Centre the preview on a square canvas.

        The icon view uses uniform item sizes, so a landscape thumbnail sitting
        next to a square type glyph would shrink every cell to the smaller of
        the two and clip the file names. Same footprint for both fixes that.
        """
        canvas = QPixmap(self.size, self.size)
        canvas.fill(QColor(0, 0, 0, 0))
        painter = QPainter(canvas)
        painter.drawPixmap(
            (self.size - pixmap.width()) // 2,
            (self.size - pixmap.height()) // 2,
            pixmap,
        )
        painter.end()
        return canvas

    def clear(self) -> int:
        with self._lock:
            self._memory.clear()
        removed = 0
        for item in self.root.glob("*.png"):
            try:
                item.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def size_bytes(self) -> int:
        return sum(item.stat().st_size for item in self.root.glob("*.png") if item.is_file())


_GLYPH_ICONS: dict[str, QIcon] = {}
# One icon per category carrying every size the views ask for. A single-pixmap
# QIcon reports that one size to the item delegate, which then lays the icon
# view out around a 48-pixel square however large the icons are set to.
GLYPH_SIZES = (16, 24, 32, 48, 64, 96, 128, 256)


def _glyph_pixmap(glyph: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = painter.font()
    font.setPointSizeF(size * 0.62)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter), glyph)
    painter.end()
    return pixmap


def glyph_icon(name: str, kind: str = "file", size: int = 48) -> QIcon:
    """A clean type icon drawn from the category glyph, cached per category."""
    category = describe(name, kind).category
    cache_key = str(category)
    cached = _GLYPH_ICONS.get(cache_key)
    if cached is not None:
        return cached

    glyph = ICONS.get(category, ICONS[Category.UNKNOWN])
    icon = QIcon()
    for edge in sorted({*GLYPH_SIZES, size}):
        icon.addPixmap(_glyph_pixmap(glyph, edge))
    _GLYPH_ICONS[cache_key] = icon
    return icon


def placeholder_image() -> QImage:
    image = QImage(1, 1, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    return image
