from pathlib import Path

import pytest

from lanlink.files import FileAccessError, copy_or_move, list_folder, resolve_in_share
from lanlink.state import HubState


@pytest.fixture
def shared_state(tmp_path: Path) -> HubState:
    state = HubState(tmp_path / "settings.json")
    state.add_share(tmp_path, "Test share")
    return state


def test_lists_only_files_inside_explicit_share(shared_state: HubState, tmp_path: Path) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"test")
    entries = list_folder(shared_state, next(iter(shared_state.shares)))
    assert entries[0]["name"] == "photo.jpg"
    assert entries[0]["kind"] == "file"


def test_blocks_directory_traversal(shared_state: HubState) -> None:
    share = next(iter(shared_state.shares.values()))
    with pytest.raises(FileAccessError, match="leaves the shared folder"):
        resolve_in_share(share, "../private.txt")


def test_copy_is_limited_to_shared_roots(shared_state: HubState, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    source_share = next(iter(shared_state.shares.values()))
    destination_share = shared_state.add_share(destination)

    copied = copy_or_move(
        shared_state,
        source_share_id=source_share.id,
        source_path="source.txt",
        destination_share_id=destination_share.id,
        destination_path="",
        operation="copy",
    )
    assert copied.read_text(encoding="utf-8") == "hello"
    assert source.exists()
