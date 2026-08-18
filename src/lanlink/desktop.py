"""Entry point for the native LanLink desktop application."""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .state import DATA_DIR_ENV, SettingsCorruptError
from .ui.main_window import MainWindow

__all__ = ["MainWindow", "main"]


def main() -> None:
    parser = argparse.ArgumentParser(description="LanLink desktop application.")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Settings folder to use. A separate folder gives this window its own device "
        "identity, so a second instance can run on the same machine.",
    )
    options, remaining = parser.parse_known_args()
    if options.data_dir:
        os.environ[DATA_DIR_ENV] = options.data_dir

    app = QApplication([sys.argv[0], *remaining])
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
