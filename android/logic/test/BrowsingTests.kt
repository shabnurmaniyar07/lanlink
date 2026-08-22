package link.lan.app.test

import link.lan.app.BrowseState
import link.lan.app.Direction
import link.lan.app.Sorting
import link.lan.app.TransferQueue
import link.lan.app.TransferState
import link.lan.app.bytes
import link.lan.app.downloadName
import link.lan.app.partialName
import link.lan.core.Entry
import link.lan.core.Share
import link.lan.core.test.Suite
import link.lan.core.test.assertEquals
import link.lan.core.test.assertFalse
import link.lan.core.test.assertNotNull
import link.lan.core.test.assertNull
import link.lan.core.test.assertTrue

private fun share(permissions: String = "rwd") = Share("s1", "Demo", permissions, available = true)

private fun file(name: String, size: Long? = 10, modified: Double? = 1.0, path: String = name) =
    Entry(name, path, isFolder = false, size = size, modifiedAt = modified)

private fun folder(name: String, path: String = name) =
    Entry(name, path, isFolder = true, size = null, modifiedAt = null)

private fun known() = link.lan.app.KnownDevice(
    id = "pc-1", name = "Desk PC", host = "192.168.1.4", port = 8765,
    fingerprint = "a".repeat(64), token = "t",
)

fun registerBrowsingTests() {
    Suite.group("browsing")

    Suite.test("folders come first, then whatever the sort asks for") {
        val state = BrowseState(share = share()).loaded(
            listOf(file("zebra.txt"), folder("photos"), file("apple.txt"), folder("archive"))
        )
        assertEquals(
            listOf("archive", "photos", "apple.txt", "zebra.txt"),
            state.visible.map { it.name },
            "folders lead, then alphabetical",
        )
    }

    Suite.test("newest and largest sort the files without disturbing the folders") {
        val state = BrowseState(share = share()).loaded(
            listOf(
                file("old.txt", size = 900, modified = 1.0),
                folder("photos"),
                file("new.txt", size = 5, modified = 99.0),
            )
        )
        assertEquals(
            listOf("photos", "new.txt", "old.txt"),
            state.sortedBy(Sorting.NEWEST).visible.map { it.name },
        )
        assertEquals(
            listOf("photos", "old.txt", "new.txt"),
            state.sortedBy(Sorting.LARGEST).visible.map { it.name },
        )
    }

    Suite.test("a half-finished upload is not offered as a file") {
        val state = BrowseState(share = share()).loaded(
            listOf(file("holiday.mp4"), file("holiday.mp4.lanlink-part"))
        )
        assertEquals(listOf("holiday.mp4"), state.visible.map { it.name }, "the .part is not a file")
    }

    Suite.test("search filters without losing the folders-first rule") {
        val state = BrowseState(share = share())
            .loaded(listOf(file("report.pdf"), folder("reports"), file("photo.jpg")))
            .searching("rep")
        assertEquals(listOf("reports", "report.pdf"), state.visible.map { it.name })
    }

    Suite.test("an empty folder and an empty search say different things") {
        val empty = BrowseState(share = share()).loaded(emptyList())
        assertTrue(empty.isEmpty, "nothing to show")
        assertTrue(empty.emptyMessage.contains("empty"), "and it says the folder is empty")

        val filtered = BrowseState(share = share()).loaded(listOf(file("a.txt"))).searching("zzz")
        assertTrue(filtered.isEmpty, "a search that matches nothing is empty")
        assertTrue(filtered.emptyMessage.contains("zzz"), "and it says what was searched for")
    }

    Suite.test("the path bar names every step from the share to here") {
        val state = BrowseState(share = share(), path = "photos/2026/june")
        assertEquals(listOf("Demo", "photos", "2026", "june"), state.crumbs.map { it.label })
        assertEquals(listOf("", "photos", "photos/2026", "photos/2026/june"), state.crumbs.map { it.path })
    }

    Suite.test("opening a folder goes to its path and clears the search") {
        val state = BrowseState(share = share(), path = "photos")
            .loaded(listOf(folder("2026", path = "photos/2026")))
            .searching("20")
        val opened = state.into(state.visible.first())

        assertEquals("photos/2026", opened.path)
        assertEquals("", opened.query, "a search does not follow you into a folder")
        assertTrue(opened.loading, "and the listing is being fetched")
    }

    Suite.test("a file is not a folder and does not navigate") {
        val state = BrowseState(share = share()).loaded(listOf(file("a.txt")))
        assertEquals(state, state.into(state.visible.first()), "nothing moved")
    }

    Suite.test("a listing cannot walk the phone out of its share") {
        // §9: a path from the other end is checked before it is used, whatever
        // the other end claims it is.
        val state = BrowseState(share = share())
        for (hostile in listOf("../secrets", "/etc/passwd", "photos/../../root")) {
            val moved = state.into(folder("x", path = hostile))
            assertEquals("", moved.path, "<$hostile> did not move anything")
            assertNotNull(moved.problem, "<$hostile> was reported")
        }
    }

    Suite.test("up goes one level, and stops at the root") {
        val deep = BrowseState(share = share(), path = "photos/2026")
        assertEquals("photos", deep.up()!!.path)
        assertEquals("", deep.up()!!.up()!!.path)
        assertNull(deep.up()!!.up()!!.up(), "the root is where up ends")
        assertTrue(deep.up()!!.up()!!.backLeavesTheScreen, "so back leaves the browser")
    }

    Suite.test("jumping to a crumb goes straight there") {
        val state = BrowseState(share = share(), path = "photos/2026/june")
        val jumped = state.jumpTo(state.crumbs[1])
        assertEquals("photos", jumped.path)
        assertTrue(jumped.loading, "and it fetches that folder")
        assertEquals(state, state.jumpTo(state.crumbs.last()), "jumping to where you are does nothing")
    }

    Suite.test("what the screen may offer follows the share's permissions") {
        assertTrue(BrowseState(share = share("rwd")).canWrite, "w means upload is offered")
        assertTrue(BrowseState(share = share("rwd")).canDelete, "d means delete is offered")
        assertFalse(BrowseState(share = share("r")).canWrite, "read-only means read-only")
        assertFalse(BrowseState(share = share("rw")).canDelete, "without d, nothing is deleted")
    }

    Suite.test("a failed listing shows the reason instead of a stale one") {
        val state = BrowseState(share = share()).loaded(listOf(file("a.txt"))).failed("The device went away.")
        assertTrue(state.visible.isEmpty(), "the old listing is not left on screen")
        assertFalse(state.loading, "and it is not still spinning")
        assertEquals("The device went away.", state.problem)
    }

    // ------------------------------------------------------------ saving names

    Suite.group("download names")

    Suite.test("a name that would collide is numbered, not overwritten") {
        assertEquals("photo.jpg", downloadName("photo.jpg", emptySet()))
        assertEquals("photo (2).jpg", downloadName("photo.jpg", setOf("photo.jpg")))
        assertEquals("photo (3).jpg", downloadName("photo.jpg", setOf("photo.jpg", "photo (2).jpg")))
        assertEquals("README (2)", downloadName("README", setOf("README")), "no extension, still numbered")
    }

    Suite.test("a hostile name from the other end never becomes a path") {
        for (hostile in listOf("../../etc/passwd", "a/b.txt", "..", "  ", "CON")) {
            val safe = downloadName(hostile, emptySet())
            assertFalse(safe.contains('/'), "<$hostile> kept a separator")
            assertFalse(safe == "." || safe == "..", "<$hostile> stayed a traversal")
            assertTrue(safe.isNotBlank(), "<$hostile> left nothing")
        }
    }

    Suite.test("a partial download is held under a name nothing else will list") {
        assertEquals("film.mp4.lanlink-part", partialName("film.mp4"))
    }

    // ---------------------------------------------------------------- transfers

    Suite.group("transfers")

    Suite.test("one at a time: the second waits for the first") {
        val queue = TransferQueue()
        val first = queue.enqueueDownload(known(), "s1", "", "a.txt", 100)
        queue.enqueueDownload(known(), "s1", "", "b.txt", 100)

        assertEquals(first.id, queue.next()!!.id, "the first one runs")
        assertNull(queue.next(), "and the second waits its turn")
        queue.finished(first.id)
        assertEquals("b.txt", queue.next()!!.name, "then it runs")
    }

    Suite.test("progress never walks backwards") {
        val queue = TransferQueue()
        val item = queue.enqueueDownload(known(), "s1", "", "a.txt", 100)
        queue.next()
        queue.progress(item.id, 60)
        assertEquals(60L, queue.progress(item.id, 10)!!.transferred, "a late report does not undo real progress")
        assertEquals(0.6f, queue.find(item.id)!!.fraction!!, "and the bar agrees")
    }

    Suite.test("finishing fills the bar even when the size was never known") {
        val queue = TransferQueue()
        val item = queue.enqueueDownload(known(), "s1", "", "a.txt", null)
        queue.next()
        queue.progress(item.id, 4242)
        val done = queue.finished(item.id)!!
        assertEquals(TransferState.DONE, done.state)
        assertEquals(4242L, done.transferred, "what actually moved is what is reported")
        assertTrue(done.summary.contains("Finished"), "and it says so")
    }

    Suite.test("a failure resumes from what the other end confirmed") {
        val queue = TransferQueue()
        val item = queue.enqueueUpload(known(), "s1", "", "film.mp4", 1000, "content://x")
        queue.next()
        queue.progress(item.id, 800)

        // The phone had written 800; the PC confirmed 512. 512 is the truth.
        val failed = queue.failed(item.id, "Wi-Fi went away", reached = 512)!!
        assertEquals(512L, failed.transferred, "resume from what landed, not from what was sent")
        assertTrue(failed.canResume, "something moved, so there is something to resume")

        val again = queue.retry(item.id)!!
        assertEquals(TransferState.WAITING, again.state)
        assertEquals(512L, again.transferred, "and it keeps that when it goes back in the queue")
    }

    Suite.test("a retry that had moved nothing starts from the beginning") {
        val queue = TransferQueue()
        val item = queue.enqueueDownload(known(), "s1", "", "a.txt", 100)
        queue.next()
        queue.failed(item.id, "refused")
        assertFalse(queue.find(item.id)!!.canResume, "nothing to resume from")
        assertEquals(0L, queue.retry(item.id)!!.transferred)
    }

    Suite.test("cancelling a waiting transfer is immediate; a running one is asked") {
        val queue = TransferQueue()
        val running = queue.enqueueDownload(known(), "s1", "", "a.txt", 100)
        val waiting = queue.enqueueDownload(known(), "s1", "", "b.txt", 100)
        queue.next()

        assertEquals(TransferState.CANCELLED, queue.cancel(waiting.id)!!.state, "it never started")

        queue.cancel(running.id)
        assertTrue(queue.isCancelling(running.id), "the worker is asked to stop")
        assertEquals(TransferState.RUNNING, queue.find(running.id)!!.state, "and the file is closed first")

        val stopped = queue.failed(running.id, "cancelled by the user")!!
        assertEquals(TransferState.CANCELLED, stopped.state, "which is a cancellation, not a failure")
        assertNull(stopped.problem, "and not something to apologise for")
    }

    Suite.test("a device leaving fails everything queued for it, and nothing else") {
        val queue = TransferQueue()
        val other = known().copy(id = "pc-2", name = "Laptop")
        val mine = queue.enqueueDownload(known(), "s1", "", "a.txt", 100)
        val theirs = queue.enqueueDownload(other, "s1", "", "b.txt", 100)

        assertEquals(1, queue.deviceLost("pc-1"), "one transfer was for that device")
        assertEquals(TransferState.FAILED, queue.find(mine.id)!!.state)
        assertEquals(TransferState.WAITING, queue.find(theirs.id)!!.state, "the other device is unaffected")
        assertTrue(queue.find(mine.id)!!.problem!!.contains("left the network"), "and it says why")
    }

    Suite.test("an uploaded name is sanitised before it is ever sent") {
        val queue = TransferQueue()
        val item = queue.enqueueUpload(known(), "s1", "", "../../evil.txt", 10, "content://x")
        assertFalse(item.name.contains('/'), "the peer never sees a path")
    }

    Suite.test("a download is named so it cannot overwrite what is already there") {
        val queue = TransferQueue()
        val item = queue.enqueueDownload(known(), "s1", "", "photo.jpg", 10, taken = setOf("photo.jpg"))
        assertEquals("photo (2).jpg", item.name)
    }

    Suite.test("clearing finished rows leaves the moving ones alone") {
        val queue = TransferQueue()
        val done = queue.enqueueDownload(known(), "s1", "", "a.txt", 1)
        queue.enqueueDownload(known(), "s1", "", "b.txt", 1)
        queue.next()
        queue.finished(done.id)

        assertEquals(1, queue.clearFinished())
        assertEquals(1, queue.all().size)
        assertEquals("b.txt", queue.all().first().name)
    }

    Suite.test("the notification line says what is actually happening") {
        val queue = TransferQueue()
        assertEquals("No transfers", queue.overallSummary())
        val first = queue.enqueueDownload(known(), "s1", "", "a.txt", 1)
        queue.enqueueDownload(known(), "s1", "", "b.txt", 1)
        queue.next()
        assertEquals("1 in progress, 1 waiting", queue.overallSummary())
        queue.finished(first.id)
        queue.next()
        assertEquals("1 in progress", queue.overallSummary())
    }

    Suite.test("sizes are readable by a person") {
        assertEquals("512 B", bytes(512))
        assertEquals("1.0 KB", bytes(1024))
        assertEquals("1.5 MB", bytes(1024 * 1024 * 3 / 2))
        assertTrue(bytes(5L * 1024 * 1024 * 1024).endsWith("GB"), "gigabytes stay gigabytes")
    }

    Suite.test("both directions are queued the same way and reported apart") {
        val queue = TransferQueue()
        queue.enqueueDownload(known(), "s1", "", "in.txt", 1)
        queue.enqueueUpload(known(), "s1", "", "out.txt", 1, "content://x")
        assertEquals(1, queue.all().count { it.direction == Direction.DOWNLOAD })
        assertEquals(1, queue.all().count { it.direction == Direction.UPLOAD })
    }
}

fun registerInviteTests() {
    Suite.group("invites")

    val pin = "a".repeat(64)

    Suite.test("a full invite carries everything needed to pin and pair") {
        val url = link.lan.app.inviteFor(
            host = "192.168.1.4", port = 8765, code = "123456",
            deviceId = "pc-1", name = "Desk PC", fingerprint = pin,
        )
        val checked = link.lan.app.examineInvite(url)
        assertTrue(checked is link.lan.app.InviteCheck.Ready, "it is worth pairing with")
        val ready = checked as link.lan.app.InviteCheck.Ready

        assertEquals(pin, ready.pinnedFingerprint, "the pin came from the invite")
        assertFalse(ready.needsFingerprintByEye, "so nobody reads hex off a screen")
        assertFalse(ready.needsCodeTyped, "and the code came with it")
        assertEquals("Desk PC", ready.label)
    }

    Suite.test("an address typed by hand still works, with the comparison by eye") {
        val ready = link.lan.app.examineInvite("192.168.1.4:8765") as link.lan.app.InviteCheck.Ready
        assertTrue(ready.needsFingerprintByEye, "no pin was supplied, so a person checks")
        assertTrue(ready.needsCodeTyped, "and types the code")
        assertEquals("192.168.1.4:8765", ready.label)
    }

    Suite.test("a bare host gets the default port rather than a refusal") {
        val ready = link.lan.app.examineInvite("192.168.1.4") as link.lan.app.InviteCheck.Ready
        assertEquals(link.lan.core.DEFAULT_PORT, ready.invite.port)
    }

    Suite.test("a plaintext invite is refused: the code and the token would be in the open") {
        val rejected = link.lan.app.examineInvite("lanlink://pair?host=h&port=8765&scheme=http")
        assertTrue(rejected is link.lan.app.InviteCheck.Rejected, "not without TLS")
        assertTrue((rejected as link.lan.app.InviteCheck.Rejected).reason.contains("open"), "and it says why")

        // Somebody deliberately troubleshooting with TLS off can still say so.
        val allowed = link.lan.app.examineInvite(
            "lanlink://pair?host=h&port=8765&scheme=http", allowInsecure = true
        )
        assertTrue(allowed is link.lan.app.InviteCheck.Ready, "but only on purpose")
    }

    Suite.test("a damaged fingerprint is not guessed at") {
        val rejected = link.lan.app.examineInvite("lanlink://pair?host=h&port=8765&fp=abc123")
        assertTrue(rejected is link.lan.app.InviteCheck.Rejected, "half a pin is not a pin")
    }

    Suite.test("a bare computer name is an address; prose is not") {
        // Windows machines answer to their own name, so `desk-pc` is a real
        // address. Anything with a space in it never was one.
        val named = link.lan.app.examineInvite("desk-pc")
        assertTrue(named is link.lan.app.InviteCheck.Ready, "a hostname is usable")

        for (text in listOf("", "   ", "not an address", "what is this", "lanlink://something-else?host=h&port=1")) {
            val outcome = link.lan.app.examineInvite(text)
            assertTrue(outcome is link.lan.app.InviteCheck.Rejected, "<$text> was refused")
            assertTrue(
                (outcome as link.lan.app.InviteCheck.Rejected).reason.isNotBlank(),
                "<$text> was explained",
            )
        }
    }

    Suite.test("a device already paired is recognised rather than paired twice") {
        val store = link.lan.app.DeviceStore(listOf(known()))
        val url = link.lan.app.inviteFor(host = "192.168.1.4", deviceId = "pc-1", name = "Desk PC", fingerprint = pin)
        val outcome = link.lan.app.examineInvite(url, store)

        assertTrue(outcome is link.lan.app.InviteCheck.AlreadyPaired, "it is already known")
        assertTrue(
            (outcome as link.lan.app.InviteCheck.AlreadyPaired).message.contains("Desk PC"),
            "and it says which device",
        )
    }

    Suite.test("a port that is not a port is refused") {
        val rejected = link.lan.app.examineInvite("lanlink://pair?host=h&port=99999")
        assertTrue(rejected is link.lan.app.InviteCheck.Rejected, "99999 is not a port")
    }

    Suite.test("an invite's pin is checked by the phone, not by the person") {
        val certificate = SelfSignedForTests.certificate()
        val real = link.lan.core.Pinning.fingerprintOf(certificate)

        val matching = link.lan.app.PairingFlow("h", 1, "phone", "Phone", probe = { _, _ -> certificate })
        matching.probeCertificate()
        assertTrue(matching.confirmAgainst(real.uppercase()), "case does not matter")
        assertEquals(link.lan.app.PairingStep.AWAITING_CODE, matching.step)

        var connected = false
        val wrong = link.lan.app.PairingFlow(
            "h", 1, "phone", "Phone",
            probe = { _, _ -> certificate },
            connect = { connected = true; error("must not be reached") },
        )
        wrong.probeCertificate()
        assertFalse(wrong.confirmAgainst("b".repeat(64)), "the invite said something else")
        assertEquals(link.lan.app.PairingStep.FAILED, wrong.step)
        assertFalse(connected, "and nothing was sent to it")
        assertNull(wrong.submitCode("123456"), "not even afterwards")
    }
}
