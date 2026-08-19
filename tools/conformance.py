"""Check that a live LanLink node implements docs/protocol/v1.md.

Black box on purpose: it speaks only the documented protocol over a real TLS
socket. Nothing inside ``lanlink`` is imported except the client and the pinning
helper, so this proves the *wire* is right rather than that the code agrees with
itself. Point it at another implementation — an Android node sharing the phone's
folders, say — and it works the same way.

    python tools/conformance.py --host 192.168.1.20 --port 8765 --code 48210937

The node must have pairing switched on and one share with read + write + delete,
named with --share when there is more than one. Everything the run creates goes
in a folder called ``lanlink-conformance`` and is removed afterwards; nothing
already on the device is read, renamed or deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import ssl
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx

WORK_FOLDER = "lanlink-conformance"
TOKEN_HEADER = "X-LanLink-Token"


class CheckFailed(AssertionError):
    pass


def expect(condition: object, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def expect_status(response: httpx.Response, *allowed: int) -> httpx.Response:
    expect(
        response.status_code in allowed,
        f"{response.request.method} {response.request.url.path} "
        f"returned {response.status_code}, expected {' or '.join(map(str, allowed))}",
    )
    return response


@dataclass
class Result:
    section: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def record(self, section: str, name: str, run: Callable[[], None]) -> bool:
        try:
            run()
        except CheckFailed as failure:
            self.results.append(Result(section, name, False, str(failure)))
            return False
        except Exception as error:  # noqa: BLE001 - an unexpected error is still a failure
            self.results.append(Result(section, name, False, f"{type(error).__name__}: {error}"))
            return False
        self.results.append(Result(section, name, True))
        return True

    @property
    def failures(self) -> list[Result]:
        return [item for item in self.results if not item.ok]

    def render(self) -> str:
        lines = []
        section = None
        for item in self.results:
            if item.section != section:
                section = item.section
                lines.append(f"\n{section}")
            mark = "  ok  " if item.ok else "  FAIL"
            lines.append(f"{mark}  {item.name}")
            if item.detail:
                lines.append(f"        {item.detail}")
        passed = len(self.results) - len(self.failures)
        lines.append(f"\n{passed}/{len(self.results)} checks passed")
        return "\n".join(lines)


# --------------------------------------------------------------------- session


def peer_certificate(host: str, port: int, timeout: float = 8.0) -> str:
    """Read the certificate before trusting it — §4 of the specification."""
    import socket

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection((host, port), timeout=timeout) as raw,
        context.wrap_socket(raw, server_hostname=host) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    if not der:
        raise CheckFailed("the node presented no certificate")
    return ssl.DER_cert_to_PEM_cert(der)


def pinned_context(pem: str) -> ssl.SSLContext:
    context = ssl.create_default_context(cadata=pem)
    context.check_hostname = False
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def fingerprint_of(pem: str) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem)
    return hashlib.sha256(der).hexdigest()


@contextmanager
def session(base_url: str, verify: ssl.SSLContext | bool) -> Iterator[httpx.Client]:
    client = httpx.Client(base_url=base_url, verify=verify, timeout=httpx.Timeout(30, read=120))
    try:
        yield client
    finally:
        client.close()


# ----------------------------------------------------------------- the checks


class Conformance:
    def __init__(self, http: httpx.Client, token: str, share: str, report: Report) -> None:
        self.http = http
        self.auth = {TOKEN_HEADER: token}
        self.share = share
        self.report = report
        self.folder = WORK_FOLDER

    # helpers ---------------------------------------------------------------

    def get(self, url: str, **params: object) -> httpx.Response:
        return self.http.get(url, params=params, headers=self.auth)

    def entries(self, path: str = "") -> list[dict]:
        response = expect_status(self.get(f"/v1/shares/{self.share}/list", path=path), 200)
        return response.json()["entries"]

    def put_file(self, name: str, body: bytes, **params) -> httpx.Response:
        return self.http.put(
            f"/v1/files/{self.share}",
            params={"path": self.folder, "name": name, **params},
            headers={**self.auth, "Content-Type": "application/octet-stream"},
            content=body,
        )

    def upload(self, name: str, body: bytes) -> httpx.Response:
        digest = hashlib.sha256(body).hexdigest()
        return self.put_file(name, body, offset=0, finalize=True, sha256=digest)

    # §7 device -------------------------------------------------------------

    def check_device(self) -> None:
        body = expect_status(self.http.get("/health"), 200).json()
        expect(body.get("status") == "ok", "/health did not report status ok")
        device = body.get("device", {})
        for field_name in ("id", "name", "hostname", "platform", "version", "fingerprint"):
            expect(field_name in device, f"device object is missing {field_name}")
        expect(device["id"], "device id is empty")

        info = expect_status(self.http.get("/v1/device"), 200).json()
        expect("pairing_armed" in info, "/v1/device does not report pairing_armed")
        expect(
            info["device"]["id"] == device["id"],
            "/health and /v1/device disagree about the device id",
        )

    # §6 authentication -----------------------------------------------------

    def check_auth_required(self) -> None:
        for path in (
            "/v1/shares",
            f"/v1/shares/{self.share}/list",
            f"/v1/shares/{self.share}/properties",
        ):
            response = self.http.get(path)
            expect(
                response.status_code == 401,
                f"{path} answered {response.status_code} without a token, expected 401",
            )
            expect(
                isinstance(response.json().get("detail"), str),
                f"{path} did not return a detail sentence with its 401",
            )

    def check_bad_token_is_refused(self) -> None:
        response = self.http.get("/v1/shares", headers={TOKEN_HEADER: "not-a-real-token"})
        expect(response.status_code == 401, f"a made-up token was answered {response.status_code}")

    # §8 shares -------------------------------------------------------------

    def check_shares(self) -> None:
        body = expect_status(self.http.get("/v1/shares", headers=self.auth), 200).json()
        shares = body.get("shares")
        expect(isinstance(shares, list) and shares, "no shares were returned")
        for share in shares:
            for field_name in ("id", "name", "permissions", "available"):
                expect(field_name in share, f"share object is missing {field_name}")
            expect(
                set(share["permissions"]) <= {"r", "w", "d"},
                f"share {share['name']} has unknown permission flags {share['permissions']!r}",
            )
            expect(
                "/" not in share["name"] and "\\" not in share["name"],
                f"share name {share['name']!r} looks like a filesystem path",
            )

    # §9 listing ------------------------------------------------------------

    def check_listing(self) -> None:
        entries = self.entries()
        kinds = [entry.get("kind") for entry in entries]
        expect(set(kinds) <= {"file", "folder"}, f"unknown entry kinds {set(kinds)}")
        expect(
            kinds == sorted(kinds, key=lambda kind: kind != "folder"),
            "folders are not sorted above files",
        )
        for entry in entries:
            expect(not entry["path"].startswith("/"), f"path {entry['path']!r} has a leading slash")
            expect("\\" not in entry["path"], f"path {entry['path']!r} uses backslashes")
            expect(
                not entry["name"].endswith(".lanlink-part"),
                "an unfinished upload was listed as a real file",
            )
            if entry["kind"] == "folder":
                expect(entry["size"] is None, "a folder reported a size")
            else:
                expect(isinstance(entry["size"], int), "a file reported a non-integer size")
            expect(
                isinstance(entry["modified_at"], int | float),
                "modified_at is not a number",
            )

    # §13 create folder -----------------------------------------------------

    def check_create_folder(self) -> None:
        response = self.http.post(
            f"/v1/shares/{self.share}/folders",
            json={"path": "", "name": self.folder},
            headers=self.auth,
        )
        expect_status(response, 200, 409)  # 409 means a previous run left it behind
        if response.status_code == 200:
            expect(response.json().get("result") == "ok", "folder creation did not report ok")
        again = self.http.post(
            f"/v1/shares/{self.share}/folders",
            json={"path": "", "name": self.folder},
            headers=self.auth,
        )
        expect(again.status_code == 409, "creating an existing folder was not refused with 409")

    # §12 upload ------------------------------------------------------------

    def check_streaming_upload(self) -> None:
        body = b"conformance payload " * 64
        response = expect_status(self.upload("upload.bin", body), 200)
        result = response.json()
        expect(result.get("complete") is True, "a finalised upload did not report complete")
        expect(result.get("bytes") == len(body), "the stored size does not match what was sent")

    def check_upload_never_overwrites(self) -> None:
        response = self.upload("upload.bin", b"different")
        expect(response.status_code == 409, "an upload was allowed to overwrite an existing file")

    def check_resume(self) -> None:
        body = b"resume me please" * 32
        name = "resume.bin"
        first = expect_status(self.put_file(name, body[:100], offset=0, finalize=False), 200)
        expect(first.json().get("received") == 100, "the node did not report 100 bytes received")

        status = expect_status(
            self.get(f"/v1/shares/{self.share}/partial", path=self.folder, name=name), 200
        ).json()
        expect(status.get("received") == 100, "partial status disagrees with the upload")
        expect(status.get("complete") is False, "an unfinished upload was reported complete")

        ahead = self.put_file(name, b"x", offset=100_000, finalize=False)
        expect(ahead.status_code == 409, "an offset past what the node holds was accepted")
        expect(
            ahead.headers.get("X-LanLink-Received") == "100",
            "the 409 did not say where to resume from",
        )

        digest = hashlib.sha256(body).hexdigest()
        done = expect_status(self.put_file(name, body[100:], offset=100, finalize=True, sha256=digest), 200)
        expect(done.json().get("complete") is True, "the resumed upload did not complete")
        expect(done.json().get("bytes") == len(body), "the resumed file is the wrong size")

    def check_checksum_mismatch_is_discarded(self) -> None:
        name = "bad-checksum.bin"
        self.put_file(name, b"payload", offset=0, finalize=False)
        response = self.put_file(name, b"", offset=7, finalize=True, sha256="0" * 64)
        expect(response.status_code == 409, "a wrong checksum was accepted")
        names = [entry["name"] for entry in self.entries(self.folder)]
        expect(name not in names, "a file that failed its checksum was published anyway")

    # §11 download ----------------------------------------------------------

    def check_download_and_ranges(self) -> None:
        path = f"{self.folder}/upload.bin"
        whole = expect_status(self.get(f"/v1/files/{self.share}", path=path), 200)
        body = whole.content
        expect(whole.headers.get("accept-ranges") == "bytes", "Accept-Ranges: bytes is missing")
        expect(
            "attachment" in whole.headers.get("content-disposition", ""),
            "Content-Disposition does not mark the reply as an attachment",
        )

        digest = expect_status(self.get(f"/v1/shares/{self.share}/checksum", path=path), 200).json()
        expect(
            digest.get("sha256") == hashlib.sha256(body).hexdigest(),
            "the node's checksum does not match the bytes it served",
        )

        offset = len(body) // 2
        part = self.http.get(
            f"/v1/files/{self.share}",
            params={"path": path},
            headers={**self.auth, "Range": f"bytes={offset}-"},
        )
        expect_status(part, 206)
        expect(
            part.headers.get("content-range") == f"bytes {offset}-{len(body) - 1}/{len(body)}",
            f"Content-Range was {part.headers.get('content-range')!r}",
        )
        expect(part.content == body[offset:], "the resumed bytes do not line up with the whole file")

        closed = self.http.get(
            f"/v1/files/{self.share}",
            params={"path": path},
            headers={**self.auth, "Range": "bytes=0-9"},
        )
        expect_status(closed, 206)
        expect(closed.content == body[:10], "a closed range returned the wrong bytes")

        past_end = self.http.get(
            f"/v1/files/{self.share}",
            params={"path": path},
            headers={**self.auth, "Range": f"bytes={len(body)}-"},
        )
        expect_status(past_end, 416)
        expect(
            past_end.headers.get("content-range") == f"bytes */{len(body)}",
            "416 did not report the file size in Content-Range",
        )

    def check_unsupported_range_is_not_multipart(self) -> None:
        """A resuming client would write a multipart body straight into the file."""
        path = f"{self.folder}/upload.bin"
        for header in ("bytes=-10", "bytes=0-4,10-14", "items=0-"):
            response = self.http.get(
                f"/v1/files/{self.share}",
                params={"path": path},
                headers={**self.auth, "Range": header},
            )
            expect(
                response.status_code == 200,
                f"Range: {header} answered {response.status_code}; expected the whole file",
            )
            expect(
                "multipart" not in response.headers.get("content-type", "").lower(),
                f"Range: {header} produced a multipart body",
            )

    # §10 properties --------------------------------------------------------

    def check_properties(self) -> None:
        root = expect_status(self.get(f"/v1/shares/{self.share}/properties"), 200).json()
        expect(root.get("path") == "", "the share root did not report an empty path")
        expect(root.get("kind") == "folder", "the share root is not a folder")
        expect("item_count" in root, "a folder did not report item_count")

        one = expect_status(
            self.get(f"/v1/shares/{self.share}/properties", path=f"{self.folder}/upload.bin"), 200
        ).json()
        for field_name in ("name", "kind", "size", "modified_at", "extension", "read_only", "share"):
            expect(field_name in one, f"properties is missing {field_name}")
        expect(one["extension"] == ".bin", f"extension was {one['extension']!r}")

    # §14/§15/§17 operations ------------------------------------------------

    def check_rename(self) -> None:
        response = expect_status(
            self.http.post(
                f"/v1/shares/{self.share}/rename",
                json={"path": f"{self.folder}/resume.bin", "new_name": "renamed.bin"},
                headers=self.auth,
            ),
            200,
        )
        expect(response.json().get("name") == "renamed.bin", "rename reported the wrong name")

    def check_copy_returns_a_relative_path(self) -> None:
        response = expect_status(
            self.http.post(
                "/v1/operations",
                json={
                    "source_share_id": self.share,
                    "source_path": f"{self.folder}/upload.bin",
                    "destination_share_id": self.share,
                    "destination_path": self.folder,
                    "operation": "copy",
                },
                headers=self.auth,
            ),
            200,
            409,
        )
        if response.status_code == 409:
            return  # copying into the same folder as the source: nothing to prove
        path = response.json().get("path", "")
        expect(path, "the copy did not report a path")
        expect(not path.startswith("/"), f"the copy returned an absolute path: {path!r}")
        expect(":" not in path[:3], f"the copy returned a drive-qualified path: {path!r}")

    def check_delete(self) -> None:
        for name in ("upload.bin", "renamed.bin"):
            expect_status(
                self.http.request(
                    "DELETE",
                    f"/v1/shares/{self.share}/entries",
                    params={"path": f"{self.folder}/{name}"},
                    headers=self.auth,
                ),
                200,
                404,
            )

    def check_non_empty_folder_needs_recursive(self) -> None:
        self.upload("keep.bin", b"still here")
        refused = self.http.request(
            "DELETE",
            f"/v1/shares/{self.share}/entries",
            params={"path": self.folder, "recursive": False},
            headers=self.auth,
        )
        expect(refused.status_code == 409, "a non-empty folder was deleted without recursive=true")

    # §29 path and name rules ----------------------------------------------

    def check_paths_cannot_escape(self) -> None:
        for path in ("..", "../..", "..\\..", "/etc/passwd", "C:\\Windows", "//server/share"):
            response = self.get(f"/v1/shares/{self.share}/list", path=path)
            expect(
                response.status_code == 404,
                f"path {path!r} answered {response.status_code}; it must be refused with 404",
            )

    def check_names_must_be_one_safe_leaf(self) -> None:
        for name in ("../escape", "a/b", "a\\b", "CON", "trailing.", " leading", "x" * 300):
            response = self.http.post(
                f"/v1/shares/{self.share}/folders",
                json={"path": self.folder, "name": name},
                headers=self.auth,
            )
            expect(
                response.status_code in {409, 422},
                f"name {name!r} answered {response.status_code}; it must be refused",
            )

    def check_unknown_share_is_refused(self) -> None:
        response = self.get("/v1/shares/share_000000000000/list")
        expect(response.status_code == 404, "an unknown share id was not refused with 404")

    # cleanup ---------------------------------------------------------------

    def cleanup(self) -> None:
        self.http.request(
            "DELETE",
            f"/v1/shares/{self.share}/entries",
            params={"path": self.folder, "recursive": True},
            headers=self.auth,
        )


PLAN: list[tuple[str, str, str]] = [
    ("§7  device", "health and device agree", "check_device"),
    ("§6  authentication", "every file endpoint needs a token", "check_auth_required"),
    ("§6  authentication", "an invented token is refused", "check_bad_token_is_refused"),
    ("§8  shares", "share objects are complete and leak no path", "check_shares"),
    ("§9  listing", "entries are well formed and folders sort first", "check_listing"),
    ("§13 folders", "create, and refuse a duplicate", "check_create_folder"),
    ("§12 upload", "streaming upload with a checksum", "check_streaming_upload"),
    ("§12 upload", "an upload never overwrites", "check_upload_never_overwrites"),
    ("§12 upload", "resume from the offset the node holds", "check_resume"),
    (
        "§24 verification",
        "a wrong checksum is discarded, not published",
        "check_checksum_mismatch_is_discarded",
    ),
    ("§11 download", "whole file, checksum, and byte ranges", "check_download_and_ranges"),
    ("§11 download", "an unsupported range is never multipart", "check_unsupported_range_is_not_multipart"),
    ("§10 properties", "share root and a single file", "check_properties"),
    ("§14 rename", "rename reports the new name", "check_rename"),
    ("§17 copy", "the reply path is share-relative", "check_copy_returns_a_relative_path"),
    ("§15 delete", "delete a file", "check_delete"),
    ("§15 delete", "a non-empty folder needs recursive", "check_non_empty_folder_needs_recursive"),
    ("§29 paths", "a path can never leave the share", "check_paths_cannot_escape"),
    ("§29 names", "a name must be one safe leaf", "check_names_must_be_one_safe_leaf"),
    ("§8  shares", "an unknown share id is refused", "check_unknown_share_is_refused"),
]


def run_checks(http: httpx.Client, token: str, share_id: str, report: Report) -> None:
    suite = Conformance(http, token, share_id, report)
    try:
        for section, name, method in PLAN:
            report.record(section, name, getattr(suite, method))
    finally:
        suite.cleanup()


def choose_share(shares: list[dict], wanted: str | None) -> dict:
    if wanted:
        for share in shares:
            if share["name"].lower() == wanted.lower():
                return share
        raise SystemExit(f"No share named {wanted!r}. Found: {', '.join(s['name'] for s in shares)}")
    writable = [s for s in shares if "w" in s["permissions"] and "d" in s["permissions"] and s["available"]]
    if not writable:
        found = ", ".join("{name} ({permissions})".format(**share) for share in shares)
        raise SystemExit(
            "The conformance run needs one share with read + write + delete. Found: " + found
        )
    return writable[0]


def run(host: str, port: int, code: str, share_name: str | None, insecure: bool) -> Report:
    report = Report()
    base_url = f"{'http' if insecure else 'https'}://{host}:{port}"

    verify: ssl.SSLContext | bool = True
    if not insecure:
        pem = peer_certificate(host, port)
        print(f"Certificate fingerprint: {fingerprint_of(pem)}")
        print("Compare the first 16 characters with the other device's My Device page.\n")
        verify = pinned_context(pem)

    with session(base_url, verify) as http:
        # The client owns its id (§5). The reply carries only the token and the
        # other device's public identity, so this is the id to unpair with.
        client_id = f"conformance-{uuid.uuid4()}"
        paired = http.post(
            "/v1/pair",
            json={
                "client_id": client_id,
                "client_name": "Conformance runner",
                "pair_code": code,
            },
        )
        if paired.status_code != 200:
            raise SystemExit(
                f"Pairing failed with {paired.status_code}: {paired.text}\n"
                "Switch pairing on at the other device and pass its current code."
            )
        token = paired.json()["token"]

        shares = http.get("/v1/shares", headers={TOKEN_HEADER: token}).json()["shares"]
        share = choose_share(shares, share_name)
        print(f"Testing against share {share['name']!r} ({share['permissions']})\n")

        run_checks(http, token, share["id"], report)

        # Leave nothing behind: §6 says a device may always revoke itself.
        http.delete(f"/v1/pairings/{client_id}", headers={TOKEN_HEADER: token})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--code", required=True, help="the 8-digit code shown on the other device")
    parser.add_argument("--share", help="share name to test against; the first writable one otherwise")
    parser.add_argument(
        "--insecure", action="store_true", help="plain HTTP, for a node with TLS switched off"
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    report = run(args.host, args.port, args.code, args.share, args.insecure)
    print(report.render())
    print(f"finished in {time.monotonic() - started:.1f}s")

    if report.failures:
        print(f"\n{len(report.failures)} check(s) failed. This node does not implement v1 correctly.")
        return 1
    print("\nThis node conforms to docs/protocol/v1.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
