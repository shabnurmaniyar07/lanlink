"""Prove a real device-to-device transfer between two machines.

Run this on Laptop A, pointed at Laptop B. It uses the same client, transfer
engine and verification code the desktop app uses — nothing is stubbed.

    Laptop B:  python -m lanlink.server --share "C:\\LanLink\\Shared" --pair
    Laptop A:  python tools/verify_transfer.py --peer https://192.168.1.21:8765 --code 12345678

Add --size 200 to push a 200 MB file, or --keep to leave the test files behind.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lanlink.client import LanLinkClient  # noqa: E402
from lanlink.crypto import fetch_peer_certificate, fingerprint_of_pem, short_fingerprint  # noqa: E402
from lanlink.files import sha256_of  # noqa: E402
from lanlink.transfers import (  # noqa: E402
    TransferManager,
    TransferStatus,
    download_folder_runner,
    download_runner,
    upload_folder_runner,
    upload_runner,
)

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((PASS if ok else FAIL, label, detail))
    marker = "  ok  " if ok else " FAIL "
    print(f"[{marker}] {label}" + (f"  — {detail}" if detail else ""), flush=True)
    return ok


def run(manager: TransferManager, transfer, timeout: float = 900.0) -> bool:
    """Wait for a transfer, printing progress as it goes."""
    started = time.monotonic()
    last = -1
    while transfer.is_active and time.monotonic() - started < timeout:
        percent = int(transfer.progress * 100)
        if percent != last and transfer.size:
            rate = f"{transfer.rate / 1_000_000:.1f} MB/s" if transfer.rate else ""
            print(f"        {percent:3d}%  {transfer.transferred:,} bytes  {rate}", end="\r", flush=True)
            last = percent
        time.sleep(0.1)
    print(" " * 70, end="\r")
    return transfer.status is TransferStatus.COMPLETED


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a real LanLink transfer to another device.")
    parser.add_argument("--peer", required=True, help="Other device's address, e.g. https://192.168.1.21:8765")
    parser.add_argument("--code", required=True, help="8-digit pairing code shown on the other device")
    parser.add_argument("--size", type=int, default=64, help="Large-file test size in MB (default 64)")
    parser.add_argument("--name", default="verify-laptop-a", help="Name to pair as")
    parser.add_argument("--keep", action="store_true", help="Leave test files on the other device")
    args = parser.parse_args()

    peer = args.peer.rstrip("/")
    print(f"\nLanLink transfer verification → {peer}\n" + "=" * 62)

    # 1. TLS identity
    certificate = None
    if peer.startswith("https://"):
        host, _, port = peer.removeprefix("https://").partition(":")
        try:
            certificate = fetch_peer_certificate(host, int(port or 8765))
            fingerprint = fingerprint_of_pem(certificate)
            check("Fetched the other device's certificate", True, short_fingerprint(fingerprint))
            print("        Compare that with the fingerprint on its My Device page.")
        except Exception as error:  # noqa: BLE001
            check("Fetched the other device's certificate", False, str(error))
            return 1

    # 2. Pairing
    client = LanLinkClient(peer, peer_certificate=certificate)
    try:
        result = client.pair(args.name, args.code.strip())
        client.token = result["token"]
        remote_name = result["device"]["name"]
        check("Paired with the other device", True, remote_name)
    except Exception as error:  # noqa: BLE001
        check("Paired with the other device", False, str(error))
        return 1

    # 3. Shared folders
    try:
        shares = client.shares()
    except Exception as error:  # noqa: BLE001
        check("Listed the other device's shared folders", False, str(error))
        return 1
    check("Listed the other device's shared folders", bool(shares), ", ".join(s["name"] for s in shares))

    writable = [s for s in shares if "w" in s.get("permissions", "") and s.get("available")]
    if not check("Found a writable destination folder", bool(writable)):
        print("\n        Set a share to read+write on the other device and try again.")
        return 1
    target = writable[0]
    share_id = target["id"]
    print(f"        Destination: {remote_name} / {target['name']}  [{target['permissions']}]")

    workspace = Path(tempfile.mkdtemp(prefix="lanlink-verify-"))
    manager = TransferManager(workers=2)
    failures = 0
    try:
        # 4. Single file
        payload = os.urandom(3 * 1024 * 1024)
        source = workspace / "verify-single.bin"
        source.write_bytes(payload)
        digest = sha256_of(source)

        transfer = manager.submit(
            kind="upload",
            filename=source.name,
            source=str(workspace),
            destination=f"{remote_name}/{target['name']}",
            runner=upload_runner(manager, client, share_id, "", source),
        )
        ok = run(manager, transfer)
        check("Transferred a 3 MB file", ok, transfer.error or f"{transfer.transferred:,} bytes")

        # 5. It physically arrived, with the right size and checksum
        try:
            listed = {entry["name"]: entry for entry in client.list_folder(share_id, "")}
            entry = listed.get(source.name)
            check("File exists on the other device", entry is not None)
            check(
                "Size matches",
                entry is not None and entry["size"] == len(payload),
                f"{entry['size']:,} vs {len(payload):,}" if entry is not None else "",
            )
            remote_digest = client.checksum(share_id, source.name)
            check("SHA-256 matches", remote_digest == digest, remote_digest[:16] + "…")
        except Exception as error:  # noqa: BLE001
            check("Verified the arrived file", False, str(error))

        # 6. Round trip back
        returned = workspace / "returned.bin"
        transfer = manager.submit(
            kind="download",
            filename=source.name,
            source=remote_name,
            destination=str(workspace),
            runner=download_runner(manager, client, share_id, source.name, returned),
        )
        ok = run(manager, transfer)
        check("Downloaded it back", ok, transfer.error)
        check("Round-tripped content is identical", returned.exists() and returned.read_bytes() == payload)

        # 7. Recursive folder
        tree = workspace / "verify-tree"
        (tree / "cad" / "revisions").mkdir(parents=True)
        (tree / "docs").mkdir(parents=True)
        (tree / "empty").mkdir(parents=True)
        expected = {
            "readme.txt": b"top level\n",
            "cad/part.step": os.urandom(400_000),
            "cad/revisions/rev1.txt": b"revision one\n",
            "docs/spec.md": b"# spec\n" * 400,
        }
        for relative, content in expected.items():
            (tree / relative).write_bytes(content)

        transfer = manager.submit(
            kind="upload-folder",
            filename="verify-tree/",
            source=str(workspace),
            destination=f"{remote_name}/{target['name']}",
            runner=upload_folder_runner(manager, client, share_id, "", tree),
        )
        ok = run(manager, transfer)
        check("Transferred a folder recursively", ok, transfer.error)

        mirrored = workspace / "mirrored"
        transfer = manager.submit(
            kind="download-folder",
            filename="verify-tree/",
            source=remote_name,
            destination=str(mirrored),
            runner=download_folder_runner(manager, client, share_id, "verify-tree", mirrored),
        )
        ok = run(manager, transfer)
        every = ok and all(
            (mirrored / relative).exists() and (mirrored / relative).read_bytes() == content
            for relative, content in expected.items()
        )
        check("Every file in the tree came back byte-for-byte", every, transfer.error)

        # 8. Large file
        large_bytes = args.size * 1024 * 1024
        large = workspace / "verify-large.bin"
        with large.open("wb") as handle:
            for _ in range(args.size):
                handle.write(os.urandom(1024 * 1024))
        large_digest = sha256_of(large)

        started = time.monotonic()
        transfer = manager.submit(
            kind="upload",
            filename=large.name,
            source=str(workspace),
            destination=f"{remote_name}/{target['name']}",
            runner=upload_runner(manager, client, share_id, "", large),
        )
        ok = run(manager, transfer)
        elapsed = max(0.001, time.monotonic() - started)
        speed = large_bytes / elapsed / 1_000_000
        check(f"Transferred a {args.size} MB file", ok, f"{speed:.1f} MB/s, {elapsed:.1f}s")
        try:
            check("Large file SHA-256 matches", client.checksum(share_id, large.name) == large_digest)
        except Exception as error:  # noqa: BLE001
            check("Large file SHA-256 matches", False, str(error))

        # 9. Nothing partial was left behind
        remaining = [
            entry["name"]
            for entry in client.list_folder(share_id, "")
            if entry["name"].endswith(".lanlink-part")
        ]
        check("No partial files left on the other device", not remaining, ", ".join(remaining))
        local_parts = [item.name for item in workspace.rglob("*.lanlink-part")]
        check("No partial files left on this device", not local_parts, ", ".join(local_parts))

        # 10. Clean up unless asked not to
        if not args.keep:
            removed = 0
            for name in (source.name, large.name):
                try:
                    client.delete(share_id, name)
                    removed += 1
                except Exception:  # noqa: BLE001, S110
                    pass
            try:
                client.delete(share_id, "verify-tree", recursive=True)
                removed += 1
            except Exception:  # noqa: BLE001, S110
                pass
            check(
                "Cleaned up the test files",
                removed >= 1,
                "set the share to read+write+delete for full cleanup" if removed < 3 else "",
            )
    finally:
        manager.shutdown()
        client.close()
        shutil.rmtree(workspace, ignore_errors=True)

    failures = sum(1 for status, _, _ in results if status == FAIL)
    print("=" * 62)
    print(f"{len(results) - failures}/{len(results)} checks passed")
    if failures:
        print("\nFailed checks:")
        for status, label, detail in results:
            if status == FAIL:
                print(f"  - {label}: {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
