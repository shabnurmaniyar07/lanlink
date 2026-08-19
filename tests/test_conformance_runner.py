"""Run tools/conformance.py against a real TLS node on a real socket.

The rest of the protocol suite talks to the app in-process. This one starts
uvicorn with the device's own self-signed certificate, pins it, and drives the
whole documented exchange over the network stack — which is the path an Android
client will take, and the only way to catch something that only breaks once
there is a socket in the middle.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import conformance  # noqa: E402

from lanlink.api import create_app  # noqa: E402
from lanlink.crypto import ensure_device_certificate  # noqa: E402
from lanlink.state import ALL_PERMISSIONS, HubState  # noqa: E402


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def running_node(state: HubState, certificate) -> Iterator[int]:
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
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("the conformance test node did not start")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def node(tmp_path: Path):
    root = tmp_path / "shared"
    root.mkdir()
    (root / "existing.txt").write_text("do not touch me", encoding="utf-8")
    (root / "Folder").mkdir()

    state = HubState(tmp_path / "settings.json")
    share = state.add_share(root, "Demo")
    state.set_share_permissions(share.id, ALL_PERMISSIONS)
    certificate = ensure_device_certificate(tmp_path, state.device_id, "Test PC", ["127.0.0.1"])
    state.certificate_fingerprint = certificate.fingerprint

    with running_node(state, certificate) as port:
        yield {"state": state, "port": port, "root": root, "share": share}


def test_a_real_node_passes_every_conformance_check(node) -> None:
    code, _expires = node["state"].start_pairing()

    report = conformance.run(
        host="127.0.0.1", port=node["port"], code=code, share_name="Demo", insecure=False
    )

    assert not report.failures, report.render()
    assert len(report.results) == len(conformance.PLAN)


def test_the_run_cleans_up_after_itself(node) -> None:
    """Nothing the user already had may be touched, and no litter left behind."""
    code, _expires = node["state"].start_pairing()
    before = sorted(item.name for item in node["root"].iterdir())

    conformance.run(host="127.0.0.1", port=node["port"], code=code, share_name="Demo", insecure=False)

    assert sorted(item.name for item in node["root"].iterdir()) == before
    assert (node["root"] / "existing.txt").read_text(encoding="utf-8") == "do not touch me"


def test_the_runner_unpairs_itself(node) -> None:
    code, _expires = node["state"].start_pairing()
    conformance.run(host="127.0.0.1", port=node["port"], code=code, share_name="Demo", insecure=False)
    assert node["state"].paired_devices == {}, "the runner left a pairing behind"


def test_a_wrong_pairing_code_stops_the_run(node) -> None:
    node["state"].start_pairing()
    with pytest.raises(SystemExit) as failure:
        conformance.run(
            host="127.0.0.1", port=node["port"], code="00000000", share_name="Demo", insecure=False
        )
    assert "Pairing failed" in str(failure.value)


def test_a_read_only_share_is_reported_as_unusable(node) -> None:
    node["state"].set_share_permissions(node["share"].id, "r")
    node["state"].start_pairing()
    code, _ = node["state"].pairing_code() or ("", 0)
    with pytest.raises(SystemExit) as failure:
        conformance.run(host="127.0.0.1", port=node["port"], code=code, share_name=None, insecure=False)
    assert "read + write + delete" in str(failure.value)


def test_the_pin_is_the_certificate_the_node_serves(node) -> None:
    pem = conformance.peer_certificate("127.0.0.1", node["port"])
    assert conformance.fingerprint_of(pem) == node["state"].certificate_fingerprint


def test_an_unpinned_client_cannot_reach_the_node(node) -> None:
    """Proof the checks below run over real TLS, not a permissive stub."""
    import httpx

    with pytest.raises(httpx.ConnectError), httpx.Client(verify=True) as client:
        client.get(f"https://127.0.0.1:{node['port']}/health")


def test_the_report_names_the_check_that_failed(node, monkeypatch) -> None:
    """A failing node must produce something a developer can act on."""

    def broken(self) -> None:
        conformance.expect(False, "the node returned a purple elephant")

    monkeypatch.setattr(conformance.Conformance, "check_listing", broken)
    code, _expires = node["state"].start_pairing()

    report = conformance.run(
        host="127.0.0.1", port=node["port"], code=code, share_name="Demo", insecure=False
    )

    assert len(report.failures) == 1
    failure = report.failures[0]
    assert failure.name == "entries are well formed and folders sort first"
    assert "purple elephant" in failure.detail
    assert "FAIL" in report.render()
