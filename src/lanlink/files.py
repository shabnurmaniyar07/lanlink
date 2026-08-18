from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

from .state import HubState, Share

# Reserved on Windows regardless of extension; rejected everywhere for portability.
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}
INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_NAME_LENGTH = 255


class FileAccessError(ValueError):
    pass


def validate_filename(filename: str) -> str:
    """Reduce an untrusted name to one safe leaf component on every platform.

    Both path flavours are applied, so a Windows-style name uploaded to a POSIX
    host (or the reverse) cannot smuggle a separator through.
    """
    candidate = filename or ""
    if not candidate or not candidate.strip():
        raise FileAccessError("A file name is required.")
    if candidate in {".", ".."}:
        raise FileAccessError("A file name is required.")
    # Reject rather than silently rewrite: a name carrying a separator under
    # either flavour is a caller bug or an attack, not something to salvage.
    if PurePosixPath(candidate).name != candidate or PureWindowsPath(candidate).name != candidate:
        raise FileAccessError("A file name cannot contain a folder path.")
    if INVALID_NAME_CHARS.search(candidate):
        raise FileAccessError("The file name contains characters that are not allowed.")
    if candidate != candidate.rstrip(". ") or candidate != candidate.lstrip():
        raise FileAccessError("A file name cannot begin or end with a space, or end with a dot.")
    if candidate.split(".")[0].upper() in WINDOWS_RESERVED:
        raise FileAccessError("That file name is reserved by the operating system.")
    if len(candidate) > MAX_NAME_LENGTH:
        raise FileAccessError("The file name is too long.")
    return candidate


def resolve_in_share(share: Share, relative_path: str = "") -> Path:
    """Resolve a URL path and prove it remains inside the explicitly shared root."""
    if "\x00" in relative_path:
        raise FileAccessError("The path is not valid.")
    relative_path = relative_path.replace("\\", "/")
    # Reject UNC and drive-qualified input under either flavour before touching disk.
    if relative_path.startswith("//") or PureWindowsPath(relative_path).drive:
        raise FileAccessError("The path leaves the shared folder.")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise FileAccessError("The path leaves the shared folder.")
    if any(":" in part for part in relative.parts):
        raise FileAccessError("The path is not valid.")
    if not share.available:
        raise FileAccessError("This shared folder is currently unavailable.")
    root = Path(share.path).resolve()
    # resolve() follows symlinks and Windows junctions, so the containment check
    # below catches both kinds of escape.
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FileAccessError("The path leaves the shared folder.") from exc
    return candidate


def get_file(state: HubState, share_id: str, relative_path: str) -> Path:
    share = state.get_share(share_id)
    if not share:
        raise FileAccessError("Unknown share.")
    item = resolve_in_share(share, relative_path)
    if not item.is_file():
        raise FileAccessError("File not found.")
    return item


def list_folder(state: HubState, share_id: str, relative_path: str = "") -> list[dict]:
    share = state.get_share(share_id)
    if not share:
        raise FileAccessError("Unknown share.")
    folder = resolve_in_share(share, relative_path)
    if not folder.is_dir():
        raise FileAccessError("Folder not found.")
    root = Path(share.path).resolve()
    entries = []
    for item in folder.iterdir():
        try:
            stat = item.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": item.name,
                "path": item.relative_to(root).as_posix(),
                "kind": "folder" if item.is_dir() else "file",
                "size": None if item.is_dir() else stat.st_size,
                "modified_at": stat.st_mtime,
            }
        )
    return sorted(entries, key=lambda entry: (entry["kind"] != "folder", entry["name"].lower()))


def destination_for_upload(state: HubState, share_id: str, folder_path: str, filename: str) -> Path:
    share = state.get_share(share_id)
    if not share:
        raise FileAccessError("Unknown share.")
    folder = resolve_in_share(share, folder_path)
    safe_name = validate_filename(filename)
    if not folder.is_dir():
        raise FileAccessError("Destination folder not found.")
    return folder / safe_name


def copy_or_move(
    state: HubState,
    *,
    source_share_id: str,
    source_path: str,
    destination_share_id: str,
    destination_path: str,
    operation: str,
) -> Path:
    source = get_file(state, source_share_id, source_path)
    destination = destination_for_upload(state, destination_share_id, destination_path, source.name)
    if source == destination:
        raise FileAccessError("Source and destination are the same file.")
    if destination.exists():
        raise FileAccessError("Destination already has a file with this name.")
    if operation == "copy":
        shutil.copy2(source, destination)
    elif operation == "move":
        # Copy, verify, then unlink: the source is never removed before the
        # destination is known-good.
        shutil.copy2(source, destination)
        if destination.stat().st_size != source.stat().st_size:
            destination.unlink(missing_ok=True)
            raise FileAccessError("The move could not be verified; the source was left untouched.")
        source.unlink()
    else:
        raise FileAccessError("Unsupported file operation.")
    return destination
