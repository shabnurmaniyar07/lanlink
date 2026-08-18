# LanLink — two-laptop test procedure

Everything so far has been proven by automated tests, but those run two processes
on **one** machine over loopback. This procedure covers what only real hardware
can prove: Windows Firewall, real mDNS across a switch or access point, TLS over
a router, and sleep/wake.

Work through it in order. Each step says what you should see.

- **Laptop A** — the machine you drive
- **Laptop B** — the other machine

---

## 0. Before you start

On **both** laptops:

- [ ] Python 3.11 or newer: `python --version`
- [ ] Both on the **same** Wi-Fi, Ethernet segment or phone hotspot
- [ ] Note each machine's address: `ipconfig` → **IPv4 Address** (e.g. `192.168.1.18`)
- [ ] Windows network profile is **Private**, not Public:
      `Settings → Network & Internet → Wi-Fi → (your network) → Network profile type`

> A **Public** profile blocks inbound connections and mDNS. This is the single
> most common reason two devices cannot see each other.

---

## 1. Install on both laptops

Copy the `lanlink-hub` folder to Laptop B, then on **each** machine:

```powershell
cd <path>\lanlink-hub
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

- [ ] **Expect:** `265 passed, 2 skipped` on both machines.

The two skips are POSIX-only permission tests — correct on Windows.

---

## 2. First launch

On **both** laptops:

```powershell
.\.venv\Scripts\python.exe -m lanlink.desktop
```

- [ ] Windows Firewall prompt appears → tick **Private networks**, click **Allow access**
- [ ] The window opens on the **Devices** page

If you accidentally clicked Cancel, fix it later with:
`Control Panel → Windows Defender Firewall → Allow an app` → find Python → tick **Private**.

Go to **My Device** on each and confirm:

- [ ] **Status:** 🟢 Online and sharing
- [ ] **Address:** `https://<that machine's IP>:8765` — matches `ipconfig`
- [ ] **Certificate:** four groups of four characters (e.g. `3F9A 2B71 C4D5 E6F7`)

Write down **Laptop B's** address and certificate fingerprint — you will check both.

---

## 3. Share a folder on each laptop

On **both** laptops, **Shared Folders** page:

- [ ] **Add shared folder** → pick a test folder (e.g. `C:\LanLink\Shared`) — *not* a folder of real work
- [ ] Select it, set **Access** to **Read + write + delete**

> Delete is opt-in by design. With only **Read + write** the delete and move
> tests later will correctly refuse.

Put a few test files in Laptop A's shared folder, and create a nested folder
with files at two or three depths — you will move that tree later.

---

## 4. Pair the two laptops  *(requirements 1 & 2)*

**On Laptop B** — My Device:

- [ ] Press **Allow a device to pair**
- [ ] An 8-digit code appears with a countdown, plus a QR code

**On Laptop A** — Devices:

- [ ] **Expect:** `LAPTOP-B` appears in the list on its own within a few seconds *(requirement 1: discovery)*
- [ ] Its badge is 🟢 or 🟡, and it says *not paired yet*
- [ ] Select it → **Pair with selected device**
- [ ] Type the 8-digit code from Laptop B

**Back on Laptop B:**

- [ ] A dialog asks *"Allow this device?"* with Laptop A's name → **Yes**

**On Laptop A:**

- [ ] A dialog shows Laptop B's certificate fingerprint
- [ ] **Check it matches** what Laptop B shows on its My Device page — if it does not, stop and tell me
- [ ] Laptop B now shows *certificate pinned* in the device list *(requirement 2)*
- [ ] On Laptop B, My Device now shows pairing is **off** again (single-use code)

### If Laptop B never appears in the list

Discovery is the fragile part. Pair by address instead — everything downstream
works identically:

- [ ] On Laptop A, paste `192.168.1.21:8765` (B's address) into the box at the
      bottom of the Devices page → **Use invite / address**

Or use the QR/invite link: on B press **Copy invite link**, send yourself the
`lanlink://pair?…` text, paste it into that same box on A. The invite carries
the code *and* the fingerprint, so there is nothing to type.

If pairing by address works but discovery does not, that is an mDNS problem —
some access points block multicast between wireless clients. Note it and carry on.

---

## 5. Browse Laptop B from Laptop A  *(requirements 3, 4, 5)*

On Laptop A:

- [ ] Laptop B shows 🟢 **Online** with its address *(requirement 3)*
- [ ] **Double-click** Laptop B
- [ ] **Expect:** its shared folders appear inside LanLink *(requirement 4)*
- [ ] Double-click the shared folder → its files and folders appear
- [ ] Breadcrumb reads `LAPTOP-B › Shared › …` and each segment is clickable
- [ ] Create a destination folder: **New folder** → `Incoming` *(requirement 5)*
- [ ] Double-click `Incoming` to open it

**No browser should open at any point.** If one does, stop and tell me.

---

## 6. Send a file A → B  *(requirements 6, 7, 8, 9, 10)*

Still inside `Incoming` on Laptop B:

- [ ] **Upload here** → pick a file from Laptop A *(requirement 6)*, **or** drag
      the file from Explorer straight onto the file list
- [ ] LanLink switches to the **Transfers** page *(requirement 10)*
- [ ] **Expect:** a progress bar, a speed in MB/s, an ETA, and status *Transferring*
- [ ] It reaches 100% and moves to **History**

**On Laptop B**, in Explorer:

- [ ] The file is physically in `...\Shared\Incoming\` *(requirement 8)*
- [ ] Right-click → Properties → **size matches the original exactly** *(requirement 9)*

Confirm the contents, not just the size — in PowerShell on **each** laptop:

```powershell
Get-FileHash "<path to the file>" -Algorithm SHA256
```

- [ ] **The two hashes are identical.** *(requirement 9)*

> **Naming note:** "Copy to…" in the menu means *remote → remote* (B → C). To
> send a file from **this** laptop, use **Upload here** or drag-and-drop, which
> is what you just did. See §12.

---

## 7. Send a folder recursively

- [ ] Right-click in the file list → **Upload folder…** → pick your nested folder,
      **or** drag the folder from Explorer onto the list
- [ ] **Expect:** one transfer row for the whole folder, with a total size

On Laptop B:

- [ ] The whole tree arrived — every subfolder, at every depth
- [ ] **Empty subfolders were recreated too**
- [ ] Spot-check a file deep in the tree with `Get-FileHash` on both sides

---

## 8. Large file  *(requirement 12)*

Make a large file on Laptop A:

```powershell
fsutil file createnew C:\LanLink\Shared\large-test.bin 2147483648   # 2 GB
```

- [ ] Upload it to Laptop B
- [ ] **Expect:** steady progress, a believable speed, an ETA that counts down
- [ ] `Get-FileHash` matches on both sides
- [ ] The LanLink window stays responsive throughout — you can click other pages

Note the speed. Over Wi-Fi 5 expect roughly 20–60 MB/s; gigabit Ethernet
roughly 80–110 MB/s. Much slower than that is worth investigating.

---

## 9. Pull a file back B → A

- [ ] Select a file on Laptop B → **Download** → choose a folder on Laptop A
- [ ] `Get-FileHash` matches
- [ ] Right-click a file → **Properties** → size, modified date, type all sensible
- [ ] Right-click a file → **Open** → it opens in its normal Windows app
      *(LanLink downloads it to a temp folder and hands it to Windows — no browser)*

---

## 10. Rename, new folder, delete

On Laptop A, browsing Laptop B:

- [ ] Right-click a file → **Rename…** → the name changes on Laptop B in Explorer
- [ ] **New folder…** → it appears on Laptop B
- [ ] Right-click a file → **Delete** → confirm → it is gone from Laptop B

Then prove permissions are enforced:

- [ ] On Laptop B, set the share to **Read + write** (no delete)
- [ ] On Laptop A press **Refresh**, right-click a file
- [ ] **Expect:** *Delete* and *Move to…* are greyed out; *Download* still works
- [ ] Set it back to **Read + write + delete**

---

## 11. Disconnect handling  *(requirement 13)*

- [ ] Start the 2 GB upload again
- [ ] Part-way through, **turn off Wi-Fi on Laptop B** (or pull its Ethernet cable)
- [ ] **Expect on Laptop A:** the transfer goes to **Failed** with a reason, within a minute or so
- [ ] **Expect:** the original file on Laptop A is **untouched**
- [ ] On Laptop B there is no half-file — at most a `.lanlink-part` sidecar, and
      it is **not** listed in LanLink as a real file
- [ ] Turn Wi-Fi back on. Laptop B returns to 🟢 within about 10 seconds
- [ ] Select the failed transfer → **Retry** → it completes, and the hash matches

Also try the softer cases:

- [ ] **Cancel** a running transfer → status *Cancelled*, source untouched
- [ ] **Pause**, wait a few seconds, **Resume** → progress stops and continues
- [ ] Close the lid on Laptop B, reopen it → it comes back 🟢 without restarting either app

---

## 12. Move, and the relay  *(requirements 14, 15)*

**"Copy to…" and "Move to…" mean *remote → remote*.** The destination picker
deliberately excludes the device you are currently browsing, so with only two
machines the list would be empty. Start a **second LanLink instance on Laptop A**
to act as the third device — it gets its own identity, certificate and shares:

```powershell
mkdir C:\LanLink\Second
.\.venv\Scripts\python.exe -m lanlink.desktop --data-dir C:\LanLink\Second
```

- [ ] A second LanLink window opens on port **8766** (8765 is taken)
- [ ] Its **My Device** page shows a *different* device id and a *different* certificate
- [ ] Name it something obvious in **Settings** (e.g. `LAPTOP-A-2`) → **Save settings**
- [ ] Add a shared folder to it, set **Read + write + delete**

Now pair the first window with it:

- [ ] In the second window: **Allow a device to pair**
- [ ] In the first window: Devices → it appears → **Pair with selected device** → code → approve

You now have three paired nodes and the relay is reachable.

### Copy to…  *(requirement 7)*

From the **first** window on Laptop A:

- [ ] Browse **Laptop B**, open a shared folder, select a file
- [ ] Right-click → **Copy to…**
- [ ] Choose `LAPTOP-A-2` and one of its shared folders → **OK**
- [ ] **Expect:** a transfer row of kind *remote-copy*, then the file appears in
      `C:\LanLink\Second`'s shared folder
- [ ] `Get-FileHash` matches the file on Laptop B
- [ ] **The file is still on Laptop B** — a copy leaves the source alone

### The hub keeps nothing  *(requirement 15)*

That transfer streamed **B → Laptop A → the second instance** without ever
writing to disk in between:

- [ ] While a large one runs, watch `C:\Users\shabn\.lanlink-hub` and `C:\LanLink\Second`
      — no growing temp file appears in either
- [ ] Afterwards, search for leftovers:
      `Get-ChildItem C:\LanLink -Recurse -Filter *.lanlink-part`
- [ ] **Expect:** nothing found

### Move to…  *(requirement 14)*

- [ ] Select a file on Laptop B → right-click → **Move to…** → pick `LAPTOP-A-2`
- [ ] **Expect:** it arrives at the destination, hash matches, **and only then**
      disappears from Laptop B
- [ ] Repeat with a **folder** → the whole tree moves, and the source tree is
      removed only after every file has landed

The order matters: transfer, verify, *then* delete. To see the guarantee hold
under failure, set the destination share to **Read + write** (no delete is not
needed — read-only is enough), retry a Move, and confirm it is refused with the
source untouched.

## 13. Automated end-to-end check

This runs the same client, transfer engine and verification the app uses, and
prints a pass/fail line per check.

**On Laptop B:**

```powershell
.\.venv\Scripts\python.exe -m lanlink.server --share "C:\LanLink\Shared" --pair
```

It prints its address, its certificate fingerprint and an 8-digit code.

**On Laptop A:**

```powershell
.\.venv\Scripts\python.exe tools\verify_transfer.py --peer https://192.168.1.21:8765 --code 12345678 --size 200
```

- [ ] **Expect:** `17/17 checks passed`
- [ ] The fingerprint it prints matches the one Laptop B printed

Note the MB/s it reports for the 200 MB file.

---

## 14. Security spot-checks

- [ ] On Laptop A, open `https://<Laptop B IP>:8765` **in a web browser**
      → certificate warning, then `{"detail":"Not Found"}`. **No LanLink UI in the browser.**
- [ ] On Laptop B, My Device → confirm pairing shows **off** when you have not armed it
- [ ] Try pairing from A while B is *not* in pairing mode
      → *"This device is not in pairing mode"*
- [ ] Arm pairing on B, then type five wrong codes on A
      → it locks out and B's pairing mode switches **off** by itself
- [ ] On B, Devices → select Laptop A → **Forget selected device**
      → on A, browsing B now fails until you pair again

---

## What to send me

Whatever the outcome, this is what I need:

1. Which numbered steps passed and which failed
2. For any failure: the exact message from the status bar or dialog
3. The speed you saw in step 8 and step 13
4. Whether discovery (§4) worked on its own or you had to pair by address
5. The `17/17` line from step 13

If something fails, that is useful — it is exactly what this procedure is for.
