"""Entry point for the packaged application.

PyInstaller needs a plain script rather than a console_scripts entry point, and
this is also the right place for the few things that only matter once LanLink is
a frozen .exe.
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def main() -> None:
    # A frozen application that spawns itself must be told, or every child
    # re-runs the whole program. Harmless when nothing spawns; fatal when
    # something does.
    multiprocessing.freeze_support()

    # Qt looks for plugins beside the executable in a frozen build.
    if getattr(sys, "frozen", False):
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(sys._MEIPASS, "PySide6", "plugins"))

    from lanlink.desktop import main as run

    run()


if __name__ == "__main__":
    main()
