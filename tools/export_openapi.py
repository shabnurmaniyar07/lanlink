"""Write docs/protocol/openapi.yaml straight from the running FastAPI app.

The specification must describe what the code actually does, so it is generated
rather than written by hand. ``tests/test_protocol_contract.py`` regenerates it
and fails if the committed file has drifted.

    python tools/export_openapi.py           # rewrite the file
    python tools/export_openapi.py --check   # exit 1 if it is out of date
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

OUTPUT = REPO / "docs" / "protocol" / "openapi.yaml"

HEADER = """# GENERATED FILE - do not edit by hand.
# Regenerate with: python tools/export_openapi.py
# This is the LanLink v1 wire protocol exactly as src/lanlink/api.py implements it.
"""


def document() -> dict:
    import yaml  # noqa: F401 - proves the dependency is present before we build

    from lanlink.api import create_app
    from lanlink.state import HubState

    with tempfile.TemporaryDirectory() as folder:
        state = HubState(Path(folder) / "settings.json")
        schema = create_app(state).openapi()

    schema["info"] = {
        "title": "LanLink v1",
        "version": "1.0",
        "description": (
            "Internal transport between paired LanLink devices on a local network. "
            "This API never serves a user interface. Every deployment is HTTPS with a "
            "self-signed per-device certificate that peers pin at pairing time."
        ),
    }
    schema["servers"] = [{"url": "https://{host}:{port}", "variables": {
        "host": {"default": "192.168.1.20", "description": "LAN address from mDNS or an invite"},
        "port": {"default": "8765", "description": "TCP port from mDNS or an invite"},
    }}]
    return schema


def render() -> str:
    import yaml

    return HEADER + yaml.safe_dump(document(), sort_keys=True, width=100, allow_unicode=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail instead of writing")
    args = parser.parse_args()

    rendered = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != rendered:
            print(f"{OUTPUT} is out of date. Run: python tools/export_openapi.py")
            return 1
        print(f"{OUTPUT} matches the implementation.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
