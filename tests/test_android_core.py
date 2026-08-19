"""The Android client's protocol core, compiled and run against a real node.

The Kotlin in android/core/ touches no Android API, so it builds and runs on a
plain JVM here. That matters more than it sounds: the path rules, the resume
arithmetic and the certificate pinning are where a client corrupts files or
trusts the wrong peer, and none of it would otherwise be tested until somebody
had a phone in their hand.

The interop test drives that same Kotlin client against the Python node over a
real pinned TLS socket, which is the only thing that proves the two
implementations agree rather than each agreeing with itself.
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

from kotlin_env import find_toolchain  # noqa: E402

from lanlink.api import create_app  # noqa: E402
from lanlink.crypto import ensure_device_certificate  # noqa: E402
from lanlink.state import ALL_PERMISSIONS, HubState  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "android" / "core" / "src"
TESTS = REPO / "android" / "core" / "test"

TOOLCHAIN = find_toolchain()
needs_kotlin = pytest.mark.skipif(
    TOOLCHAIN is None,
    reason="No Kotlin toolchain. Install one with: pip install kotlin-jupyter-kernel",
)


@pytest.fixture(scope="module")
def compiled(tmp_path_factory) -> dict[str, Path]:
    """Compile the core and its suite once for the whole module."""
    assert TOOLCHAIN is not None
    build = tmp_path_factory.mktemp("kotlin")
    classes, test_classes = build / "core", build / "test"

    core_sources = sorted(CORE.glob("*.kt"))
    assert core_sources, "no Kotlin sources found"
    result = TOOLCHAIN.compile(core_sources, classes)
    assert result.returncode == 0, f"the core did not compile:\n{result.stderr}"

    result = TOOLCHAIN.compile(sorted(TESTS.glob("*.kt")), test_classes, extra_classpath=str(classes))
    assert result.returncode == 0, f"the suite did not compile:\n{result.stderr}"
    return {"core": classes, "test": test_classes}


def run_suite(compiled: dict[str, Path], args: list[str] | None = None):
    assert TOOLCHAIN is not None
    return TOOLCHAIN.run(
        "link.lan.core.test.MainKt",
        compiled["test"],
        extra_classpath=str(compiled["core"]),
        args=args or [],
    )


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
    deadline = time.monotonic() + 25
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("the interop node did not start")
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
    state = HubState(tmp_path / "settings.json")
    share = state.add_share(root, "Demo")
    state.set_share_permissions(share.id, ALL_PERMISSIONS)
    certificate = ensure_device_certificate(tmp_path, state.device_id, "Test PC", ["127.0.0.1"])
    state.certificate_fingerprint = certificate.fingerprint
    with running_node(state, certificate) as port:
        yield {"state": state, "port": port, "root": root, "share": share}


# ------------------------------------------------------------------- the core


@needs_kotlin
def test_the_kotlin_core_compiles(compiled) -> None:
    """A build failure here is a build failure in Android Studio too."""
    assert (compiled["core"] / "link" / "lan" / "core").is_dir()


@needs_kotlin
def test_the_kotlin_core_passes_its_own_suite(compiled) -> None:
    result = run_suite(compiled)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "kotlin checks passed" in result.stdout
    passed, total = _counts(result.stdout)
    assert passed == total and total >= 40, result.stdout


@needs_kotlin
def test_the_core_uses_no_android_api(compiled) -> None:
    """It must stay buildable on a plain JVM, or this test cannot run at all."""
    for source in sorted(CORE.glob("*.kt")):
        imports = [
            line.strip()
            for line in source.read_text(encoding="utf-8").splitlines()
            if line.startswith("import ")
        ]
        for line in imports:
            for forbidden in ("android.", "androidx.", "okhttp", "com.squareup", "kotlinx.serialization"):
                assert forbidden not in line, f"{source.name} depends on {forbidden}: {line}"
        # Everything it does import is either the Kotlin stdlib or the JDK.
        for line in imports:
            module = line.removeprefix("import ").split(".")[0]
            assert module in {"java", "javax", "kotlin", "kotlinx", "link"}, f"{source.name}: {line}"


def _counts(output: str) -> tuple[int, int]:
    line = next(item for item in output.splitlines() if "kotlin checks passed" in item)
    passed, total = line.split()[0].split("/")
    return int(passed), int(total)


# ----------------------------------------------------------------- the interop


@needs_kotlin
def test_the_kotlin_client_drives_a_real_node_over_pinned_tls(compiled, node) -> None:
    """Pair, browse, upload with resume, download with ranges, clean up, unpair."""
    code, _expires = node["state"].start_pairing()

    result = run_suite(compiled, ["--interop", "127.0.0.1", str(node["port"]), code])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "interop ::" in result.stdout
    passed, total = _counts(result.stdout)
    assert passed == total, result.stdout


@needs_kotlin
def test_the_interop_run_leaves_the_node_as_it_found_it(compiled, node) -> None:
    before = sorted(item.name for item in node["root"].iterdir())
    code, _expires = node["state"].start_pairing()

    run_suite(compiled, ["--interop", "127.0.0.1", str(node["port"]), code])

    assert sorted(item.name for item in node["root"].iterdir()) == before
    assert (node["root"] / "existing.txt").read_text(encoding="utf-8") == "do not touch me"
    assert node["state"].paired_devices == {}, "the Kotlin client left a pairing behind"


@needs_kotlin
def test_the_interop_run_fails_loudly_when_the_node_refuses_to_pair(compiled, node) -> None:
    """A green run must mean something: the wrong code has to fail it."""
    node["state"].start_pairing()

    result = run_suite(compiled, ["--interop", "127.0.0.1", str(node["port"]), "00000000"])

    assert result.returncode != 0
    assert "FAIL" in result.stdout
