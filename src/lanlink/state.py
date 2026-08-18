from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


def app_data_dir() -> Path:
    """Return a platform-friendly folder for settings, without shared files."""
    base = Path.home() / ".lanlink-hub"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Share:
    id: str
    name: str
    path: str


@dataclass
class PairedDevice:
    id: str
    name: str
    token: str
    paired_at: float


@dataclass
class RemoteDevice:
    id: str
    name: str
    base_url: str
    token: str
    paired_at: float


class HubState:
    """Persistent, thread-safe state owned by one local LanLink node."""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self.settings_path = settings_path or app_data_dir() / "settings.json"
        self.device_id = ""
        self.device_name = ""
        self.shares: dict[str, Share] = {}
        self.paired_devices: dict[str, PairedDevice] = {}
        self.remote_devices: dict[str, RemoteDevice] = {}
        self._pair_code = ""
        self._pair_code_expires = 0.0
        self._load()
        self.rotate_pair_code()

    def _load(self) -> None:
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            saved = {}
        self.device_id = saved.get("device_id", str(uuid.uuid4()))
        self.device_name = saved.get("device_name", "LanLink computer")
        for item in saved.get("shares", []):
            path = Path(item["path"])
            if path.is_dir():
                share = Share(**item)
                self.shares[share.id] = share
        for item in saved.get("paired_devices", []):
            device = PairedDevice(**item)
            self.paired_devices[device.id] = device
        for item in saved.get("remote_devices", []):
            device = RemoteDevice(**item)
            self.remote_devices[device.id] = device
        self._save()

    def _save(self) -> None:
        self.settings_path.write_text(
            json.dumps(
                {
                    "device_id": self.device_id,
                    "device_name": self.device_name,
                    "shares": [asdict(share) for share in self.shares.values()],
                    "paired_devices": [asdict(device) for device in self.paired_devices.values()],
                    "remote_devices": [asdict(device) for device in self.remote_devices.values()],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def rotate_pair_code(self, lifetime_seconds: int = 600) -> tuple[str, float]:
        """Create a short-lived code that the local user must actively reveal."""
        with self._lock:
            self._pair_code = f"{secrets.randbelow(1_000_000):06d}"
            self._pair_code_expires = time.time() + lifetime_seconds
            return self._pair_code, self._pair_code_expires

    def pairing_code(self) -> tuple[str, float]:
        with self._lock:
            if time.time() >= self._pair_code_expires:
                return self.rotate_pair_code()
            return self._pair_code, self._pair_code_expires

    def pair(self, client_id: str, client_name: str, pair_code: str) -> str | None:
        with self._lock:
            valid = (
                time.time() < self._pair_code_expires
                and secrets.compare_digest(self._pair_code, pair_code)
            )
            if not valid:
                return None
            token = secrets.token_urlsafe(32)
            self.paired_devices[client_id] = PairedDevice(
                id=client_id, name=client_name.strip(), token=token, paired_at=time.time()
            )
            self._save()
            return token

    def authenticate(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return any(
                secrets.compare_digest(token, device.token)
                for device in self.paired_devices.values()
            )

    def revoke(self, client_id: str) -> bool:
        with self._lock:
            if client_id not in self.paired_devices:
                return False
            del self.paired_devices[client_id]
            self._save()
            return True

    def paired_devices_snapshot(self) -> list[PairedDevice]:
        with self._lock:
            return list(self.paired_devices.values())

    def upsert_remote_device(self, device_id: str, name: str, base_url: str, token: str) -> RemoteDevice:
        with self._lock:
            device = RemoteDevice(
                id=device_id,
                name=name.strip() or "LanLink device",
                base_url=base_url.rstrip("/"),
                token=token,
                paired_at=time.time(),
            )
            self.remote_devices[device.id] = device
            self._save()
            return device

    def remove_remote_device(self, device_id: str) -> bool:
        with self._lock:
            if device_id not in self.remote_devices:
                return False
            del self.remote_devices[device_id]
            self._save()
            return True

    def remote_devices_snapshot(self) -> list[RemoteDevice]:
        with self._lock:
            return list(self.remote_devices.values())

    def get_remote_device(self, device_id: str) -> RemoteDevice | None:
        with self._lock:
            return self.remote_devices.get(device_id)

    def add_share(self, path: str | Path, display_name: str | None = None) -> Share:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError("A shared location must be an existing folder.")
        with self._lock:
            existing = next((s for s in self.shares.values() if s.path == str(resolved)), None)
            if existing:
                return existing
            share = Share(
                id=f"share_{uuid.uuid4().hex[:12]}",
                name=(display_name or resolved.name or str(resolved)).strip(),
                path=str(resolved),
            )
            self.shares[share.id] = share
            self._save()
            return share

    def remove_share(self, share_id: str) -> bool:
        with self._lock:
            if share_id not in self.shares:
                return False
            del self.shares[share_id]
            self._save()
            return True

    def get_share(self, share_id: str) -> Share | None:
        with self._lock:
            return self.shares.get(share_id)

    def public_device(self) -> dict[str, str]:
        return {"id": self.device_id, "name": self.device_name}
