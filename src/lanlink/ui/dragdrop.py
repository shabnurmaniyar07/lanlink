"""Native Windows drag-and-drop of remote files.

The target application — KUKA.Sim Pro, SolidWorks, Explorer — is given a plain
``file:///C:/...`` URL for a staged local copy. It never sees an HTTPS URL and
needs to know nothing about LanLink.

Timing is the awkward part. Qt hands the drop target its data when
``QDrag.exec()`` starts, so the local file has to exist by then. Blocking the
GUI thread to download first would freeze the window, and Qt cannot start a drag
without a real mouse gesture. So a file that is not staged yet is *prepared*:
staging starts in the background with progress on the Transfers page, and the
drag works the moment it finishes. Already-staged files drag instantly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl

from ..staging import RemoteFile, RemoteFileStager


def build_mime_data(paths: list[Path]) -> QMimeData:
    """text/uri-list of local files — what every Windows drop target expects."""
    mime = QMimeData()
    resolved = [Path(path).resolve() for path in paths]
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in resolved])
    # Some targets take the text flavour instead; give them paths, never a URL.
    mime.setText("\n".join(str(path) for path in resolved))
    return mime


def local_paths_from(mime: QMimeData) -> list[Path]:
    """The inverse, for drops arriving from Explorer."""
    return [
        Path(url.toLocalFile())
        for url in mime.urls()
        if url.isLocalFile() and Path(url.toLocalFile()).exists()
    ]


@dataclass
class DragPlan:
    """What a drag needs before it can start."""

    ready: list[Path]
    pending: list[RemoteFile]

    @property
    def can_drag_now(self) -> bool:
        return bool(self.ready) and not self.pending

    @property
    def needs_preparation(self) -> bool:
        return bool(self.pending)

    def summary(self) -> str:
        if self.can_drag_now:
            count = len(self.ready)
            return f"Dragging {count} file{'s' if count != 1 else ''}"
        if not self.pending:
            return "Nothing to drag"
        first = self.pending[0].name
        if len(self.pending) == 1:
            return f"Preparing {first}…"
        return f"Preparing {first} and {len(self.pending) - 1} more…"


def plan_drag(stager: RemoteFileStager, remotes: list[RemoteFile]) -> DragPlan:
    """Split a selection into 'already local' and 'needs downloading first'."""
    ready: list[Path] = []
    pending: list[RemoteFile] = []
    for remote in remotes:
        local = stager.get_local_path(remote)
        if local is not None:
            ready.append(local)
        else:
            pending.append(remote)
    return DragPlan(ready=ready, pending=pending)


def entry_to_remote(entry: dict, device_id: str, device_name: str, share_id: str) -> RemoteFile:
    """Turn one listing row into the identity the stager caches against."""
    return RemoteFile(
        device_id=device_id,
        share_id=share_id,
        path=str(entry.get("path", "")),
        name=str(entry.get("name", "")),
        size=entry.get("size"),
        modified_at=entry.get("modified_at"),
        device_name=device_name,
    )
