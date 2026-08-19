"""Local copies of remote files, so Windows can treat them as ordinary files.

Windows applications expect a filesystem path when you drop something on them.
They will not fetch an HTTPS URL, and teaching every CAD package about LanLink
is not an option. So before a drag completes, the file is staged: downloaded to
a local cache, checksum-verified, and only then handed over as a normal path.

Nothing here knows about Qt or widgets — the UI drives it, the tests drive it
directly.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .files import PART_SUFFIX, sha256_of, validate_filename
from .state import DATA_DIR_ENV

DEFAULT_RETENTION_HOURS = 24.0


def default_cache_root() -> Path:
    """%LOCALAPPDATA%\\LanLink on Windows, an XDG-ish cache elsewhere.

    LANLINK_DATA_DIR moves it, the same override the settings folder uses, so a
    second instance on one machine keeps its own cache.
    """
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser() / "cache"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "LanLink"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "lanlink"


class StagingError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteFile:
    """Enough to identify one remote file, and to know when it has changed."""

    device_id: str
    share_id: str
    path: str
    name: str
    size: int | None = None
    modified_at: float | None = None
    device_name: str = ""

    @property
    def identity(self) -> str:
        """Stable per (device, share, path, size, mtime).

        Size and mtime are in the key on purpose: when the remote file changes,
        the key changes, so a stale copy is never handed to an application.
        """
        raw = "\x00".join(
            [
                self.device_id,
                self.share_id,
                self.path,
                str(self.size if self.size is not None else ""),
                f"{self.modified_at:.3f}" if self.modified_at is not None else "",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @property
    def safe_name(self) -> str:
        """The name to use on disk — validated, never taken on trust.

        Remote input must never steer a local path, so this goes through the
        same validator the share sandbox uses. A name that cannot be made safe
        falls back to the identity hash while keeping its extension.
        """
        try:
            return validate_filename(self.name)
        except ValueError:
            suffix = Path(self.name or "").suffix
            cleaned = "".join(char for char in suffix if char.isalnum() or char == ".")[:16]
            return f"{self.identity}{cleaned}"


@dataclass
class StagedFile:
    remote: RemoteFile
    path: Path
    staged_at: float
    bytes: int

    @property
    def url(self) -> str:
        return self.path.as_uri()


class RemoteFileStager:
    """Downloads remote files into a local cache and hands back Windows paths."""

    def __init__(
        self,
        root: Path | None = None,
        retention_hours: float = DEFAULT_RETENTION_HOURS,
    ) -> None:
        self.root = Path(root) if root else default_cache_root() / "staging"
        self.retention_hours = retention_hours
        self._lock = threading.RLock()
        self._active: set[Path] = set()
        self.root.mkdir(parents=True, exist_ok=True)
        self._restrict(self.root)

    @staticmethod
    def _restrict(path: Path) -> None:
        """Staged files are the user's data; keep them out of other accounts."""
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)

    # ------------------------------------------------------------- addressing

    def folder_for(self, remote: RemoteFile) -> Path:
        """One directory per identity, so two files named the same never clash."""
        return self.root / remote.identity

    def staged_path(self, remote: RemoteFile) -> Path:
        return self.folder_for(remote) / remote.safe_name

    def is_cached(self, remote: RemoteFile) -> bool:
        target = self.staged_path(remote)
        if not target.is_file():
            return False
        # A size mismatch means truncated or replaced: treat it as a miss.
        return remote.size is None or target.stat().st_size == remote.size

    def get_local_path(self, remote: RemoteFile) -> Path | None:
        """The finished local file, or None. Never returns a partial download."""
        return self.staged_path(remote) if self.is_cached(remote) else None

    # --------------------------------------------------------------- staging

    def stage_file(
        self,
        remote: RemoteFile,
        fetch: Callable[[Path], None],
        sha256: str | None = None,
        force: bool = False,
    ) -> Path:
        """Make ``remote`` available locally and return its path.

        ``fetch`` streams the bytes into the partial file it is given; it never
        sees the final path, so an interrupted download cannot be mistaken for a
        finished file.
        """
        target = self.staged_path(remote)
        if not force and self.is_cached(remote):
            os.utime(target, None)  # keep recently-used files out of the sweep
            return target

        folder = self.folder_for(remote)
        folder.mkdir(parents=True, exist_ok=True)
        self._restrict(folder)
        partial = target.with_name(target.name + PART_SUFFIX)

        with self._lock:
            # Both paths count as in use: a cleanup running during a drag must
            # not delete the partial we are still writing.
            self._active.add(target)
            self._active.add(partial)
        try:
            partial.unlink(missing_ok=True)
            fetch(partial)
            if not partial.exists():
                raise StagingError(f"{remote.name} did not download.")
            if sha256:
                actual = sha256_of(partial)
                if actual.lower() != sha256.lower():
                    raise StagingError(
                        f"{remote.name} failed its SHA-256 check and was discarded."
                    )
            if remote.size is not None and partial.stat().st_size != remote.size:
                raise StagingError(f"{remote.name} arrived with an unexpected size.")
            self._restrict(partial)
            os.replace(partial, target)  # atomic: the name appears complete or not at all
            return target
        except BaseException:
            partial.unlink(missing_ok=True)
            self._prune_folder(folder)
            raise
        finally:
            with self._lock:
                self._active.discard(target)
                self._active.discard(partial)

    def stage_files(
        self,
        remotes: list[RemoteFile],
        fetch: Callable[[RemoteFile, Path], None],
        checksums: dict[str, str] | None = None,
    ) -> list[Path]:
        checksums = checksums or {}
        staged: list[Path] = []
        for remote in remotes:

            def one(partial: Path, item: RemoteFile = remote) -> None:
                fetch(item, partial)

            staged.append(self.stage_file(remote, one, sha256=checksums.get(remote.path)))
        return staged

    def stage_folder(
        self,
        remote: RemoteFile,
        files: list[RemoteFile],
        fetch: Callable[[RemoteFile, Path], None],
        relative: Callable[[RemoteFile], str] | None = None,
    ) -> Path:
        """Mirror a remote folder into one staged directory and return its root."""
        root = self.folder_for(remote) / remote.safe_name
        root.mkdir(parents=True, exist_ok=True)
        self._restrict(root)
        with self._lock:
            self._active.add(root)
        try:
            for item in files:
                inner = relative(item) if relative else item.name
                destination = root / inner
                # Remote input must never escape the staged folder.
                resolved = destination.resolve()
                if not str(resolved).startswith(str(root.resolve())):
                    raise StagingError(f"{inner} would leave the staging folder.")
                resolved.parent.mkdir(parents=True, exist_ok=True)
                partial = resolved.with_name(resolved.name + PART_SUFFIX)
                fetch(item, partial)
                os.replace(partial, resolved)
            return root
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        finally:
            with self._lock:
                self._active.discard(root)

    # -------------------------------------------------------------- cleanup

    def _prune_folder(self, folder: Path) -> None:
        try:
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass

    def active_paths(self) -> set[Path]:
        with self._lock:
            return set(self._active)

    def size_bytes(self) -> int:
        return sum(item.stat().st_size for item in self.root.rglob("*") if item.is_file())

    def usage(self) -> tuple[int, int]:
        """(finished file count, total bytes) — what the Settings page reports."""
        count = 0
        total = 0
        for item in self.root.rglob("*"):
            if not item.is_file():
                continue
            total += item.stat().st_size
            if not item.name.endswith(PART_SUFFIX):
                count += 1
        return count, total

    def entries(self) -> list[StagedFile]:
        found: list[StagedFile] = []
        for item in self.root.rglob("*"):
            if item.is_file() and not item.name.endswith(PART_SUFFIX):
                stat = item.stat()
                found.append(
                    StagedFile(
                        remote=RemoteFile("", "", "", item.name),
                        path=item,
                        staged_at=stat.st_mtime,
                        bytes=stat.st_size,
                    )
                )
        return found

    def cleanup(self, older_than_hours: float | None = None) -> int:
        """Remove abandoned partials and files past their retention.

        A file in use right now is never removed — the application that was
        handed it may still be reading.
        """
        cutoff = time.time() - (
            (self.retention_hours if older_than_hours is None else older_than_hours) * 3600
        )
        active = self.active_paths()
        removed = 0

        for item in list(self.root.rglob("*")):
            if not item.is_file():
                continue
            if item in active or any(str(item).startswith(str(path)) for path in active):
                continue  # a drag is relying on this right now
            if item.name.endswith(PART_SUFFIX):
                # An abandoned partial helps nobody; it is not a usable file.
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    pass
                continue
            try:
                if item.stat().st_mtime < cutoff:
                    item.unlink()
                    removed += 1
            except OSError:
                pass

        for folder in sorted(self.root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if folder.is_dir():
                self._prune_folder(folder)
        return removed

    def clear(self) -> int:
        """Empty the cache, except anything a drag is relying on right now."""
        active = self.active_paths()
        removed = 0
        for item in list(self.root.iterdir()):
            if any(str(path).startswith(str(item)) for path in active):
                continue
            try:
                if item.is_dir():
                    removed += sum(1 for child in item.rglob("*") if child.is_file())
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink()
                    removed += 1
            except OSError:
                pass
        return removed
