from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx


class LanLinkClient:
    """Small reusable client for a desktop UI, Android app, or CLI peer."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.http = httpx.Client(timeout=httpx.Timeout(30, read=300))

    @property
    def headers(self) -> dict[str, str]:
        return {"X-LanLink-Token": self.token} if self.token else {}

    def close(self) -> None:
        self.http.close()

    def pair(self, device_name: str, pair_code: str, client_id: str | None = None) -> dict:
        response = self.http.post(
            f"{self.base_url}/v1/pair",
            json={
                "client_id": client_id or str(uuid.uuid4()),
                "client_name": device_name,
                "pair_code": pair_code,
            },
        )
        response.raise_for_status()
        data = response.json()
        self.token = data["token"]
        return data

    def shares(self) -> list[dict]:
        response = self.http.get(f"{self.base_url}/v1/shares", headers=self.headers)
        response.raise_for_status()
        return response.json()["shares"]

    def list_folder(self, share_id: str, path: str = "") -> list[dict]:
        response = self.http.get(
            f"{self.base_url}/v1/shares/{share_id}/list",
            params={"path": path},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()["entries"]

    def device_info(self) -> dict:
        response = self.http.get(f"{self.base_url}/v1/device", timeout=5)
        response.raise_for_status()
        return response.json()

    def unpair(self, client_id: str) -> bool:
        """Remove this client's own pairing. A device cannot revoke any other."""
        response = self.http.delete(f"{self.base_url}/v1/pairings/{client_id}", headers=self.headers)
        response.raise_for_status()
        return bool(response.json().get("revoked"))

    def download(self, share_id: str, path: str, destination: Path) -> Path:
        """Stream to disk; a failed transfer never leaves a partial file behind."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with self.http.stream(
                "GET",
                f"{self.base_url}/v1/files/{share_id}",
                params={"path": path},
                headers=self.headers,
            ) as response:
                response.raise_for_status()
                with destination.open("xb") as output:
                    created = True
                    for chunk in response.iter_bytes():
                        output.write(chunk)
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
        return destination

    @contextmanager
    def open_stream(self, share_id: str, path: str) -> Iterator[httpx.Response]:
        """Yield an open streaming response for a remote file, without buffering it."""
        with self.http.stream(
            "GET",
            f"{self.base_url}/v1/files/{share_id}",
            params={"path": path},
            headers=self.headers,
        ) as response:
            response.raise_for_status()
            yield response

    def put_stream(
        self, share_id: str, destination_folder: str, name: str, chunks: Iterable[bytes]
    ) -> dict:
        """Upload from an iterator so a relay never lands the file on the hub's disk."""
        response = self.http.put(
            f"{self.base_url}/v1/files/{share_id}",
            params={"path": destination_folder, "name": name},
            headers={**self.headers, "Content-Type": "application/octet-stream"},
            content=chunks,
        )
        response.raise_for_status()
        return response.json()

    def create_folder(self, share_id: str, path: str, name: str) -> dict:
        response = self.http.post(
            f"{self.base_url}/v1/shares/{share_id}/folders",
            json={"path": path, "name": name},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def rename(self, share_id: str, path: str, new_name: str) -> dict:
        response = self.http.post(
            f"{self.base_url}/v1/shares/{share_id}/rename",
            json={"path": path, "new_name": new_name},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def delete(self, share_id: str, path: str, recursive: bool = False) -> dict:
        response = self.http.request(
            "DELETE",
            f"{self.base_url}/v1/shares/{share_id}/entries",
            params={"path": path, "recursive": recursive},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def properties(self, share_id: str, path: str = "") -> dict:
        response = self.http.get(
            f"{self.base_url}/v1/shares/{share_id}/properties",
            params={"path": path},
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def operation(
        self,
        source_share_id: str,
        source_path: str,
        destination_share_id: str,
        destination_path: str = "",
        operation: str = "copy",
    ) -> dict:
        response = self.http.post(
            f"{self.base_url}/v1/operations",
            json={
                "source_share_id": source_share_id,
                "source_path": source_path,
                "destination_share_id": destination_share_id,
                "destination_path": destination_path,
                "operation": operation,
            },
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    def upload(self, share_id: str, destination_folder: str, source: Path) -> dict:
        with source.open("rb") as content:
            response = self.http.post(
                f"{self.base_url}/v1/uploads/{share_id}",
                params={"path": destination_folder},
                headers=self.headers,
                files={"file": (source.name, content)},
            )
        response.raise_for_status()
        return response.json()
