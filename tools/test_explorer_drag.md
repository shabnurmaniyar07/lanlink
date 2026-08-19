# Explorer browser and native drag-and-drop — manual Windows tests

The automated suite proves the pieces (staging, MIME payload, cache safety). It
cannot prove that *Windows itself* accepts the drop, because a real drag needs a
real mouse and a real target application. These eight tests close that gap.

Run them on two Windows machines on the same network, paired, with at least one
shared folder containing:

- a small text file (`notes.txt`)
- a photo (`photo.jpg` or `.png`)
- a CAD file the target application understands (`part.step`, `part.dwg`)
- a folder with a few files in it
- a file larger than 200 MB

Laptop **A** shares the folder. Laptop **B** browses it. Everything below is done
on **B** unless stated otherwise.

---

## A. Explorer navigation

1. Devices → select A → **Open**.
2. Double-click a share, then a subfolder, then a deeper subfolder.
3. Press **←** (Back) twice, then **→** (Forward) twice.
4. Press **↑** (Up) until it greys out.
5. Click each breadcrumb segment.

**Pass:** every step lands where the label says; Back/Forward/Up grey out at the
ends of the trail; the breadcrumb always matches the folder on screen; no step
freezes the window for more than a moment.

---

## B. View modes, sorting and search

1. Cycle the view combo: Details → Small icons → Medium icons → Large icons.
2. In Details, click each column header (Name, Size, Type, Modified) twice.
3. Type `step` into the search box, then `text document`, then clear it.
4. Select a file in Details, switch to Large icons.

**Pass:** icons resize; folders stay above files in every sort order and both
directions; searching matches file names *and* the Type column; the selection
survives the view change.

---

## C. Thumbnails

1. Open a folder containing photos.
2. Switch to Large icons and wait a few seconds.
3. Leave the folder and come back.
4. Settings → note the cache size → **Clear thumbnails** → return to the folder.

**Pass:** photos turn from a generic glyph into real previews; the window stays
responsive while they generate; the second visit is instant (disk cache); after
clearing they regenerate. Non-images keep their type glyph.

---

## D. Drag one remote file into Explorer  — the core test

1. Open A's share on B.
2. Drag `part.step` from LanLink onto an open Explorer window (Desktop is fine).
3. The status bar says *Preparing part.step…* and a **stage** row appears on the
   Transfers page. Wait for it to complete.
4. Drag the same file again.

**Pass:** the second drag drops a real file into Explorer. Right-click it →
Properties → the location is under
`%LOCALAPPDATA%\LanLink\staging\<id>\`. The file opens correctly and its size
matches the original on A.

**Fail if:** Explorer creates an internet shortcut, shows a URL, or reports
"cannot copy" — that would mean an HTTPS URL leaked into the drop payload.

---

## E. Drag into a real application

1. Open KUKA.Sim Pro (or SolidWorks, or any CAD/CAM package).
2. From LanLink, drag `part.step` onto its 3D viewport / import target.
3. Repeat with `part.dwg` into AutoCAD or a DWG viewer.

**Pass:** the application imports the geometry exactly as it would from a local
disk, without any LanLink-specific configuration and without a browser opening.

**Note:** if the first drag only prepares the file, wait for the Transfers row to
complete and drag again. This is expected — see the note at the top of
`src/lanlink/ui/dragdrop.py`.

---

## F. Multi-file drag, and a folder

1. Ctrl-click three files. Drag them out. Wait, then drag again.
2. Select a folder and try to drag it.
3. Select a mixed set (two files + one folder) and drag.

**Pass:** all three files arrive together in one drop, with their original names.
Folders are not offered to a drag (use **Download to…** for those); a mixed
selection drags only the files.

---

## G. Open, context menu, and permissions

1. Right-click a file: **Open with default app**. Wait.
2. Right-click the same file again and choose **Copy local staged path**; paste
   it into Notepad.
3. Right-click a file on a **read-only** share.
4. Right-click a file on a **read + write + delete** share.

**Pass:** the file opens in its normal Windows application. The pasted path is a
plain `C:\Users\…\AppData\Local\LanLink\staging\…` path — never `https://`.
On a read-only share, Delete / Rename / Upload / New folder are greyed out while
Open, Download to…, Copy path and Prepare for drag stay available. On a
read+write+delete share everything is enabled.

---

## H. Large file, cancellation and cache hygiene

1. Start a drag of the 200 MB+ file. While it stages, use the window: change
   folder, sort, search, switch view mode.
2. Cancel the stage row on the Transfers page.
3. Check `%LOCALAPPDATA%\LanLink\staging` in Explorer.
4. Pull the network cable / switch off Wi-Fi mid-stage on another large file,
   then reconnect and retry.
5. Settings → **Clear staged files**.

**Pass:** the window never freezes; a cancelled or interrupted stage leaves **no**
`.lanlink-part` file and no half-written file with the real name; retrying works;
clearing the cache reports a count and empties the folder. A file that is being
dragged at that exact moment is not deleted underneath the target application.

---

## What to report if something fails

Note the test letter, what you saw, and:

- the Transfers page row (status and error text),
- the contents of `%LOCALAPPDATA%\LanLink\staging`,
- whether the target application showed a path or a URL.
