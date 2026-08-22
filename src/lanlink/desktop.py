"""Entry point for the native LanLink desktop application."""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .state import DATA_DIR_ENV, SettingsCorruptError
from .ui.main_window import MainWindow
from .ui.theme import apply_theme, saved_theme

__all__ = ["MainWindow", "main"]


def _application_icon() -> QIcon | None:
    """The window and taskbar icon, from the same file the installer uses.

    Frozen builds keep it beside the executable; a source checkout has it in
    packaging/. Missing is not a failure — Qt falls back to a default.
    """
    from pathlib import Path

    candidates = [Path(getattr(sys, "_MEIPASS", "")) / "lanlink.ico"] if getattr(sys, "frozen", False) else []
    candidates.append(Path(__file__).resolve().parent.parent.parent / "packaging" / "lanlink.ico")
    for path in candidates:
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return None


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
    app.setApplicationVersion(__version__)
    icon = _application_icon()
    if icon is not None:
        app.setWindowIcon(icon)
    # Paint before the window exists, so it never flashes the wrong theme.
    apply_theme(app, saved_theme())
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
