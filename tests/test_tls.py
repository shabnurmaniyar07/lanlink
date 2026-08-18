"""Phase 4: device certificates, fingerprint pinning and credential protection."""

from __future__ import annotations

import socket
import ssl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from lanlink.api import create_app
from lanlink.client import LanLinkClient
from lanlink.crypto import (
    ensure_device_certificate,
    fetch_peer_certificate,
    fingerprint_of_pem,
    pinned_ssl_context,
    protect_secret,
    short_fingerprint,
    unprotect_secret,
)
from lanlink.state import ALL_PERMISSIONS, HubState


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def tls_node(state: HubState, certificate) -> Iterator[str]:
    port = free_port()
    config = uvicorn.Config(
        create_app(state),
        host="127.0.0.1",
        port=port,
        log_level="error",
        ssl_certfile=str(certificate.certificate_path),
        ssl_keyfile=str(certificate.key_path),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("TLS test node did not start")
    try:
        yield f"https://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def tls_state(tmp_path: Path):
    root = tmp_path / "shared"
    root.mkdir()
    state = HubState(tmp_path / "settings.json")
    share = state.add_share(root, "Demo")
    state.set_share_permissions(share.id, ALL_PERMISSIONS)
    (root / "secret.txt").write_text("classified", encoding="utf-8")
    certificate = ensure_device_certificate(tmp_path, state.device_id, "Test PC", ["127.0.0.1"])
    state.certificate_fingerprint = certificate.fingerprint
    return state, share.id, certificate, root


def token_for(state: HubState) -> str:
    code, _ = state.start_pairing()
    result = state.pair("client-tls00001", "Peer", code, source="127.0.0.1")
    assert result.ok and result.token
    return result.token


# ---------------------------------------------------------------- certificates


def test_certificate_is_generated_once_and_reused(tmp_path: Path) -> None:
    first = ensure_device_certificate(tmp_path, "device-1", "PC")
    second = ensure_device_certificate(tmp_path, "device-1", "PC")
    assert first.fingerprint == second.fingerprint
    assert first.certificate_path.exists() and first.key_path.exists()


def test_two_devices_get_different_identities(tmp_path: Path) -> None:
    one = ensure_device_certificate(tmp_path / "a", "device-1", "A")
    two = ensure_device_certificate(tmp_path / "b", "device-2", "B")
    assert one.fingerprint != two.fingerprint


def test_private_key_is_owner_only(tmp_path: Path) -> None:
    import sys

    certificate = ensure_device_certificate(tmp_path, "device-1", "PC")
    if sys.platform != "win32":
        assert certificate.key_path.stat().st_mode & 0o777 == 0o600


def test_fingerprint_is_readable_for_a_person(tmp_path: Path) -> None:
    certificate = ensure_device_certificate(tmp_path, "device-1", "PC")
    assert len(certificate.fingerprint) == 64
    assert certificate.short_fingerprint == short_fingerprint(certificate.fingerprint)
    assert certificate.short_fingerprint.count(" ") == 3


# ------------------------------------------------------------------- pinning


def test_pinned_client_reaches_the_node(tls_state) -> None:
    state, share_id, certificate, _root = tls_state
    token = token_for(state)
    with tls_node(state, certificate) as url:
        client = LanLinkClient(url, token=token, peer_certificate=certificate.pem)
        try:
            assert client.shares()[0]["name"] == "Demo"
        finally:
            client.close()


def test_traffic_is_actually_encrypted(tls_state) -> None:
    state, share_id, certificate, _root = tls_state
    token_for(state)
    with tls_node(state, certificate) as url:
        host, port = url.removeprefix("https://").split(":")
        # A plain-HTTP request to a TLS port must not succeed.
        with pytest.raises(Exception):  # noqa: B017 - any transport failure proves the point
            httpx.get(f"http://{host}:{port}/health", timeout=5)


def test_wrong_pin_is_refused(tls_state, tmp_path: Path) -> None:
    state, share_id, certificate, _root = tls_state
    token = token_for(state)
    impostor = ensure_device_certificate(tmp_path / "impostor", "device-evil", "Evil")

    with tls_node(state, certificate) as url:
        client = LanLinkClient(url, token=token, peer_certificate=impostor.pem)
        try:
            with pytest.raises(Exception) as caught:  # noqa: PT011
                client.shares()
            assert "certificate" in str(caught.value).lower() or "ssl" in str(caught.value).lower()
        finally:
            client.close()


def test_unpinned_client_cannot_verify_a_self_signed_node(tls_state) -> None:
    state, share_id, certificate, _root = tls_state
    token = token_for(state)
    with tls_node(state, certificate) as url:
        # verify=True with no pin: the system trust store does not know this cert.
        plain = httpx.Client(verify=True, timeout=5)
        try:
            with pytest.raises(Exception):  # noqa: B017 - any verification failure is the point
                plain.get(f"{url}/health", headers={"X-LanLink-Token": token})
        finally:
            plain.close()


def test_fetch_peer_certificate_matches_what_is_served(tls_state) -> None:
    state, share_id, certificate, _root = tls_state
    with tls_node(state, certificate) as url:
        host, port = url.removeprefix("https://").split(":")
        fetched = fetch_peer_certificate(host, int(port))
    assert fingerprint_of_pem(fetched) == certificate.fingerprint


def test_pinned_context_rejects_everything_else(tmp_path: Path) -> None:
    certificate = ensure_device_certificate(tmp_path, "device-1", "PC")
    context = pinned_ssl_context(certificate.pem)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is False
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_pairing_stores_the_pin(tls_state, tmp_path: Path) -> None:
    state, share_id, certificate, _root = tls_state
    peer = HubState(tmp_path / "peer.json")

    with tls_node(state, certificate) as url:
        host, port = url.removeprefix("https://").split(":")
        fetched = fetch_peer_certificate(host, int(port))
        code, _ = state.start_pairing()
        client = LanLinkClient(url, peer_certificate=fetched)
        try:
            result = client.pair("Peer", code, client_id=peer.device_id)
        finally:
            client.close()

    saved = peer.upsert_remote_device(
        result["device"]["id"], "Node", url, result["token"], fetched, fingerprint_of_pem(fetched)
    )
    assert saved.fingerprint == certificate.fingerprint

    reloaded = HubState(tmp_path / "peer.json")
    assert reloaded.remote_devices[saved.id].fingerprint == certificate.fingerprint
    assert reloaded.remote_devices[saved.id].certificate == fetched


def test_advertised_fingerprint_matches_the_served_certificate(tls_state) -> None:
    state, _share_id, certificate, _root = tls_state
    assert state.public_device()["fingerprint"] == certificate.fingerprint


# ------------------------------------------------------------ credential storage


def test_secret_round_trip() -> None:
    token = "a-very-secret-token"
    sealed = protect_secret(token)
    assert unprotect_secret(sealed) == token


def test_empty_secret_is_left_alone() -> None:
    assert protect_secret("") == ""
    assert unprotect_secret("") == ""


def test_unprotect_tolerates_plain_values() -> None:
    # POSIX stores plaintext behind 0600; reading it back must not corrupt it.
    assert unprotect_secret("plain-token") == "plain-token"


def test_remote_tokens_survive_a_restart(tmp_path: Path) -> None:
    state = HubState(tmp_path / "settings.json")
    state.upsert_remote_device("dev-1", "Office PC", "https://192.168.1.20:8765", "token-abc")
    reloaded = HubState(tmp_path / "settings.json")
    assert reloaded.remote_devices["dev-1"].token == "token-abc"
