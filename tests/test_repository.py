"""The repository's own files: workflows, ignore rules, and what must never ship.

A published repository is the one place a mistake is permanent, so the checks
here are about the things that cannot be taken back: a committed private key, a
release workflow that publishes an installer nobody reviewed, a tag that
disagrees with the version the update check compares against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
RELEASE = WORKFLOWS / "release.yml"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def commands(workflow: dict, job: str) -> str:
    return "\n".join(str(step.get("run", "")) for step in steps(workflow, job))


# ------------------------------------------------------------------ the basics


def test_the_workflows_are_valid_yaml() -> None:
    for path in (CI, RELEASE):
        assert path.is_file(), f"{path.name} is missing"
        assert load(path)["jobs"], f"{path.name} defines no jobs"


def test_the_repository_has_the_files_a_reader_expects() -> None:
    for name in ("README.md", "CONTRIBUTING.md", ".gitignore", "pyproject.toml"):
        assert (REPO / name).is_file(), f"{name} is missing"


# --------------------------------------------------------------- nothing secret


def test_no_private_key_or_settings_file_is_tracked() -> None:
    """A committed device key would hand every pairing to whoever cloned it."""
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.splitlines()
    for name in listed:
        assert not name.endswith(".pem"), f"{name} is a certificate or key"
        assert Path(name).name != "settings.json", f"{name} is somebody's settings"
        assert ".lanlink-part" not in name


def test_the_ignore_rules_cover_what_must_never_be_committed() -> None:
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("*.pem", "settings.json", "dist/", "build/", "packaging/output/"):
        assert pattern in ignored, f".gitignore does not cover {pattern}"


# --------------------------------------------------------------------- the CI


def test_ci_runs_the_checks_that_matter_on_both_platforms() -> None:
    workflow = load(CI)
    matrix = workflow["jobs"]["test"]["strategy"]["matrix"]["os"]
    assert "windows-latest" in matrix, "LanLink is a Windows application"
    assert "ubuntu-latest" in matrix

    run = commands(workflow, "test")
    for command in ("ruff check", "mypy", "pytest", "export_openapi.py --check", "sync_version.py --check"):
        assert command in run, f"CI does not run {command}"


def test_ci_installs_what_the_tests_need() -> None:
    run = commands(load(CI), "test")
    assert "kotlin-jupyter-kernel" in run, "the Android interop tests would silently skip"
    assert "libxkbcommon" in run, "PySide6 will not start on the Linux runner without this"
    step_names = [str(step.get("uses", "")) for step in steps(load(CI), "test")]
    assert any("setup-java" in name for name in step_names), "the Kotlin suite needs a JDK"


def test_ci_runs_qt_offscreen() -> None:
    for step in steps(load(CI), "test"):
        if "pytest" in str(step.get("run", "")):
            assert step.get("env", {}).get("QT_QPA_PLATFORM") == "offscreen"
            return
    raise AssertionError("no pytest step found")


# ---------------------------------------------------------------- the release


def test_the_release_builds_on_windows_and_tests_first() -> None:
    workflow = load(RELEASE)
    assert workflow["jobs"]["windows"]["runs-on"] == "windows-latest"
    run = commands(workflow, "windows")
    assert "pytest" in run, "a release that skips the tests will ship a broken one"
    assert "PyInstaller packaging/lanlink.spec" in run
    assert "lanlink.iss" in run


def test_the_release_refuses_a_tag_that_disagrees_with_the_package() -> None:
    """Otherwise the published version is not the one the update check sees."""
    run = commands(load(RELEASE), "windows")
    assert "does not match lanlink.__version__" in run
    assert "exit 1" in run


def test_the_release_is_a_draft_for_a_person_to_approve() -> None:
    workflow = load(RELEASE)
    publish = [step for step in steps(workflow, "windows") if "gh-release" in str(step.get("uses", ""))]
    assert publish, "nothing publishes the release"
    settings = publish[0]["with"]
    assert settings["draft"] is True, "a release should not go out unreviewed"
    assert "LanLinkSetup-" in settings["files"], "the installer is not attached"


def test_the_release_only_fires_on_a_version_tag() -> None:
    workflow = load(RELEASE)
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert triggers["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in triggers, "a manual build is useful before tagging"


def test_the_release_asks_for_no_more_permission_than_it_needs() -> None:
    workflow = load(RELEASE)
    assert workflow["permissions"] == {"contents": "write"}


# -------------------------------------------- the artifacts the updater needs


def test_the_release_publishes_all_three_artifacts() -> None:
    """LanLink refuses to install without SHA256SUMS.txt, so it must be there."""
    workflow = load(RELEASE)
    publish = next(step for step in steps(workflow, "windows") if "gh-release" in str(step.get("uses", "")))
    files = publish["with"]["files"]
    assert "LanLinkSetup-" in files and ".exe" in files
    assert "-portable.zip" in files
    assert "SHA256SUMS.txt" in files


def test_the_release_computes_the_checksums_itself() -> None:
    run = commands(load(RELEASE), "windows")
    assert "Get-FileHash" in run
    assert "SHA256" in run
    assert "SHA256SUMS.txt" in run


def test_the_release_checks_the_artifacts_before_publishing() -> None:
    """A missing file should fail the build, not produce a release nobody can use."""
    run = commands(load(RELEASE), "windows")
    assert "test -f \"packaging/output/SHA256SUMS.txt\"" in run
    assert "grep -q \"LanLinkSetup-$version.exe\" packaging/output/SHA256SUMS.txt" in run


def test_the_artifacts_are_named_after_the_application_version() -> None:
    run = commands(load(RELEASE), "windows")
    assert "LanLinkSetup-${{ steps.version.outputs.value }}.exe" in run or "LanLinkSetup-$version.exe" in run
    assert "steps.version.outputs.value" in run, "the version comes from the package, not the tag"


def test_the_local_build_produces_the_same_three_artifacts() -> None:
    build = (REPO / "packaging" / "build.bat").read_text(encoding="utf-8")
    assert "Compress-Archive" in build, "no portable zip"
    assert "SHA256SUMS.txt" in build
    assert "Get-FileHash" in build
