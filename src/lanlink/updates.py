"""Updating LanLink from its GitHub releases.

The update channel is **published releases only** — never the repository source
tree, never a branch, never a draft or a pre-release. A branch build has not
been through the release workflow, has no checksum published beside it, and is
not what anybody agreed to install.

Nothing is ever installed silently. The user asks for the check, sees what
changed, asks for the download, watches it arrive, and the installer only runs
once its SHA-256 matches the checksum published in the same release. That last
part is structural rather than a convention: `launch_installer` takes a
`VerifiedInstaller`, and the only way to obtain one is `verify_download`
agreeing with the published digest.

Qt-free on purpose, so all of it is testable without a window.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import __version__

# api.github.com serves this without a token for public repositories, and the
# unauthenticated rate limit is far more than a desktop application will use.
RELEASES_URL = "https://api.github.com/repos/{repository}/releases"

# Where LanLink itself is published. An empty setting means "the place this
# build came from", not "never check" — an update system nobody configures is
# an update system nobody gets.
DEFAULT_REPOSITORY = "shabnurmaniyar07/lanlink"
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
DEFAULT_TIMEOUT = 8.0
DOWNLOAD_TIMEOUT = 60.0
DOWNLOAD_CHUNK = 256 * 1024

# The release workflow publishes this beside the installer. Without it there is
# nothing to check the download against, and LanLink will not run it.
CHECKSUM_ASSET = "SHA256SUMS.txt"

# How stale a successful check may be before automatic checking asks again.
# Once a day, not once a launch: somebody who opens LanLink ten times before
# lunch should not make ten requests to GitHub.
CHECK_INTERVAL = timedelta(days=1)


@dataclass(frozen=True, order=True)
class Version:
    """A comparable version. Anything unparseable sorts below everything."""

    parts: tuple[int, ...] = field(default_factory=tuple)
    suffix: str = field(default="", compare=False)

    def __str__(self) -> str:
        base = ".".join(str(part) for part in self.parts) if self.parts else "0"
        return f"{base}{self.suffix}"

    @property
    def is_known(self) -> bool:
        return bool(self.parts)

    @classmethod
    def parse(cls, text: object) -> Version:
        raw = str(text or "").strip().lstrip("vV")
        match = re.match(r"^(\d+(?:\.\d+)*)(.*)$", raw)
        if not match:
            return cls()
        numbers = tuple(int(part) for part in match.group(1).split("."))
        # Pad to three so 0.2 and 0.2.0 compare equal rather than by length.
        padded = (numbers + (0, 0, 0))[:3] if len(numbers) < 3 else numbers
        return cls(parts=padded, suffix=match.group(2).strip())


class UpdateStatus(StrEnum):
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"


@dataclass(frozen=True)
class Asset:
    """One file attached to a release."""

    name: str
    url: str
    size: int = 0

    @property
    def is_installer(self) -> bool:
        return self.name.lower().endswith(".exe")


@dataclass(frozen=True)
class Release:
    version: Version
    name: str
    notes: str
    page_url: str
    download_url: str
    published_at: str = ""
    assets: tuple[Asset, ...] = ()

    @property
    def label(self) -> str:
        return self.name or str(self.version)

    @property
    def installer(self) -> Asset | None:
        """The Windows installer, preferred over any other executable."""
        candidates = [item for item in self.assets if item.is_installer]
        for item in candidates:
            if "setup" in item.name.lower():
                return item
        return candidates[0] if candidates else None

    @property
    def checksums(self) -> Asset | None:
        for item in self.assets:
            if item.name.lower() == CHECKSUM_ASSET.lower():
                return item
        return None

    @property
    def can_install(self) -> bool:
        """Both halves must be present: an installer, and something to check it."""
        return self.installer is not None and self.checksums is not None


@dataclass(frozen=True)
class UpdateCheck:
    status: UpdateStatus
    message: str
    release: Release | None = None
    current: Version = field(default_factory=Version)

    @property
    def has_update(self) -> bool:
        return self.status is UpdateStatus.UPDATE_AVAILABLE and self.release is not None

    @property
    def link(self) -> str:
        if self.release is None:
            return ""
        return self.release.download_url or self.release.page_url

    @property
    def latest(self) -> Version | None:
        """The newest published version, whether or not it is newer than ours."""
        return self.release.version if self.release is not None else None

    @property
    def can_install(self) -> bool:
        """An update LanLink can install itself: verifiable, and actually newer."""
        return self.has_update and self.release is not None and self.release.can_install


def is_valid_repository(repository: str) -> bool:
    """owner/name, which is all a releases URL needs."""
    return bool(REPOSITORY_PATTERN.match((repository or "").strip()))


def _installer_asset(assets: list[dict[str, Any]]) -> str:
    """Prefer the Windows installer, then any .exe, then the first attachment."""
    named = [
        (str(item.get("name", "")).lower(), str(item.get("browser_download_url", "")))
        for item in assets
        if item.get("browser_download_url")
    ]
    for wanted in ("setup.exe", ".exe", ".zip", ".msi"):
        for name, url in named:
            if name.endswith(wanted):
                return url
    return named[0][1] if named else ""


def release_from(payload: dict[str, Any]) -> Release | None:
    """One GitHub release object, or None when it is not something to offer."""
    if payload.get("draft") or payload.get("prerelease"):
        return None
    version = Version.parse(payload.get("tag_name") or payload.get("name"))
    if not version.is_known:
        return None
    raw_assets = payload.get("assets")
    assets = tuple(
        Asset(
            name=str(item.get("name", "")),
            url=str(item.get("browser_download_url", "")),
            size=int(item.get("size") or 0),
        )
        for item in (raw_assets if isinstance(raw_assets, list) else [])
        if item.get("browser_download_url")
    )
    return Release(
        version=version,
        name=str(payload.get("name") or payload.get("tag_name") or ""),
        notes=str(payload.get("body") or "").strip(),
        page_url=str(payload.get("html_url") or ""),
        download_url=_installer_asset(raw_assets if isinstance(raw_assets, list) else []),
        published_at=str(payload.get("published_at") or ""),
        assets=assets,
    )


def newest(payloads: list[dict[str, Any]]) -> Release | None:
    """The highest released version, ignoring drafts and pre-releases.

    GitHub returns newest-first, but a re-tagged release can break that, so the
    versions are compared rather than trusted in order.
    """
    releases = [release for release in map(release_from, payloads) if release is not None]
    return max(releases, key=lambda item: item.version, default=None)


def _fetch_releases(repository: str, timeout: float) -> list[dict[str, Any]]:
    import httpx

    response = httpx.get(
        RELEASES_URL.format(repository=repository),
        timeout=timeout,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"LanLink/{__version__}"},
        follow_redirects=True,
    )
    if response.status_code == 404:
        raise LookupError("No releases were found for that repository.")
    if response.status_code == 403:
        raise LookupError("GitHub is rate limiting this address. Try again later.")
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def check_for_update(
    repository: str,
    current: str = __version__,
    fetch: Callable[[str, float], list[dict[str, Any]]] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> UpdateCheck:
    """Ask GitHub what the newest release is. Never raises; it reports instead."""
    running = Version.parse(current)
    repository = (repository or "").strip()
    if not is_valid_repository(repository):
        return UpdateCheck(
            UpdateStatus.NOT_CONFIGURED,
            "Set the update repository in Settings, in the form owner/name.",
            current=running,
        )

    try:
        payloads = (fetch or _fetch_releases)(repository, timeout)
    except LookupError as error:
        return UpdateCheck(UpdateStatus.FAILED, str(error), current=running)
    except Exception as error:  # noqa: BLE001 - offline is the normal case here
        return UpdateCheck(
            UpdateStatus.FAILED,
            f"Could not reach GitHub: {error.__class__.__name__}. LanLink works offline; "
            "this check does not.",
            current=running,
        )

    release = newest(payloads)
    if release is None:
        return UpdateCheck(
            UpdateStatus.FAILED, "That repository has no published releases yet.", current=running
        )
    if release.version <= running:
        return UpdateCheck(
            UpdateStatus.UP_TO_DATE,
            f"LanLink {current} is the newest version.",
            release=release,
            current=running,
        )
    return UpdateCheck(
        UpdateStatus.UPDATE_AVAILABLE,
        f"LanLink {release.version} is available. You are running {current}.",
        release=release,
        current=running,
    )


# --------------------------------------------------------------- downloading


class UpdateCancelled(RuntimeError):
    """The user stopped the download. Not an error to apologise for."""


class ChecksumMismatch(RuntimeError):
    """The bytes that arrived are not the bytes that were published."""


@dataclass(frozen=True)
class DownloadProgress:
    received: int
    total: int | None

    @property
    def fraction(self) -> float | None:
        if not self.total:
            return None
        return min(1.0, self.received / self.total)


def parse_sha256sums(text: str) -> dict[str, str]:
    """The usual `sha256sum` output: digest, spaces, optional '*', filename.

    Only well-formed lines count. A truncated or reformatted file must not
    quietly produce an empty mapping that then matches nothing — the caller
    checks for the name it wants and refuses when it is absent.
    """
    digests: dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip().lower(), parts[1].strip().lstrip("*").strip()
        if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest) and name:
            # Published names are bare, but tolerate a path prefix.
            digests[name.replace("\\", "/").rsplit("/", 1)[-1]] = digest
    return digests


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _stream(url: str, timeout: float) -> Iterator[tuple[bytes, int | None]]:
    import httpx

    with httpx.stream(
        "GET",
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": f"LanLink/{__version__}"},
    ) as response:
        response.raise_for_status()
        length = response.headers.get("content-length")
        total = int(length) if length and length.isdigit() else None
        for chunk in response.iter_bytes(DOWNLOAD_CHUNK):
            yield chunk, total


def fetch_text(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    import httpx

    response = httpx.get(
        url, timeout=timeout, follow_redirects=True, headers={"User-Agent": f"LanLink/{__version__}"}
    )
    response.raise_for_status()
    return response.text


def download(
    url: str,
    destination: Path,
    on_progress: Callable[[DownloadProgress], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    stream: Callable[[str, float], Iterator[tuple[bytes, int | None]]] | None = None,
    timeout: float = DOWNLOAD_TIMEOUT,
) -> Path:
    """Stream a release asset to disk, reporting progress and honouring cancel.

    The bytes land in a `.part` file and are only renamed once the transfer
    finishes, so a cancelled or failed download can never be mistaken for a
    complete installer — the same rule the file transfer code follows.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)

    received = 0
    try:
        with partial.open("wb") as out:
            for chunk, total in (stream or _stream)(url, timeout):
                if is_cancelled is not None and is_cancelled():
                    raise UpdateCancelled("The download was cancelled.")
                out.write(chunk)
                received += len(chunk)
                if on_progress is not None:
                    on_progress(DownloadProgress(received, total))
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    destination.unlink(missing_ok=True)
    partial.replace(destination)
    return destination


# ------------------------------------------------------------- verification


@dataclass(frozen=True)
class VerifiedInstaller:
    """An installer whose SHA-256 matched the digest published with the release.

    The only way to make one is `verify_download`. `launch_installer` accepts
    nothing else, so an unverified file cannot be run even by mistake.
    """

    path: Path
    version: Version
    sha256: str


def verify_download(path: Path, name: str, published: dict[str, str], version: Version) -> VerifiedInstaller:
    """Compare the file against the digest published in the same release."""
    expected = published.get(name)
    if not expected:
        raise ChecksumMismatch(
            f"The release does not publish a SHA-256 for {name}, so LanLink will not run it."
        )
    actual = sha256_of(path)
    if actual.lower() != expected.lower():
        Path(path).unlink(missing_ok=True)
        raise ChecksumMismatch(
            "The downloaded installer does not match the checksum published with the release. "
            "It was deleted rather than run. Try again, and if it keeps happening do not "
            "install this file."
        )
    return VerifiedInstaller(path=Path(path), version=version, sha256=actual.lower())


def prepare_update(
    release: Release,
    folder: Path,
    on_progress: Callable[[DownloadProgress], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    stream: Callable[[str, float], Iterator[tuple[bytes, int | None]]] | None = None,
    read_text: Callable[[str], str] | None = None,
) -> VerifiedInstaller:
    """Fetch the checksums, download the installer, verify it, and stop there.

    Nothing is executed here. The caller decides whether to run what comes back.
    """
    installer = release.installer
    checksums = release.checksums
    if installer is None:
        raise LookupError("That release has no Windows installer attached.")
    if checksums is None:
        raise LookupError(
            f"That release does not publish {CHECKSUM_ASSET}, so the download cannot be verified. "
            "LanLink will not install an unverified file."
        )

    published = parse_sha256sums((read_text or fetch_text)(checksums.url))
    target = Path(folder) / installer.name
    download(
        installer.url,
        target,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
        stream=stream,
    )
    return verify_download(target, installer.name, published, release.version)


# ----------------------------------------------------------------- handover


def launch_installer(installer: VerifiedInstaller) -> None:
    """Start the verified installer and leave it to the user.

    Deliberately not silent: the installer shows its own window, asks for
    elevation itself, and offers to restart LanLink when it finishes. LanLink's
    settings, device identity, certificate, pairings, shares and history live
    outside the installation directory and are not touched by an upgrade.
    """
    if not isinstance(installer, VerifiedInstaller):  # pragma: no cover - defensive
        raise TypeError("Only a verified installer may be launched.")
    path = Path(installer.path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} is no longer there.")
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - a checksum-verified local file
    else:
        # Only reachable in tests and on a developer's machine; a Linux build has
        # no installer to run.
        subprocess.Popen([str(path)])  # noqa: S603


def updates_folder(root: Path) -> Path:
    folder = Path(root) / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --------------------------------------------------------- automatic checking


def is_check_due(
    last_check: str | None,
    now: datetime | None = None,
    interval: timedelta = CHECK_INTERVAL,
) -> bool:
    """Once a day, not once a launch.

    An unreadable or missing timestamp means due: better one extra request than
    a client that never checks again because it wrote a bad value once.
    """
    if not last_check:
        return True
    try:
        stamp = datetime.fromisoformat(str(last_check))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - stamp >= interval


def timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def is_skipped(version: Version, skipped: str) -> bool:
    """A version the user chose to skip stays skipped until a newer one appears."""
    marker = Version.parse(skipped)
    return marker.is_known and version <= marker
