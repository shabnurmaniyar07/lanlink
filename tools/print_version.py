"""Print the current LanLink version."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import lanlink  # noqa: E402

print(lanlink.__version__)
