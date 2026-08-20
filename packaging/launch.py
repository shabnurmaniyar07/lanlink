"""Entry point for the packaged application.

PyInstaller needs a plain script rather than a console_scripts entry point, and
this is also the right place for the few things that only matter once LanLink is
a frozen .exe.
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def give_the_streams_somewhere_to_go() -> None:
    """A windowed build has sys.stdout is None, which libraries do not expect.

    Anything that prints, or asks whether the output is a terminal, raises
    AttributeError on None and takes the whole application with it before the
    window opens. A stream pointed at nowhere costs nothing and removes the
    entire class of failure.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def main() -> None:
    give_the_streams_somewhere_to_go()

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
