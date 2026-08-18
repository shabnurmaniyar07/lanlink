"""Local-owner approval for an incoming pairing request.

The API service calls ``request()`` on a uvicorn worker thread. Qt dialogs may
only be built on the GUI thread, so instead of calling into Qt from there we
park the request and block; the window's timer picks it up, asks the owner, and
releases the waiter with the answer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

APPROVAL_TIMEOUT_SECONDS = 60.0


@dataclass
class PairingRequest:
    client_id: str
    client_name: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False

    def answer(self, approved: bool) -> None:
        self.approved = approved
        self.event.set()


class PairingApproval:
    def __init__(self, timeout: float = APPROVAL_TIMEOUT_SECONDS) -> None:
        self._lock = threading.Lock()
        self._pending: PairingRequest | None = None
        self._timeout = timeout

    def request(self, client_id: str, client_name: str) -> bool:
        """Called on the API thread. Blocks until the owner answers or time runs out."""
        pending = PairingRequest(client_id=client_id, client_name=client_name)
        with self._lock:
            if self._pending is not None:
                return False  # one request at a time
            self._pending = pending
        try:
            if not pending.event.wait(self._timeout):
                return False
            return pending.approved
        finally:
            with self._lock:
                if self._pending is pending:
                    self._pending = None

    def take(self) -> PairingRequest | None:
        """Called on the GUI thread. Returns a request awaiting an answer."""
        with self._lock:
            pending = self._pending
        if pending is None or pending.event.is_set():
            return None
        return pending
