"""Filesystem sandbox, filename validation and upload-limit regression tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lanlink.files import FileAccessError, resolve_in_share, validate_filename
from lanlink.state import HubState

TRAVERSAL_PATHS = [
    "../secret.txt",
    "../../secret.txt",
    "sub/../../secret.txt",
    "..\\secret.txt",
    "..\\..\\secret.txt",
    "/etc/passwd",
    "//server/share/file.txt",
    "\\\\server\\share\\file.txt",
    "C:\\Windows\\System32\\config\\SAM",
    "C:/Windows/win.ini",
    "file.txt:hidden",
]


@pytest.mark.parametrize("path", TRAVERSAL_PATHS)
def test_sandbox_rejects_escape_attempts(state: HubState, path: str) -> None:
    share = next(iter(state.shares.values()))
    with pytest.raises(FileAccessError):
        resolve_in_share(share, path)


def test_sandbox_allows_legitimate_nested_paths(state: HubState, share_root: Path) -> None:
    (share_root / "projects" / "cad").mkdir(parents=True)
    share = next(iter(state.shares.values()))
    resolved = resolve_in_share(share, "projects/cad")
    assert resolved == (share_root / "projects" / "cad").resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_symlink_escape_is_blocked(state: HubState, share_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
    os.symlink(outside, share_root / "escape")

    share = next(iter(state.shares.values()))
    with pytest.raises(FileAccessError):
        resolve_in_share(share, "escape/secret.txt")


BAD_NAMES = [
    "",
    "   ",
    ".",
    "..",
    "../evil.txt",
    "..\\..\\evil.txt",
    "sub/evil.txt",
    "sub\\evil.txt",
    "CON",
    "con.txt",
    "NUL.log",
    "COM1",
    "LPT9.txt",
    "trailing.",
    "trailing ",
    'quote".txt',
    "pipe|.txt",
    "star*.txt",
    "question?.txt",
    "colon:.txt",
    "null\x00.txt",
    "a" * 300,
]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_invalid_filenames_are_rejected(name: str) -> None:
    with pytest.raises(FileAccessError):
        validate_filename(name)


@pytest.mark.parametrize("name", ["report.pdf", "Drawing v2.dwg", "café.txt", "CONSOLE.txt", ".env"])
def test_valid_filenames_are_accepted(name: str) -> None:
    assert validate_filename(name) == name


@pytest.mark.parametrize("name", ["..\\..\\evil.txt", "../../evil.txt", "sub/evil.txt", "CON"])
def test_upload_rejects_unsafe_filenames(
    client: TestClient, state: HubState, auth: dict, share_root: Path, name: str
) -> None:
    share_id = next(iter(state.shares))
    response = client.post(f"/v1/uploads/{share_id}", headers=auth, files={"file": (name, b"x")})
    assert response.status_code == 404
    assert list(share_root.iterdir()) == []


def test_upload_honours_the_size_limit(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    state.max_upload_bytes = 1024
    share_id = next(iter(state.shares))
    response = client.post(f"/v1/uploads/{share_id}", headers=auth, files={"file": ("big.bin", b"0" * 4096)})
    assert response.status_code == 413
    assert not (share_root / "big.bin").exists(), "the partial file must be cleaned up"


def test_upload_within_the_limit_succeeds(
    client: TestClient, state: HubState, auth: dict, share_root: Path
) -> None:
    state.max_upload_bytes = 1024
    share_id = next(iter(state.shares))
    response = client.post(f"/v1/uploads/{share_id}", headers=auth, files={"file": ("small.bin", b"0" * 512)})
    assert response.status_code == 200
    assert (share_root / "small.bin").stat().st_size == 512


def test_upload_does_not_overwrite(client: TestClient, state: HubState, auth: dict) -> None:
    share_id = next(iter(state.shares))
    first = client.post(f"/v1/uploads/{share_id}", headers=auth, files={"file": ("a.txt", b"one")})
    assert first.status_code == 200
    second = client.post(f"/v1/uploads/{share_id}", headers=auth, files={"file": ("a.txt", b"two")})
    assert second.status_code == 409


def test_read_only_share_refuses_writes(
    client: TestClient, state: HubState, auth: dict, tmp_path: Path
) -> None:
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    share = state.add_share(readonly_dir, "Read only")
    share.permissions = "r"
    response = client.post(f"/v1/uploads/{share.id}", headers=auth, files={"file": ("a.txt", b"nope")})
    assert response.status_code == 403


def test_move_never_deletes_the_source_first(state: HubState, share_root: Path, tmp_path: Path) -> None:
    from lanlink.files import copy_or_move

    source = share_root / "moved.txt"
    source.write_text("payload", encoding="utf-8")
    destination_dir = tmp_path / "destination"
    destination_dir.mkdir()
    destination_share = state.add_share(destination_dir)

    result = copy_or_move(
        state,
        source_share_id=next(iter(state.shares)),
        source_path="moved.txt",
        destination_share_id=destination_share.id,
        destination_path="",
        operation="move",
    )
    assert result.read_text(encoding="utf-8") == "payload"
    assert not source.exists()
