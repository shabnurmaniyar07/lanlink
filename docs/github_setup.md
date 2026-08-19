# Putting LanLink on GitHub

Everything is committed — 20 commits, clean history. What's left needs your
GitHub account, which I don't have from here.

## 1. Create the repository

On <https://github.com/new>:

- **Name:** `lanlink` (or whatever you prefer)
- **Private** is fine — the update check and the release workflow both work on a
  private repo. Only make it public if you intend to.
- **Do not** tick "Add a README", "Add .gitignore" or "Choose a license". The
  repository already has the first two, and an initial commit on their side
  means a merge conflict on your first push.

## 2. Get the code onto your PC

You have two copies. Either works.

**From the bundle** (`lanlink-hub.bundle`, keeps the full history):

```powershell
cd C:\path\where\you\want\it
git clone lanlink-hub.bundle lanlink
cd lanlink
git remote remove origin
```

**From the zip:** unzip it, then `cd` in and `git init` — but you lose the
history, so prefer the bundle.

## 3. Push

```powershell
git remote add origin https://github.com/YOUR-USERNAME/lanlink.git
git branch -M main
git push -u origin main
```

If it asks for a password, use a personal access token, not your account
password — GitHub stopped accepting passwords over HTTPS. Settings → Developer
settings → Personal access tokens → Fine-grained, with **Contents: read and
write** on this one repository.

## 4. Two small edits after pushing

- `README.md` line 3 has a build badge with `OWNER/REPO` in it. Replace both
  with your username and repository name.
- **Settings → Updates** in LanLink: put `YOUR-USERNAME/lanlink` in the
  repository box and press Save settings.

## 5. Let GitHub build the installer for you

You don't have to build on your own machine at all:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

The release workflow builds `LanLink.exe` on a Windows runner, wraps it in
`LanLinkSetup-0.1.0.exe`, also makes a portable zip, and opens a **draft**
release with both attached. Go to the Releases tab, check it looks right, press
Publish. Every LanLink with that repository configured then sees the new
version.

It refuses to build if the tag and `__version__` disagree, so the published
version is always the one the update check compares against.

For the next version: raise `__version__` in `src/lanlink/__init__.py`, run
`python tools/sync_version.py`, commit, tag `v0.2.0`, push the tag.

## 6. A licence is your decision

There is deliberately no `LICENSE` file. Without one, the default is "all rights
reserved" — fine for something private. If you want people to be able to use it,
MIT is the usual permissive choice and GitHub will add it for you: **Add file →
Create new file → type `LICENSE` → Choose a license template**.

I did not pick one for you because it is hard to walk back once published.

## What is in the repository

| | |
|---|---|
| `src/lanlink/` | The Windows application and the shared core |
| `android/core/` | The Kotlin protocol client, tested on a plain JVM |
| `docs/protocol/` | The frozen v1 wire protocol, its OpenAPI and schemas |
| `packaging/` | PyInstaller spec, Inno Setup script, `build.bat`, icon |
| `tools/` | Conformance runner, generators, build helpers |
| `tests/` | 605 tests, including the Kotlin interop run |
| `.github/workflows/` | Tests on Linux and Windows; the release build |

## One thing to check before making it public

The repository history is clean of keys and settings — a test enforces it — but
have a look at `docs/two_laptop_test.md` and `tools/test_explorer_drag.md`
first. They were written for you and mention your machines by name.
