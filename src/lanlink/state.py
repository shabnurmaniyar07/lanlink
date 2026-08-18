from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import secrets
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__

SETTINGS_MODE = 0o600
PAIR_CODE_DIGITS = 8
PAIR_WINDOW_SECONDS = 120
PAIR_MAX_FAILURES = 5
PAIR_MIN_INTERVAL_SECONDS = 1.0
PAIR_BURST = 5


class SettingsCorruptError(RuntimeError):
    """Raised when settings cannot be read and no usable backup exists."""


def app_data_dir() -> Path:
    """Return a platform-friendly folder for settings, without shared files."""
    base = Path.home() / ".lanlink-hub"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _restrict(path: Path) -> None:
    """Best-effort owner-only permissions. Windows relies on the per-user profile ACL."""
    with contextlib.suppress(OSError):
        os.chmod(path, SETTINGS_MODE)


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file + os.replace so a crash can never truncate the live file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    _restrict(temp)
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        try:
            backup.unlink(missing_ok=True)
            os.replace(path, backup)
            _restrict(backup)
        except OSError:
            pass
    os.replace(temp, path)
    _restrict(path)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    """compare_digest on bytes; the str overload raises TypeError for non-ASCII input."""
    return secrets.compare_digest(left.encode("utf-8", "surrogatepass"), right.encode("utf-8"))


# Per-share permission flags. Delete is opt-in because it is the irreversible one.
PERM_READ = "r"
PERM_WRITE = "w"
PERM_DELETE = "d"
DEFAULT_PERMISSIONS = "rw"
ALL_PERMISSIONS = "rwd"


@dataclass
class Share:
    id: str
    name: str
    path: str
    permissions: str = DEFAULT_PERMISSIONS

    @property
    def available(self) -> bool:
        try:
            return Path(self.path).is_dir()
        except OSError:
            return False

    def allows(self, flag: str) -> bool:
        return flag in self.permissions


@dataclass
class PairedDevice:
    id: str
    name: str
    token_hash: str
    paired_at: float
    last_seen: float = 0.0


@dataclass
class RemoteDevice:
    id: str
    name: str
    base_url: str
    token: str
    paired_at: float


@dataclass
class PairResult:
    ok: bool
    reason: str = ""
    token: str | None = None


@dataclass
class _RateBucket:
    tokens: float = float(PAIR_BURST)
    updated: float = field(default_factory=time.monotonic)


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
        self.max_upload_bytes = 0  # 0 == unlimited
        self.bind_all_interfaces = False
        self.recovered_from_backup = False
        # Optional hook so a UI can require an explicit per-request approval.
        self.approval_callback: Callable[[str, str], bool] | None = None
        self._pairing_armed = False
        self._pair_code = ""
        self._pair_code_expires = 0.0
        self._pair_failures = 0
        self._pair_buckets: dict[str, _RateBucket] = {}
        self._load()

    # ---------------------------------------------------------------- loading

    def _read_settings(self) -> dict:
        """Read settings, falling back to the backup. Never silently resets identity."""
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
            backup = self.settings_path.with_name(self.settings_path.name + ".bak")
            try:
                recovered = json.loads(backup.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
                quarantine = self.settings_path.with_name(
                    f"{self.settings_path.name}.corrupt-{int(time.time())}"
                )
                try:
                    os.replace(self.settings_path, quarantine)
                except OSError:
                    quarantine = self.settings_path
                raise SettingsCorruptError(
                    "LanLink settings are unreadable and no usable backup exists. "
                    f"The damaged file was kept at {quarantine}. "
                    "Delete it to start over with a new device identity."
                ) from error
            self.recovered_from_backup = True
            return recovered

    def _load(self) -> None:
        saved = self._read_settings()
        had_identity = bool(saved.get("device_id"))
        self.device_id = saved.get("device_id") or str(uuid.uuid4())
        self.device_name = saved.get("device_name") or socket.gethostname() or "LanLink computer"
        self.max_upload_bytes = int(saved.get("max_upload_bytes", 0))
        self.bind_all_interfaces = bool(saved.get("bind_all_interfaces", False))

        for item in saved.get("shares", []):
            try:
                share = Share(
                    id=item["id"],
                    name=item["name"],
                    path=item["path"],
                    permissions=item.get("permissions", DEFAULT_PERMISSIONS),
                )
            except (KeyError, TypeError):
                continue
            # Unavailable drives stay configured; shares are never pruned on load.
            self.shares[share.id] = share

        for item in saved.get("paired_devices", []):
            try:
                token_hash = item.get("token_hash") or hash_token(item["token"])
                device = PairedDevice(
                    id=item["id"],
                    name=item["name"],
                    token_hash=token_hash,
                    paired_at=float(item["paired_at"]),
                    last_seen=float(item.get("last_seen", 0.0)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self.paired_devices[device.id] = device

        for item in saved.get("remote_devices", []):
            try:
                remote = RemoteDevice(
                    id=item["id"],
                    name=item["name"],
                    base_url=item["base_url"],
                    token=item["token"],
                    paired_at=float(item["paired_at"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self.remote_devices[remote.id] = remote

        if not had_identity or self.recovered_from_backup:
            self._save()

    def _save(self) -> None:
        payload = {
            "version": __version__,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "max_upload_bytes": self.max_upload_bytes,
            "bind_all_interfaces": self.bind_all_interfaces,
            "shares": [asdict(share) for share in self.shares.values()],
            "paired_devices": [asdict(device) for device in self.paired_devices.values()],
            "remote_devices": [asdict(device) for device in self.remote_devices.values()],
        }
        atomic_write_text(self.settings_path, json.dumps(payload, indent=2))

    # ---------------------------------------------------------------- pairing

    @property
    def pairing_armed(self) -> bool:
        with self._lock:
            return self._pairing_armed and time.time() < self._pair_code_expires

    def start_pairing(self, lifetime_seconds: int = PAIR_WINDOW_SECONDS) -> tuple[str, float]:
        """Arm pairing. Only the local owner calls this; no code exists otherwise."""
        with self._lock:
            digits = 10**PAIR_CODE_DIGITS
            self._pair_code = f"{secrets.randbelow(digits):0{PAIR_CODE_DIGITS}d}"
            self._pair_code_expires = time.time() + lifetime_seconds
            self._pairing_armed = True
            self._pair_failures = 0
            self._pair_buckets.clear()
            return self._pair_code, self._pair_code_expires

    def cancel_pairing(self) -> None:
        with self._lock:
            self._pairing_armed = False
            self._pair_code = ""
            self._pair_code_expires = 0.0

    def pairing_code(self) -> tuple[str, float] | None:
        """Current code, or None when pairing mode is off or expired. Never auto-arms."""
        with self._lock:
            if not self._pairing_armed:
                return None
            if time.time() >= self._pair_code_expires:
                self._pairing_armed = False
                self._pair_code = ""
                return None
            return self._pair_code, self._pair_code_expires

    def _allow_attempt(self, source: str) -> bool:
        """Token bucket: PAIR_BURST attempts, refilled at 1 per PAIR_MIN_INTERVAL_SECONDS."""
        now = time.monotonic()
        bucket = self._pair_buckets.setdefault(source, _RateBucket())
        elapsed = now - bucket.updated
        bucket.tokens = min(float(PAIR_BURST), bucket.tokens + elapsed / PAIR_MIN_INTERVAL_SECONDS)
        bucket.updated = now
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def pair(self, client_id: str, client_name: str, pair_code: str, source: str = "local") -> PairResult:
        with self._lock:
            if not self._pairing_armed or time.time() >= self._pair_code_expires:
                self._pairing_armed = False
                return PairResult(False, "not_armed")
            if not self._allow_attempt(source):
                return PairResult(False, "rate_limited")
            if not constant_time_equal(pair_code, self._pair_code):
                self._pair_failures += 1
                if self._pair_failures >= PAIR_MAX_FAILURES:
                    self._pairing_armed = False
                    self._pair_code = ""
                    self._pair_code_expires = 0.0
                    return PairResult(False, "locked_out")
                return PairResult(False, "invalid_code")
            approve = self.approval_callback
        # Approval runs outside the lock: a UI prompt may block for a long time.
        if approve is not None and not approve(client_id, client_name.strip()):
            with self._lock:
                self._pairing_armed = False
                self._pair_code = ""
                self._pair_code_expires = 0.0
            return PairResult(False, "declined")
        with self._lock:
            token = secrets.token_urlsafe(32)
            self.paired_devices[client_id] = PairedDevice(
                id=client_id,
                name=client_name.strip(),
                token_hash=hash_token(token),
                paired_at=time.time(),
                last_seen=time.time(),
            )
            # Successful pairing consumes the code and disables pairing mode.
            self._pairing_armed = False
            self._pair_code = ""
            self._pair_code_expires = 0.0
            self._save()
            return PairResult(True, "paired", token)

    def identify(self, token: str | None) -> PairedDevice | None:
        """Return the paired device owning this token, or None."""
        if not token or len(token) > 512:
            return None
        digest = hash_token(token)
        with self._lock:
            for device in self.paired_devices.values():
                if constant_time_equal(digest, device.token_hash):
                    device.last_seen = time.time()
                    return device
        return None

    def authenticate(self, token: str | None) -> bool:
        return self.identify(token) is not None

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

    # --------------------------------------------------------- remote devices

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

    # ----------------------------------------------------------------- shares

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

    def set_share_permissions(self, share_id: str, permissions: str) -> Share | None:
        """Normalise to the known flags so a typo can never widen access."""
        cleaned = "".join(flag for flag in ALL_PERMISSIONS if flag in permissions.lower())
        if PERM_READ not in cleaned:
            cleaned = PERM_READ + cleaned
        with self._lock:
            share = self.shares.get(share_id)
            if not share:
                return None
            share.permissions = cleaned
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

    def set_device_name(self, name: str) -> None:
        with self._lock:
            self.device_name = name.strip() or self.device_name
            self._save()

    def public_device(self) -> dict[str, str]:
        return {
            "id": self.device_id,
            "name": self.device_name,
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "version": __version__,
        }
