"""A windowed .exe has no stdout. Nothing LanLink starts may assume otherwise.

LanLink 0.1.0 died on launch with

    AttributeError: 'NoneType' object has no attribute 'isatty'

because uvicorn's default logging configuration asks ``sys.stdout`` whether it
is a terminal, and PyInstaller's windowed build sets ``sys.stdout`` to None.
The window never appeared. These tests take stdout away and check that the
service still starts.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from lanlink.server import LocalService
from lanlink.state import HubState

LAUNCHER = Path(__file__).resolve().parents[1] / "packaging" / "launch.py"


@pytest.fixture
def without_stdout(monkeypatch: pytest.MonkeyPatch):
    """Exactly what PyInstaller hands a windowed application."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)


def test_the_service_starts_when_there_is_no_stdout(tmp_path: Path, without_stdout: None) -> None:
    state = HubState(tmp_path / "settings.json")
    state.use_tls = False  # a certificate is not what is under test here
    service = LocalService(state, port=8971, host="127.0.0.1")
    try:
        assert service.start(), service.last_error
    finally:
        service.stop()


def test_the_launcher_replaces_the_missing_streams(without_stdout: None) -> None:
    """The launcher's guard runs before anything else can trip over None."""
    module = runpy.run_path(str(LAUNCHER))
    module["give_the_streams_somewhere_to_go"]()

    for stream in (sys.stdout, sys.stderr):
        assert stream is not None
        stream.write("this must not raise")
        assert stream.isatty() is False


def test_uvicorn_is_never_allowed_to_configure_logging() -> None:
    """The fix is the absence of a default; say so where it can be checked."""
    source = (Path(__file__).resolve().parents[1] / "src" / "lanlink" / "server.py").read_text()
    assert "log_config=None" in source


# ------------------------------------------------------------------ diagnostics


def test_a_run_leaves_a_log_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 0.1.0 crash was invisible because nothing was written down."""
    import logging

    from lanlink import logs

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(logs, "_started", False)
    monkeypatch.setattr(logging.getLogger(), "handlers", [])

    path = logs.start_logging()
    assert path is not None and path.parent.is_dir()

    logs.describe_environment("9.9.9")
    logging.getLogger().handlers[0].flush()
    written = path.read_text(encoding="utf-8")
    assert "LanLink 9.9.9 starting" in written


def test_an_unhandled_exception_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    from lanlink import logs

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(logs, "_started", False)
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    seen: list[str] = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append("passed on"))

    path = logs.start_logging()
    logs.install_excepthook()
    assert path is not None

    try:
        raise RuntimeError("a window that never opened")
    except RuntimeError as error:
        sys.excepthook(type(error), error, error.__traceback__)

    logging.getLogger().handlers[0].flush()
    written = path.read_text(encoding="utf-8")
    assert "a window that never opened" in written
    assert seen == ["passed on"], "the previous hook still runs"


def test_the_log_never_lands_somewhere_unwritable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed .exe lives in Program Files. The log must not follow it."""
    from lanlink import logs

    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")
    assert "AppData" in str(logs.log_folder())
    assert logs.log_folder().name == "logs"
