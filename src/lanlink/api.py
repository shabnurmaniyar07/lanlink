from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from .files import (
    FileAccessError,
    checksum,
    copy_or_move,
    create_folder,
    delete_entry,
    destination_for_upload,
    finalize_upload,
    get_file,
    list_folder,
    partial_for,
    properties,
    rename_entry,
    resumable_state,
    share_relative,
)
from .models import CopyMoveRequest, CreateFolderRequest, PairRequest, RenameRequest
from .state import PERM_DELETE, PERM_READ, PERM_WRITE, HubState, PairedDevice

UPLOAD_CHUNK = 1024 * 1024
DOWNLOAD_CHUNK = 512 * 1024


def parse_range(header: str | None) -> tuple[int, int | None] | None:
    """A single ``bytes=N-`` or ``bytes=N-M`` request, as inclusive offsets.

    The end is None for an open-ended request. None overall means "not a form
    LanLink serves" — a suffix range, a multi-range, another unit, or nonsense.
    The caller then sends the whole file, which is always a correct answer to a
    range request.
    """
    if not header or not header.strip().lower().startswith("bytes="):
        return None
    spec = header.split("=", 1)[1].strip()
    if "," in spec:
        return None  # multi-range would mean a multipart body; not worth the surface
    start_text, _, end_text = spec.partition("-")
    start_text, end_text = start_text.strip(), end_text.strip()
    if not start_text:
        return None  # suffix ranges ("-500") are not used either
    try:
        start = int(start_text)
        end = int(end_text) if end_text else None
    except ValueError:
        return None
    if start < 0 or (end is not None and end < start):
        return None
    return start, end


def iter_file_from(path: Any, start: int, end: int | None = None) -> Iterator[bytes]:
    """Yield ``path`` from ``start`` to ``end`` inclusive, or to EOF when end is None."""
    remaining = None if end is None else end - start + 1
    with open(path, "rb") as handle:
        handle.seek(start)
        while remaining is None or remaining > 0:
            size = DOWNLOAD_CHUNK if remaining is None else min(DOWNLOAD_CHUNK, remaining)
            block = handle.read(size)
            if not block:
                return
            if remaining is not None:
                remaining -= len(block)
            yield block


# HTTP status per pairing outcome. Distinct codes let a native client explain itself.
PAIR_FAILURE_STATUS = {
    "not_armed": 409,
    "rate_limited": 429,
    "locked_out": 429,
    "invalid_code": 403,
    "declined": 403,
}
PAIR_FAILURE_DETAIL = {
    "not_armed": "This device is not in pairing mode. Enable pairing on it first.",
    "rate_limited": "Too many pairing attempts. Wait a moment and try again.",
    "locked_out": "Too many incorrect codes. Pairing mode was switched off on the other device.",
    "invalid_code": "That pairing code is not correct.",
    "declined": "The other device declined the pairing request.",
}


def create_app(state: HubState) -> FastAPI:
    """Internal network transport only. This service never serves a user interface."""
    app = FastAPI(title="LanLink Hub API", version="0.1.0", docs_url=None, redoc_url=None)

    def require_pairing(x_lanlink_token: str | None = Header(default=None)) -> PairedDevice:
        device = state.identify(x_lanlink_token)
        if device is None:
            raise HTTPException(status_code=401, detail="Pair this device before accessing files.")
        return device

    def file_error(error: FileAccessError) -> HTTPException:
        return HTTPException(status_code=404, detail=str(error))

    denial = {
        PERM_READ: "This shared folder is not readable.",
        PERM_WRITE: "This shared folder is read-only.",
        PERM_DELETE: "Deleting is not permitted in this shared folder.",
    }

    def require(share_id: str, flag: str) -> None:
        share = state.get_share(share_id)
        if share and not share.allows(flag):
            raise HTTPException(status_code=403, detail=denial[flag])

    def writable(share_id: str) -> None:
        require(share_id, PERM_WRITE)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "device": state.public_device()}

    @app.get("/v1/device")
    def device() -> dict:
        return {"device": state.public_device(), "pairing_armed": state.pairing_armed}

    @app.post("/v1/pair")
    def pair(request: PairRequest, http_request: Request) -> dict:
        source = http_request.client.host if http_request.client else "unknown"
        result = state.pair(request.client_id, request.client_name, request.pair_code, source=source)
        if not result.ok:
            raise HTTPException(
                status_code=PAIR_FAILURE_STATUS.get(result.reason, 403),
                detail=PAIR_FAILURE_DETAIL.get(result.reason, "Pairing was refused."),
            )
        return {"token": result.token, "device": state.public_device()}

    @app.get("/v1/shares")
    def shares(caller: PairedDevice = Depends(require_pairing)) -> dict:
        return {
            "shares": [
                {
                    "id": share.id,
                    "name": share.name,
                    "permissions": share.permissions,
                    "available": share.available,
                }
                for share in state.shares.values()
            ]
        }

    @app.get("/v1/shares/{share_id}/list")
    def browse(
        share_id: str,
        path: str = Query(default=""),
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        try:
            return {"entries": list_folder(state, share_id, path)}
        except FileAccessError as error:
            raise file_error(error) from error

    @app.get("/v1/files/{share_id}")
    def download(
        share_id: str,
        path: str = Query(...),
        caller: PairedDevice = Depends(require_pairing),
        range_header: str | None = Header(default=None, alias="Range"),
    ) -> Response:
        try:
            item = get_file(state, share_id, path)
        except FileAccessError as error:
            raise file_error(error) from error

        total = item.stat().st_size
        if range_header is None:
            return FileResponse(item, filename=item.name)

        # A Range header is handled here and nowhere else. Delegating the odd
        # forms to the framework would make the reply depend on which version of
        # it is installed, and could return a multipart body a client is not
        # expecting. Everything LanLink does not serve becomes the whole file.
        disposition = f'attachment; filename="{item.name}"'
        requested = parse_range(range_header)
        if requested is None:
            return StreamingResponse(
                iter_file_from(item, 0),
                status_code=200,
                media_type="application/octet-stream",
                headers={
                    "Content-Length": str(total),
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": disposition,
                },
            )

        start, requested_end = requested
        if start >= total:
            raise HTTPException(
                status_code=416,
                detail="The requested range is past the end of the file.",
                headers={"Content-Range": f"bytes */{total}"},
            )
        end = total - 1 if requested_end is None else min(requested_end, total - 1)
        # Resume: stream from the offset the client already has.
        return StreamingResponse(
            iter_file_from(item, start, end),
            status_code=206,
            media_type="application/octet-stream",
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Content-Length": str(end - start + 1),
                "Accept-Ranges": "bytes",
                "Content-Disposition": disposition,
            },
        )

    @app.get("/v1/shares/{share_id}/checksum")
    def file_checksum(
        share_id: str,
        path: str = Query(...),
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        try:
            return {"sha256": checksum(state, share_id, path), "path": path}
        except FileAccessError as error:
            raise file_error(error) from error

    @app.post("/v1/uploads/{share_id}")
    async def upload(
        share_id: str,
        file: UploadFile = File(...),
        path: str = Query(default=""),
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        writable(share_id)
        try:
            target = destination_for_upload(state, share_id, path, file.filename or "")
        except FileAccessError as error:
            raise file_error(error) from error

        limit = state.max_upload_bytes
        written = 0
        created = False
        try:
            with target.open("xb") as output:
                created = True
                while block := await file.read(UPLOAD_CHUNK):
                    written += len(block)
                    if limit and written > limit:
                        raise HTTPException(
                            status_code=413,
                            detail=f"This file is larger than the {limit} byte upload limit.",
                        )
                    output.write(block)
        except FileExistsError as error:
            raise HTTPException(status_code=409, detail="A file with this name already exists.") from error
        except BaseException:
            # Never leave a truncated file behind; it would also block the retry.
            if created:
                _discard(target)
            raise
        finally:
            await file.close()
        return {"path": target.name, "bytes": target.stat().st_size}

    @app.get("/v1/shares/{share_id}/partial")
    def partial_status(
        share_id: str,
        name: str = Query(...),
        path: str = Query(default=""),
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        """How many bytes of an interrupted upload survived, so it can resume."""
        try:
            return resumable_state(state, share_id, path, name)
        except FileAccessError as error:
            raise file_error(error) from error

    @app.put("/v1/files/{share_id}")
    async def stream_upload(
        share_id: str,
        request: Request,
        path: str = Query(default=""),
        name: str = Query(...),
        offset: int = Query(default=0, ge=0),
        finalize: bool = Query(default=True),
        sha256: str | None = Query(default=None),
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        """Raw resumable upload; never buffers a whole file.

        Bytes land in a ``.lanlink-part`` sidecar. Only once the transfer
        completes (and matches its checksum, when one is supplied) does the file
        appear under its real name — so an interrupted transfer can never be
        mistaken for a finished one.
        """
        writable(share_id)
        try:
            target = destination_for_upload(state, share_id, path, name)
        except FileAccessError as error:
            raise file_error(error) from error

        if target.exists():
            raise HTTPException(status_code=409, detail="A file with this name already exists.")

        partial = partial_for(target)
        existing = partial.stat().st_size if partial.exists() else 0
        if offset > existing:
            raise HTTPException(
                status_code=409,
                detail=f"This device holds {existing} bytes; resume from there.",
                headers={"X-LanLink-Received": str(existing)},
            )
        if offset < existing:
            # The sender rewound: truncate so the bytes always line up.
            with partial.open("r+b") as handle:
                handle.truncate(offset)

        limit = state.max_upload_bytes
        written = offset
        try:
            with partial.open("r+b" if partial.exists() else "wb") as output:
                output.seek(offset)
                async for block in request.stream():
                    if not block:
                        continue
                    written += len(block)
                    if limit and written > limit:
                        raise HTTPException(
                            status_code=413,
                            detail=f"This file is larger than the {limit} byte upload limit.",
                        )
                    output.write(block)
        except HTTPException:
            _discard(partial)
            raise
        except BaseException:
            # Keep the part file: an interrupted transfer is meant to resume.
            raise

        if not finalize:
            return {"path": name, "received": written, "complete": False}

        try:
            final = finalize_upload(state, share_id, path, name, sha256)
        except FileAccessError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "path": final.name,
            "bytes": final.stat().st_size,
            "received": written,
            "complete": True,
        }

    @app.post("/v1/shares/{share_id}/finalize")
    def finalize(
        share_id: str,
        name: str = Query(...),
        path: str = Query(default=""),
        sha256: str | None = Query(default=None),
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        writable(share_id)
        try:
            final = finalize_upload(state, share_id, path, name, sha256)
        except FileAccessError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"path": final.name, "bytes": final.stat().st_size, "complete": True}

    @app.post("/v1/shares/{share_id}/folders")
    def make_folder(
        share_id: str,
        request: CreateFolderRequest,
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        writable(share_id)
        try:
            created = create_folder(state, share_id, request.path, request.name)
        except FileAccessError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"result": "ok", "name": created.name}

    @app.post("/v1/shares/{share_id}/rename")
    def rename(
        share_id: str,
        request: RenameRequest,
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        writable(share_id)
        try:
            renamed = rename_entry(state, share_id, request.path, request.new_name)
        except FileAccessError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"result": "ok", "name": renamed.name}

    @app.delete("/v1/shares/{share_id}/entries")
    def delete(
        share_id: str,
        path: str = Query(...),
        recursive: bool = Query(default=False),
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        require(share_id, PERM_DELETE)
        try:
            kind = delete_entry(state, share_id, path, recursive=recursive)
        except FileAccessError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"result": "ok", "kind": kind}

    @app.get("/v1/shares/{share_id}/properties")
    def entry_properties(
        share_id: str,
        path: str = Query(default=""),
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        try:
            return properties(state, share_id, path)
        except FileAccessError as error:
            raise file_error(error) from error

    @app.post("/v1/operations")
    def operation(request: CopyMoveRequest, caller: PairedDevice = Depends(require_pairing)) -> dict:
        writable(request.destination_share_id)
        if request.operation == "move":
            require(request.source_share_id, PERM_DELETE)
        try:
            result = copy_or_move(state, **request.model_dump())
        except FileAccessError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        # Share-relative, like every other path this API returns. The absolute
        # location would tell a remote device where our folders live on disk.
        return {"result": "ok", "path": share_relative(state, request.destination_share_id, result)}

    @app.delete("/v1/pairings/{client_id}")
    def revoke_pairing(client_id: str, caller: PairedDevice = Depends(require_pairing)) -> dict:
        # Self-unpair only. Revoking any other device is a local-owner action and
        # is deliberately not reachable over the network.
        if client_id != caller.id:
            raise HTTPException(status_code=403, detail="A device may only remove its own pairing.")
        return {"revoked": state.revoke(client_id)}

    @app.get("/v1/clipboard")
    def get_clipboard(caller: PairedDevice = Depends(require_pairing)) -> dict:
        from .ui.theme import allow_clipboard_sync

        if not allow_clipboard_sync():
            return {"text": "", "status": "disabled"}
        from .remote import get_system_clipboard

        return {"text": get_system_clipboard()}

    @app.post("/v1/clipboard")
    async def post_clipboard(request: Request, caller: PairedDevice = Depends(require_pairing)) -> dict:
        from .ui.theme import allow_clipboard_sync

        if not allow_clipboard_sync():
            return {"status": "disabled"}
        from .remote import set_system_clipboard

        body = await request.json()
        text = str(body.get("text", ""))
        set_system_clipboard(text)
        try:
            from PySide6.QtGui import QGuiApplication

            gui_app = QGuiApplication.instance()
            if isinstance(gui_app, QGuiApplication):
                gui_app.clipboard().setText(text)
        except Exception:
            pass
        return {"status": "ok", "length": len(text)}

    @app.post("/v1/remote/mouse")
    async def remote_mouse(request: Request, caller: PairedDevice = Depends(require_pairing)) -> dict:
        from .ui.theme import allow_remote_mouse

        if not allow_remote_mouse():
            return {"result": "disabled"}
        from .remote import handle_mouse_event

        data = await request.json()
        ok = handle_mouse_event(data)
        return {"result": "ok" if ok else "ignored"}

    @app.post("/v1/remote/keyboard")
    async def remote_keyboard(request: Request, caller: PairedDevice = Depends(require_pairing)) -> dict:
        from .ui.theme import allow_remote_keyboard

        if not allow_remote_keyboard():
            return {"result": "disabled"}
        from .remote import handle_keyboard_event

        data = await request.json()
        ok = handle_keyboard_event(data)
        return {"result": "ok" if ok else "ignored"}

    @app.post("/v1/remote/media")
    async def remote_media(request: Request, caller: PairedDevice = Depends(require_pairing)) -> dict:
        from .ui.theme import allow_remote_media

        if not allow_remote_media():
            return {"result": "disabled"}
        from .remote import handle_media_event

        data = await request.json()
        action = str(data.get("action", ""))
        ok = handle_media_event(action)
        return {"result": "ok" if ok else "ignored"}

    @app.get("/v1/screen/frame")
    def screen_frame(
        quality: int = 55,
        width: int = 1280,
        caller: PairedDevice = Depends(require_pairing),
    ) -> Response:
        from .ui.theme import allow_screen_mirror

        if not allow_screen_mirror():
            raise HTTPException(status_code=403, detail="Screen mirroring is disabled on this host.")
        from .remote import capture_screen_jpeg

        frame = capture_screen_jpeg(quality=quality, max_width=width)
        if not frame:
            raise HTTPException(status_code=500, detail="Could not capture desktop screen.")
        return Response(content=frame, media_type="image/jpeg")

    @app.post("/v1/backup/camera")
    async def backup_camera(
        request: Request,
        caller: PairedDevice = Depends(require_pairing),
    ) -> dict:
        from .ui.theme import allow_camera_backup, saved_camera_backup_path

        if not allow_camera_backup():
            raise HTTPException(status_code=403, detail="Camera backup is disabled on this host.")

        filename = request.headers.get("x-file-name", "").strip()
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            filename = f"photo_{int(time.time())}.jpg"

        custom_path = saved_camera_backup_path()
        if custom_path and Path(custom_path).exists():
            backup_dir = Path(custom_path) / (caller.name or caller.id[:8])
        else:
            backup_dir = Path.home() / "Downloads" / "LanLink Camera Backup" / (caller.name or caller.id[:8])

        backup_dir.mkdir(parents=True, exist_ok=True)
        target_file = backup_dir / filename

        body = await request.body()
        target_file.write_bytes(body)

        modified_ts = request.headers.get("x-modified-at")
        if modified_ts:
            try:
                ts = float(modified_ts)
                os.utime(target_file, (ts, ts))
            except Exception:
                pass

        return {
            "status": "saved",
            "filename": filename,
            "size": len(body),
            "path": str(target_file),
        }

    return app


def _discard(target: Path) -> None:
    with contextlib.suppress(OSError):
        target.unlink(missing_ok=True)
