"""Somewhere for a windowed build to say what went wrong.

LanLink 0.1.0 crashed on launch and left nothing behind: a frozen application
has no console, so the traceback existed only inside a dialog the user had to
photograph. That is not a way to run software people install.

Every run now writes to a rotating log beside the settings, and an unhandled
exception is recorded there before the process dies. The log holds versions,
paths and error text — never file contents, never tokens, never share names.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path
from types import TracebackType

LOG_NAME = "lanlink.log"
MAX_BYTES = 512 * 1024
BACKUPS = 2

_started = False


def log_folder() -> Path:
    """Beside the cache, not beside the executable — an installed .exe lives in
    Program Files, where a normal user cannot write."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "LanLink" / "logs"


def log_file() -> Path:
    return log_folder() / LOG_NAME


def start_logging(level: int = logging.INFO) -> Path | None:
    """Attach the rotating file handler. Safe to call twice; only the first counts."""
    global _started
    if _started:
        return log_file()

    folder = log_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            folder / LOG_NAME, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    except OSError:
        # A read-only or missing profile must not stop the application starting.
        return None

    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _started = True
    return folder / LOG_NAME


def install_excepthook() -> None:
    """Record what killed the application, then let the old hook do its job."""
    previous = sys.excepthook

    def hook(kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
        logging.getLogger("lanlink").critical(
            "Unhandled exception\n%s", "".join(traceback.format_exception(kind, value, tb))
        )
        previous(kind, value, tb)

    sys.excepthook = hook


def describe_environment(version: str) -> None:
    """One line per run. When somebody reports a bug, this is the line to ask for."""
    logging.getLogger("lanlink").info(
        "LanLink %s starting — python %s, frozen=%s, platform=%s",
        version,
        sys.version.split()[0],
        bool(getattr(sys, "frozen", False)),
        sys.platform,
    )
