"""Regression tests for the persistence defects found in docs/current_state.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lanlink.state import HubState, SettingsCorruptError, atomic_write_text


def test_offline_share_is_kept_not_pruned(tmp_path: Path) -> None:
    removable = tmp_path / "usb"
    removable.mkdir()
    state = HubState(tmp_path / "settings.json")
    share = state.add_share(removable, "USB drive")

    removable.rename(tmp_path / "usb-unplugged")
    restarted = HubState(tmp_path / "settings.json")
    assert share.id in restarted.shares, "an unplugged drive must stay configured"
    assert restarted.shares[share.id].available is False

    (tmp_path / "usb-unplugged").rename(removable)
    replugged = HubState(tmp_path / "settings.json")
    assert replugged.shares[share.id].available is True


def test_unavailable_share_refuses_access(tmp_path: Path) -> None:
    from lanlink.files import FileAccessError, list_folder

    removable = tmp_path / "usb"
    removable.mkdir()
    state = HubState(tmp_path / "settings.json")
    share = state.add_share(removable, "USB drive")
    removable.rename(tmp_path / "gone")

    with pytest.raises(FileAccessError, match="unavailable"):
        list_folder(state, share.id)


def test_corrupt_settings_recovers_from_backup(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    state = HubState(settings)
    original_id = state.device_id
    state.set_device_name("Workshop PC")  # forces a save, creating the .bak

    settings.write_text("{ this is not json", encoding="utf-8")
    recovered = HubState(settings)
    assert recovered.device_id == original_id
    assert recovered.recovered_from_backup is True


def test_corrupt_settings_without_backup_never_mints_a_new_identity(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{ broken", encoding="utf-8")

    with pytest.raises(SettingsCorruptError):
        HubState(settings)

    quarantined = list(tmp_path.glob("settings.json.corrupt-*"))
    assert quarantined, "the damaged file must be preserved, not discarded"


def test_atomic_write_keeps_a_backup(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_write_text(target, '{"generation": 1}')
    atomic_write_text(target, '{"generation": 2}')

    assert json.loads(target.read_text())["generation"] == 2
    assert json.loads((tmp_path / "data.json.bak").read_text())["generation"] == 1
    assert not (tmp_path / "data.json.tmp").exists()


def test_device_identity_is_stable_across_restarts(tmp_path: Path) -> None:
    first = HubState(tmp_path / "settings.json")
    second = HubState(tmp_path / "settings.json")
    assert first.device_id == second.device_id


def test_legacy_plaintext_tokens_are_migrated_to_hashes(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "device_id": "device-1",
                "device_name": "Old PC",
                "shares": [],
                "paired_devices": [
                    {"id": "client-1", "name": "Phone", "token": "legacy-token", "paired_at": 1.0}
                ],
                "remote_devices": [],
            }
        ),
        encoding="utf-8",
    )
    state = HubState(settings)
    assert state.authenticate("legacy-token") is True
    assert state.paired_devices["client-1"].token_hash != "legacy-token"


def test_malformed_entries_are_skipped_not_fatal(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "device_id": "device-1",
                "device_name": "PC",
                "shares": [{"id": "s1"}, {"id": "s2", "name": "Good", "path": str(tmp_path)}],
                "paired_devices": [{"nonsense": True}],
                "remote_devices": [],
            }
        ),
        encoding="utf-8",
    )
    state = HubState(settings)
    assert list(state.shares) == ["s2"]
    assert state.paired_devices == {}


def test_data_dir_override_gives_a_separate_identity(tmp_path: Path, monkeypatch) -> None:
    """A second instance on one machine must not share the first one's identity."""
    from lanlink.state import DATA_DIR_ENV, app_data_dir

    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "instance-one"))
    first = HubState()
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "instance-two"))
    second = HubState()

    assert first.device_id != second.device_id
    assert first.settings_path != second.settings_path
    assert first.settings_path.exists() and second.settings_path.exists()

    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "instance-one"))
    assert app_data_dir() == tmp_path / "instance-one"
    assert HubState().device_id == first.device_id, "the identity must persist per folder"


def test_data_dir_override_is_ignored_when_blank(tmp_path: Path, monkeypatch) -> None:
    from lanlink.state import DATA_DIR_ENV, app_data_dir

    monkeypatch.setenv(DATA_DIR_ENV, "   ")
    assert app_data_dir() == Path.home() / ".lanlink-hub"
