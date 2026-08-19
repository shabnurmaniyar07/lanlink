# Working on LanLink

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # .venv\Scripts\pip on Windows
.venv/bin/python -m pytest -q
```

Before pushing:

```bash
ruff check .
mypy
python -m pytest -q
python tools/export_openapi.py --check
python tools/sync_version.py --check
```

CI runs all of that on Linux and Windows.

## The rules this project is built to

These are not style preferences. Several of them are the reason the security
model works at all.

- **No browser UI anywhere.** Windows is native PySide6, Android is native
  Kotlin. HTTP is transport between devices and nothing else. A test fails the
  build if `QDesktopServices`, `webbrowser` or a WebEngine import appears in the
  package, and the installer excludes Qt's browser engine outright.
- **LAN membership is never authorisation.** Being discovered grants nothing.
  Pairing is off by default, needs an 8-digit code that expires in 120 seconds,
  and five wrong codes switch it off entirely.
- **The certificate is the identity.** Peers pin it at pairing time. A changed
  certificate means refuse the connection — never offer to continue anyway.
- **Never build a local path out of a remote string** without going through the
  `files.py` sandbox and `validate_filename`.
- **Never publish a file under its real name until it verifies.** Everything
  lands in a `.lanlink-part` sidecar first.
- **Never claim something works without testing it.** If it genuinely cannot be
  tested, say so plainly rather than implying it was.

## The protocol is frozen

`docs/protocol/v1.md` is what other clients are written against, so changing it
changes somebody else's product. `tests/test_protocol_contract.py` fails if the
code and the document drift apart in either direction. Adding an optional
response field is fine; removing one, changing a type, or changing a status code
needs `/v2`.

`tools/conformance.py` checks a *running* node against that document over a real
pinned TLS connection — useful when working on a new client.

## Layout

```
src/lanlink/          the Windows application and the shared core
  api.py              the /v1 transport. Never a user interface
  files.py            the path sandbox. Read it before touching paths
  ui/                 PySide6: pages, models, theme, drag and drop
android/core/         the Kotlin protocol client, tested on a plain JVM
docs/protocol/        the frozen v1 wire protocol
packaging/            PyInstaller spec, Inno Setup script, build.bat
tools/                conformance runner, generators, build helpers
tests/                pytest, including the Kotlin interop run
```

## Releasing

1. Raise `__version__` in `src/lanlink/__init__.py`.
2. `python tools/sync_version.py`
3. Commit, then `git tag v<version> && git push origin v<version>`.

The release workflow builds the installer on Windows, checks the tag against the
package version, and publishes a **draft** release with the setup .exe and a
portable zip attached. Review it, then publish. Every LanLink with this
repository set under Settings → Updates will notice it.
