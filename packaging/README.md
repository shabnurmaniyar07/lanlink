# Building LanLink for Windows

One command, from the repository root on a Windows machine:

```powershell
packaging\build.bat
```

It creates a virtual environment if there isn't one, installs LanLink and
PyInstaller, syncs the version resource, **runs the test suite**, builds
`dist\LanLink\LanLink.exe`, and — if Inno Setup 6 is installed — wraps it into
`packaging\output\LanLinkSetup-<version>.exe`.

Without Inno Setup the application is still built; the script says so and stops
there. Get it from <https://jrsoftware.org/isdl.php> if you want the installer.

## What the installer does

- Installs to `C:\Program Files\LanLink`, per machine, asking for elevation once.
- Start Menu shortcut always; Desktop shortcut and start-at-sign-in are optional.
- Adds a Windows Firewall rule for **private networks only**. LanLink binds this
  machine's LAN address by default and has no business being reachable from a
  public network such as an airport hotspot.
- Upgrades in place. Same `AppId`, so a newer installer replaces the old
  installation rather than sitting beside it.
- Uninstalling removes the firewall rule and the staging and thumbnail caches,
  and **leaves your settings, this device's identity and its paired devices
  alone**. Deleting the certificate would silently break every pairing; the
  uninstaller tells you where that folder is if you want it gone.

## Publishing an update

Push a tag and GitHub does the rest:

1. Raise `__version__` in `src/lanlink/__init__.py`.
2. `python tools/sync_version.py`
3. Commit, then `git tag v<version> && git push origin v<version>`.

The release workflow builds on a Windows runner and attaches **three** files to
a draft release:

| Artifact | Why |
|---|---|
| `LanLinkSetup-<version>.exe` | What LanLink downloads and runs |
| `LanLink-<version>-portable.zip` | For people who would rather not install |
| `SHA256SUMS.txt` | **Required.** Without it no LanLink will install the update |

That last one is not optional. LanLink verifies the installer against the digest
published in the same release and refuses to run anything it cannot check, so a
release without `SHA256SUMS.txt` is a release nobody can auto-update to — the
Update Now button stays disabled and the dialog says why.

`build.bat` produces the same three files locally if you would rather build by
hand.

Drafts and pre-releases are ignored, and a release older than what is running is
never offered, so a yanked build cannot talk somebody into downgrading.

## What the update actually does

1. Once a day at most, LanLink asks the GitHub releases API for the newest
   stable release. Nothing is downloaded by the check.
2. If there is a newer one it puts a line at the top of the window. It does not
   interrupt with a dialog.
3. The user opens it, reads the release notes, and presses **Update Now**.
4. The installer downloads with a progress bar, off the UI thread, cancellable.
5. Its SHA-256 is compared against `SHA256SUMS.txt` from the same release. On a
   mismatch the file is **deleted** and nothing runs.
6. Only then does the installer start. It closes LanLink, upgrades in place, and
   offers to start LanLink again.
7. Settings, the device identity, its certificate, pairings, shared folders and
   history all live outside the installation directory and are untouched. Nobody
   has to pair again.

## Files

| File | What it is |
|---|---|
| `build.bat` | The whole build. Start here. |
| `lanlink.spec` | PyInstaller: one folder, no console, no browser engine. |
| `lanlink.iss` | Inno Setup: shortcuts, firewall rule, in-place upgrade. |
| `launch.py` | The frozen entry point. |
| `version_info.txt` | Windows version resource. Generated — do not edit. |
| `lanlink.ico` | Application icon, seven sizes. |

`tests/test_packaging.py` checks the parts of this that can be checked without
Windows: the spec parses, the version resource matches the application, the
browser engine is excluded, the firewall rule is private-only, and the
uninstaller does not touch the device identity.

## Why one folder rather than one file

A one-file build unpacks itself into a temporary directory on every launch. It
starts more slowly, and Windows Firewall sees a different program path each time,
so the rule stops matching and the prompt comes back. The installer hides the
folder anyway.
