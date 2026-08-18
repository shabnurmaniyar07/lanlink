from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from .files import FileAccessError, copy_or_move, destination_for_upload, get_file, list_folder
from .models import CopyMoveRequest, PairRequest
from .state import HubState, PairedDevice

UPLOAD_CHUNK = 1024 * 1024

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

    def writable(share_id: str) -> None:
        share = state.get_share(share_id)
        if share and "w" not in share.permissions:
            raise HTTPException(status_code=403, detail="This shared folder is read-only.")

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
    ) -> FileResponse:
        try:
            item = get_file(state, share_id, path)
        except FileAccessError as error:
            raise file_error(error) from error
        return FileResponse(item, filename=item.name)

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

    @app.post("/v1/operations")
    def operation(request: CopyMoveRequest, caller: PairedDevice = Depends(require_pairing)) -> dict:
        writable(request.destination_share_id)
        if request.operation == "move":
            writable(request.source_share_id)
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
