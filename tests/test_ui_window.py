"""Smoke tests that build the real window with the network stack stubbed out.

These catch the class of mistake unit tests on the models cannot: a mistyped
attribute or a signal wired to a slot that does not exist.
"""

from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lanlink.state import HubState  # noqa: E402
from lanlink.ui import main_window as mw  # noqa: E402


class FakeCertificate:
    fingerprint = "a" * 64
    short_fingerprint = "AAAA AAAA AAAA AAAA"


class FakeService:
    def __init__(self, state, port=8765, host=None):
        self.state = state
        self.port = port
        self.host = "127.0.0.1"
        self.last_error = None
        self.thread = None
        self.started = False
        self.certificate = FakeCertificate()
        self.scheme = "https"

    def start(self):
        self.started = True
        return True

    @property
    def url(self):
        return "https://127.0.0.1:8765"

    def address_changed(self):
        return False

    def restart(self):
        return True

    def stop(self):
        self.started = False


class FakeDiscovery:
    def __init__(self, local_device_id=None, stale_after_seconds=20):
        self.zeroconf = object()
        self.last_error = None
        self._devices = []

    def start(self):
        return True

    def stop(self):
        self.zeroconf = None

    def devices(self):
        return list(self._devices)


class _Request:
    """Stand-in with PairingRequest's surface, driven synchronously."""

    def __init__(self, attempt, block: bool = False) -> None:
        self._attempt = attempt
        self._block = block
        self.cancelled = False
        self.finished = False
        self.waiting_for_peer = False
        self.attempts = 0

    def cancel(self) -> None:
        self.cancelled = True

    def run(self):
        from lanlink.pairing_request import PairingRequest

        inner = PairingRequest(self._attempt, timeout=5, interval=0.01)
        outcome = inner.run()
        self.attempts = inner.attempts
        self.finished = True
        return outcome


def select_row(window, row: int) -> None:
    """QTreeView has no selectRow(); select through the selection model."""
    from PySide6.QtCore import QItemSelectionModel

    index = window.entry_proxy.index(row, 0)
    window.entry_view.setCurrentIndex(index)
    window.entry_view.selectionModel().select(
        index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
    )


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "LocalService", FakeService)
    monkeypatch.setattr(mw, "DiscoveryBrowser", FakeDiscovery)

    share = tmp_path / "shared"
    share.mkdir()
    state = HubState(tmp_path / "settings.json")
    state.add_share(share, "Demo")

    instance = mw.MainWindow(state=state)
    # The timers would fire network probes during the test run.
    instance.fast_timer.stop()
    instance.slow_timer.stop()
    yield instance
    instance.transfers.shutdown()
    instance.runner.wait(2000)
    instance.deleteLater()


def test_window_builds_with_all_pages(window) -> None:
    assert window.sidebar.count() == len(mw.PAGES)
    assert window.pages.count() == len(mw.PAGES) + 1  # + the hidden browser page
    assert [window.sidebar.item(row).text() for row in range(window.sidebar.count())] == [
        "My Device",
        "Devices",
        "Transfers",
        "Shared Folders",
        "History",
        "Settings",
    ]


def test_every_sidebar_entry_switches_page(window) -> None:
    for row in range(window.sidebar.count()):
        window.sidebar.setCurrentRow(row)
        assert window.pages.currentIndex() == row


def test_pairing_toggle_arms_and_disarms(window) -> None:
    assert window.state.pairing_armed is False
    window.toggle_pairing()
    assert window.state.pairing_armed is True
    assert window.pairing_code.text().isdigit()
    assert len(window.pairing_code.text()) == 8

    window.toggle_pairing()
    assert window.state.pairing_armed is False
    assert window.pairing_code.text() == "————"


def test_my_device_page_shows_identity(window) -> None:
    window.refresh_my_device()
    assert window.state.device_id in window.my_id.text()
    assert "127.0.0.1" in window.my_address.text()
    assert "AAAA AAAA" in window.my_fingerprint.text()


def test_shares_page_lists_shares_and_permissions(window) -> None:
    window.refresh_shares()
    assert window.share_list.count() == 1
    assert "Demo" in window.share_list.item(0).text()
    assert "Read + write" in window.share_list.item(0).text()

    window.share_list.setCurrentRow(0)
    assert window.permission_box.isEnabled()
    index = window.permission_box.findData("rwd")
    window.permission_box.setCurrentIndex(index)
    share = next(iter(window.state.shares.values()))
    assert share.permissions == "rwd"


def test_browser_navigation_state(window) -> None:
    device = mw.UnifiedDevice(id="dev-1", name="Office PC", address="http://x", paired_out=True)
    window.current_device = device
    window._shares_loaded([{"id": "s1", "name": "Projects", "permissions": "rw", "available": True}])

    assert window.entry_model.rowCount() == 1
    assert window.breadcrumb.segment_count() == 1

    share_entry = window.entry_model.entry_at(0)
    window.current_share = share_entry
    window.current_path = "cad/parts"
    window._update_breadcrumb()
    assert window.breadcrumb.segment_count() == 4  # device / share / cad / parts


def test_drops_are_disabled_on_a_read_only_folder(window) -> None:
    window.current_device = mw.UnifiedDevice(id="dev-1", name="PC", paired_out=True)
    window.current_share = {"share_id": "s1", "name": "Docs", "permissions": "r"}
    window._folder_loaded([{"name": "a.txt", "kind": "file", "path": "a.txt", "size": 1}])
    assert window.entry_view.acceptDrops() is False
    assert "read-only" in window.browser_status.text()

    window.current_share = {"share_id": "s1", "name": "Docs", "permissions": "rw"}
    window._folder_loaded([{"name": "a.txt", "kind": "file", "path": "a.txt", "size": 1}])
    assert window.entry_view.acceptDrops() is True
    assert "Drag files here" in window.browser_status.text()


def test_search_box_filters_the_view(window) -> None:
    window.current_device = mw.UnifiedDevice(id="dev-1", name="PC", paired_out=True)
    window.current_share = {"share_id": "s1", "name": "Docs", "permissions": "rw"}
    window._folder_loaded(
        [
            {"name": "alpha.txt", "kind": "file", "path": "alpha.txt", "size": 1},
            {"name": "beta.txt", "kind": "file", "path": "beta.txt", "size": 1},
        ]
    )
    assert window.entry_proxy.rowCount() == 2
    window.search_box.setText("beta")
    assert window.entry_proxy.rowCount() == 1


def test_transfer_summary_and_sidebar_badge(window) -> None:
    window.refresh_transfers()
    assert window.transfer_summary.text() == "No transfers running"
    assert window.sidebar.item(mw.PAGE_TRANSFERS).text() == "Transfers"

    started = []
    window.transfers.submit(
        kind="test",
        filename="a.bin",
        source="A",
        destination="B",
        size=10,
        runner=lambda transfer, control: started.append(1),
    )
    window.refresh_transfers()
    assert window.sidebar.item(mw.PAGE_TRANSFERS).text() in {"Transfers", "Transfers (1)"}


def test_settings_round_trip(window) -> None:
    window.setting_name.setText("Workshop PC")
    window.setting_limit.setValue(50)
    window.setting_bind_all.setChecked(True)
    window.save_settings()

    assert window.state.device_name == "Workshop PC"
    assert window.state.max_upload_bytes == 50 * 1024 * 1024
    assert window.state.bind_all_interfaces is True

    reloaded = HubState(window.state.settings_path)
    assert reloaded.device_name == "Workshop PC"
    assert reloaded.max_upload_bytes == 50 * 1024 * 1024


def test_context_menu_respects_permissions(window, monkeypatch) -> None:
    """Destructive actions must be greyed out unless the share allows them."""
    captured = {}

    class FakeMenu:
        def __init__(self, parent=None):
            self.actions = {}

        def addAction(self, label, slot=None):
            action = type("Action", (), {"setEnabled": lambda s, value: captured.__setitem__(label, value)})()
            self.actions[label] = action
            captured.setdefault(label, True)
            return action

        def addSeparator(self):
            return None

        def exec(self, _position):
            return None

    monkeypatch.setattr(mw, "QMenu", FakeMenu)
    window.current_device = mw.UnifiedDevice(id="dev-1", name="PC", paired_out=True)
    window.current_share = {"share_id": "s1", "name": "Docs", "permissions": "r"}
    window._folder_loaded([{"name": "a.txt", "kind": "file", "path": "a.txt", "size": 1}])
    select_row(window, 0)

    window.show_entry_menu(window.entry_view.viewport().rect().topLeft())
    assert captured["Delete"] is False
    assert captured["Rename…"] is False
    assert captured["Upload here…"] is False
    assert captured["Download…"] is True

    captured.clear()
    window.current_share = {"share_id": "s1", "name": "Docs", "permissions": "rwd"}
    window._folder_loaded([{"name": "a.txt", "kind": "file", "path": "a.txt", "size": 1}])
    select_row(window, 0)
    window.show_entry_menu(window.entry_view.viewport().rect().topLeft())
    assert captured["Delete"] is True
    assert captured["Move to…"] is True


def test_unpaired_device_cannot_be_opened(window, monkeypatch) -> None:
    warnings = []
    monkeypatch.setattr(window, "_warn", lambda title, message: warnings.append(title))
    window.device_model.set_devices(
        [mw.UnifiedDevice(id="dev-9", name="Stranger", address="http://x", paired_out=False)]
    )
    window.device_view.setCurrentIndex(window.device_model.index(0, 0))
    window.open_selected_device()
    assert warnings == ["Not paired yet"]


def test_entry_activation_opens_a_folder(window, monkeypatch) -> None:
    loaded = {}
    monkeypatch.setattr(
        window, "load_folder", lambda share, path: loaded.update({"share": share, "path": path})
    )
    window.current_device = mw.UnifiedDevice(id="dev-1", name="PC", paired_out=True)
    window.current_share = {"share_id": "s1", "name": "Docs", "permissions": "rw"}
    window._folder_loaded([{"name": "CAD", "kind": "folder", "path": "CAD", "size": None}])
    select_row(window, 0)
    window.activate_entry()
    assert loaded["path"] == "CAD"


def test_window_never_references_a_web_view(window) -> None:
    assert not hasattr(window, "web")
    assert window.pages.count() == 7
    for index in range(window.pages.count()):
        widget = window.pages.widget(index)
        assert "Web" not in type(widget).__name__


def test_probe_marks_device_online_and_offline(window) -> None:
    window.health.mark_connecting("dev-1")
    assert window.health.statuses["dev-1"] is mw.DeviceStatus.CONNECTING
    window._probe_ok("dev-1")
    assert window.health.statuses["dev-1"] is mw.DeviceStatus.ONLINE
    window._probe_failed("dev-1", "timed out")
    assert window.health.statuses["dev-1"] is mw.DeviceStatus.OFFLINE


def test_pairing_request_prompt_is_answered(window, monkeypatch) -> None:
    import threading

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        mw.QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    result = {}
    thread = threading.Thread(
        target=lambda: result.__setitem__("ok", window.approval.request("client-1", "Phone"))
    )
    thread.start()
    for _ in range(200):
        window._check_pairing_request()
        if "ok" in result:
            break
        QApplication.processEvents()
        threading.Event().wait(0.01)
    thread.join(timeout=5)
    assert result.get("ok") is True


def test_alignment_role_on_size_column(window) -> None:
    window.entry_model.set_entries([{"name": "a", "kind": "file", "path": "a", "size": 10}])
    value = window.entry_model.data(window.entry_model.index(0, 1), Qt.ItemDataRole.TextAlignmentRole)
    assert value is not None


# ----------------------------------------------------------------- Phase 4 UI


def test_qr_and_invite_appear_only_while_pairing(window) -> None:
    assert window.copy_invite_button.isEnabled() is False
    assert window.qr_label.pixmap().isNull()

    window.toggle_pairing()
    window.refresh_pairing_panel()
    assert window.copy_invite_button.isEnabled() is True
    assert not window.qr_label.pixmap().isNull(), "an armed device must show a scannable invite"

    window.toggle_pairing()
    window.refresh_pairing_panel()
    assert window.copy_invite_button.isEnabled() is False
    assert window.qr_label.pixmap().isNull()


def test_invite_carries_the_certificate_fingerprint(window) -> None:
    from lanlink.invite import parse_invite

    code, _ = window.state.start_pairing()
    invite = window.current_invite(code)
    parsed = parse_invite(invite.to_url())

    assert parsed.code == code
    assert parsed.device_id == window.state.device_id
    assert parsed.fingerprint == window.service.certificate.fingerprint
    assert parsed.scheme == "https"
    assert parsed.port == window.service.port


def test_copy_invite_puts_a_link_on_the_clipboard(window) -> None:
    window.state.start_pairing()
    window.copy_invite()
    assert QApplication.clipboard().text().startswith("lanlink://pair?")


def test_pasting_an_invite_prefills_pairing(window, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(window, "_pair_with", lambda invite: captured.update({"invite": invite}))
    window.manual_address.setText("lanlink://pair?host=10.0.0.9&port=8765&code=11223344&fp=abc")
    window.pair_manual_address()

    invite = captured["invite"]
    assert invite.host == "10.0.0.9"
    assert invite.code == "11223344"
    assert invite.fingerprint == "abc"


def test_a_bad_invite_is_reported_not_swallowed(window, monkeypatch) -> None:
    warnings = []
    monkeypatch.setattr(window, "_warn", lambda title, message: warnings.append(title))
    window.manual_address.setText("not-an-address://")
    window.pair_manual_address()
    assert warnings == ["Cannot use that"]


def test_certificate_mismatch_is_an_error_not_offline(window) -> None:
    window._probe_failed("dev-1", "certificate verify failed: self signed certificate")
    assert window.health.statuses["dev-1"] is mw.DeviceStatus.ERROR
    assert "Certificate mismatch" in window.health.errors["dev-1"]

    window._probe_failed("dev-2", "All connection attempts failed")
    assert window.health.statuses["dev-2"] is mw.DeviceStatus.OFFLINE


def test_transfer_bridge_marshals_progress_to_the_gui_thread(window) -> None:
    ticks = []
    window.bridge.changed.connect(lambda: ticks.append(1))
    window.bridge.notify(None)
    QApplication.processEvents()
    assert ticks, "worker-thread progress must reach the UI without polling"


def test_pinned_devices_say_so(window) -> None:
    device = mw.UnifiedDevice(id="d1", name="PC", address="https://x", paired_out=True, pinned=True)
    assert "certificate pinned" in device.detail


def test_settings_expose_the_tls_switch(window) -> None:
    assert window.setting_tls.isChecked() is True
    assert window.setting_verify.isChecked() is True
    assert window.setting_verify.isEnabled() is False, "checksum verification is not optional"


# ------------------------------------------------- pairing order (dialog side)


def test_pairing_dialog_waits_instead_of_failing(window, monkeypatch) -> None:
    """Pressing Pair before the other device arms must wait, not error out."""
    import httpx

    from lanlink.invite import Invite

    calls = {"n": 0}

    def attempt():
        calls["n"] += 1
        if calls["n"] < 3:
            request = httpx.Request("POST", "https://10.0.0.9:8765/v1/pair")
            raise httpx.HTTPStatusError(
                "not armed", request=request, response=httpx.Response(409, request=request)
            )
        return {"token": "t", "device": {"id": "dev-b", "name": "LAPTOP-B"}, "certificate": ""}

    dialog = mw.PairingDialog(
        Invite(host="10.0.0.9", port=8765, code="12345678", scheme="http"),
        window.state,
        window.runner,
        window,
    )
    monkeypatch.setattr(mw, "PairingRequest", lambda _attempt, **_k: _Request(attempt))
    dialog.start()
    for _ in range(200):
        QApplication.processEvents()
        if dialog.result_payload:
            break
        threading.Event().wait(0.01)

    assert calls["n"] == 3, "the dialog kept the request pending while the peer armed"
    assert dialog.result_payload is not None
    assert dialog.result_payload["device"]["name"] == "LAPTOP-B"
    dialog.deleteLater()


def test_pairing_dialog_rejects_a_short_code(window) -> None:
    from lanlink.invite import Invite

    dialog = mw.PairingDialog(
        Invite(host="10.0.0.9", port=8765, code="", scheme="http"), window.state, window.runner, window
    )
    dialog.code_input.setText("123")
    dialog.start()
    assert dialog.request is None, "no request should leave the machine for a short code"
    assert "8-digit" in dialog.status.text()
    dialog.deleteLater()


def test_pairing_dialog_cancel_stops_the_request(window) -> None:
    from lanlink.invite import Invite

    dialog = mw.PairingDialog(
        Invite(host="10.0.0.9", port=8765, code="12345678", scheme="http"),
        window.state,
        window.runner,
        window,
    )
    dialog.request = _Request(lambda: {}, block=True)
    dialog.reject()
    assert dialog.request.cancelled is True
