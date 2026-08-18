"""Pairing must work whichever side enables pairing first.

Before this, only one order worked: the receiver had to arm *before* the other
side asked. Asking first failed instantly with "not in pairing mode".
"""

from __future__ import annotations

import socket
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
from lanlink.pairing_request import (
    TIMED_OUT_MESSAGE,
    PairingRequest,
    is_not_armed,
    is_not_listening,
    is_retryable,
)
from lanlink.state import HubState

# ------------------------------------------------------------------ fake clock


class FakeClock:
    """Lets the bounded wait be exercised without actually waiting."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []
        self.cancel_after: int | None = None

    def __call__(self) -> float:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.now += seconds
        # Returning True signals "cancelled", the way Event.wait does.
        return self.cancel_after is not None and len(self.waits) >= self.cancel_after


def http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://192.168.1.9:8765/v1/pair")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# ------------------------------------------------------------ error classifying


def test_409_means_the_peer_has_not_armed_yet() -> None:
    assert is_not_armed(http_error(409)) is True
    assert is_not_armed(http_error(403)) is False
    assert is_not_armed(http_error(429)) is False


def test_a_closed_port_is_worth_retrying() -> None:
    assert is_not_listening(httpx.ConnectError("Connection refused")) is True
    assert is_not_listening(ConnectionError("no route")) is True


def test_a_certificate_problem_is_never_retried() -> None:
    """A pin that stopped matching must surface, not spin."""
    assert is_not_listening(httpx.ConnectError("certificate verify failed")) is False
    assert is_not_listening(httpx.ConnectError("SSL handshake failed")) is False
    assert is_retryable(httpx.ConnectError("certificate verify failed")) is False


@pytest.mark.parametrize("status", [400, 403, 422, 429, 500])
def test_only_409_is_retried(status: int) -> None:
    assert is_retryable(http_error(status)) is False


# ---------------------------------------------------- A: receiver arms first


def test_a_receiver_armed_first_pairs_on_the_first_attempt() -> None:
    request = PairingRequest(lambda: {"token": "t"}, clock=FakeClock())
    outcome = request.run()

    assert outcome.ok is True
    assert outcome.reason == "paired"
    assert outcome.result == {"token": "t"}
    assert request.attempts == 1, "an armed peer must not be polled"


# ------------------------------------- B: initiator first, receiver arms later


def test_b_initiator_first_then_receiver_arms() -> None:
    clock = FakeClock()
    calls = {"n": 0}

    def attempt() -> dict:
        calls["n"] += 1
        if calls["n"] < 4:
            raise http_error(409)  # peer has not pressed "Allow" yet
        return {"token": "t", "device": {"id": "dev-b", "name": "LAPTOP-B"}}

    request = PairingRequest(attempt, timeout=120, interval=2, waiter=clock.wait, clock=clock)
    outcome = request.run()

    assert outcome.ok is True
    assert outcome.result is not None
    assert outcome.result["device"]["name"] == "LAPTOP-B"
    assert calls["n"] == 4, "the request should have kept waiting and retried"
    assert clock.waits == [2, 2, 2]


def test_b_also_waits_for_a_device_that_is_not_running_yet() -> None:
    clock = FakeClock()
    calls = {"n": 0}

    def attempt() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("Connection refused")
        if calls["n"] == 2:
            raise http_error(409)
        return {"token": "t"}

    outcome = PairingRequest(attempt, waiter=clock.wait, clock=clock).run()
    assert outcome.ok is True
    assert calls["n"] == 3


# ------------------------------------------------ C: receiver never arms


def test_c_times_out_with_a_clear_message() -> None:
    clock = FakeClock()

    def attempt() -> dict:
        raise http_error(409)

    request = PairingRequest(attempt, timeout=120, interval=2, waiter=clock.wait, clock=clock)
    outcome = request.run()

    assert outcome.ok is False
    assert outcome.reason == "timed_out"
    assert outcome.message == TIMED_OUT_MESSAGE
    assert "enable pairing" in outcome.message.lower()
    assert clock.now == pytest.approx(120), "the wait must be bounded, not endless"
    assert request.finished is True


def test_c_the_wait_never_overshoots_the_deadline() -> None:
    clock = FakeClock()

    def attempt() -> dict:
        raise http_error(409)

    PairingRequest(attempt, timeout=5, interval=2, waiter=clock.wait, clock=clock).run()
    assert sum(clock.waits) == pytest.approx(5)
    assert clock.waits[-1] == pytest.approx(1), "the final wait is trimmed to the deadline"


# ------------------------------------------------------- D: cancelling cleanly


def test_d_cancel_leaves_no_pending_state() -> None:
    clock = FakeClock()
    clock.cancel_after = 2

    def attempt() -> dict:
        raise http_error(409)

    request = PairingRequest(attempt, waiter=clock.wait, clock=clock)
    outcome = request.run()

    assert outcome.ok is False
    assert outcome.reason == "cancelled"
    assert request.finished is True
    assert request.waiting_for_peer is False


def test_d_cancelling_before_it_starts_makes_no_request() -> None:
    calls = {"n": 0}

    def attempt() -> dict:
        calls["n"] += 1
        return {}

    request = PairingRequest(attempt)
    request.cancel()
    outcome = request.run()

    assert outcome.reason == "cancelled"
    assert calls["n"] == 0, "a cancelled request must not reach the network"


def test_d_cancel_interrupts_a_real_wait_promptly() -> None:
    """Cancel must not be one poll interval away."""

    def attempt() -> dict:
        raise http_error(409)

    request = PairingRequest(attempt, timeout=30, interval=10)
    thread = threading.Thread(target=request.run)
    started = time.monotonic()
    thread.start()
    time.sleep(0.1)
    request.cancel()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert time.monotonic() - started < 3, "cancel should not wait out the poll interval"


# --------------------------------------------- a wrong code is never retried


def test_a_wrong_code_fails_immediately_and_is_not_retried() -> None:
    """Retrying a wrong code would burn the attempt budget and trip the lockout."""
    clock = FakeClock()
    calls = {"n": 0}

    def attempt() -> dict:
        calls["n"] += 1
        raise http_error(403)

    outcome = PairingRequest(attempt, waiter=clock.wait, clock=clock).run()

    assert outcome.ok is False
    assert outcome.reason == "failed"
    assert calls["n"] == 1
    assert clock.waits == []


def test_rate_limiting_is_not_retried_around() -> None:
    calls = {"n": 0}

    def attempt() -> dict:
        calls["n"] += 1
        raise http_error(429)

    assert PairingRequest(attempt).run().reason == "failed"
    assert calls["n"] == 1


# --------------------------------------------------- E + real end-to-end proof


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def run_node(state: HubState) -> Iterator[str]:
    port = free_port()
    config = uvicorn.Config(create_app(state), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("test node did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_initiator_first_against_a_real_node(tmp_path: Path) -> None:
    """The reported bug, end to end: ask first, arm afterwards."""
    receiver = HubState(tmp_path / "receiver.json")
    receiver.device_name = "LAPTOP-B"
    approvals: list[str] = []
    receiver.approval_callback = lambda client_id, name: approvals.append(name) is None

    with run_node(receiver) as url:
        client = LanLinkClient(url)
        codes: dict[str, str] = {}

        def attempt() -> dict:
            # The code only exists once the other side arms; read it each try.
            return client.pair("LAPTOP-A", codes.get("value", "00000000"), client_id="client-aaaaaaaa")

        request = PairingRequest(attempt, timeout=20, interval=0.2)
        outcome: list = []
        thread = threading.Thread(target=lambda: outcome.append(request.run()))
        thread.start()

        # The receiver presses "Allow a device to pair" only now.
        time.sleep(0.5)
        assert request.waiting_for_peer is True, "it should be waiting, not failed"
        code, _ = receiver.start_pairing()
        codes["value"] = code

        thread.join(timeout=25)
        client.close()

    assert outcome and outcome[0].ok is True, outcome[0].message if outcome else "no outcome"
    assert approvals == ["LAPTOP-A"], "the owner still had to approve"
    # E: a successful pairing still consumes the code and disarms.
    assert receiver.pairing_armed is False
    assert receiver.pairing_code() is None
    assert len(receiver.paired_devices) == 1


def test_receiver_first_still_works_against_a_real_node(tmp_path: Path) -> None:
    """The order that already worked must keep working."""
    receiver = HubState(tmp_path / "receiver.json")
    with run_node(receiver) as url:
        code, _ = receiver.start_pairing()
        client = LanLinkClient(url)
        request = PairingRequest(
            lambda: client.pair("LAPTOP-A", code, client_id="client-bbbbbbbb"),
            timeout=20,
            interval=0.2,
        )
        outcome = request.run()
        client.close()

    assert outcome.ok is True
    assert request.attempts == 1
    assert receiver.pairing_armed is False


def test_a_wrong_code_against_a_real_node_does_not_spin(tmp_path: Path) -> None:
    receiver = HubState(tmp_path / "receiver.json")
    with run_node(receiver) as url:
        receiver.start_pairing()
        client = LanLinkClient(url)
        request = PairingRequest(
            lambda: client.pair("LAPTOP-A", "00000000", client_id="client-cccccccc"),
            timeout=20,
            interval=0.2,
        )
        outcome = request.run()
        client.close()

    assert outcome.ok is False
    assert request.attempts == 1, "a wrong code must not be retried"
    assert receiver.paired_devices == {}


def test_a_declined_request_is_not_retried(tmp_path: Path) -> None:
    receiver = HubState(tmp_path / "receiver.json")
    receiver.approval_callback = lambda client_id, name: False  # owner says no

    with run_node(receiver) as url:
        code, _ = receiver.start_pairing()
        client = LanLinkClient(url)
        request = PairingRequest(
            lambda: client.pair("LAPTOP-A", code, client_id="client-dddddddd"),
            timeout=20,
            interval=0.2,
        )
        outcome = request.run()
        client.close()

    assert outcome.ok is False
    assert request.attempts == 1, "a refusal by the owner is final"
    assert receiver.paired_devices == {}
