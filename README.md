# LanLink

[![tests](https://github.com/OWNER/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/ci.yml)

LanLink is a native cross-device local file-sharing application. Every device runs an installed
LanLink app; there is **no browser interface, no HTML UI, and no WebView**. HTTP/FastAPI is used
purely as the internal network transport between nodes.

- **Windows:** Python + PySide6 (native desktop app)
- **Android:** native Kotlin client (Phase 5, not started)

## Architecture

```text
PySide6 UI  →  LanLinkClient  →  local network  →  remote LanLink service  →  remote filesystem
```

Every installation is both client and server. There is no central server and no internet
dependency. Discovery uses mDNS (`_lanlink._tcp.local.`) with a manual address as the fallback.

## Security model

**Network membership is never authorization.**

- Every connection is TLS. Each device generates its own certificate on first run;
  peers **pin** that exact certificate when they pair. There is no certificate
  authority — the pinned certificate *is* the device identity, so an attacker who
  takes over the address cannot impersonate a paired device. LanLink refuses to
  connect if a pinned certificate ever changes.

- Only folders the owner explicitly adds are reachable. Whole disks are never exposed.
- Pairing mode is **off by default**. No pairing code exists until the local owner switches it on.
- A code is 8 digits, expires after 120 seconds, is single-use, and is rate limited per source.
  Five wrong attempts switch pairing mode off entirely.
- Successful pairing issues a random 32-byte token; only its SHA-256 hash is stored.
- Settings are written atomically with a `.bak` generation, at owner-only permissions.
  Corrupt settings never silently mint a new device identity.
- Every path is canonicalised and proven to remain inside the shared root. `..`, absolute paths,
  UNC paths, drive-qualified paths, symlinks and Windows junctions are all refused.
- Filenames are validated under both POSIX and Windows rules, including reserved device names.
- Uploads cannot overwrite, are size-capped, and never leave a partial file behind.
- Over the network a device can only remove **its own** pairing. Revoking any other device is a
  local-owner action.
- Every transfer is verified with SHA-256. A file that fails its checksum is discarded, never
  published under its real name.
- Interrupted transfers accumulate in a `.lanlink-part` sidecar and resume from where they
  stopped. A partial file is never listed as a real one.
- On Windows, stored credentials are sealed with DPAPI under the current user account.

### Pairing with an invite

Press **Allow a device to pair** on one device. It shows an 8-digit code and a QR code. Either
scan the QR from a phone, or press **Copy invite link** and paste the `lanlink://pair?…` link into
the other computer's Devices page. The invite carries the certificate fingerprint, so the
receiving device pins the right identity rather than trusting whatever answers on that address.

After pairing, compare the fingerprint LanLink shows with the one on the other device's
**My Device** page. They should match exactly.

## Run it on Windows

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m lanlink.desktop
```

Then:

1. Open **Shared Folders** and add a folder. Set its access to read-only, read+write, or
   read+write+delete — delete is opt-in.
2. Put both machines on the same Wi-Fi, Ethernet or hotspot.
3. On the sharing machine, open **My Device** and press **Allow a device to pair**.
4. On the other machine, open **Devices**, select the discovered device and press
   **Pair with selected device** — or paste the invite link.
5. Enter the 8-digit code within 120 seconds and approve the request on the sharing machine.
6. Double-click the device to browse its shared folders and files inside LanLink. Right-click any
   file for download, upload, copy, move, rename, delete, new folder and properties.

Windows may ask whether Python can communicate on private networks — allow **Private networks**
only. LanLink binds this machine's LAN address by default, so VPN and public adapters stay
untouched; pass `--bind-all` to override. If the machine changes network or wakes from sleep,
LanLink notices the new address and rebinds itself.

Headless node:

```powershell
python -m lanlink.server --share "C:\LanLink\Shared" --pair
```

It prints its certificate fingerprint at startup so you can verify it from the other device.

## API surface (internal transport, `/v1`)

| Endpoint | Use |
| --- | --- |
| `GET /health`, `GET /v1/device` | Liveness and device identity |
| `POST /v1/pair` | Exchange a code for a device token (409 not armed, 429 throttled, 403 wrong) |
| `GET /v1/shares` | List approved shares with permissions and availability |
| `GET /v1/shares/{id}/list?path=` | Browse a folder inside a share |
| `GET /v1/files/{id}?path=` | Stream one file; honours `Range:` for resume |
| `PUT /v1/files/{id}?path=&name=&offset=` | Resumable streaming upload |
| `GET /v1/shares/{id}/partial?name=` | How many bytes of an interrupted upload survived |
| `POST /v1/shares/{id}/finalize?name=&sha256=` | Verify and publish a completed upload |
| `GET /v1/shares/{id}/checksum?path=` | SHA-256 of one file |
| `POST /v1/uploads/{id}?path=` | Multipart upload without overwriting |
| `POST /v1/operations` | Copy or move between local shares |
| `DELETE /v1/pairings/{id}` | Self-unpair only |

All endpoints except `/health`, `/v1/device` and `/v1/pair` require `X-LanLink-Token`.

## Development

```bash
python -m pytest      # 269 tests
python -m ruff check .
python -m mypy
```

Running a second instance on one machine (useful for testing device-to-device
features without a third computer):

```powershell
python -m lanlink.desktop --data-dir C:\LanLink\Second
```

It gets its own device identity, certificate and shares, and picks the next free
port. `python -m lanlink.server --data-dir ...` does the same headless.

## Protocol

The wire protocol between devices is frozen at **v1** and documented in `docs/protocol/`:

| File | What it is |
|---|---|
| `v1.md` | The specification. Discovery, TLS pinning, pairing, authentication, every endpoint, error codes, path rules, and what a client must do. |
| `openapi.yaml` | Generated from the running app by `tools/export_openapi.py`. Never edited by hand. |
| `schema.json` | JSON Schema for every response body. |

`tests/test_protocol_contract.py` fails if the implementation drifts from those documents, or the
documents from the implementation. Read `v1.md` before writing any new client.

To check a **running** node — over the real network, with a real pinned certificate — switch
pairing on there and point the conformance runner at it:

```powershell
python tools\conformance.py --host 192.168.1.20 --port 8765 --code 48210937
```

It speaks only the documented protocol, so it works against any implementation, not just this one.
Everything it creates goes in a folder called `lanlink-conformance` and is deleted afterwards, and
it unpairs itself when it finishes.

## Building an installer

```powershell
packaging\build.bat
```

Produces `dist\LanLink\LanLink.exe` and, with Inno Setup 6 installed,
`packaging\output\LanLinkSetup-<version>.exe` — Start Menu and Desktop shortcuts, a
private-networks-only firewall rule, and in-place upgrades. Uninstalling leaves the device
identity and pairings alone. See `packaging/README.md`.

**Settings → Updates** can watch a GitHub repository's releases and say when a newer version
exists. LanLink never downloads or installs anything itself; it shows what changed and gives you
the link.

## Android

`android/core/` holds the Kotlin protocol client — models, path rules, resume arithmetic,
certificate pinning, invites. It imports only the Kotlin standard library and the JDK, so it
compiles and is tested on a plain JVM here, and drops into an Android project unchanged.
`tests/test_android_core.py` compiles it, runs its own suite, then drives it against a real Python
node over a pinned TLS socket. See `android/README.md`.

The Android application around it — Gradle, Compose, NsdManager, SAF — is not written yet; it
cannot be compiled in this environment.

Phase status: **0 through 4 complete**, plus the Explorer browser with native drag-and-drop, the
frozen v1 protocol, and the Android protocol core. Next: the Android app shell, built in Android
Studio against `docs/protocol/v1.md`.

## Project layout

```text
src/lanlink/
  api.py        internal /v1 transport (never a user interface)
  client.py     reusable peer client, with certificate pinning
  crypto.py     device certificates, fingerprint pinning, credential sealing
  desktop.py    application entry point
  discovery.py  mDNS advertise/browse over one shared Zeroconf instance
  files.py      share-root sandbox, filename validation, file operations
  invite.py     lanlink:// pairing invites and QR payloads
  server.py     TLS service, interface/port selection, network-change recovery
  state.py      atomic persistence, identity, shares, pairing
  transfers.py  queue, progress, cancel/retry, resume, relay between two nodes
  ui/           native PySide6 interface (models, widgets, main window)
tests/          269 tests incl. regressions for every audited defect
tools/          verify_transfer.py — end-to-end check against a real second device
docs/           the audit, and the two-laptop test procedure
```
