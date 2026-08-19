"""The file-type resolver behind the Explorer view's Type column and icons."""

from __future__ import annotations

import pytest

from lanlink.filetypes import Category, describe, extension_of, icon_for, is_image


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("model.step", "step"),
        ("MODEL.STEP", "step"),
        ("drawing.DXF", "dxf"),
        ("archive.tar.gz", "tar.gz"),
        ("no-extension", ""),
        (".gitignore", ""),
        ("trailing.", ""),
        ("", ""),
        ("a.b.c.txt", "txt"),
    ],
)
def test_extension_of(name: str, expected: str) -> None:
    assert extension_of(name) == expected


@pytest.mark.parametrize(
    ("name", "label", "category"),
    [
        ("model.step", "STEP 3D Model", Category.CAD),
        ("part.stp", "STEP 3D Model", Category.CAD),
        ("frame.iges", "IGES 3D Model", Category.CAD),
        ("bracket.sldprt", "SolidWorks Part", Category.CAD),
        ("design.f3d", "Fusion 360 Design", Category.CAD),
        ("drawing.dwg", "AutoCAD Drawing", Category.DRAWING),
        ("drawing.dxf", "DXF Drawing", Category.DRAWING),
        ("print.stl", "STL Mesh", Category.MODEL_3D),
        ("print.3mf", "3MF Model", Category.MODEL_3D),
        ("document.pdf", "PDF Document", Category.DOCUMENT),
        ("image.jpg", "JPEG Image", Category.IMAGE),
        ("photo.JPEG", "JPEG Image", Category.IMAGE),
        ("sheet.xlsx", "Excel Workbook", Category.SPREADSHEET),
        ("notes.docx", "Word Document", Category.DOCUMENT),
        ("bundle.zip", "ZIP Archive", Category.ARCHIVE),
        ("program.src", "KUKA Robot Program", Category.CODE),
        ("readme.txt", "Text Document", Category.TEXT),
        ("setup.exe", "Application", Category.EXECUTABLE),
    ],
)
def test_known_types(name: str, label: str, category: Category) -> None:
    resolved = describe(name)
    assert resolved.label == label
    assert resolved.category is category


def test_an_unknown_extension_still_reads_sensibly() -> None:
    resolved = describe("part.qqq")
    assert resolved.label == "QQQ File"
    assert resolved.category is Category.UNKNOWN


def test_a_file_with_no_extension() -> None:
    assert describe("Makefile").label == "File"


def test_folders_and_shares() -> None:
    assert describe("Projects", kind="folder").category is Category.FOLDER
    assert describe("Projects", kind="folder").label == "Folder"
    assert describe("Shared", kind="share").label == "Shared folder"


@pytest.mark.parametrize("name", ["a.png", "a.jpg", "a.jpeg", "a.bmp", "a.gif", "a.webp", "a.tif", "a.tiff"])
def test_thumbnailable_images(name: str) -> None:
    assert is_image(name) is True
    assert describe(name).can_thumbnail is True


@pytest.mark.parametrize("name", ["a.step", "a.pdf", "a.zip", "a.txt", "a.dwg"])
def test_non_images_do_not_claim_a_thumbnail(name: str) -> None:
    assert is_image(name) is False
    assert describe(name).can_thumbnail is False


def test_every_category_has_an_icon() -> None:
    for category in Category:
        assert icon_for("x.step" if category is Category.CAD else "x", "file")
    assert icon_for("Projects", "folder")
    assert icon_for("model.step") != icon_for("photo.jpg")
