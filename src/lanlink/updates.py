"""Check whether a newer LanLink has been published, and say so.

LanLink never downloads or installs anything by itself. It compares the running
version against the newest GitHub release and, when there is one, shows what
changed and hands the person the download link to use however they like. There
is no silent update, no elevation prompt, and no browser: opening one is against
the rules this application is built to, and a link the user can copy is enough.

Qt-free on purpose, so it is testable without a window.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from . import __version__

# api.github.com serves this without a token for public repositories, and the
# unauthenticated rate limit is far more than a desktop application will use.
RELEASES_URL = "https://api.github.com/repos/{repository}/releases"
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
DEFAULT_TIMEOUT = 8.0


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
class Release:
    version: Version
    name: str
    notes: str
    page_url: str
    download_url: str
    published_at: str = ""

    @property
    def label(self) -> str:
        return self.name or str(self.version)


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
    assets = payload.get("assets")
    return Release(
        version=version,
        name=str(payload.get("name") or payload.get("tag_name") or ""),
        notes=str(payload.get("body") or "").strip(),
        page_url=str(payload.get("html_url") or ""),
        download_url=_installer_asset(assets if isinstance(assets, list) else []),
        published_at=str(payload.get("published_at") or ""),
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
