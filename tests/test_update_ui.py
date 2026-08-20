"""The update dialog and the Settings wiring around it.

The properties worth pinning down are the ones that would be bad in the field:
nothing downloads until asked, nothing runs unless it verified, the automatic
check does not interrupt, and a skipped version stays skipped.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lanlink.ui import theme as theme_module  # noqa: E402
from lanlink.ui.updater import UpdateBanner, UpdateDialog, format_size  # noqa: E402
from lanlink.updates import (  # noqa: E402
    CHECKSUM_ASSET,
    Asset,
    Release,
    UpdateCheck,
    UpdateStatus,
    VerifiedInstaller,
    Version,
)

PAYLOAD = b"installer bytes" * 500
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
INSTALLER = "LanLinkSetup-0.2.0.exe"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "ui.ini"
    monkeypatch.setattr(
        theme_module, "settings", lambda: QSettings(str(path), QSettings.Format.IniFormat)
    )
    return path


def make_release(*, installer: bool = True, checksums: bool = True) -> Release:
    assets = []
    if installer:
        assets.append(Asset(INSTALLER, "https://example.invalid/setup.exe", len(PAYLOAD)))
    if checksums:
        assets.append(Asset(CHECKSUM_ASSET, "https://example.invalid/SHA256SUMS.txt", 120))
    return Release(
        version=Version.parse("0.2.0"),
        name="LanLink 0.2.0",
        notes="Faster transfers.\nFixed a drag bug.",
        page_url="https://example.invalid/releases/v0.2.0",
        download_url="https://example.invalid/setup.exe",
        assets=tuple(assets),
    )


def make_check(**kwargs) -> UpdateCheck:
    return UpdateCheck(
        UpdateStatus.UPDATE_AVAILABLE,
        "LanLink 0.2.0 is available. You are running 0.1.0.",
        make_release(**kwargs),
        Version.parse("0.1.0"),
    )


def settle(qapp, predicate, limit: int = 600) -> None:
    from threading import Event

    waiter = Event()
    for _ in range(limit):
        qapp.processEvents()
        if predicate():
            return
        waiter.wait(0.01)


# ------------------------------------------------------------------- the dialog


def test_the_dialog_shows_the_version_and_the_notes(qapp, tmp_path: Path) -> None:
    dialog = UpdateDialog(make_check(), tmp_path)
    try:
        assert "0.2.0" in dialog.windowTitle()
        assert dialog.update_button.text() == "Update Now"
        assert dialog.update_button.isEnabled()
        assert INSTALLER in dialog.detail.text()
        assert dialog.progress.isHidden(), "nothing is downloading yet"
    finally:
        dialog.deleteLater()


def test_a_release_without_checksums_cannot_be_installed_from_the_dialog(qapp, tmp_path: Path) -> None:
    dialog = UpdateDialog(make_check(checksums=False), tmp_path)
    try:
        assert not dialog.update_button.isEnabled()
        assert "not install" in dialog.status.text()
    finally:
        dialog.deleteLater()


def test_a_release_without_an_installer_says_so(qapp, tmp_path: Path) -> None:
    dialog = UpdateDialog(make_check(installer=False), tmp_path)
    try:
        assert not dialog.update_button.isEnabled()
        assert "no Windows installer" in dialog.detail.text()
    finally:
        dialog.deleteLater()


def test_downloading_reports_progress_and_verifies(qapp, tmp_path: Path, monkeypatch) -> None:
    from lanlink.ui import updater

    def fake_prepare(release, folder, on_progress=None, is_cancelled=None, **_kwargs):
        target = Path(folder) / INSTALLER
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PAYLOAD)
        if on_progress:
            on_progress(updater.DownloadProgress(len(PAYLOAD) // 2, len(PAYLOAD)))
            on_progress(updater.DownloadProgress(len(PAYLOAD), len(PAYLOAD)))
        return VerifiedInstaller(target, release.version, DIGEST)

    monkeypatch.setattr(updater, "prepare_update", fake_prepare)
    dialog = UpdateDialog(make_check(), tmp_path)
    try:
        dialog.start_download()
        settle(qapp, lambda: dialog.installer is not None)

        assert dialog.installer is not None
        assert dialog.progress.value() == 100
        assert "verified" in dialog.status.text()
        assert DIGEST[:16] in dialog.status.text(), "the user should see what it verified as"
        assert dialog.update_button.text() == "Install and restart"
        assert "identity" in dialog.status.text(), "say that pairings survive"
    finally:
        dialog.deleteLater()


def test_a_checksum_mismatch_is_reported_and_nothing_is_installed(qapp, tmp_path: Path, monkeypatch) -> None:
    from lanlink.ui import updater
    from lanlink.updates import ChecksumMismatch

    def refuses(*_args, **_kwargs):
        raise ChecksumMismatch("The downloaded installer does not match the checksum.")

    monkeypatch.setattr(updater, "prepare_update", refuses)
    launched: list[object] = []
    monkeypatch.setattr(updater, "launch_installer", launched.append)

    dialog = UpdateDialog(make_check(), tmp_path)
    try:
        dialog.start_download()
        settle(qapp, lambda: "checksum" in dialog.status.text())

        assert dialog.installer is None
        assert launched == [], "nothing may run after a mismatch"
        assert dialog.update_button.isEnabled(), "the user can try again"
    finally:
        dialog.deleteLater()


def test_a_network_failure_is_reported_calmly(qapp, tmp_path: Path, monkeypatch) -> None:
    from lanlink.ui import updater

    def offline(*_args, **_kwargs):
        raise OSError("Network is unreachable")

    monkeypatch.setattr(updater, "prepare_update", offline)
    dialog = UpdateDialog(make_check(), tmp_path)
    try:
        dialog.start_download()
        settle(qapp, lambda: "failed" in dialog.status.text())
        assert "network" in dialog.status.text().lower()
        assert dialog.installer is None
    finally:
        dialog.deleteLater()


def test_cancelling_a_download_installs_nothing(qapp, tmp_path: Path, monkeypatch) -> None:
    from lanlink.ui import updater
    from lanlink.updates import UpdateCancelled

    def cancelled(*_args, **_kwargs):
        raise UpdateCancelled("The download was cancelled.")

    monkeypatch.setattr(updater, "prepare_update", cancelled)
    dialog = UpdateDialog(make_check(), tmp_path)
    try:
        dialog.start_download()
        settle(qapp, lambda: "cancelled" in dialog.status.text())
        assert "Nothing was installed" in dialog.status.text()
        assert dialog.installer is None
    finally:
        dialog.deleteLater()


def test_the_installer_only_runs_after_verification(qapp, tmp_path: Path, monkeypatch) -> None:
    from lanlink.ui import updater

    launched: list[object] = []
    monkeypatch.setattr(updater, "launch_installer", launched.append)
    dialog = UpdateDialog(make_check(), tmp_path)
    try:
        dialog._install()  # nothing verified yet
        assert launched == []

        target = tmp_path / INSTALLER
        target.write_bytes(PAYLOAD)
        dialog.installer = VerifiedInstaller(target, Version.parse("0.2.0"), DIGEST)
        starting: list[int] = []
        dialog.installStarting.connect(lambda: starting.append(1))
        dialog._install()

        assert len(launched) == 1
        assert isinstance(launched[0], VerifiedInstaller)
        assert starting == [1], "the window is told to close"
    finally:
        dialog.deleteLater()


def test_an_installer_that_will_not_start_says_where_it_is(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    from lanlink.ui import updater

    def explodes(_installer):
        raise OSError("Access is denied")

    monkeypatch.setattr(updater, "launch_installer", explodes)
    target = tmp_path / INSTALLER
    target.write_bytes(PAYLOAD)

    dialog = UpdateDialog(make_check(), tmp_path)
    try:
        dialog.installer = VerifiedInstaller(target, Version.parse("0.2.0"), DIGEST)
        dialog._install()
        assert "Access is denied" in dialog.status.text()
        assert str(target) in dialog.status.text()
    finally:
        dialog.deleteLater()


def test_skipping_reports_the_version_and_closes(qapp, tmp_path: Path) -> None:
    dialog = UpdateDialog(make_check(), tmp_path)
    skipped: list[str] = []
    dialog.skipRequested.connect(skipped.append)
    dialog._skip()
    assert skipped == ["0.2.0"]
    dialog.deleteLater()


def test_sizes_read_the_way_people_expect() -> None:
    assert format_size(0) == ""
    assert format_size(512) == "512 B"
    assert format_size(2048) == "2.0 KB"
    assert format_size(5 * 1024 * 1024) == "5.0 MB"


# ------------------------------------------------------------------- the banner


def test_the_banner_is_hidden_until_there_is_something_to_say(qapp) -> None:
    banner = UpdateBanner()
    try:
        assert banner.isHidden()
        banner.show_update("0.2.0", "0.1.0")
        assert "0.2.0" in banner.label.text()
        assert "0.1.0" in banner.label.text()
    finally:
        banner.deleteLater()


def test_dismissing_the_banner_hides_it(qapp) -> None:
    banner = UpdateBanner()
    dismissed: list[int] = []
    banner.dismissed.connect(lambda: dismissed.append(1))
    banner.show_update("0.2.0", "0.1.0")
    banner._dismiss()
    assert banner.isHidden()
    assert dismissed == [1]
    banner.deleteLater()


# --------------------------------------------------------------- the settings


def test_the_last_successful_check_is_cached(store) -> None:
    assert theme_module.saved_last_check() == ""
    stamp = datetime.now(UTC).isoformat()
    theme_module.save_last_check(stamp, "0.2.0")
    assert theme_module.saved_last_check() == stamp
    assert theme_module.saved_last_version() == "0.2.0"


def test_a_skipped_version_is_remembered(store) -> None:
    assert theme_module.saved_skipped_version() == ""
    theme_module.save_skipped_version("0.2.0")
    assert theme_module.saved_skipped_version() == "0.2.0"


def test_the_cache_survives_a_restart(store) -> None:
    theme_module.save_last_check("2026-08-19T10:00:00+00:00", "0.5.0")
    theme_module.save_skipped_version("0.5.0")

    reopened = QSettings(str(store), QSettings.Format.IniFormat)
    assert reopened.value(theme_module.UPDATE_LAST_VERSION_KEY) == "0.5.0"
    assert reopened.value(theme_module.UPDATE_SKIPPED_KEY) == "0.5.0"


def test_automatic_checking_is_on_unless_it_is_turned_off(store) -> None:
    """A fix nobody hears about is not a fix. Off has to be a decision."""
    assert theme_module.checks_updates_at_startup() is True
    theme_module.save_check_at_startup(False)
    assert theme_module.checks_updates_at_startup() is False
    theme_module.save_check_at_startup(True)
    assert theme_module.checks_updates_at_startup() is True


def test_an_empty_repository_setting_means_the_one_lanlink_ships_from(store) -> None:
    from lanlink.updates import DEFAULT_REPOSITORY

    assert theme_module.saved_update_repository() == DEFAULT_REPOSITORY
    theme_module.save_update_repository("someone/fork")
    assert theme_module.saved_update_repository() == "someone/fork"
    theme_module.save_update_repository("")
    assert theme_module.saved_update_repository() == DEFAULT_REPOSITORY


def test_a_recent_check_stops_the_next_launch_from_asking_again(store) -> None:
    """Requirement: once a day, not once a launch."""
    from lanlink.updates import is_check_due

    theme_module.save_check_at_startup(True)
    theme_module.save_update_repository("owner/lanlink")
    theme_module.save_last_check(datetime.now(UTC).isoformat(), "0.1.0")

    assert not is_check_due(theme_module.saved_last_check())

    theme_module.save_last_check((datetime.now(UTC) - timedelta(days=2)).isoformat(), "0.1.0")
    assert is_check_due(theme_module.saved_last_check())


# ==========================================================================
# End to end over a real socket, and the Windows-specific handover
# ==========================================================================

import http.server  # noqa: E402
import socket  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from contextlib import contextmanager  # noqa: E402


@contextmanager
def serving(files: dict[str, bytes]):
    """A real HTTP server, so the download path is exercised rather than stubbed."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = files.get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def released(base: str, digest: str = DIGEST) -> Release:
    return Release(
        version=Version.parse("0.2.0"),
        name="LanLink 0.2.0",
        notes="Notes",
        page_url=f"{base}/page",
        download_url=f"{base}/{INSTALLER}",
        assets=(
            Asset(INSTALLER, f"{base}/{INSTALLER}", len(PAYLOAD)),
            Asset(CHECKSUM_ASSET, f"{base}/{CHECKSUM_ASSET}", 100),
        ),
    )


def sums(digest: str = DIGEST, name: str = INSTALLER) -> bytes:
    return f"{digest}  {name}\n".encode("ascii")


def test_a_real_download_verifies_end_to_end(tmp_path: Path) -> None:
    """Real HTTP, real streaming, real checksum — no stubs in the middle."""
    from lanlink.updates import prepare_update

    with serving({f"/{INSTALLER}": PAYLOAD, f"/{CHECKSUM_ASSET}": sums()}) as base:
        seen: list[int] = []
        verified = prepare_update(
            released(base), tmp_path, on_progress=lambda progress: seen.append(progress.received)
        )

    assert verified.path.read_bytes() == PAYLOAD
    assert verified.sha256 == DIGEST
    assert seen and seen[-1] == len(PAYLOAD), "progress was not reported to the end"


def test_a_real_download_with_a_wrong_checksum_is_deleted(tmp_path: Path) -> None:
    from lanlink.updates import ChecksumMismatch, prepare_update

    with (
        serving({f"/{INSTALLER}": PAYLOAD, f"/{CHECKSUM_ASSET}": sums("a" * 64)}) as base,
        pytest.raises(ChecksumMismatch),
    ):
        prepare_update(released(base), tmp_path)

    assert list(tmp_path.iterdir()) == [], "the rejected installer is still on disk"


def test_a_release_whose_checksum_file_is_missing_fails_before_running_anything(tmp_path: Path) -> None:
    from lanlink.updates import prepare_update

    with serving({f"/{INSTALLER}": PAYLOAD}) as base, pytest.raises(Exception) as failure:
        prepare_update(released(base), tmp_path)

    assert "404" in str(failure.value) or "Client error" in str(failure.value)
    assert not (tmp_path / INSTALLER).exists()


def test_a_server_that_disappears_mid_download_leaves_nothing(tmp_path: Path) -> None:
    """The checksums are served but the installer is not: a 404 part-way through."""
    import httpx

    from lanlink.updates import prepare_update

    with serving({f"/{CHECKSUM_ASSET}": sums()}) as base, pytest.raises(httpx.HTTPStatusError):
        prepare_update(released(base), tmp_path)

    assert not (tmp_path / INSTALLER).exists()
    assert not (tmp_path / f"{INSTALLER}.part").exists()


# ------------------------------------------------------------ Windows handover


@pytest.mark.skipif(sys.platform != "win32", reason="the installer handover is Windows only")
def test_the_windows_handover_uses_startfile(tmp_path: Path, monkeypatch) -> None:
    """Integration on Windows: the verified path is what gets handed to the shell."""
    from lanlink import updates

    target = tmp_path / INSTALLER
    target.write_bytes(PAYLOAD)
    started: list[str] = []
    monkeypatch.setattr(updates.os, "startfile", started.append, raising=False)

    updates.launch_installer(VerifiedInstaller(target, Version.parse("0.2.0"), DIGEST))

    assert started == [str(target)]


def test_the_handover_is_the_only_thing_that_runs_a_file() -> None:
    """Nothing else in the update system may start a process."""
    import inspect

    from lanlink import updates

    for name, function in vars(updates).items():
        if not inspect.isfunction(function) or name == "launch_installer":
            continue
        source = inspect.getsource(function)
        assert "startfile" not in source, f"{name} starts a process"
        assert "Popen" not in source, f"{name} starts a process"


# ------------------------------------------- what an update must not disturb


def test_an_upgrade_keeps_the_device_identity_and_its_pairings(tmp_path: Path) -> None:
    """Requirements 17 to 19: the installer replaces the program, not the data.

    Settings live outside the installation directory, so this reproduces an
    upgrade by reopening the same data folder — which is exactly what the new
    build does on first run.
    """
    from lanlink.state import ALL_PERMISSIONS, HubState

    settings_path = tmp_path / "settings.json"
    share_root = tmp_path / "shared"
    share_root.mkdir()

    before = HubState(settings_path)
    share = before.add_share(share_root, "Demo")
    before.set_share_permissions(share.id, ALL_PERMISSIONS)
    before.upsert_remote_device(
        "peer-1", "Workshop PC", "https://10.0.0.5:8765", "their-token",
        certificate="-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n",
        fingerprint="ab" * 32,
    )
    code, _ = before.start_pairing()
    result = before.pair("peer-2", "Studio", code, source="10.0.0.6")
    assert result.ok

    identity = before.device_id
    certificate_fingerprint = before.certificate_fingerprint

    after = HubState(settings_path)

    assert after.device_id == identity, "an update must never change the device identity"
    assert after.certificate_fingerprint == certificate_fingerprint
    assert set(after.remote_devices) == {"peer-1"}, "outbound pairings must survive"
    assert after.remote_devices["peer-1"].token == "their-token"
    assert after.remote_devices["peer-1"].certificate, "the pinned certificate must survive"
    assert set(after.paired_devices) == {"peer-2"}, "inbound pairings must survive"
    assert after.identify(result.token) is not None, "peers must not have to pair again"
    assert [item.name for item in after.shares.values()] == ["Demo"]
    assert next(iter(after.shares.values())).permissions == ALL_PERMISSIONS


def test_the_uninstaller_never_touches_the_identity_folder() -> None:
    """The other half of the same promise, enforced in the installer script."""
    inno = (Path(__file__).resolve().parent.parent / "packaging" / "lanlink.iss").read_text(
        encoding="utf-8"
    )
    deletions = inno.split("[UninstallDelete]", 1)[1].split("[Code]", 1)[0]
    assert "staging" in deletions and "thumbnails" in deletions
    for protected in ("lanlink-hub", "settings.json", "device-key", "device-cert"):
        assert protected not in deletions, f"the uninstaller would remove {protected}"
