"""Forgetting a device must actually forget it — offline, and across a restart.

Removing only the outbound pairing used to leave the other device holding a
working token for our shares, and the entry came back on the next start from the
inbound record. These tests pin the behaviour down in both directions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lanlink.api import create_app
from lanlink.discovery import NearbyDevice
from lanlink.state import HubState
from lanlink.ui.devices import DeviceListModel, merge_devices

PEER = "peer-device-0001"
PEER_PEM = "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"


@pytest.fixture
def state(tmp_path: Path) -> HubState:
    hub = HubState(tmp_path / "settings.json")
    root = tmp_path / "shared"
    root.mkdir()
    hub.add_share(root, "Demo")
    return hub


def pair_outbound(state: HubState, device_id: str = PEER, name: str = "Workshop PC") -> None:
    """We hold a token for them: we can browse them."""
    state.upsert_remote_device(
        device_id=device_id,
        name=name,
        base_url="https://192.168.1.20:8765",
        token="their-token-value",
        certificate=PEER_PEM,
        fingerprint="ab" * 32,
    )


def pair_inbound(state: HubState, device_id: str = PEER, name: str = "Workshop PC") -> str:
    """They hold a token for us: they can browse our shares."""
    code, _expires = state.start_pairing()
    result = state.pair(device_id, name, code, source="192.168.1.20")
    assert result.ok and result.token
    return result.token


# ------------------------------------------------------------------ the state


def test_forget_removes_an_offline_device(state: HubState) -> None:
    """Nothing about forgetting needs the other device to be reachable."""
    pair_outbound(state)
    assert state.knows_device(PEER)

    assert state.forget_device(PEER) is True

    assert state.knows_device(PEER) is False
    assert state.get_remote_device(PEER) is None


def test_forget_removes_a_fully_paired_device_in_both_directions(state: HubState) -> None:
    pair_outbound(state)
    token = pair_inbound(state)
    assert state.identify(token) is not None

    assert state.forget_device(PEER) is True

    assert state.remote_devices == {}
    assert state.paired_devices == {}
    assert state.identify(token) is None, "the forgotten device kept a working token"


def test_forget_removes_an_inbound_only_pairing(state: HubState) -> None:
    """A device that paired with us but that we never paired out to."""
    token = pair_inbound(state)

    assert state.forget_device(PEER) is True
    assert state.identify(token) is None


def test_forgetting_deletes_the_token_and_certificate_from_disk(state: HubState) -> None:
    pair_outbound(state)
    token = pair_inbound(state)
    saved = state.settings_path.read_text(encoding="utf-8")
    assert PEER in saved

    state.forget_device(PEER)

    saved = state.settings_path.read_text(encoding="utf-8")
    assert PEER not in saved
    assert "their-token-value" not in saved
    assert "fake" not in saved, "the pinned certificate is still on disk"
    assert token not in saved


def test_it_stays_forgotten_after_a_restart(state: HubState, tmp_path: Path) -> None:
    pair_outbound(state)
    token = pair_inbound(state)
    state.forget_device(PEER)

    restarted = HubState(tmp_path / "settings.json")

    assert restarted.knows_device(PEER) is False
    assert restarted.remote_devices == {}
    assert restarted.paired_devices == {}
    assert restarted.identify(token) is None


def test_forgetting_leaves_the_local_identity_alone(state: HubState) -> None:
    identity, name = state.device_id, state.device_name
    pair_outbound(state)

    state.forget_device(PEER)

    assert state.device_id == identity
    assert state.device_name == name
    assert len(state.shares) == 1, "shares are not part of forgetting a device"


def test_the_local_device_can_never_be_forgotten(state: HubState) -> None:
    assert state.forget_device(state.device_id) is False
    assert state.forget_device("") is False
    assert state.device_id


def test_forgetting_one_device_leaves_the_others_paired(state: HubState) -> None:
    pair_outbound(state, PEER, "Workshop PC")
    pair_outbound(state, "peer-device-0002", "Studio PC")
    keep = pair_inbound(state, "peer-device-0003", "Laptop B")

    assert state.forget_device(PEER) is True

    assert set(state.remote_devices) == {"peer-device-0002"}
    assert set(state.paired_devices) == {"peer-device-0003"}
    assert state.identify(keep) is not None


def test_forgetting_an_unknown_device_reports_nothing_removed(state: HubState) -> None:
    assert state.forget_device("never-heard-of-it") is False


# ------------------------------------------------------------------- the list


def unified(state: HubState, nearby: list[NearbyDevice] | None = None):
    return merge_devices(
        nearby or [],
        state.remote_devices_snapshot(),
        state.paired_devices_snapshot(),
    )


def test_the_device_disappears_from_the_list_immediately(state: HubState) -> None:
    pair_outbound(state)
    pair_inbound(state)
    model = DeviceListModel()
    model.set_devices(unified(state))
    assert model.row_of(PEER) >= 0

    state.forget_device(PEER)
    model.set_devices(unified(state))

    assert model.row_of(PEER) == -1
    assert model.rowCount() == 0


def test_rediscovery_brings_it_back_unpaired_not_restored(state: HubState) -> None:
    """§5 of the request: it may return as a stranger, never with old credentials."""
    pair_outbound(state)
    pair_inbound(state)
    state.forget_device(PEER)

    seen = NearbyDevice(
        id=PEER,
        name="Workshop PC",
        host="192.168.1.20",
        port=8765,
        api="v1",
        service_name="Workshop PC._lanlink._tcp.local.",
        last_seen=1_700_000_000.0,
        fingerprint="cd" * 16,
    )
    devices = unified(state, [seen])

    assert len(devices) == 1
    device = devices[0]
    assert device.id == PEER
    assert device.discovered is True
    assert device.paired_out is False, "an old token came back with the device"
    assert device.paired_in is False
    assert device.pinned is False, "an old pinned certificate came back with the device"
    assert device.is_browsable is False
    assert state.get_remote_device(PEER) is None


# -------------------------------------------------------------- the API rules


def test_a_forgotten_device_is_locked_out_over_the_network(state: HubState) -> None:
    """The real point: the token it still holds must stop working."""
    token = pair_inbound(state)
    client = TestClient(create_app(state), raise_server_exceptions=False)
    assert client.get("/v1/shares", headers={"X-LanLink-Token": token}).status_code == 200

    state.forget_device(PEER)

    assert client.get("/v1/shares", headers={"X-LanLink-Token": token}).status_code == 401


def test_forgetting_is_a_local_action_and_does_not_change_the_self_unpair_rule(state: HubState) -> None:
    """§8: a device may still only revoke itself over the network."""
    token = pair_inbound(state, "peer-device-0009", "Other")
    pair_inbound(state, PEER, "Workshop PC")
    client = TestClient(create_app(state), raise_server_exceptions=False)

    refused = client.delete("/v1/pairings/" + PEER, headers={"X-LanLink-Token": token})
    assert refused.status_code == 403
    assert PEER in state.paired_devices, "a remote device revoked someone else"

    assert state.forget_device(PEER) is True, "the local owner may remove any device"
