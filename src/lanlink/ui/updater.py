"""The update dialog: what changed, download it, verify it, hand it over.

Everything slow happens on a worker thread. The dialog only ever paints — a
download that blocked the GUI would freeze the window for the length of an
installer, which is exactly the defect the transfer code was written to avoid.

Nothing installs on its own. The user presses Update Now, watches the download,
and the installer is only started once its SHA-256 matches the digest published
in the same release. If it does not match, the file is deleted and said so.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..updates import (
    ChecksumMismatch,
    DownloadProgress,
    UpdateCancelled,
    UpdateCheck,
    VerifiedInstaller,
    prepare_update,
)


def launch_installer(installer: VerifiedInstaller) -> None:
    """Start the verified installer and leave it to the user."""
    from .. import updates

    if not isinstance(installer, VerifiedInstaller):
        raise TypeError("Only a verified installer may be launched.")
    path = Path(installer.path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} is no longer there.")
    if sys.platform == "win32":
        startfile = getattr(updates.os, "startfile", None) or getattr(os, "startfile", None)
        if startfile is not None:
            startfile(str(path))
        else:
            subprocess.Popen([str(path)])
    else:
        subprocess.Popen([str(path)])


def format_size(size: int | None) -> str:
    if not size:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class DownloadSignals(QObject):
    progress = Signal(int, object)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()


class DownloadJob(QRunnable):
    """Fetch and verify one release, off the GUI thread."""

    def __init__(self, check: UpdateCheck, folder: Path) -> None:
        super().__init__()
        self.check = check
        self.folder = folder
        self.signals = DownloadSignals()
        self._cancelled = False
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        release = self.check.release
        if release is None:  # pragma: no cover - the dialog does not open without one
            self.signals.failed.emit("There is no release to download.")
            return

        def report(progress: DownloadProgress) -> None:
            self.signals.progress.emit(progress.received, progress.total)

        try:
            installer = prepare_update(
                release,
                self.folder,
                on_progress=report,
                is_cancelled=lambda: self._cancelled,
            )
        except UpdateCancelled:
            self.signals.cancelled.emit()
        except ChecksumMismatch as error:
            self.signals.failed.emit(str(error))
        except LookupError as error:
            self.signals.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - shown to the user verbatim
            self.signals.failed.emit(
                f"The download failed: {error.__class__.__name__}. Check the network and try again."
            )
        else:
            self.signals.finished.emit(installer)


class UpdateDialog(QDialog):
    """Shows the release, downloads it on request, and hands over the installer.

    Emits `skipRequested` when the user chooses to skip the version, so the
    caller can remember it — the dialog itself owns no settings.
    """

    skipRequested = Signal(str)
    installStarting = Signal()

    def __init__(self, check: UpdateCheck, folder: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.check = check
        self.folder = Path(folder)
        self.installer: VerifiedInstaller | None = None
        self._job: DownloadJob | None = None
        self._pool = QThreadPool()

        release = check.release
        version = str(check.latest or "")
        self.setWindowTitle(f"LanLink {version} is available")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        headline = QLabel(f"<b>LanLink {version}</b> — you are running {check.current}.")
        headline.setWordWrap(True)
        layout.addWidget(headline)

        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText((release.notes if release else "").strip() or "No release notes were published.")
        notes.setMinimumHeight(180)
        layout.addWidget(notes)

        installer_asset = release.installer if release else None
        size = format_size(installer_asset.size if installer_asset else 0)
        self.detail = QLabel(
            f"{installer_asset.name}{f' · {size}' if size else ''}"
            if installer_asset
            else "This release has no Windows installer attached."
        )
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.update_button = QPushButton("Update Now")
        self.update_button.setDefault(True)
        self.update_button.clicked.connect(self.start_download)
        self.update_button.setEnabled(bool(check.can_install))
        buttons.addWidget(self.update_button)

        self.cancel_button = QPushButton("Cancel download")
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.hide()
        buttons.addWidget(self.cancel_button)

        self.skip_button = QPushButton("Skip this version")
        self.skip_button.clicked.connect(self._skip)
        buttons.addWidget(self.skip_button)

        buttons.addStretch()
        later = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        later.rejected.connect(self.reject)
        later.button(QDialogButtonBox.StandardButton.Close).setText("Later")
        buttons.addWidget(later)
        layout.addLayout(buttons)

        if not check.can_install:
            self.status.setText(
                "This release does not publish a verifiable Windows installer, so LanLink will "
                "not install it. Use the download link on the release page instead."
            )

    # ------------------------------------------------------------- downloading

    def start_download(self) -> None:
        if self._job is not None:
            return
        self.update_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.cancel_button.show()
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Downloading…")

        job = DownloadJob(self.check, self.folder)
        job.signals.progress.connect(self._on_progress)
        job.signals.finished.connect(self._on_verified)
        job.signals.failed.connect(self._on_failed)
        job.signals.cancelled.connect(self._on_cancelled)
        self._job = job
        self._pool.start(job)

    def cancel_download(self) -> None:
        if self._job is not None:
            self._job.cancel()
            self.status.setText("Cancelling…")

    def _on_progress(self, received: int, total: object) -> None:
        size = int(total) if isinstance(total, int) and total > 0 else 0
        if size:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(received / size * 100))
            self.status.setText(f"Downloading… {format_size(received)} of {format_size(size)}")
        else:
            # An unknown length still deserves a moving bar rather than a frozen one.
            self.progress.setRange(0, 0)
            self.status.setText(f"Downloading… {format_size(received)}")

    def _on_verified(self, installer: object) -> None:
        self._job = None
        self.cancel_button.hide()
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if not isinstance(installer, VerifiedInstaller):  # pragma: no cover - defensive
            self._on_failed("The download could not be verified.")
            return
        self.installer = installer
        self.status.setText(
            f"Downloaded and verified against the checksum published with the release "
            f"({installer.sha256[:16]}…).\n\n"
            "LanLink will close and the installer will open. Your settings, this device's "
            "identity, its certificate and its paired devices are kept."
        )
        self.update_button.setText("Install and restart")
        self.update_button.setEnabled(True)
        # The same button becomes Install; drop the download connection first.
        with contextlib.suppress(RuntimeError, TypeError):
            self.update_button.clicked.disconnect()
        self.update_button.clicked.connect(self._install)

    def _on_failed(self, message: str) -> None:
        self._job = None
        self.cancel_button.hide()
        self.progress.hide()
        self.update_button.setEnabled(bool(self.check.can_install))
        self.skip_button.setEnabled(True)
        self.status.setText(message)

    def _on_cancelled(self) -> None:
        self._job = None
        self.cancel_button.hide()
        self.progress.hide()
        self.update_button.setEnabled(True)
        self.skip_button.setEnabled(True)
        self.status.setText("The download was cancelled. Nothing was installed.")

    # ---------------------------------------------------------------- handover

    def _install(self) -> None:
        """Start the verified installer, then let the window close LanLink."""
        if self.installer is None:  # pragma: no cover - the button is not live before then
            return
        try:
            launch_installer(self.installer)
        except Exception as error:  # noqa: BLE001 - the user is standing right there
            self.status.setText(
                f"The installer would not start: {error}. You can run it yourself from {self.installer.path}"
            )
            return
        self.installStarting.emit()
        self.accept()

    def _skip(self) -> None:
        version = str(self.check.latest or "")
        if version:
            self.skipRequested.emit(version)
        self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.cancel_download()
        self._pool.waitForDone(2000)
        super().closeEvent(event)


class UpdateBanner(QWidget):
    """A quiet line at the top of a page: an update exists, here is what to do.

    Used by the automatic check, which must never interrupt with a dialog —
    somebody opening LanLink to move a file does not want a modal about versions.
    """

    detailsRequested = Signal()
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("updateBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        details = QPushButton("What changed")
        details.clicked.connect(self.detailsRequested)
        layout.addWidget(details)

        close = QPushButton("✕")
        close.setFlat(True)
        close.setFixedWidth(26)
        close.setToolTip("Dismiss until the next check")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self._dismiss)
        layout.addWidget(close)
        self.hide()

    def show_update(self, version: str, current: str) -> None:
        self.label.setText(f"<b>LanLink {version}</b> is available. You are running {current}.")
        self.show()

    def _dismiss(self) -> None:
        self.hide()
        self.dismissed.emit()
