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

1. Raise `__version__` in `src/lanlink/__init__.py`.
2. `packaging\build.bat`
3. Create a GitHub release tagged `v<version>` and attach
   `LanLinkSetup-<version>.exe`.

Every LanLink with that repository set under **Settings → Updates** will then
notice the new version, show what changed and offer the download link. LanLink
never downloads or installs anything by itself — that stays your decision, and
the user's.

Drafts and pre-releases are ignored, and a release older than what is running is
never offered, so a yanked build cannot talk somebody into downgrading.

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
