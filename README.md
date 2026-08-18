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
untouched; pass `--bind-all` to override.

Headless node:

```powershell
python -m lanlink.server --share "C:\LanLink\Shared" --pair
```

## API surface (internal transport, `/v1`)

| Endpoint | Use |
| --- | --- |
| `GET /health`, `GET /v1/device` | Liveness and device identity |
| `POST /v1/pair` | Exchange a code for a device token (409 not armed, 429 throttled, 403 wrong) |
| `GET /v1/shares` | List approved shares with permissions and availability |
| `GET /v1/shares/{id}/list?path=` | Browse a folder inside a share |
| `GET /v1/files/{id}?path=` | Stream one file |
| `POST /v1/uploads/{id}?path=` | Upload without overwriting |
| `POST /v1/operations` | Copy or move between local shares |
| `DELETE /v1/pairings/{id}` | Self-unpair only |

All endpoints except `/health`, `/v1/device` and `/v1/pair` require `X-LanLink-Token`.

## Development

```bash
python -m pytest      # 92 tests
python -m ruff check .
python -m mypy
```

Phase status: **0 and 1 complete.** See `docs/current_state.md` for the audit this work is based
on. Next: Phase 2 — rename/delete/mkdir/properties, per-share permissions, remote-to-remote
transfers.

## Project layout

```text
src/lanlink/
  api.py        internal /v1 transport (never a user interface)
  client.py     reusable peer client for desktop and future Android nodes
  desktop.py    PySide6 window (full rewrite due in Phase 3)
  discovery.py  mDNS advertise/browse over one shared Zeroconf instance
  files.py      share-root sandbox, filename validation, file operations
  server.py     background service, interface/port selection, headless entry point
  state.py      atomic persistence, identity, shares, pairing
tests/          92 tests incl. regressions for every audited defect
```
