"""Write packaging/version_info.txt from lanlink.__version__.

Windows reads the version out of a resource compiled into the .exe, and if it
disagrees with what the application reports the update check compares the wrong
numbers. One source of truth: `lanlink.__version__`.

    python tools/sync_version.py            # rewrite the resource
    python tools/sync_version.py --check    # exit 1 when it is out of date
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

OUTPUT = REPO / "packaging" / "version_info.txt"

TEMPLATE = """# UTF-8
# GENERATED FILE - regenerate with: python tools/sync_version.py
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'LanLink'),
          StringStruct('FileDescription', 'LanLink - private file sharing on your local network'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'LanLink'),
          StringStruct('LegalCopyright', 'LanLink'),
          StringStruct('OriginalFilename', 'LanLink.exe'),
          StringStruct('ProductName', 'LanLink'),
          StringStruct('ProductVersion', '{version}'),
        ],
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
"""


def numbers(version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", version.strip().lstrip("vV"))
    if not match:
        raise SystemExit(f"Cannot read a version out of {version!r}")
    major, minor, patch = (int(part) if part else 0 for part in match.groups())
    return major, minor, patch


def render() -> str:
    from lanlink import __version__

    major, minor, patch = numbers(__version__)
    return TEMPLATE.format(major=major, minor=minor, patch=patch, version=__version__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail instead of writing")
    args = parser.parse_args()

    rendered = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != rendered:
            print(f"{OUTPUT} is out of date. Run: python tools/sync_version.py")
            return 1
        print(f"{OUTPUT} matches lanlink.__version__.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
