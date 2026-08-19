"""What a file *is*, from its name alone.

The remote listing gives a name and a size; the browser needs a human type
("STEP 3D Model"), a category to pick an icon, and a hint about whether a
thumbnail is worth attempting. Kept as data so adding a format is one line.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    FOLDER = "folder"
    SHARE = "share"
    IMAGE = "image"
    CAD = "cad"
    DRAWING = "drawing"
    MODEL_3D = "model3d"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    ARCHIVE = "archive"
    CODE = "code"
    TEXT = "text"
    AUDIO = "audio"
    VIDEO = "video"
    EXECUTABLE = "executable"
    UNKNOWN = "file"


@dataclass(frozen=True)
class FileType:
    label: str
    category: Category

    @property
    def is_image(self) -> bool:
        return self.category is Category.IMAGE

    @property
    def can_thumbnail(self) -> bool:
        """Only images get a real preview today; the rest fall back to an icon."""
        return self.category is Category.IMAGE


# extension -> (label, category). Lower case, without the dot.
_TYPES: dict[str, tuple[str, Category]] = {
    # images
    "jpg": ("JPEG Image", Category.IMAGE),
    "jpeg": ("JPEG Image", Category.IMAGE),
    "png": ("PNG Image", Category.IMAGE),
    "gif": ("GIF Image", Category.IMAGE),
    "bmp": ("Bitmap Image", Category.IMAGE),
    "webp": ("WebP Image", Category.IMAGE),
    "tif": ("TIFF Image", Category.IMAGE),
    "tiff": ("TIFF Image", Category.IMAGE),
    "svg": ("SVG Image", Category.IMAGE),
    "ico": ("Icon", Category.IMAGE),
    # CAD / engineering
    "step": ("STEP 3D Model", Category.CAD),
    "stp": ("STEP 3D Model", Category.CAD),
    "iges": ("IGES 3D Model", Category.CAD),
    "igs": ("IGES 3D Model", Category.CAD),
    "sldprt": ("SolidWorks Part", Category.CAD),
    "sldasm": ("SolidWorks Assembly", Category.CAD),
    "slddrw": ("SolidWorks Drawing", Category.CAD),
    "ipt": ("Inventor Part", Category.CAD),
    "iam": ("Inventor Assembly", Category.CAD),
    "f3d": ("Fusion 360 Design", Category.CAD),
    "f3z": ("Fusion 360 Archive", Category.CAD),
    "catpart": ("CATIA Part", Category.CAD),
    "catproduct": ("CATIA Assembly", Category.CAD),
    "prt": ("NX / Creo Part", Category.CAD),
    "asm": ("Assembly", Category.CAD),
    "x_t": ("Parasolid Model", Category.CAD),
    "x_b": ("Parasolid Model", Category.CAD),
    "sat": ("ACIS Model", Category.CAD),
    "jt": ("JT Model", Category.CAD),
    # drawings
    "dwg": ("AutoCAD Drawing", Category.DRAWING),
    "dxf": ("DXF Drawing", Category.DRAWING),
    "dwf": ("Design Web Format", Category.DRAWING),
    # 3D printing / meshes
    "stl": ("STL Mesh", Category.MODEL_3D),
    "3mf": ("3MF Model", Category.MODEL_3D),
    "obj": ("OBJ Model", Category.MODEL_3D),
    "ply": ("PLY Mesh", Category.MODEL_3D),
    "fbx": ("FBX Model", Category.MODEL_3D),
    "gltf": ("glTF Model", Category.MODEL_3D),
    "glb": ("glTF Binary", Category.MODEL_3D),
    # documents
    "pdf": ("PDF Document", Category.DOCUMENT),
    "doc": ("Word Document", Category.DOCUMENT),
    "docx": ("Word Document", Category.DOCUMENT),
    "odt": ("OpenDocument Text", Category.DOCUMENT),
    "rtf": ("Rich Text Document", Category.DOCUMENT),
    "xls": ("Excel Workbook", Category.SPREADSHEET),
    "xlsx": ("Excel Workbook", Category.SPREADSHEET),
    "xlsm": ("Excel Macro Workbook", Category.SPREADSHEET),
    "csv": ("CSV Spreadsheet", Category.SPREADSHEET),
    "ods": ("OpenDocument Spreadsheet", Category.SPREADSHEET),
    "ppt": ("PowerPoint Presentation", Category.PRESENTATION),
    "pptx": ("PowerPoint Presentation", Category.PRESENTATION),
    "odp": ("OpenDocument Presentation", Category.PRESENTATION),
    # archives
    "zip": ("ZIP Archive", Category.ARCHIVE),
    "7z": ("7-Zip Archive", Category.ARCHIVE),
    "rar": ("RAR Archive", Category.ARCHIVE),
    "tar": ("TAR Archive", Category.ARCHIVE),
    "gz": ("GZip Archive", Category.ARCHIVE),
    "bz2": ("BZip2 Archive", Category.ARCHIVE),
    "xz": ("XZ Archive", Category.ARCHIVE),
    "iso": ("Disc Image", Category.ARCHIVE),
    # robot / automation programs
    "src": ("KUKA Robot Program", Category.CODE),
    "dat": ("KUKA Data File", Category.CODE),
    "ls": ("FANUC Robot Program", Category.CODE),
    "mod": ("ABB Robot Module", Category.CODE),
    "prg": ("Robot Program", Category.CODE),
    # code and text
    "py": ("Python Script", Category.CODE),
    "js": ("JavaScript File", Category.CODE),
    "ts": ("TypeScript File", Category.CODE),
    "json": ("JSON File", Category.CODE),
    "xml": ("XML Document", Category.CODE),
    "yaml": ("YAML File", Category.CODE),
    "yml": ("YAML File", Category.CODE),
    "html": ("HTML Document", Category.CODE),
    "css": ("Stylesheet", Category.CODE),
    "c": ("C Source", Category.CODE),
    "cpp": ("C++ Source", Category.CODE),
    "h": ("C Header", Category.CODE),
    "cs": ("C# Source", Category.CODE),
    "java": ("Java Source", Category.CODE),
    "txt": ("Text Document", Category.TEXT),
    "md": ("Markdown Document", Category.TEXT),
    "log": ("Log File", Category.TEXT),
    "ini": ("Configuration File", Category.TEXT),
    "cfg": ("Configuration File", Category.TEXT),
    # media
    "mp3": ("MP3 Audio", Category.AUDIO),
    "wav": ("WAV Audio", Category.AUDIO),
    "flac": ("FLAC Audio", Category.AUDIO),
    "m4a": ("AAC Audio", Category.AUDIO),
    "ogg": ("Ogg Audio", Category.AUDIO),
    "mp4": ("MP4 Video", Category.VIDEO),
    "mkv": ("Matroska Video", Category.VIDEO),
    "avi": ("AVI Video", Category.VIDEO),
    "mov": ("QuickTime Video", Category.VIDEO),
    "wmv": ("Windows Media Video", Category.VIDEO),
    "webm": ("WebM Video", Category.VIDEO),
    # executables
    "exe": ("Application", Category.EXECUTABLE),
    "msi": ("Windows Installer", Category.EXECUTABLE),
    "bat": ("Batch File", Category.EXECUTABLE),
    "ps1": ("PowerShell Script", Category.EXECUTABLE),
    "sh": ("Shell Script", Category.EXECUTABLE),
    "dll": ("Application Extension", Category.EXECUTABLE),
}

FOLDER_TYPE = FileType("Folder", Category.FOLDER)
SHARE_TYPE = FileType("Shared folder", Category.SHARE)

ICONS: dict[Category, str] = {
    Category.FOLDER: "\U0001f4c1",
    Category.SHARE: "\U0001f4c1",
    Category.IMAGE: "\U0001f5bc",
    Category.CAD: "\U0001f9ca",
    Category.DRAWING: "\U0001f4d0",
    Category.MODEL_3D: "\U0001f9ca",
    Category.DOCUMENT: "\U0001f4c4",
    Category.SPREADSHEET: "\U0001f4ca",
    Category.PRESENTATION: "\U0001f4d1",
    Category.ARCHIVE: "\U0001f5dc",
    Category.CODE: "\U0001f4dc",
    Category.TEXT: "\U0001f4dd",
    Category.AUDIO: "\U0001f3b5",
    Category.VIDEO: "\U0001f3ac",
    Category.EXECUTABLE: "⚙",
    Category.UNKNOWN: "\U0001f4c4",
}


def extension_of(name: str) -> str:
    """Lower-case extension without the dot. Handles .tar.gz and dotfiles."""
    cleaned = (name or "").strip().rstrip(".")
    lowered = cleaned.lower()
    for compound in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lowered.endswith(compound):
            return compound.lstrip(".")
    _, dot, tail = cleaned.rpartition(".")
    if not dot or not _:
        return ""  # "README" or ".gitignore" have no usable extension
    return tail.lower()


def describe(name: str, kind: str = "file") -> FileType:
    """Resolve a listing entry to a displayable type."""
    if kind == "folder":
        return FOLDER_TYPE
    if kind == "share":
        return SHARE_TYPE

    extension = extension_of(name)
    known = _TYPES.get(extension)
    if known:
        return FileType(known[0], known[1])
    if extension:
        return FileType(f"{extension.upper()} File", Category.UNKNOWN)
    return FileType("File", Category.UNKNOWN)


def icon_for(name: str, kind: str = "file") -> str:
    return ICONS[describe(name, kind).category]


def is_image(name: str) -> bool:
    return describe(name).is_image
