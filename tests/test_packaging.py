"""The packaging files, checked for the things that silently break a build.

A Windows executable cannot be produced on Linux, so this does not prove the
installer works. It does prove the spec is valid Python, that the version in the
Windows resource matches the application, that the excludes really do keep a
browser engine out, and that the pieces refer to each other by the right names —
which is where these files usually go wrong.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGING = REPO / "packaging"
SPEC = PACKAGING / "lanlink.spec"
INNO = PACKAGING / "lanlink.iss"
BUILD = PACKAGING / "build.bat"
LAUNCH = PACKAGING / "launch.py"


def test_every_packaging_file_is_present() -> None:
    for path in (SPEC, INNO, BUILD, LAUNCH, PACKAGING / "version_info.txt", PACKAGING / "lanlink.ico"):
        assert path.is_file(), f"{path.name} is missing"


def test_the_spec_and_launcher_are_valid_python() -> None:
    for path in (SPEC, LAUNCH):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_launcher_starts_the_desktop_application() -> None:
    text = LAUNCH.read_text(encoding="utf-8")
    assert "from lanlink.desktop import main" in text
    assert "freeze_support" in text, "a frozen app that spawns itself must call this"


def test_the_windows_resource_matches_the_application_version() -> None:
    """A disagreement here makes the update check compare the wrong numbers."""
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "sync_version.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_resource_carries_the_running_version() -> None:
    from lanlink import __version__

    text = (PACKAGING / "version_info.txt").read_text(encoding="utf-8")
    assert f"'{__version__}'" in text


def test_no_browser_engine_is_bundled() -> None:
    """LanLink has no browser UI; shipping one anyway would be absurd."""
    spec = SPEC.read_text(encoding="utf-8")
    for module in (
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtQml",
    ):
        assert module in spec, f"{module} is not excluded from the build"
    excludes = spec.split("excludes=[", 1)[1].split("]", 1)[0]
    assert "QtWebEngineCore" in excludes


def test_zeroconf_is_collected_because_it_is_imported_late() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    assert 'collect_submodules("zeroconf")' in spec, "discovery breaks in a frozen build without this"


def test_the_build_is_one_folder_not_one_file() -> None:
    spec = SPEC.read_text(encoding="utf-8")
    assert "exclude_binaries=True" in spec, "one-file unpacks on every launch and confuses the firewall"
    assert "COLLECT(" in spec
    assert "console=False" in spec, "a desktop app should not open a terminal"


# ------------------------------------------------------------------ the installer


def test_the_installer_installs_what_the_spec_builds() -> None:
    inno = INNO.read_text(encoding="utf-8")
    assert r"..\dist\LanLink\*" in inno, "the installer looks in the wrong place"
    assert '#define AppExe "LanLink.exe"' in inno


def test_the_installer_upgrades_in_place() -> None:
    inno = INNO.read_text(encoding="utf-8")
    assert "AppId={{" in inno, "without a stable AppId every version installs alongside the last"
    assert "CloseApplications=yes" in inno


def test_the_firewall_rule_is_private_networks_only() -> None:
    """LanLink has no business being reachable from an airport hotspot."""
    inno = INNO.read_text(encoding="utf-8")
    rule = next(line for line in inno.splitlines() if "advfirewall firewall add rule" in line)
    assert "profile=private" in rule
    assert "profile=public" not in inno
    assert "dir=in" in rule
    assert "advfirewall firewall delete rule" in inno, "uninstalling must remove the rule"


def test_uninstalling_clears_the_cache_but_not_the_identity() -> None:
    """Deleting the device certificate would silently break every pairing."""
    inno = INNO.read_text(encoding="utf-8")
    deletions = inno.split("[UninstallDelete]", 1)[1].split("[", 1)[0]
    assert "staging" in deletions
    assert "thumbnails" in deletions
    assert "lanlink-hub" not in deletions, "the identity folder must survive an uninstall"
    assert "settings.json" not in deletions


def test_the_installer_version_comes_from_the_build_script() -> None:
    inno = INNO.read_text(encoding="utf-8")
    build = BUILD.read_text(encoding="utf-8")
    assert "#ifndef AppVersion" in inno, "the version must be overridable"
    assert "/DAppVersion=%VERSION%" in build
    assert "OutputBaseFilename=LanLinkSetup-{#AppVersion}" in inno


def test_the_build_script_runs_the_tests_before_shipping() -> None:
    build = BUILD.read_text(encoding="utf-8")
    assert "-m pytest" in build, "a build that skips the tests will ship a broken one"
    assert "sync_version.py" in build
    assert "lanlink.spec" in build
    assert "lanlink.iss" in build


def test_the_build_script_reports_a_missing_inno_setup_rather_than_failing() -> None:
    build = BUILD.read_text(encoding="utf-8")
    assert "Inno Setup 6 was not found" in build
    assert "jrsoftware.org" in build


def test_the_icon_is_a_multi_size_windows_icon() -> None:
    """Windows picks a size per context; a single 256px image looks wrong small."""
    data = (PACKAGING / "lanlink.ico").read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00", "not an ICO file"
    count = int.from_bytes(data[4:6], "little")
    assert count >= 5, f"only {count} sizes in the icon"


@pytest.mark.parametrize("path", [SPEC, INNO, BUILD])
def test_the_packaging_files_name_the_application_consistently(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "LanLink" in text
    assert not re.search(r"\bLanlink\b", text), f"{path.name} spells the name inconsistently"
