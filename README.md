# LanLink Hub

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

### Current limitation

Transport is still plain HTTP, intended for a trusted LAN or hotspot during development. TLS,
certificate pinning and QR pairing land in Phase 4. Do not use this on public or untrusted Wi-Fi.

## Run it on Windows

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m lanlink.desktop
```

Then:

1. **Add shared folder** and pick a folder.
2. Put both machines on the same Wi-Fi, Ethernet or hotspot.
3. On the receiving machine, open the **Remote browser** tab and enter the other machine's address.
4. On the sharing machine, press **Allow a device to pair** and read out the 8-digit code.
5. Enter that code on the receiving machine within 120 seconds.
6. Browse the paired device's shared folders and files inside LanLink.

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
python -m pytest      # 252 tests
python -m ruff check .
python -m mypy
```

Phase status: **0 through 4 complete.** See `docs/current_state.md` for the audit this
work is based on. Next: Phase 5 — the native Kotlin Android client.

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
tests/          252 tests incl. regressions for every audited defect
```
