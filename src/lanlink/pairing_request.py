"""Keep a pairing attempt alive while the other device has not armed yet.

Pairing used to work in one order only: the receiving device had to press
"Allow a device to pair" *before* the other side asked. Asking first failed
immediately with "not in pairing mode".

The service side is right to refuse — it must never pair without the owner
arming it. So this is the initiator's problem: instead of giving up on the first
refusal, hold the request open for a bounded window and retry, so either order
works.

Retrying while the peer is disarmed is safe: ``HubState.pair`` checks the armed
flag *before* the rate limiter, so a waiting request consumes no attempt budget
and cannot help a brute-force attempt. A wrong code is never retried.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

PAIRING_WAIT_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 2.0

TIMED_OUT_MESSAGE = (
    "Pairing timed out — enable pairing on the other device and try again."
)


def is_not_armed(error: BaseException) -> bool:
    """True when the peer refused *only* because pairing mode is off there."""
    return isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 409


def is_not_listening(error: BaseException) -> bool:
    """True for 'that device is not answering yet' — but never for a bad certificate.

    A certificate that stopped matching must surface immediately; quietly
    retrying it would turn a possible impersonation into a spinner.
    """
    if not isinstance(error, httpx.ConnectError | httpx.ConnectTimeout | ConnectionError | TimeoutError):
        return False
    text = str(error).lower()
    return not any(word in text for word in ("certificate", "ssl", "verify", "handshake"))


def is_retryable(error: BaseException) -> bool:
    return is_not_armed(error) or is_not_listening(error)


@dataclass
class PairingOutcome:
    ok: bool
    reason: str
    result: dict | None = None
    message: str = ""


class PairingRequest:
    """One pending pairing attempt, retried until armed, cancelled or timed out."""

    def __init__(
        self,
        attempt: Callable[[], dict],
        timeout: float = PAIRING_WAIT_SECONDS,
        interval: float = POLL_INTERVAL_SECONDS,
        waiter: Callable[[float], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._attempt = attempt
        self._timeout = timeout
        self._interval = interval
        self._clock = clock
        self._cancelled = threading.Event()
        # Waiting on the cancel event means Cancel is instant, not one poll away.
        self._wait = waiter if waiter is not None else self._cancelled.wait
        self.attempts = 0
        self.waiting_for_peer = False
        self.finished = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> PairingOutcome:
        deadline = self._clock() + self._timeout
        try:
            while True:
                if self._cancelled.is_set():
                    return PairingOutcome(False, "cancelled", message="Pairing cancelled.")

                self.attempts += 1
                try:
                    result = self._attempt()
                except Exception as error:  # noqa: BLE001 - classified below
                    if not is_retryable(error):
                        # Wrong code, declined, rate limited, bad certificate:
                        # all final. Retrying would burn the attempt budget.
                        return PairingOutcome(False, "failed", message=str(error))
                    self.waiting_for_peer = True
                else:
                    return PairingOutcome(True, "paired", result=result)

                remaining = deadline - self._clock()
                if remaining <= 0:
                    return PairingOutcome(False, "timed_out", message=TIMED_OUT_MESSAGE)
                if self._wait(min(self._interval, remaining)):
                    return PairingOutcome(False, "cancelled", message="Pairing cancelled.")
        finally:
            self.finished = True
            self.waiting_for_peer = False
