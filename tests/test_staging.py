"""RemoteFileStager: local copies that Windows can treat as ordinary files."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from lanlink.files import PART_SUFFIX
from lanlink.staging import RemoteFile, RemoteFileStager, StagingError, default_cache_root


def remote(name: str = "model.step", **overrides) -> RemoteFile:
    values = {
        "device_id": "dev-b",
        "share_id": "share-1",
        "path": f"CAD/{name}",
        "name": name,
        "size": None,
        "modified_at": 1_700_000_000.0,
    }
    values.update(overrides)
    return RemoteFile(**values)


def writer(payload: bytes):
    def fetch(partial: Path) -> None:
        partial.write_bytes(payload)

    return fetch


@pytest.fixture
def stager(tmp_path: Path) -> RemoteFileStager:
    return RemoteFileStager(root=tmp_path / "staging")


# --------------------------------------------------------------- 1. staging


def test_stage_file_creates_a_local_file(stager: RemoteFileStager) -> None:
    payload = b"ISO-10303-21;\nSTEP DATA\n"
    item = remote(size=len(payload))

    path = stager.stage_file(item, writer(payload))

    assert path.is_file()
    assert path.read_bytes() == payload
    assert path.name == "model.step", "the original extension must survive"
    assert stager.is_cached(item) is True
    assert stager.get_local_path(item) == path


def test_the_staged_path_is_absolute_and_local(stager: RemoteFileStager) -> None:
    path = stager.stage_file(remote(), writer(b"x"))
    assert path.is_absolute()
    assert path.as_uri().startswith("file://")
    assert "https" not in path.as_uri()


def test_default_cache_root_is_under_localappdata_on_windows(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    assert default_cache_root() == Path(r"C:\Users\tester\AppData\Local") / "LanLink"


# ------------------------------------------------------- 2. SHA-256 checking


def test_a_matching_checksum_is_accepted(stager: RemoteFileStager) -> None:
    payload = b"verified bytes"
    digest = hashlib.sha256(payload).hexdigest()
    path = stager.stage_file(remote(), writer(payload), sha256=digest)
    assert path.read_bytes() == payload


def test_a_bad_checksum_is_refused_and_nothing_is_left(stager: RemoteFileStager) -> None:
    item = remote()
    with pytest.raises(StagingError, match="SHA-256"):
        stager.stage_file(item, writer(b"tampered"), sha256="00" * 32)

    assert stager.is_cached(item) is False
    assert stager.get_local_path(item) is None
    assert list(stager.root.rglob("*.step")) == []


def test_a_wrong_size_is_refused(stager: RemoteFileStager) -> None:
    item = remote(size=999)
    with pytest.raises(StagingError, match="unexpected size"):
        stager.stage_file(item, writer(b"short"))
    assert stager.is_cached(item) is False


# ---------------------------------------------- 3. failed download cleanup


def test_a_failed_download_removes_the_partial(stager: RemoteFileStager) -> None:
    item = remote()

    def fetch(partial: Path) -> None:
        partial.write_bytes(b"half of the file")
        raise ConnectionError("the other device went away")

    with pytest.raises(ConnectionError):
        stager.stage_file(item, fetch)

    assert list(stager.root.rglob(f"*{PART_SUFFIX}")) == [], "no partial may survive"
    assert stager.get_local_path(item) is None


def test_a_download_that_writes_nothing_is_an_error(stager: RemoteFileStager) -> None:
    with pytest.raises(StagingError, match="did not download"):
        stager.stage_file(remote(), lambda partial: None)


def test_a_partial_is_never_exposed_as_the_finished_file(stager: RemoteFileStager) -> None:
    """The whole point: an application must never receive a half file."""
    item = remote()
    seen: list[bool] = []

    def fetch(partial: Path) -> None:
        partial.write_bytes(b"first half")
        # Mid-download, the final path must not exist yet.
        seen.append(stager.staged_path(item).exists())
        partial.write_bytes(b"first half + second half")

    stager.stage_file(item, fetch)
    assert seen == [False]
    assert stager.staged_path(item).read_bytes() == b"first half + second half"


# ------------------------------------------------- 4. collisions and identity


def test_two_files_with_the_same_name_do_not_collide(stager: RemoteFileStager) -> None:
    one = remote(path="CAD/a/model.step")
    two = remote(path="CAD/b/model.step")

    first = stager.stage_file(one, writer(b"from folder a"))
    second = stager.stage_file(two, writer(b"from folder b"))

    assert first != second
    assert first.read_bytes() == b"from folder a"
    assert second.read_bytes() == b"from folder b"
    assert first.name == second.name == "model.step"


def test_the_same_file_on_two_devices_does_not_collide(stager: RemoteFileStager) -> None:
    first = stager.stage_file(remote(device_id="dev-b"), writer(b"B"))
    second = stager.stage_file(remote(device_id="dev-c"), writer(b"C"))
    assert first.read_bytes() == b"B"
    assert second.read_bytes() == b"C"


def test_a_changed_remote_file_gets_a_fresh_copy(stager: RemoteFileStager) -> None:
    """A newer mtime must not serve the old cached bytes."""
    old = remote(modified_at=1000.0, size=3)
    new = remote(modified_at=2000.0, size=3)

    stager.stage_file(old, writer(b"old"))
    assert stager.is_cached(new) is False, "a modified remote file is a cache miss"

    stager.stage_file(new, writer(b"new"))
    assert stager.get_local_path(new).read_bytes() == b"new"


def test_a_hostile_remote_name_cannot_escape_the_cache(stager: RemoteFileStager) -> None:
    item = remote(name="..\\..\\evil.exe", path="CAD/..\\..\\evil.exe")
    path = stager.stage_file(item, writer(b"x"))

    assert stager.root.resolve() in path.resolve().parents
    assert ".." not in path.name


# ------------------------------------------------------ 5. reusing the cache


def test_a_cached_file_is_not_downloaded_again(stager: RemoteFileStager) -> None:
    item = remote(size=7)
    calls = {"n": 0}

    def fetch(partial: Path) -> None:
        calls["n"] += 1
        partial.write_bytes(b"payload")

    first = stager.stage_file(item, fetch)
    second = stager.stage_file(item, fetch)

    assert first == second
    assert calls["n"] == 1, "the second drag should reuse the staged copy"


def test_force_restages_even_when_cached(stager: RemoteFileStager) -> None:
    item = remote()
    stager.stage_file(item, writer(b"one"))
    stager.stage_file(item, writer(b"two"), force=True)
    assert stager.staged_path(item).read_bytes() == b"two"


# ------------------------------------------------------- 6. multiple files


def test_stage_files_handles_a_multi_selection(stager: RemoteFileStager) -> None:
    items = [remote(name=f"part{index}.step", path=f"CAD/part{index}.step") for index in range(3)]

    def fetch(item: RemoteFile, partial: Path) -> None:
        partial.write_bytes(item.name.encode())

    paths = stager.stage_files(items, fetch)

    assert len(paths) == 3
    assert [path.name for path in paths] == ["part0.step", "part1.step", "part2.step"]
    assert all(path.is_file() for path in paths)
    assert paths[1].read_bytes() == b"part1.step"


# --------------------------------------------------------- 7. folder staging


def test_stage_folder_mirrors_a_tree(stager: RemoteFileStager) -> None:
    folder = remote(name="Project", path="CAD/Project")
    files = [
        remote(name="readme.txt", path="CAD/Project/readme.txt"),
        remote(name="part.step", path="CAD/Project/sub/part.step"),
    ]
    layout = {"CAD/Project/readme.txt": "readme.txt", "CAD/Project/sub/part.step": "sub/part.step"}

    def fetch(item: RemoteFile, partial: Path) -> None:
        partial.write_bytes(item.name.encode())

    root = stager.stage_folder(folder, files, fetch, relative=lambda item: layout[item.path])

    assert root.is_dir()
    assert (root / "readme.txt").read_bytes() == b"readme.txt"
    assert (root / "sub" / "part.step").read_bytes() == b"part.step"


def test_folder_staging_refuses_to_escape(stager: RemoteFileStager) -> None:
    folder = remote(name="Project", path="CAD/Project")
    files = [remote(name="evil", path="CAD/Project/evil")]

    with pytest.raises(StagingError, match="leave the staging folder"):
        stager.stage_folder(
            folder,
            files,
            lambda item, partial: partial.write_bytes(b"x"),
            relative=lambda item: "../../escaped.txt",
        )


def test_a_failed_folder_stage_removes_the_whole_tree(stager: RemoteFileStager) -> None:
    folder = remote(name="Project", path="CAD/Project")
    files = [
        remote(name="ok.txt", path="CAD/Project/ok.txt"),
        remote(name="bad.txt", path="CAD/Project/bad.txt"),
    ]

    def fetch(item: RemoteFile, partial: Path) -> None:
        if item.name == "bad.txt":
            raise ConnectionError("dropped")
        partial.write_bytes(b"ok")

    with pytest.raises(ConnectionError):
        stager.stage_folder(folder, files, fetch, relative=lambda item: item.name)

    assert not (stager.folder_for(folder) / "Project").exists()


# ------------------------------------------------------------- 8. cleanup


def test_cleanup_removes_old_files_but_keeps_fresh_ones(stager: RemoteFileStager) -> None:
    stale = stager.stage_file(remote(name="old.step", path="a/old.step"), writer(b"old"))
    fresh = stager.stage_file(remote(name="new.step", path="a/new.step"), writer(b"new"))
    os.utime(stale, (0, 0))  # long past its retention

    removed = stager.cleanup(older_than_hours=24)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_cleanup_sweeps_abandoned_partials(stager: RemoteFileStager) -> None:
    orphan = stager.root / "leftover" / ("gone.step" + PART_SUFFIX)
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"half a file")

    assert stager.cleanup() >= 1
    assert not orphan.exists(), "an abandoned partial is not a usable file"


def test_cleanup_never_touches_a_file_being_staged(stager: RemoteFileStager) -> None:
    item = remote(name="busy.step", path="a/busy.step")
    swept: list[int] = []

    def fetch(partial: Path) -> None:
        partial.write_bytes(b"in flight")
        # A cleanup running mid-drag must not delete what we are about to hand over.
        swept.append(stager.cleanup(older_than_hours=0))

    path = stager.stage_file(item, fetch)
    assert path.exists()
    assert path.read_bytes() == b"in flight"


def test_clear_empties_the_cache(stager: RemoteFileStager) -> None:
    stager.stage_file(remote(name="a.step", path="x/a.step"), writer(b"a"))
    stager.stage_file(remote(name="b.step", path="x/b.step"), writer(b"b"))
    assert stager.size_bytes() == 2

    stager.clear()

    assert stager.size_bytes() == 0
    assert stager.root.is_dir(), "the cache folder itself stays"


def test_size_and_entries_report_the_cache(stager: RemoteFileStager) -> None:
    stager.stage_file(remote(name="a.bin", path="x/a.bin"), writer(b"12345"))
    assert stager.size_bytes() == 5
    assert [entry.path.name for entry in stager.entries()] == ["a.bin"]
    assert stager.entries()[0].url.startswith("file://")
