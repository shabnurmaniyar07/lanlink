from __future__ import annotations

from pathlib import Path

import pytest

from lanlink.files import FileAccessError, copy_or_move, list_folder, resolve_in_share
from lanlink.state import HubState


def test_lists_only_files_inside_explicit_share(state: HubState, share_root: Path) -> None:
    (share_root / "photo.jpg").write_bytes(b"test")
    entries = list_folder(state, next(iter(state.shares)))
    assert entries[0]["name"] == "photo.jpg"
    assert entries[0]["kind"] == "file"


def test_folders_sort_before_files(state: HubState, share_root: Path) -> None:
    (share_root / "zzz_folder").mkdir()
    (share_root / "aaa_file.txt").write_text("x", encoding="utf-8")
    entries = list_folder(state, next(iter(state.shares)))
    assert [entry["kind"] for entry in entries] == ["folder", "file"]


def test_blocks_directory_traversal(state: HubState) -> None:
    share = next(iter(state.shares.values()))
    with pytest.raises(FileAccessError, match="leaves the shared folder"):
        resolve_in_share(share, "../private.txt")


def test_copy_is_limited_to_shared_roots(state: HubState, share_root: Path, tmp_path: Path) -> None:
    source = share_root / "source.txt"
    source.write_text("hello", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    source_share = next(iter(state.shares.values()))
    destination_share = state.add_share(destination)

    copied = copy_or_move(
        state,
        source_share_id=source_share.id,
        source_path="source.txt",
        destination_share_id=destination_share.id,
        destination_path="",
        operation="copy",
    )
    assert copied.read_text(encoding="utf-8") == "hello"
    assert source.exists(), "copy must leave the source in place"


def test_move_refuses_to_overwrite_and_keeps_source(
    state: HubState, share_root: Path, tmp_path: Path
) -> None:
    (share_root / "source.txt").write_text("new", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "source.txt").write_text("existing", encoding="utf-8")
    destination_share = state.add_share(destination)

    with pytest.raises(FileAccessError, match="already has a file"):
        copy_or_move(
            state,
            source_share_id=next(iter(state.shares)),
            source_path="source.txt",
            destination_share_id=destination_share.id,
            destination_path="",
            operation="move",
        )
    assert (share_root / "source.txt").read_text(encoding="utf-8") == "new"
