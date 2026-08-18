"""Regression tests for the pairing weaknesses found in docs/current_state.md."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lanlink.state import PAIR_CODE_DIGITS, PAIR_MAX_FAILURES, HubState


def attempt(client: TestClient, code: str, client_id: str = "client-attacker1") -> int:
    return client.post(
        "/v1/pair", json={"client_id": client_id, "client_name": "Attacker", "pair_code": code}
    ).status_code


def test_no_code_exists_until_the_owner_arms_pairing(state: HubState) -> None:
    assert state.pairing_code() is None
    assert state.pairing_armed is False


def test_pairing_is_refused_while_pairing_mode_is_off(client: TestClient) -> None:
    assert attempt(client, "12345678") == 409


def test_code_is_eight_digits(state: HubState) -> None:
    code, _ = state.start_pairing()
    assert len(code) == PAIR_CODE_DIGITS == 8
    assert code.isdigit()


def test_wrong_codes_are_rate_limited(client: TestClient, state: HubState) -> None:
    state.start_pairing()
    statuses = [attempt(client, f"{index:08d}") for index in range(8)]
    assert 429 in statuses, "burst of wrong codes must be throttled"


def test_repeated_failures_disarm_pairing_mode(state: HubState) -> None:
    state.start_pairing()
    reasons = [
        state.pair("client-attacker1", "Attacker", f"{index:08d}", source=f"host-{index}").reason
        for index in range(PAIR_MAX_FAILURES)
    ]
    assert reasons[-1] == "locked_out"
    assert state.pairing_armed is False
    assert state.pair("client-attacker1", "Attacker", "00000000").reason == "not_armed"


def test_successful_pairing_consumes_the_code(state: HubState) -> None:
    code, _ = state.start_pairing()
    assert state.pair("client-12345678", "Phone", code).ok
    assert state.pairing_armed is False
    assert state.pairing_code() is None
    # The same code cannot be replayed by a second device.
    assert state.pair("client-87654321", "Other", code).reason == "not_armed"


def test_code_expires(state: HubState) -> None:
    code, _ = state.start_pairing(lifetime_seconds=0)
    time.sleep(0.01)
    assert state.pairing_code() is None
    assert state.pair("client-12345678", "Phone", code).reason == "not_armed"


def test_owner_can_decline_a_request(state: HubState) -> None:
    state.approval_callback = lambda client_id, name: False
    code, _ = state.start_pairing()
    result = state.pair("client-12345678", "Phone", code)
    assert not result.ok
    assert result.reason == "declined"
    assert state.paired_devices == {}


def test_owner_approval_receives_the_requesting_device(state: HubState) -> None:
    seen: list[tuple[str, str]] = []
    state.approval_callback = lambda client_id, name: seen.append((client_id, name)) is None
    code, _ = state.start_pairing()
    assert state.pair("client-12345678", "  Phone  ", code).ok
    assert seen == [("client-12345678", "Phone")]


def test_non_ascii_token_is_rejected_not_a_crash(client: TestClient, token: str) -> None:
    # uvicorn decodes header bytes as latin-1, so a non-ASCII token reaches identify().
    response = client.get("/v1/shares", headers={"X-LanLink-Token": "t\xf6k\xe9n".encode("latin-1")})
    assert response.status_code == 401


def test_non_ascii_token_does_not_break_later_requests(
    client: TestClient, token: str, auth: dict[str, str]
) -> None:
    client.get("/v1/shares", headers={"X-LanLink-Token": "t\xf6k\xe9n".encode("latin-1")})
    assert client.get("/v1/shares", headers=auth).status_code == 200


def test_non_ascii_pair_code_is_rejected_not_a_crash(client: TestClient, state: HubState) -> None:
    state.start_pairing()
    response = client.post(
        "/v1/pair",
        json={"client_id": "client-12345678", "client_name": "Phone", "pair_code": "c\xf6de12"},
    )
    assert response.status_code in {403, 422}


def test_tokens_are_not_stored_in_plaintext(state: HubState, token: str) -> None:
    saved = state.settings_path.read_text(encoding="utf-8")
    assert token not in saved
    assert "token_hash" in saved


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses ACLs, not POSIX mode bits")
def test_settings_file_is_owner_only(state: HubState) -> None:
    mode = state.settings_path.stat().st_mode & 0o777
    assert mode == 0o600, f"settings must not be group/world readable, got {oct(mode)}"


def test_device_cannot_revoke_another_device(
    client: TestClient, state: HubState, pair_device
) -> None:
    token_a = pair_device("client-aaaaaaaa", "A")
    token_b = pair_device("client-bbbbbbbb", "B")

    denied = client.delete("/v1/pairings/client-aaaaaaaa", headers={"X-LanLink-Token": token_b})
    assert denied.status_code == 403
    assert state.authenticate(token_a) is True

    allowed = client.delete("/v1/pairings/client-bbbbbbbb", headers={"X-LanLink-Token": token_b})
    assert allowed.status_code == 200
    assert state.authenticate(token_b) is False


def test_owner_revocation_still_works_locally(state: HubState, token: str) -> None:
    assert state.authenticate(token) is True
    assert state.revoke("client-12345678") is True
    assert state.authenticate(token) is False


def test_unpaired_access_is_refused(client: TestClient) -> None:
    assert client.get("/v1/shares").status_code == 401
    assert client.get("/v1/shares", headers={"X-LanLink-Token": "wrong"}).status_code == 401


def test_pairing_survives_restart(state: HubState, token: str, tmp_path: Path) -> None:
    reloaded = HubState(tmp_path / "settings.json")
    assert reloaded.authenticate(token) is True
    assert reloaded.device_id == state.device_id
