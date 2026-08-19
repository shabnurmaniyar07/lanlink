"""Off-thread job plumbing.

Every network call in the UI goes through here. Nothing that touches the network
may run on the Qt main thread, or the window freezes for the length of the
request — that was the single worst defect in the pre-Phase-3 window.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class JobSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    """Run one callable on a worker thread and report back to the GUI thread.

    The signals object is created on the calling (GUI) thread, so Qt queues the
    emissions across the thread boundary automatically.
    """

    def __init__(self, work: Callable[[], Any]) -> None:
        super().__init__()
        self._work = work
        self.signals = JobSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self._work()
        except Exception as error:  # noqa: BLE001 - reported to the user verbatim
            self.signals.failed.emit(str(error) or error.__class__.__name__)
        else:
            self.signals.finished.emit(result)


class JobRunner:
    """Thin wrapper over a QThreadPool with a friendlier call signature."""

    def __init__(self, max_threads: int = 6) -> None:
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max_threads)
        # Jobs still in flight. Callers routinely discard the return value, and
        # a Job whose last Python reference goes away takes its signals object
        # with it — Qt then drops the queued emission and the callback never
        # runs. Holding them here until they report back is what makes the
        # result arrive at all.
        self._running: set[Job] = set()

    def run(
        self,
        work: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> Job:
        job = Job(work)
        if on_success is not None:
            job.signals.finished.connect(on_success)
        if on_error is not None:
            job.signals.failed.connect(on_error)
        self._running.add(job)
        job.signals.finished.connect(lambda _result, item=job: self._running.discard(item))
        job.signals.failed.connect(lambda _message, item=job: self._running.discard(item))
        self.pool.start(job)
        return job

    def pending(self) -> int:
        """Jobs that have not reported back yet."""
        return len(self._running)

    def wait(self, milliseconds: int = 3000) -> bool:
        return bool(self.pool.waitForDone(milliseconds))
