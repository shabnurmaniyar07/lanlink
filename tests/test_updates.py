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


def test_the_check_never_downloads_anything() -> None:
    """It reports a link. Fetching and running it stays the user's decision."""
    import inspect

    from lanlink import updates

    source = inspect.getsource(updates)
    for term in ("subprocess", "os.startfile", "ShellExecute", "urlretrieve", "webbrowser"):
        assert term not in source, f"the update check must not use {term}"


def test_the_link_is_empty_when_there_is_nothing_to_offer() -> None:
    result = check_for_update("owner/lanlink", "0.1.0", fetch=feed())
    assert result.link == ""
    assert result.release is None


def test_a_release_label_falls_back_to_its_version() -> None:
    item = Release(Version.parse("1.0.0"), "", "", "", "")
    assert item.label == "1.0.0"
