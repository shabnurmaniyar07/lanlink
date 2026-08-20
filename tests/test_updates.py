"""The update check: comparing versions, and never doing anything drastic."""

from __future__ import annotations

import pytest

from lanlink.updates import (
    Release,
    UpdateStatus,
    Version,
    check_for_update,
    is_valid_repository,
    newest,
    release_from,
)


def release(tag: str, **overrides) -> dict:
    payload = {
        "tag_name": tag,
        "name": f"LanLink {tag}",
        "body": "Fixed the thing.",
        "html_url": f"https://github.com/owner/lanlink/releases/tag/{tag}",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-19T10:00:00Z",
        "assets": [
            {
                "name": f"LanLinkSetup-{tag.lstrip('v')}.exe",
                "browser_download_url": f"https://github.com/owner/lanlink/releases/download/{tag}/setup.exe",
            }
        ],
    }
    payload.update(overrides)
    return payload


def feed(*payloads: dict):
    def fetch(_repository: str, _timeout: float) -> list[dict]:
        return list(payloads)

    return fetch


# ------------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.1.0", (0, 1, 0)),
        ("v0.1.0", (0, 1, 0)),
        ("V2.5.9", (2, 5, 9)),
        ("1.2", (1, 2, 0)),
        ("3", (3, 0, 0)),
        ("0.1.0-beta1", (0, 1, 0)),
        ("1.2.3.4", (1, 2, 3, 4)),
    ],
)
def test_versions_are_read_the_way_people_write_them(text: str, expected: tuple[int, ...]) -> None:
    assert Version.parse(text).parts == expected


@pytest.mark.parametrize("text", ["", "   ", None, "latest", "nightly", "v"])
def test_an_unreadable_version_is_not_treated_as_a_number(text) -> None:
    parsed = Version.parse(text)
    assert not parsed.is_known
    assert parsed < Version.parse("0.0.1"), "an unknown version must never look newer"


def test_versions_compare_by_number_not_by_text() -> None:
    assert Version.parse("0.10.0") > Version.parse("0.9.0"), "0.10 is newer than 0.9"
    assert Version.parse("1.0.0") > Version.parse("0.99.99")
    assert Version.parse("0.1") == Version.parse("0.1.0"), "0.1 and 0.1.0 are the same release"


def test_a_suffix_does_not_change_the_ordering() -> None:
    assert Version.parse("1.2.3-rc1") == Version.parse("1.2.3")
    assert str(Version.parse("1.2.3-rc1")) == "1.2.3-rc1"


# ------------------------------------------------------------------- releases


def test_a_release_carries_the_installer_link() -> None:
    parsed = release_from(release("v0.2.0"))
    assert parsed is not None
    assert parsed.version == Version.parse("0.2.0")
    assert parsed.download_url.endswith("setup.exe")
    assert parsed.notes == "Fixed the thing."


def test_a_draft_or_prerelease_is_never_offered() -> None:
    assert release_from(release("v9.0.0", draft=True)) is None
    assert release_from(release("v9.0.0", prerelease=True)) is None


def test_a_release_with_no_readable_tag_is_ignored() -> None:
    assert release_from(release("nightly", name="nightly")) is None


def test_the_installer_is_preferred_over_other_attachments() -> None:
    payload = release(
        "v0.2.0",
        assets=[
            {"name": "source.zip", "browser_download_url": "https://example.invalid/source.zip"},
            {"name": "LanLinkSetup-0.2.0.exe", "browser_download_url": "https://example.invalid/setup.exe"},
        ],
    )
    parsed = release_from(payload)
    assert parsed is not None
    assert parsed.download_url.endswith("setup.exe")


def test_a_release_without_attachments_falls_back_to_its_page() -> None:
    parsed = release_from(release("v0.2.0", assets=[]))
    assert parsed is not None
    assert parsed.download_url == ""
    assert parsed.page_url.endswith("v0.2.0")


def test_the_newest_release_wins_even_when_the_order_is_wrong() -> None:
    """GitHub sorts by date, and a re-tag can put an older version first."""
    best = newest([release("v0.9.0"), release("v0.10.0"), release("v0.2.0")])
    assert best is not None
    assert best.version == Version.parse("0.10.0")


def test_no_usable_release_is_reported_rather_than_guessed() -> None:
    assert newest([]) is None
    assert newest([release("v1.0.0", draft=True)]) is None


# --------------------------------------------------------------- the check


def test_a_newer_release_is_offered() -> None:
    result = check_for_update("owner/lanlink", "0.1.0", fetch=feed(release("v0.2.0")))
    assert result.status is UpdateStatus.UPDATE_AVAILABLE
    assert result.has_update
    assert "0.2.0" in result.message and "0.1.0" in result.message
    assert result.link.endswith("setup.exe")


def test_the_same_version_is_not_offered() -> None:
    result = check_for_update("owner/lanlink", "0.2.0", fetch=feed(release("v0.2.0")))
    assert result.status is UpdateStatus.UP_TO_DATE
    assert not result.has_update


def test_an_older_release_is_not_offered_as_an_update() -> None:
    """A yanked release must never talk somebody into downgrading."""
    result = check_for_update("owner/lanlink", "0.3.0", fetch=feed(release("v0.2.0")))
    assert result.status is UpdateStatus.UP_TO_DATE


@pytest.mark.parametrize(
    "repository", ["", "   ", "lanlink", "owner/", "/name", "owner/name/extra", "owner name", "http://x/y"]
)
def test_a_repository_that_is_not_owner_slash_name_asks_to_be_configured(repository: str) -> None:
    assert not is_valid_repository(repository)
    result = check_for_update(repository, "0.1.0", fetch=feed(release("v9.9.9")))
    assert result.status is UpdateStatus.NOT_CONFIGURED
    assert "Settings" in result.message


@pytest.mark.parametrize("repository", ["owner/name", "Owner/Lan-Link", "a/b", "user.name/repo_1"])
def test_a_sensible_repository_is_accepted(repository: str) -> None:
    assert is_valid_repository(repository)


def test_being_offline_is_reported_calmly_and_never_raises() -> None:
    def offline(_repository: str, _timeout: float):
        raise OSError("Network is unreachable")

    result = check_for_update("owner/lanlink", "0.1.0", fetch=offline)
    assert result.status is UpdateStatus.FAILED
    assert "works offline" in result.message
    assert not result.has_update


def test_a_rate_limit_says_so_plainly() -> None:
    def limited(_repository: str, _timeout: float):
        raise LookupError("GitHub is rate limiting this address. Try again later.")

    result = check_for_update("owner/lanlink", "0.1.0", fetch=limited)
    assert result.status is UpdateStatus.FAILED
    assert "rate limiting" in result.message


def test_a_repository_with_no_releases_is_not_an_error_state_worth_alarming_about() -> None:
    result = check_for_update("owner/lanlink", "0.1.0", fetch=feed())
    assert result.status is UpdateStatus.FAILED
    assert "no published releases" in result.message


def test_checking_for_an_update_downloads_nothing() -> None:
    """The check reports. Downloading is a separate, explicit step."""
    import inspect

    from lanlink import updates

    source = inspect.getsource(updates.check_for_update)
    for term in ("download", "startfile", "subprocess", "prepare_update"):
        assert term not in source, f"check_for_update must not {term}"


def test_the_module_never_opens_a_browser() -> None:
    import inspect

    from lanlink import updates

    source = inspect.getsource(updates)
    for term in ("webbrowser", "QDesktopServices", "start http"):
        assert term not in source, f"the update system must not use {term}"


def test_the_link_is_empty_when_there_is_nothing_to_offer() -> None:
    result = check_for_update("owner/lanlink", "0.1.0", fetch=feed())
    assert result.link == ""
    assert result.release is None


def test_a_release_label_falls_back_to_its_version() -> None:
    item = Release(Version.parse("1.0.0"), "", "", "", "")
    assert item.label == "1.0.0"


# ==========================================================================
# Downloading, verifying, and refusing to run anything unverified
# ==========================================================================

import hashlib  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from lanlink.updates import (  # noqa: E402
    CHECKSUM_ASSET,
    Asset,
    ChecksumMismatch,
    DownloadProgress,
    UpdateCancelled,
    VerifiedInstaller,
    download,
    is_check_due,
    is_skipped,
    launch_installer,
    parse_sha256sums,
    prepare_update,
    sha256_of,
    timestamp,
    verify_download,
)

PAYLOAD = b"pretend this is an installer" * 400
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def full_release(tag: str = "v0.2.0", *, with_checksums: bool = True, with_installer: bool = True) -> dict:
    assets = []
    if with_installer:
        assets.append(
            {
                "name": f"LanLinkSetup-{tag.lstrip('v')}.exe",
                "browser_download_url": f"https://example.invalid/{tag}/setup.exe",
                "size": len(PAYLOAD),
            }
        )
    assets.append(
        {
            "name": f"LanLink-{tag.lstrip('v')}-portable.zip",
            "browser_download_url": f"https://example.invalid/{tag}/portable.zip",
            "size": 10,
        }
    )
    if with_checksums:
        assets.append(
            {
                "name": CHECKSUM_ASSET,
                "browser_download_url": f"https://example.invalid/{tag}/SHA256SUMS.txt",
                "size": 200,
            }
        )
    return release(tag, assets=assets)


def streamer(payload: bytes = PAYLOAD, *, total: int | None = None, chunk: int = 4096):
    def stream(_url: str, _timeout: float):
        size = len(payload) if total is None else total
        for start in range(0, len(payload), chunk):
            yield payload[start : start + chunk], size

    return stream


# ------------------------------------------------------------- the assets


def test_a_release_exposes_its_installer_and_its_checksums() -> None:
    parsed = release_from(full_release())
    assert parsed is not None
    assert parsed.installer is not None and parsed.installer.name.endswith(".exe")
    assert parsed.checksums is not None and parsed.checksums.name == CHECKSUM_ASSET
    assert parsed.can_install


def test_a_release_without_checksums_cannot_be_installed() -> None:
    parsed = release_from(full_release(with_checksums=False))
    assert parsed is not None
    assert parsed.installer is not None
    assert parsed.checksums is None
    assert not parsed.can_install, "nothing to verify against means nothing to run"


def test_a_release_without_an_installer_cannot_be_installed() -> None:
    parsed = release_from(full_release(with_installer=False))
    assert parsed is not None
    assert parsed.installer is None
    assert not parsed.can_install


def test_the_setup_installer_is_preferred_over_another_executable() -> None:
    payload = release(
        "v0.2.0",
        assets=[
            {"name": "helper.exe", "browser_download_url": "https://x/helper.exe"},
            {"name": "LanLinkSetup-0.2.0.exe", "browser_download_url": "https://x/setup.exe"},
        ],
    )
    parsed = release_from(payload)
    assert parsed is not None and parsed.installer is not None
    assert parsed.installer.name == "LanLinkSetup-0.2.0.exe"


def test_the_check_reports_whether_it_can_install() -> None:
    ready = check_for_update("owner/lanlink", "0.1.0", fetch=feed(full_release()))
    assert ready.can_install
    assert str(ready.latest) == "0.2.0"

    unverifiable = check_for_update(
        "owner/lanlink", "0.1.0", fetch=feed(full_release(with_checksums=False))
    )
    assert unverifiable.has_update
    assert not unverifiable.can_install, "an unverifiable release must not offer to install"


# ------------------------------------------------------------ the checksums


def test_a_sha256sums_file_is_read_the_way_the_tools_write_it() -> None:
    text = f"""{DIGEST}  LanLinkSetup-0.2.0.exe
{"b" * 64} *LanLink-0.2.0-portable.zip
"""
    digests = parse_sha256sums(text)
    assert digests["LanLinkSetup-0.2.0.exe"] == DIGEST
    assert digests["LanLink-0.2.0-portable.zip"] == "b" * 64


def test_rubbish_in_the_checksum_file_is_ignored_not_guessed_at() -> None:
    digests = parse_sha256sums(
        "not a checksum line\n"
        "short  file.exe\n"
        f"{'z' * 64}  nonhex.exe\n"
        "\n"
        f"{DIGEST}  good.exe\n"
    )
    assert digests == {"good.exe": DIGEST}


def test_a_path_prefix_in_the_checksum_file_still_matches() -> None:
    digests = parse_sha256sums(f"{DIGEST}  packaging/output/LanLinkSetup-0.2.0.exe")
    assert digests["LanLinkSetup-0.2.0.exe"] == DIGEST


# -------------------------------------------------------------- downloading


def test_a_download_reports_progress_and_lands_complete(tmp_path: Path) -> None:
    seen: list[DownloadProgress] = []
    target = tmp_path / "setup.exe"

    download("https://x/setup.exe", target, on_progress=seen.append, stream=streamer())

    assert target.read_bytes() == PAYLOAD
    assert seen, "no progress was reported"
    assert seen[-1].received == len(PAYLOAD)
    assert seen[-1].fraction == 1.0
    assert seen[0].fraction is not None and seen[0].fraction < 1.0


def test_progress_copes_with_an_unknown_length() -> None:
    assert DownloadProgress(100, None).fraction is None
    assert DownloadProgress(100, 0).fraction is None


def test_a_cancelled_download_leaves_nothing_behind(tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    with pytest.raises(UpdateCancelled):
        download("https://x/setup.exe", target, is_cancelled=lambda: True, stream=streamer())

    assert not target.exists()
    assert list(tmp_path.iterdir()) == [], "a partial download was left on disk"


def test_a_failed_download_leaves_nothing_behind(tmp_path: Path) -> None:
    def breaks(_url: str, _timeout: float):
        yield b"some", 100
        raise OSError("the connection dropped")

    target = tmp_path / "setup.exe"
    with pytest.raises(OSError, match="dropped"):
        download("https://x/setup.exe", target, stream=breaks)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_download_never_appears_complete_until_it_is(tmp_path: Path) -> None:
    """The bytes land in a .part file, exactly like a file transfer."""
    target = tmp_path / "setup.exe"
    during: list[list[str]] = []

    def watching(_url: str, _timeout: float):
        yield PAYLOAD[:100], len(PAYLOAD)
        during.append(sorted(item.name for item in tmp_path.iterdir()))
        yield PAYLOAD[100:], len(PAYLOAD)

    download("https://x/setup.exe", target, stream=watching)

    assert during == [["setup.exe.part"]], "the real name existed before the download finished"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["setup.exe"]


# ------------------------------------------------------------- verification


def test_a_matching_download_verifies(tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    target.write_bytes(PAYLOAD)

    verified = verify_download(target, "setup.exe", {"setup.exe": DIGEST}, Version.parse("0.2.0"))

    assert isinstance(verified, VerifiedInstaller)
    assert verified.sha256 == DIGEST
    assert verified.path == target
    assert target.exists()


def test_a_mismatched_download_is_deleted_rather_than_kept(tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    target.write_bytes(b"something else entirely")

    with pytest.raises(ChecksumMismatch, match="does not match"):
        verify_download(target, "setup.exe", {"setup.exe": DIGEST}, Version.parse("0.2.0"))

    assert not target.exists(), "a file that failed its checksum must not be left runnable"


def test_a_download_with_no_published_digest_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    target.write_bytes(PAYLOAD)

    with pytest.raises(ChecksumMismatch, match="does not publish"):
        verify_download(target, "setup.exe", {"other.exe": DIGEST}, Version.parse("0.2.0"))


def test_the_digest_is_compared_case_insensitively(tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    target.write_bytes(PAYLOAD)
    verified = verify_download(target, "setup.exe", {"setup.exe": DIGEST.upper()}, Version.parse("1.0"))
    assert verified.sha256 == DIGEST


def test_sha256_of_a_file_matches_hashlib(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(PAYLOAD)
    assert sha256_of(target) == DIGEST


# ------------------------------------------------------ the whole preparation


def checksum_text(name: str = "LanLinkSetup-0.2.0.exe", digest: str = DIGEST) -> str:
    return f"{digest}  {name}\n{'c' * 64}  LanLink-0.2.0-portable.zip\n"


def test_preparing_an_update_downloads_and_verifies(tmp_path: Path) -> None:
    parsed = release_from(full_release())
    assert parsed is not None

    verified = prepare_update(
        parsed, tmp_path, stream=streamer(), read_text=lambda _url: checksum_text()
    )

    assert verified.path.name == "LanLinkSetup-0.2.0.exe"
    assert verified.sha256 == DIGEST
    assert verified.version == Version.parse("0.2.0")


def test_preparing_refuses_a_release_with_no_checksums(tmp_path: Path) -> None:
    parsed = release_from(full_release(with_checksums=False))
    assert parsed is not None

    with pytest.raises(LookupError, match="SHA256SUMS"):
        prepare_update(parsed, tmp_path, stream=streamer(), read_text=lambda _url: "")


def test_preparing_refuses_a_release_with_no_installer(tmp_path: Path) -> None:
    parsed = release_from(full_release(with_installer=False))
    assert parsed is not None

    with pytest.raises(LookupError, match="no Windows installer"):
        prepare_update(parsed, tmp_path, stream=streamer(), read_text=lambda _url: checksum_text())


def test_a_tampered_download_is_caught_and_removed(tmp_path: Path) -> None:
    """The published digest is for the real file; we serve a different one."""
    parsed = release_from(full_release())
    assert parsed is not None

    with pytest.raises(ChecksumMismatch):
        prepare_update(
            parsed,
            tmp_path,
            stream=streamer(b"malicious payload"),
            read_text=lambda _url: checksum_text(),
        )

    assert list(tmp_path.iterdir()) == [], "the bad download is still on disk"


def test_a_cancelled_preparation_raises_cancelled_not_a_failure(tmp_path: Path) -> None:
    parsed = release_from(full_release())
    assert parsed is not None

    with pytest.raises(UpdateCancelled):
        prepare_update(
            parsed,
            tmp_path,
            is_cancelled=lambda: True,
            stream=streamer(),
            read_text=lambda _url: checksum_text(),
        )


# ------------------------------------------------------------ never execute


def test_only_a_verified_installer_can_be_launched(tmp_path: Path) -> None:
    """The type system does the enforcing, not a comment."""
    stray = tmp_path / "setup.exe"
    stray.write_bytes(PAYLOAD)

    for candidate in (stray, str(stray), None, 42, {"path": stray}):
        with pytest.raises((TypeError, AttributeError)):
            launch_installer(candidate)  # type: ignore[arg-type]


def test_launching_a_verified_installer_that_vanished_is_reported(tmp_path: Path) -> None:
    verified = VerifiedInstaller(tmp_path / "gone.exe", Version.parse("0.2.0"), DIGEST)
    with pytest.raises(FileNotFoundError):
        launch_installer(verified)


def test_a_verified_installer_cannot_be_forged_from_a_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "setup.exe"
    target.write_bytes(b"not the published bytes")
    with pytest.raises(ChecksumMismatch):
        verify_download(target, "setup.exe", {"setup.exe": DIGEST}, Version.parse("0.2.0"))


# ------------------------------------------------- once a day, and skipping


def test_the_first_check_is_always_due() -> None:
    assert is_check_due(None)
    assert is_check_due("")


def test_a_check_an_hour_ago_is_not_due_but_a_day_ago_is() -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    assert not is_check_due((now - timedelta(hours=1)).isoformat(), now=now)
    assert not is_check_due((now - timedelta(hours=23)).isoformat(), now=now)
    assert is_check_due((now - timedelta(days=1, minutes=1)).isoformat(), now=now)
    assert is_check_due((now - timedelta(days=9)).isoformat(), now=now)


def test_an_unreadable_timestamp_means_check_rather_than_never_check_again() -> None:
    assert is_check_due("not a date")
    assert is_check_due("2026-13-45T99:00:00")


def test_a_naive_timestamp_is_treated_as_utc() -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    assert not is_check_due("2026-08-19T11:00:00", now=now)


def test_a_timestamp_round_trips() -> None:
    stamped = timestamp()
    assert not is_check_due(stamped)
    assert datetime.fromisoformat(stamped).tzinfo is not None


def test_a_skipped_version_stays_skipped_until_a_newer_one_appears() -> None:
    assert is_skipped(Version.parse("0.2.0"), "0.2.0")
    assert is_skipped(Version.parse("0.1.9"), "0.2.0"), "older than skipped is also skipped"
    assert not is_skipped(Version.parse("0.3.0"), "0.2.0"), "a newer version must still be offered"


def test_nothing_is_skipped_when_nothing_was_skipped() -> None:
    assert not is_skipped(Version.parse("0.2.0"), "")
    assert not is_skipped(Version.parse("0.2.0"), "not a version")


# --------------------------------------------------------------- the channel


def test_the_update_channel_is_releases_and_nothing_else() -> None:
    """Requirement one: never a branch, never the source tree."""
    import inspect

    from lanlink import updates

    source = inspect.getsource(updates)
    assert "/releases" in source
    for term in ("/tarball/", "/zipball/", "/archive/", "refs/heads", "main.zip", "master"):
        assert term not in source, f"the update channel must not reach for {term}"


def test_an_asset_knows_whether_it_is_an_installer() -> None:
    assert Asset("LanLinkSetup-1.0.0.exe", "https://x").is_installer
    assert not Asset("SHA256SUMS.txt", "https://x").is_installer
    assert not Asset("LanLink-1.0.0-portable.zip", "https://x").is_installer
