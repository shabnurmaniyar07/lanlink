"""LanLink logging folder helpers."""

from __future__ import annotations

import os
from pathlib import Path


def log_folder() -> Path:
    """Return the application logging directory, creating it if needed."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            folder = Path(base) / "LanLink" / "Logs"
        else:
            folder = Path.home() / ".lanlink" / "logs"
    else:
        folder = Path.home() / ".lanlink" / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder
