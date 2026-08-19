"""Locate a Kotlin compiler and the stdlib, without a Gradle download.

Android Studio brings its own Kotlin, and this container has no access to Maven
or the Gradle distribution — but the kotlin-jupyter-kernel wheel on PyPI ships
the whole Kotlin JVM compiler. That is enough to build and test the parts of the
Android client that touch no Android API, which is where the protocol lives.

    pip install kotlin-jupyter-kernel        # one time
    python tools/kotlin_env.py               # report what was found
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

COMPILER_MAIN = "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler"


@dataclass(frozen=True)
class KotlinToolchain:
    java: str
    compiler_jar: Path
    stdlib_jars: tuple[Path, ...]

    @property
    def classpath(self) -> str:
        return os.pathsep.join(str(jar) for jar in self.stdlib_jars)

    def compile(
        self, sources: list[Path], output: Path, extra_classpath: str = ""
    ) -> subprocess.CompletedProcess:
        output.mkdir(parents=True, exist_ok=True)
        classpath = os.pathsep.join(filter(None, [self.classpath, extra_classpath]))
        return subprocess.run(
            [
                self.java,
                "-cp",
                str(self.compiler_jar),
                COMPILER_MAIN,
                *[str(path) for path in sources],
                "-no-stdlib",
                "-nowarn",
                "-cp",
                classpath,
                "-d",
                str(output),
            ],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )

    def run(self, main_class: str, classes: Path, extra_classpath: str = "", args: list[str] | None = None):
        classpath = os.pathsep.join(filter(None, [str(classes), self.classpath, extra_classpath]))
        return subprocess.run(
            [self.java, "-cp", classpath, main_class, *(args or [])],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )


def _clean_env() -> dict[str, str]:
    # JAVA_TOOL_OPTIONS carries proxy settings that the compiler prints on every
    # run and that confuse output matching. The build needs no network.
    env = dict(os.environ)
    env["JAVA_TOOL_OPTIONS"] = ""
    return env


def _jar_roots() -> list[Path]:
    roots = []
    override = os.environ.get("LANLINK_KOTLIN_JARS", "").strip()
    if override:
        roots.append(Path(override))
    for module in ("run_kotlin_kernel",):
        for entry in sys.path:
            candidate = Path(entry) / module / "jars"
            if candidate.is_dir():
                roots.append(candidate)
    try:
        import run_kotlin_kernel  # type: ignore[import-not-found]

        roots.append(Path(run_kotlin_kernel.__file__).parent / "jars")
    except Exception:  # noqa: BLE001 - the wheel simply is not installed
        pass
    return roots


def find_toolchain() -> KotlinToolchain | None:
    java = shutil.which("java")
    if not java:
        return None
    for root in _jar_roots():
        compiler = next(iter(sorted(root.glob("kotlin-jupyter-kernel-*-all.jar"))), None)
        stdlib = sorted(root.glob("kotlin-stdlib-*.jar")) + sorted(root.glob("annotations-*.jar"))
        if compiler and stdlib:
            return KotlinToolchain(java=java, compiler_jar=compiler, stdlib_jars=tuple(stdlib))
    return None


def main() -> int:
    toolchain = find_toolchain()
    if toolchain is None:
        print("No Kotlin toolchain. Install it with: pip install kotlin-jupyter-kernel")
        return 1
    print(f"java     {toolchain.java}")
    print(f"compiler {toolchain.compiler_jar}")
    for jar in toolchain.stdlib_jars:
        print(f"stdlib   {jar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
