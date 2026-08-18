"""Entry point for the native LanLink desktop application."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .state import SettingsCorruptError
from .ui.main_window import MainWindow

__all__ = ["MainWindow", "main"]


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("LanLink")
    app.setOrganizationName("LanLink")
    try:
        window = MainWindow()
    except SettingsCorruptError as error:
        # Never silently mint a new device identity: every peer's pairing depends on it.
        QMessageBox.critical(None, "LanLink settings problem", str(error))
        sys.exit(1)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
