from __future__ import annotations

import contextlib
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
)
from .models import CopyMoveRequest, CreateFolderRequest, PairRequest, RenameRequest
from .state import PERM_DELETE, PERM_READ, PERM_WRITE, HubState, PairedDevice

UPLOAD_CHUNK = 1024 * 1024
DOWNLOAD_CHUNK = 512 * 1024


def parse_range(header: str | None, total: int) -> int | None:
    """Return the start offset of a simple ``bytes=N-`` request, else None."""
    if not header or not header.strip().lower().startswith("bytes="):
        return None
    spec = header.split("=", 1)[1].strip()
    if "," in spec:
        return None  # multi-range is not something LanLink ever asks for
    start_text = spec.split("-", 1)[0].strip()
    if not start_text:
        return None  # suffix ranges ("-500") are not used either
    try:
        start = int(start_text)
    except ValueError:
        return None
    return start if start >= 0 else None


def iter_file_from(path: Any, start: int) -> Iterator[bytes]:
    with open(path, "rb") as handle:
        handle.seek(start)
        while block := handle.read(DOWNLOAD_CHUNK):
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
        start = parse_range(range_header, total)
        if start is None:
            return FileResponse(item, filename=item.name)
        if start >= total:
            raise HTTPException(
                status_code=416,
                detail="The requested range is past the end of the file.",
                headers={"Content-Range": f"bytes */{total}"},
            )
        # Resume: stream from the offset the client already has.
        return StreamingResponse(
            iter_file_from(item, start),
            status_code=206,
            media_type="application/octet-stream",
            headers={
                "Content-Range": f"bytes {start}-{total - 1}/{total}",
                "Content-Length": str(total - start),
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'attachment; filename="{item.name}"',
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
            raise HTTPException(
                status_code=409, detail="A file with this name already exists."
            ) from error
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
        return {"result": "ok", "path": str(result)}

    @app.delete("/v1/pairings/{client_id}")
    def revoke_pairing(client_id: str, caller: PairedDevice = Depends(require_pairing)) -> dict:
        # Self-unpair only. Revoking any other device is a local-owner action and
        # is deliberately not reachable over the network.
        if client_id != caller.id:
            raise HTTPException(
                status_code=403, detail="A device may only remove its own pairing."
            )
        return {"revoked": state.revoke(client_id)}

    return app


def _discard(target: Path) -> None:
    with contextlib.suppress(OSError):
        target.unlink(missing_ok=True)
