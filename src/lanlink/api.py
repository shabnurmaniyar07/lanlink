from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .files import FileAccessError, copy_or_move, destination_for_upload, get_file, list_folder
from .models import CopyMoveRequest, PairRequest
from .state import HubState


def create_app(state: HubState) -> FastAPI:
    app = FastAPI(title="LanLink Hub API", version="0.1.0", docs_url=None, redoc_url=None)
    static_dir = Path(__file__).parent / "static"
    app.mount("/ui", StaticFiles(directory=static_dir), name="ui")

    def require_pairing(x_lanlink_token: str | None = Header(default=None)) -> None:
        if not state.authenticate(x_lanlink_token):
            raise HTTPException(status_code=401, detail="Pair this device before accessing files.")

    def file_error(error: FileAccessError) -> HTTPException:
        return HTTPException(status_code=404, detail=str(error))

    @app.get("/", include_in_schema=False)
    def mobile_ui() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "device": state.public_device()}

    @app.post("/v1/pair")
    def pair(request: PairRequest) -> dict:
        token = state.pair(request.client_id, request.client_name, request.pair_code)
        if not token:
            raise HTTPException(status_code=403, detail="Invalid or expired pairing code.")
        return {"token": token, "device": state.public_device()}

    @app.get("/v1/shares", dependencies=[Depends(require_pairing)])
    def shares() -> dict:
        return {
            "shares": [
                {"id": share.id, "name": share.name}
                for share in state.shares.values()
            ]
        }

    @app.get("/v1/shares/{share_id}/list", dependencies=[Depends(require_pairing)])
    def browse(share_id: str, path: str = Query(default="")) -> dict:
        try:
            return {"entries": list_folder(state, share_id, path)}
        except FileAccessError as error:
            raise file_error(error)

    @app.get("/v1/files/{share_id}", dependencies=[Depends(require_pairing)])
    def download(share_id: str, path: str = Query(...)) -> FileResponse:
        try:
            item = get_file(state, share_id, path)
        except FileAccessError as error:
            raise file_error(error)
        return FileResponse(item, filename=item.name)

    @app.post("/v1/uploads/{share_id}", dependencies=[Depends(require_pairing)])
    async def upload(share_id: str, file: UploadFile = File(...), path: str = Query(default="")) -> dict:
        try:
            target = destination_for_upload(state, share_id, path, file.filename or "")
        except FileAccessError as error:
            raise file_error(error)
        try:
            with target.open("xb") as output:
                while block := await file.read(1024 * 1024):
                    output.write(block)
        except FileExistsError:
            raise HTTPException(status_code=409, detail="A file with this name already exists.")
        finally:
            await file.close()
        return {"path": target.name, "bytes": target.stat().st_size}

    @app.post("/v1/operations", dependencies=[Depends(require_pairing)])
    def operation(request: CopyMoveRequest) -> dict:
        try:
            result = copy_or_move(state, **request.model_dump())
        except FileAccessError as error:
            raise HTTPException(status_code=409, detail=str(error))
        return {"result": "ok", "path": str(result)}

    @app.delete("/v1/pairings/{client_id}", dependencies=[Depends(require_pairing)])
    def revoke_pairing(client_id: str) -> dict:
        return {"revoked": state.revoke(client_id)}

    return app
