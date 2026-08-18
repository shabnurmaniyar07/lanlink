"""Transfer engine: queue, progress, cancel, retry and history.

Everything here is UI-agnostic and thread-safe so the Phase 3 PySide6 window can
drive it from the GUI thread without ever blocking on network I/O.

Node-to-node transfers are *relayed*: the hub opens a streaming read on the
source and feeds those chunks straight into a streaming write on the
destination. No full second copy ever lands on the hub's disk.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .client import LanLinkClient

CHUNK_SIZE = 512 * 1024
_RATE_SMOOTHING = 0.3


class TransferStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransferCancelled(RuntimeError):
    pass


@dataclass
class Transfer:
    id: str
    kind: str
    filename: str
    source: str
    destination: str
    size: int | None = None
    transferred: int = 0
    status: TransferStatus = TransferStatus.QUEUED
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    rate: float = 0.0

    @property
    def progress(self) -> float:
        if not self.size:
            return 0.0
        return min(1.0, self.transferred / self.size)

    @property
    def eta_seconds(self) -> float | None:
        if not self.size or self.rate <= 0 or self.status is not TransferStatus.RUNNING:
            return None
        remaining = max(0, self.size - self.transferred)
        return remaining / self.rate

    @property
    def is_active(self) -> bool:
        return self.status in {TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.PAUSED}


@dataclass
class _Control:
    cancel: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=lambda: _set_event())
    runner: Callable[[Transfer, _Control], None] | None = None
    last_tick: float = 0.0


def _set_event() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


class TransferManager:
    """Bounded worker pool over a FIFO queue of transfers."""

    def __init__(self, workers: int = 2, on_change: Callable[[Transfer], None] | None = None) -> None:
        self._lock = threading.RLock()
        self._transfers: dict[str, Transfer] = {}
        self._controls: dict[str, _Control] = {}
        self._order: list[str] = []
        self._pending: list[str] = []
        self._wake = threading.Condition(self._lock)
        self._on_change = on_change
        self._stopping = False
        self._workers = [
            threading.Thread(target=self._work, name=f"lanlink-transfer-{index}", daemon=True)
            for index in range(max(1, workers))
        ]
        for worker in self._workers:
            worker.start()

    # ------------------------------------------------------------------ public

    def submit(
        self,
        *,
        kind: str,
        filename: str,
        source: str,
        destination: str,
        runner: Callable[[Transfer, _Control], None],
        size: int | None = None,
    ) -> Transfer:
        transfer = Transfer(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            filename=filename,
            source=source,
            destination=destination,
            size=size,
        )
        control = _Control(runner=runner)
        with self._wake:
            self._transfers[transfer.id] = transfer
            self._controls[transfer.id] = control
            self._order.append(transfer.id)
            self._pending.append(transfer.id)
            self._wake.notify()
        self._notify(transfer)
        return transfer

    def cancel(self, transfer_id: str) -> bool:
        with self._wake:
            transfer = self._transfers.get(transfer_id)
            control = self._controls.get(transfer_id)
            if not transfer or not control or not transfer.is_active:
                return False
            control.cancel.set()
            control.resume.set()  # a paused transfer must wake up to notice
            if transfer.status is TransferStatus.QUEUED:
                if transfer_id in self._pending:
                    self._pending.remove(transfer_id)
                self._finish(transfer, TransferStatus.CANCELLED)
        self._notify(self._transfers[transfer_id])
        return True

    def pause(self, transfer_id: str) -> bool:
        with self._lock:
            transfer = self._transfers.get(transfer_id)
            control = self._controls.get(transfer_id)
            if not transfer or not control or transfer.status is not TransferStatus.RUNNING:
                return False
            control.resume.clear()
            transfer.status = TransferStatus.PAUSED
        self._notify(transfer)
        return True

    def resume(self, transfer_id: str) -> bool:
        with self._lock:
            transfer = self._transfers.get(transfer_id)
            control = self._controls.get(transfer_id)
            if not transfer or not control or transfer.status is not TransferStatus.PAUSED:
                return False
            control.resume.set()
            transfer.status = TransferStatus.RUNNING
        self._notify(transfer)
        return True

    def pause_all(self) -> None:
        for transfer in self.snapshot():
            self.pause(transfer.id)

    def resume_all(self) -> None:
        for transfer in self.snapshot():
            self.resume(transfer.id)

    def retry(self, transfer_id: str) -> Transfer | None:
        """Re-queue a failed or cancelled transfer using the same runner."""
        with self._wake:
            transfer = self._transfers.get(transfer_id)
            control = self._controls.get(transfer_id)
            if not transfer or not control or transfer.is_active:
                return None
            transfer.status = TransferStatus.QUEUED
            transfer.transferred = 0
            transfer.rate = 0.0
            transfer.error = ""
            transfer.started_at = None
            transfer.finished_at = None
            control.cancel = threading.Event()
            control.resume = _set_event()
            self._pending.append(transfer_id)
            self._wake.notify()
        self._notify(transfer)
        return transfer

    def snapshot(self) -> list[Transfer]:
        with self._lock:
            return [self._transfers[key] for key in self._order if key in self._transfers]

    def active(self) -> list[Transfer]:
        return [transfer for transfer in self.snapshot() if transfer.is_active]

    def history(self) -> list[Transfer]:
        return [transfer for transfer in self.snapshot() if not transfer.is_active]

    def clear_history(self) -> None:
        with self._lock:
            for key in [key for key in self._order if not self._transfers[key].is_active]:
                self._order.remove(key)
                self._transfers.pop(key, None)
                self._controls.pop(key, None)

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._wake:
            self._stopping = True
            for control in self._controls.values():
                control.cancel.set()
                control.resume.set()
            self._wake.notify_all()
        for worker in self._workers:
            worker.join(timeout=timeout)

    # ----------------------------------------------------------------- workers

    def _work(self) -> None:
        while True:
            with self._wake:
                while not self._pending and not self._stopping:
                    self._wake.wait(0.2)
                if self._stopping:
                    return
                transfer_id = self._pending.pop(0)
                transfer = self._transfers[transfer_id]
                control = self._controls[transfer_id]
                transfer.status = TransferStatus.RUNNING
                transfer.started_at = time.time()
                control.last_tick = time.monotonic()
            self._notify(transfer)

            try:
                if control.runner is None:
                    raise RuntimeError("This transfer has no runner.")
                control.runner(transfer, control)
            except TransferCancelled:
                with self._lock:
                    self._finish(transfer, TransferStatus.CANCELLED)
            except Exception as error:  # noqa: BLE001 - surfaced to the user verbatim
                with self._lock:
                    transfer.error = str(error) or error.__class__.__name__
                    self._finish(transfer, TransferStatus.FAILED)
            else:
                with self._lock:
                    if control.cancel.is_set():
                        self._finish(transfer, TransferStatus.CANCELLED)
                    else:
                        if transfer.size is None:
                            transfer.size = transfer.transferred
                        self._finish(transfer, TransferStatus.COMPLETED)
            self._notify(transfer)

    def _finish(self, transfer: Transfer, status: TransferStatus) -> None:
        transfer.status = status
        transfer.finished_at = time.time()
        transfer.rate = 0.0

    def _notify(self, transfer: Transfer) -> None:
        if self._on_change:
            self._on_change(transfer)

    # ------------------------------------------------------------- progress io

    def advance(self, transfer: Transfer, control: _Control, count: int) -> None:
        """Record progress, honour pause, and abort promptly on cancel."""
        control.resume.wait()
        if control.cancel.is_set():
            raise TransferCancelled
        now = time.monotonic()
        with self._lock:
            transfer.transferred += count
            elapsed = now - control.last_tick
            if elapsed >= 0.25:
                instant = count / elapsed if elapsed else 0.0
                transfer.rate = (
                    instant
                    if transfer.rate <= 0
                    else (1 - _RATE_SMOOTHING) * transfer.rate + _RATE_SMOOTHING * instant
                )
                control.last_tick = now
        self._notify(transfer)


# --------------------------------------------------------------------- runners


def download_runner(
    manager: TransferManager, client: LanLinkClient, share_id: str, path: str, destination: Path
) -> Callable[[Transfer, _Control], None]:
    def run(transfer: Transfer, control: _Control) -> None:
        created = False
        try:
            with client.open_stream(share_id, path) as response:
                length = response.headers.get("content-length")
                if length and transfer.size is None:
                    transfer.size = int(length)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    created = True
                    for chunk in response.iter_bytes(CHUNK_SIZE):
                        output.write(chunk)
                        manager.advance(transfer, control, len(chunk))
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise

    return run


def upload_runner(
    manager: TransferManager, client: LanLinkClient, share_id: str, folder: str, source: Path
) -> Callable[[Transfer, _Control], None]:
    def run(transfer: Transfer, control: _Control) -> None:
        transfer.size = source.stat().st_size

        def chunks() -> Iterator[bytes]:
            with source.open("rb") as handle:
                while block := handle.read(CHUNK_SIZE):
                    manager.advance(transfer, control, len(block))
                    yield block

        client.put_stream(share_id, folder, source.name, chunks())

    return run


def relay_runner(
    manager: TransferManager,
    source_client: LanLinkClient,
    source_share_id: str,
    source_path: str,
    destination_client: LanLinkClient,
    destination_share_id: str,
    destination_folder: str,
    name: str,
    delete_source: bool = False,
) -> Callable[[Transfer, _Control], None]:
    """Stream node A -> hub -> node B without buffering the file on the hub."""

    def run(transfer: Transfer, control: _Control) -> None:
        with source_client.open_stream(source_share_id, source_path) as response:
            length = response.headers.get("content-length")
            if length:
                transfer.size = int(length)

            def chunks() -> Iterator[bytes]:
                for chunk in response.iter_bytes(CHUNK_SIZE):
                    manager.advance(transfer, control, len(chunk))
                    yield chunk

            result = destination_client.put_stream(
                destination_share_id, destination_folder, name, chunks()
            )

        # Verify before touching the source. A move never deletes first.
        if transfer.size is not None and result.get("bytes") != transfer.size:
            raise RuntimeError("The transfer could not be verified; the source was left untouched.")
        if delete_source:
            source_client.delete(source_share_id, source_path)

    return run
