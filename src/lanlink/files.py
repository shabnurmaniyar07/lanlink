from __future__ import annotations

import hashlib
import os
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


def resumable_state(state: HubState, share_id: str, folder_path: str, filename: str) -> dict:
    """How many bytes of this upload the receiver already holds."""
    target = destination_for_upload(state, share_id, folder_path, filename)
    partial = partial_for(target)
    if target.exists():
        return {"received": 0, "complete": True, "size": target.stat().st_size}
    received = partial.stat().st_size if partial.exists() else 0
    return {"received": received, "complete": False, "size": None}


def finalize_upload(
    state: HubState, share_id: str, folder_path: str, filename: str, sha256: str | None = None
) -> Path:
    """Verify the accumulated part file, then publish it under its real name."""
    target = destination_for_upload(state, share_id, folder_path, filename)
    partial = partial_for(target)
    if not partial.exists():
        raise FileAccessError("There is no partial upload to finish.")
    if target.exists():
        partial.unlink(missing_ok=True)
        raise FileAccessError("A file with this name already exists.")
    if sha256:
        actual = sha256_of(partial)
        if actual.lower() != sha256.lower():
            partial.unlink(missing_ok=True)
            raise FileAccessError("The uploaded file did not match its checksum and was discarded.")
    partial.replace(target)
    return target


def checksum(state: HubState, share_id: str, relative_path: str) -> str:
    return sha256_of(get_file(state, share_id, relative_path))


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
        if is_partial(item.name):
            continue  # an unfinished upload is not a file the user owns yet
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


PART_SUFFIX = ".lanlink-part"
HASH_CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(HASH_CHUNK):
            digest.update(block)
    return digest.hexdigest()


def partial_for(target: Path) -> Path:
    """Where an in-flight upload accumulates before it is verified and renamed."""
    return target.with_name(target.name + PART_SUFFIX)


def is_partial(name: str) -> bool:
    return name.endswith(PART_SUFFIX)


def _require_share(state: HubState, share_id: str) -> Share:
    share = state.get_share(share_id)
    if not share:
        raise FileAccessError("Unknown share.")
    return share


def resolve_entry(state: HubState, share_id: str, relative_path: str) -> Path:
    """Resolve a file *or* folder inside a share; the share root itself is off limits."""
    share = _require_share(state, share_id)
    item = resolve_in_share(share, relative_path)
    if item == Path(share.path).resolve():
        raise FileAccessError("The shared folder itself cannot be changed from another device.")
    if not item.exists():
        raise FileAccessError("That item no longer exists.")
    return item


def create_folder(state: HubState, share_id: str, parent_path: str, name: str) -> Path:
    share = _require_share(state, share_id)
    parent = resolve_in_share(share, parent_path)
    if not parent.is_dir():
        raise FileAccessError("Destination folder not found.")
    target = parent / validate_filename(name)
    if target.exists():
        raise FileAccessError("A file or folder with this name already exists.")
    target.mkdir()
    return target


def rename_entry(state: HubState, share_id: str, relative_path: str, new_name: str) -> Path:
    item = resolve_entry(state, share_id, relative_path)
    target = item.parent / validate_filename(new_name)
    if target == item:
        raise FileAccessError("That is already the name of this item.")
    if target.exists():
        raise FileAccessError("A file or folder with this name already exists.")
    item.rename(target)
    return target


def delete_entry(state: HubState, share_id: str, relative_path: str, recursive: bool = False) -> str:
    """Delete a file, or a folder when the caller explicitly asked for a recursive delete."""
    item = resolve_entry(state, share_id, relative_path)
    if item.is_dir():
        if any(item.iterdir()) and not recursive:
            raise FileAccessError("This folder is not empty. Confirm a recursive delete first.")
        shutil.rmtree(item)
        return "folder"
    item.unlink()
    return "file"


def properties(state: HubState, share_id: str, relative_path: str = "") -> dict:
    share = _require_share(state, share_id)
    item = resolve_in_share(share, relative_path)
    if not item.exists():
        raise FileAccessError("That item no longer exists.")
    stat = item.stat()
    root = Path(share.path).resolve()
    is_dir = item.is_dir()
    detail: dict = {
        "name": item.name if item != root else share.name,
        "path": "" if item == root else item.relative_to(root).as_posix(),
        "kind": "folder" if is_dir else "file",
        "size": None if is_dir else stat.st_size,
        "modified_at": stat.st_mtime,
        "created_at": stat.st_ctime,
        "accessed_at": stat.st_atime,
        "extension": "" if is_dir else item.suffix.lower(),
        "read_only": not os.access(item, os.W_OK),
        "share": share.name,
        "share_permissions": share.permissions,
    }
    if is_dir:
        folders = files = 0
        for child in item.iterdir():
            if child.is_dir():
                folders += 1
            else:
                files += 1
        detail["item_count"] = {"folders": folders, "files": files}
    return detail


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


def share_relative(state: HubState, share_id: str, item: Path) -> str:
    """A path this API may return: relative to the share root, POSIX separators.

    Absolute paths never leave the device. They would tell a paired peer where
    the owner's folders actually live, which is not theirs to know.
    """
    share = state.get_share(share_id)
    if not share:
        return item.name
    try:
        return item.resolve().relative_to(Path(share.path).resolve()).as_posix()
    except ValueError:
        return item.name
