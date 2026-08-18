from __future__ import annotations

import shutil
from pathlib import Path

from .state import HubState, Share


class FileAccessError(ValueError):
    pass


def resolve_in_share(share: Share, relative_path: str = "") -> Path:
    """Resolve a URL path and prove it remains inside the explicitly shared root."""
    relative_path = relative_path.replace("\\", "/")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise FileAccessError("The path leaves the shared folder.")
    root = Path(share.path).resolve()
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
    entries = []
    for item in folder.iterdir():
        try:
            stat = item.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": item.name,
                "path": item.relative_to(Path(share.path)).as_posix(),
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
    filename = Path(filename).name
    if not filename or filename in {".", ".."}:
        raise FileAccessError("A file name is required.")
    if not folder.is_dir():
        raise FileAccessError("Destination folder not found.")
    return folder / filename


def copy_or_move(state: HubState, *, source_share_id: str, source_path: str, destination_share_id: str, destination_path: str, operation: str) -> Path:
    source = get_file(state, source_share_id, source_path)
    destination = destination_for_upload(
        state, destination_share_id, destination_path, source.name
    )
    if source == destination:
        raise FileAccessError("Source and destination are the same file.")
    if destination.exists():
        raise FileAccessError("Destination already has a file with this name.")
    if operation == "copy":
        shutil.copy2(source, destination)
    elif operation == "move":
        shutil.move(source, destination)
    else:
        raise FileAccessError("Unsupported file operation.")
    return destination
