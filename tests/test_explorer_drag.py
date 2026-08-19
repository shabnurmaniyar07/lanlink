"""Explorer browsing and native drag-and-drop.

The behaviour that matters here is what a *foreign* Windows application ends up
receiving when a remote file is dragged onto it: a real local path, never an
HTTPS URL, and never a partial download.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel, QMimeData, QSize, QUrl  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lanlink.staging import RemoteFile, RemoteFileStager, StagingError  # noqa: E402
from lanlink.ui.dragdrop import (  # noqa: E402
    DragPlan,
    build_mime_data,
    entry_to_remote,
    local_paths_from,
    plan_drag,
)
from lanlink.ui.thumbnails import ThumbnailCache, glyph_icon  # noqa: E402
from lanlink.ui.widgets import DropListView, DropTreeView  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def stager(tmp_path: Path) -> RemoteFileStager:
    return RemoteFileStager(tmp_path / "staging")


def remote(name: str = "part.step", size: int = 4) -> RemoteFile:
    return RemoteFile(
        device_id="dev-a",
        share_id="share-1",
        path=f"models/{name}",
        name=name,
        size=size,
        modified_at=1_700_000_000.0,
        device_name="Workshop PC",
    )


def stage(stager: RemoteFileStager, item: RemoteFile, payload: bytes = b"data") -> Path:
    return stager.stage_file(item, lambda partial: partial.write_bytes(payload))


# ------------------------------------------------------------------- mime data


def test_mime_data_carries_local_file_urls(qapp, tmp_path: Path) -> None:
    first = tmp_path / "a.step"
    second = tmp_path / "b.dwg"
    first.write_bytes(b"1")
    second.write_bytes(b"2")

    mime = build_mime_data([first, second])

    assert mime.hasUrls()
    urls = mime.urls()
    assert [url.isLocalFile() for url in urls] == [True, True]
    assert [Path(url.toLocalFile()) for url in urls] == [first.resolve(), second.resolve()]


def test_mime_data_never_contains_an_http_url(qapp, tmp_path: Path) -> None:
    """The whole point: KUKA.Sim must not be handed a web address."""
    target = tmp_path / "model.step"
    target.write_bytes(b"solid")

    mime = build_mime_data([target])

    for url in mime.urls():
        assert url.scheme() == "file"
        assert "http" not in url.toString().lower()
    assert "http" not in mime.text().lower()
    assert mime.text() == str(target.resolve())


def test_mime_text_flavour_is_a_windows_path(qapp, tmp_path: Path) -> None:
    one = tmp_path / "one.txt"
    two = tmp_path / "two.txt"
    one.write_bytes(b"1")
    two.write_bytes(b"2")

    mime = build_mime_data([one, two])

    assert mime.text().splitlines() == [str(one.resolve()), str(two.resolve())]


def test_local_paths_from_ignores_remote_urls(qapp, tmp_path: Path) -> None:
    real = tmp_path / "here.txt"
    real.write_bytes(b"x")
    mime = QMimeData()
    mime.setUrls(
        [
            QUrl.fromLocalFile(str(real)),
            QUrl("https://example.invalid/secret.txt"),
            QUrl.fromLocalFile(str(tmp_path / "missing.txt")),
        ]
    )

    assert local_paths_from(mime) == [real]


# ----------------------------------------------------------------- drag plans


def test_drag_plan_is_ready_when_everything_is_staged(stager) -> None:
    item = remote()
    staged = stage(stager, item)

    plan = plan_drag(stager, [item])

    assert plan.can_drag_now is True
    assert plan.needs_preparation is False
    assert plan.ready == [staged]


def test_drag_plan_defers_when_a_file_is_not_staged(stager) -> None:
    plan = plan_drag(stager, [remote("cold.step")])

    assert plan.can_drag_now is False
    assert plan.needs_preparation is True
    assert plan.ready == []
    assert [item.name for item in plan.pending] == ["cold.step"]


def test_mixed_selection_waits_for_the_slowest_file(stager) -> None:
    """A half-ready drag would hand over an incomplete set; that is worse than none."""
    ready = remote("ready.step")
    stage(stager, ready)
    plan = plan_drag(stager, [ready, remote("cold.dwg")])

    assert plan.can_drag_now is False
    assert len(plan.ready) == 1
    assert len(plan.pending) == 1


def test_multi_file_drag_offers_every_path(stager, qapp) -> None:
    items = [remote(f"part{index}.step") for index in range(3)]
    for item in items:
        stage(stager, item)

    plan = plan_drag(stager, items)
    mime = build_mime_data(plan.ready)

    assert plan.can_drag_now is True
    assert len(mime.urls()) == 3


def test_plan_summary_reads_like_a_status_line(stager) -> None:
    assert plan_drag(stager, []).summary() == "Nothing to drag"
    assert "Preparing cold.step" in plan_drag(stager, [remote("cold.step")]).summary()
    many = plan_drag(stager, [remote("a.step"), remote("b.step"), remote("c.step")])
    assert "and 2 more" in many.summary()
    ready = remote("hot.step")
    stage(stager, ready)
    assert plan_drag(stager, [ready]).summary() == "Dragging 1 file"


def test_partial_download_is_never_offered_for_drag(stager) -> None:
    """A failed staging must leave nothing a drop target could pick up."""
    item = remote("broken.step", size=10)

    def fetch(partial: Path) -> None:
        partial.write_bytes(b"short")  # wrong size on purpose

    with pytest.raises(StagingError):
        stager.stage_file(item, fetch)

    assert plan_drag(stager, [item]).can_drag_now is False
    assert list(stager.root.rglob("*.lanlink-part")) == []


def test_changed_remote_file_is_not_served_from_cache(stager) -> None:
    """Identity includes size and mtime, so an edited file re-downloads."""
    original = remote("model.step", size=4)
    stage(stager, original, b"data")
    edited = RemoteFile(**{**original.__dict__, "size": 9, "modified_at": 1_700_000_999.0})

    assert plan_drag(stager, [original]).can_drag_now is True
    assert plan_drag(stager, [edited]).can_drag_now is False


def test_entry_to_remote_keeps_the_listing_fields(stager) -> None:
    entry = {"name": "arm.src", "path": "progs/arm.src", "size": 12, "modified_at": 5.0, "kind": "file"}

    item = entry_to_remote(entry, "dev-9", "Cell PC", "share-7")

    assert (item.device_id, item.share_id, item.path, item.size) == ("dev-9", "share-7", "progs/arm.src", 12)
    assert item.device_name == "Cell PC"


def test_hostile_remote_name_cannot_escape_the_cache(stager) -> None:
    hostile = RemoteFile(
        device_id="dev",
        share_id="share",
        path="x",
        name="..\\..\\Windows\\System32\\evil.dll",
        size=2,
    )

    staged = stage(stager, hostile, b"xx")

    assert stager.root.resolve() in staged.resolve().parents
    assert ".." not in staged.name


# ------------------------------------------------------------------- widgets


def test_views_start_a_drag_with_the_provided_paths(qapp, tmp_path: Path) -> None:
    """startDrag must ask the provider, and do nothing when it is not ready."""
    target = tmp_path / "ready.step"
    target.write_bytes(b"x")

    import lanlink.ui.widgets as widgets

    for widget_class in (DropTreeView, DropListView):
        view = widget_class()
        captured: dict[str, QMimeData] = {}
        original = widgets.build_mime_data

        def spy(paths, _original=original, _captured=captured):
            mime = _original(paths)
            _captured["mime"] = mime
            return mime

        widgets.build_mime_data = spy
        try:
            # Nothing staged yet: the provider returns None, no drag begins.
            view.drag_provider = lambda: None
            view.startDrag(None)
            assert "mime" not in captured

            view.drag_provider = lambda: [target]
            view.startDrag(None)
        finally:
            widgets.build_mime_data = original

        assert isinstance(captured.get("mime"), QMimeData)
        assert Path(captured["mime"].urls()[0].toLocalFile()) == target.resolve()
        view.deleteLater()


def test_views_reject_drops_when_the_share_is_read_only(qapp) -> None:
    for widget_class in (DropTreeView, DropListView):
        view = widget_class()
        assert view.acceptDrops() is True
        view.set_drops_enabled(False)
        assert view.acceptDrops() is False
        view.deleteLater()


# ---------------------------------------------------------------- thumbnails


def make_image(path: Path, width: int = 300, height: int = 200) -> None:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0xFF3366)
    image.save(str(path), "PNG")


def test_thumbnail_is_generated_scaled_and_cached(qapp, tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    make_image(source)
    cache = ThumbnailCache(tmp_path / "thumbs", size=64)
    item = remote("photo.png", size=source.stat().st_size)

    assert cache.cached(item) is None
    pixmap = cache.build(item, source)

    assert isinstance(pixmap, QPixmap)
    assert max(pixmap.width(), pixmap.height()) <= 64
    assert cache.path_for(item).is_file()
    assert cache.cached(item) is not None


def test_thumbnail_survives_a_new_cache_object(qapp, tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    make_image(source)
    item = remote("photo.png", size=source.stat().st_size)
    ThumbnailCache(tmp_path / "thumbs", size=48).build(item, source)

    assert ThumbnailCache(tmp_path / "thumbs", size=48).cached(item) is not None


def test_thumbnail_of_a_non_image_is_declined(qapp, tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("not an image")
    cache = ThumbnailCache(tmp_path / "thumbs")

    assert cache.build(remote("notes.txt"), source) is None


def test_clearing_thumbnails_reports_what_it_removed(qapp, tmp_path: Path) -> None:
    source = tmp_path / "photo.png"
    make_image(source)
    cache = ThumbnailCache(tmp_path / "thumbs")
    cache.build(remote("photo.png", size=source.stat().st_size), source)

    assert cache.size_bytes() > 0
    assert cache.clear() == 1
    assert cache.size_bytes() == 0


def test_glyph_icons_are_reused_per_category(qapp) -> None:
    first = glyph_icon("a.step", "file", 32)
    second = glyph_icon("b.stp", "file", 32)
    folder = glyph_icon("Docs", "folder", 32)

    assert first is second  # same category, one cached icon
    assert folder is not first
    assert not first.pixmap(QSize(32, 32)).isNull()


# ------------------------------------------------------------- cache reporting


def test_usage_counts_finished_files_only(stager) -> None:
    stage(stager, remote("a.step"), b"data")
    partial = stager.root / "leftover"
    partial.mkdir()
    (partial / "x.step.lanlink-part").write_bytes(b"12345")

    count, total = stager.usage()

    assert count == 1
    assert total == 9


def test_clear_empties_the_cache(stager) -> None:
    stage(stager, remote("a.step"))
    stage(stager, remote("b.step"))

    assert stager.clear() == 2
    assert stager.usage() == (0, 0)


def test_selection_flags_are_available(qapp) -> None:
    """Guard the import the window uses to drive selection in tests."""
    assert QItemSelectionModel.SelectionFlag.ClearAndSelect is not None


def test_drag_plan_dataclass_is_explicit_about_emptiness() -> None:
    plan = DragPlan(ready=[], pending=[])
    assert plan.can_drag_now is False
    assert plan.needs_preparation is False


def test_cancelled_stage_leaves_nothing_behind(stager) -> None:
    """Cancel raises through fetch; the cache must not keep a half file."""
    from lanlink.transfers import TransferCancelled

    item = remote("big.step", size=1_000)

    def fetch(partial: Path) -> None:
        partial.write_bytes(b"0" * 100)
        raise TransferCancelled

    with pytest.raises(TransferCancelled):
        stager.stage_file(item, fetch)

    assert stager.usage() == (0, 0)
    assert plan_drag(stager, [item]).can_drag_now is False
    assert not stager.folder_for(item).exists()


def test_thumbnails_are_square_so_the_icon_grid_stays_uniform(qapp, tmp_path: Path) -> None:
    """A landscape preview beside a square glyph would clip every file name."""
    source = tmp_path / "wide.png"
    make_image(source, width=400, height=120)
    cache = ThumbnailCache(tmp_path / "thumbs", size=64)

    pixmap = cache.build(remote("wide.png", size=source.stat().st_size), source)

    assert (pixmap.width(), pixmap.height()) == (64, 64)
    glyph = glyph_icon("notes.txt", "file").actualSize(QSize(64, 64))
    assert (glyph.width(), glyph.height()) == (64, 64)


def test_glyph_icons_scale_to_whatever_the_view_asks_for(qapp) -> None:
    icon = glyph_icon("part.step", "file")
    for edge in (16, 32, 64, 112):
        assert icon.actualSize(QSize(edge, edge)) == QSize(edge, edge)
