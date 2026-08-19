package link.lan.core.test

import link.lan.core.ContentRange
import link.lan.core.Device
import link.lan.core.Downloads
import link.lan.core.Entry
import link.lan.core.Invite
import link.lan.core.InvalidInvite
import link.lan.core.Json
import link.lan.core.JsonError
import link.lan.core.Paths
import link.lan.core.Pinning
import link.lan.core.Progress
import link.lan.core.PartialStatus
import link.lan.core.Properties
import link.lan.core.ProtocolError
import link.lan.core.ResumeDecision
import link.lan.core.Share
import link.lan.core.Uploads
import link.lan.core.boolean
import link.lan.core.child
import link.lan.core.long
import link.lan.core.longOrNull
import link.lan.core.objects
import link.lan.core.string

fun registerCoreTests() {

    // ------------------------------------------------------------------- json

    Suite.group("json")

    Suite.test("parses the objects the protocol returns") {
        val values = Json.parseObject(
            """{"shares":[{"id":"share_1","name":"Demo","permissions":"rw","available":true}]}"""
        )
        val shares = values.objects("shares")
        assertEquals(1, shares.size)
        assertEquals("Demo", shares[0].string("name"))
        assertTrue(shares[0].boolean("available"), "available should be true")
    }

    Suite.test("keeps whole numbers whole and timestamps fractional") {
        val values = Json.parseObject("""{"size":4823910,"modified_at":1755100000.5,"missing":null}""")
        assertEquals(4823910L, values.long("size"))
        assertEquals(1755100000.5, values["modified_at"])
        assertNull(values.longOrNull("missing"))
        assertNull(values.longOrNull("absent"))
    }

    Suite.test("handles escapes, unicode and nesting") {
        val values = Json.parseObject("""{"a":"line\nbreak \u00e9 \"q\" \\","b":{"c":[1,2,{"d":true}]}}""")
        assertEquals("line\nbreak é \"q\" \\", values.string("a"))
        assertEquals(3, (values.child("b")["c"] as List<*>).size)
    }

    Suite.test("an unknown member is ignored, not an error") {
        val entry = Entry.from(
            Json.parseObject(
                """{"name":"a.txt","path":"a.txt","kind":"file","size":5,
                   "modified_at":1.0,"colour":"purple","extra":{"deep":true}}"""
            )
        )
        assertEquals("a.txt", entry.name)
        assertEquals(5L, entry.size)
    }

    Suite.test("refuses what it cannot understand rather than guessing") {
        assertThrows<JsonError>("truncated object") { Json.parse("""{"a":1""") }
        assertThrows<JsonError>("trailing text") { Json.parse("""{"a":1} rubbish""") }
        assertThrows<JsonError>("not an object") { Json.parseObject("""[1,2]""") }
        assertThrows<JsonError>("bad escape") { Json.parse(""""\q"""") }
    }

    Suite.test("encodes a request body safely") {
        val body = Json.encodeObject(mapOf("client_name" to """He said "hi"\ok"""))
        assertEquals("""{"client_name":"He said \"hi\"\\ok"}""", body)
    }

    // --------------------------------------------------------------- protocol

    Suite.group("protocol")

    Suite.test("a share reports its permissions") {
        val readOnly = Share.from(Json.parseObject("""{"id":"s","name":"N","permissions":"r","available":true}"""))
        assertTrue(readOnly.canRead, "r means readable")
        assertFalse(readOnly.canWrite, "r does not mean writable")
        assertFalse(readOnly.canDelete, "r does not mean deletable")

        val full = Share.from(Json.parseObject("""{"id":"s","name":"N","permissions":"rwd","available":true}"""))
        assertTrue(full.canWrite && full.canDelete, "rwd allows everything")
    }

    Suite.test("a folder has no size and a file does") {
        val folder = Entry.from(Json.parseObject("""{"name":"Sub","path":"Sub","kind":"folder","size":null,"modified_at":1.0}"""))
        assertTrue(folder.isFolder, "kind folder")
        assertNull(folder.size)
        val file = Entry.from(Json.parseObject("""{"name":"a.txt","path":"a.txt","kind":"file","size":5,"modified_at":1.0}"""))
        assertTrue(file.isFile, "kind file")
        assertEquals(5L, file.size)
    }

    Suite.test("properties carry the item count only for a folder") {
        val root = Properties.from(
            Json.parseObject(
                """{"name":"Demo","path":"","kind":"folder","size":null,"modified_at":1.0,
                   "created_at":1.0,"accessed_at":1.0,"extension":"","read_only":false,
                   "share":"Demo","share_permissions":"rw","item_count":{"folders":1,"files":12}}"""
            )
        )
        assertEquals(1L, root.folderCount)
        assertEquals(12L, root.fileCount)
        assertEquals("", root.path)
    }

    Suite.test("partial status says where to resume") {
        val status = PartialStatus.from(Json.parseObject("""{"received":4194304,"complete":false,"size":null}"""))
        assertEquals(4194304L, status.received)
        assertFalse(status.complete, "not finished yet")
        assertNull(status.size)
    }

    Suite.test("an error body becomes a sentence and a status") {
        val error = ProtocolError.of(409, """{"detail":"A file with this name already exists."}""")
        assertEquals(409, error.status)
        assertTrue(error.isConflict, "409 is a conflict")
        assertEquals("A file with this name already exists.", error.detail)
    }

    Suite.test("a validation body is summarised rather than shown raw") {
        val error = ProtocolError.of(
            422,
            """{"detail":[{"type":"string_too_short","loc":["body","pair_code"],
               "msg":"String should have at least 6 characters"}]}"""
        )
        assertTrue(error.detail.contains("at least 6 characters"), "kept the message")
    }

    Suite.test("a non-json error body is still usable") {
        val error = ProtocolError.of(500, "Internal Server Error")
        assertEquals("Internal Server Error", error.detail)
    }

    Suite.test("only some failures are worth retrying") {
        assertTrue(ProtocolError(503, "").isWorthRetrying, "5xx")
        assertTrue(ProtocolError(429, "").isWorthRetrying, "rate limited")
        assertFalse(ProtocolError(401, "").isWorthRetrying, "revoked pairing")
        assertFalse(ProtocolError(403, "").isWorthRetrying, "permission denied")
        assertFalse(ProtocolError(409, "").isWorthRetrying, "name taken")
    }

    Suite.test("a device reports its fingerprint in lower case") {
        val device = Device.from(
            Json.parseObject("""{"id":"d","name":"PC","hostname":"h","platform":"Windows","version":"0.1.0","fingerprint":"AB12"}""")
        )
        assertEquals("ab12", device.fingerprint)
    }

    // ------------------------------------------------------------ paths, §29

    Suite.group("paths")

    Suite.test("an ordinary name is accepted") {
        for (name in listOf("gripper.step", "base plate.dwg", "a", "notes.txt", "x".repeat(255))) {
            assertTrue(Paths.isSafeName(name), "$name should be allowed")
        }
    }

    Suite.test("a name that is not one safe leaf is refused") {
        for (name in listOf(
            "", "   ", ".", "..", "a/b", "a\\b", "../escape", "CON", "con.txt", "PRN", "COM1", "LPT9",
            "trailing.", "trailing ", " leading", "a<b", "a>b", "a:b", "a\"b", "a|b", "a?b", "a*b",
            "x".repeat(256),
        )) {
            assertFalse(Paths.isSafeName(name), "$name should be refused")
            assertNotNull(Paths.nameProblem(name), "$name should say why")
        }
    }

    Suite.test("a refused name can be turned into one the peer accepts") {
        assertEquals("a_b", Paths.sanitiseForPeer("a/b"))
        assertEquals("photo_1.jpg", Paths.sanitiseForPeer("photo:1.jpg"))
        assertEquals("_CON", Paths.sanitiseForPeer("CON"))
        assertEquals("name", Paths.sanitiseForPeer("name. "))
        assertEquals("file", Paths.sanitiseForPeer("   "))
        for (hostile in listOf("a/b", "photo:1.jpg", "CON", "name. ", "  ", "x".repeat(400), "a?b*c")) {
            val cleaned = Paths.sanitiseForPeer(hostile)
            assertTrue(Paths.isSafeName(cleaned), "sanitised '$hostile' to '$cleaned' which is still refused")
        }
    }

    Suite.test("a path that would leave the share is refused") {
        for (path in listOf("../secret", "..", "a/../../b", "/etc/passwd", "//server/share", "C:/Windows", "a//b")) {
            assertFalse(Paths.isSafePath(path), "$path should be refused")
        }
    }

    Suite.test("a listing path is accepted and round trips") {
        for (path in listOf("", "a.txt", "cad/parts", "cad/parts/gripper.step")) {
            assertTrue(Paths.isSafePath(path), "$path should be allowed")
        }
        assertEquals("cad/parts/a.txt", Paths.join("cad/parts", "a.txt"))
        assertEquals("a.txt", Paths.join("", "a.txt"))
        assertEquals("cad", Paths.parentOf("cad/parts"))
        assertEquals("", Paths.parentOf("cad"))
        assertEquals("gripper.step", Paths.leafOf("cad/parts/gripper.step"))
    }

    Suite.test("the breadcrumb trail walks back to the share root") {
        assertEquals(listOf("", "cad", "cad/parts"), Paths.trail("cad/parts"))
        assertEquals(listOf(""), Paths.trail(""))
    }

    Suite.test("an unfinished upload is recognised and never shown") {
        assertTrue(Paths.isPartial("photo.jpg.lanlink-part"), "part file")
        assertFalse(Paths.isPartial("photo.jpg"), "real file")
    }

    // ---------------------------------------------------------- transfers, §11

    Suite.group("download")

    Suite.test("a range header is only sent when resuming") {
        assertNull(Downloads.rangeHeader(0))
        assertEquals("bytes=1048576-", Downloads.rangeHeader(1048576))
    }

    Suite.test("206 with a content range appends at the offset the peer used") {
        val decision = Downloads.decide(206, 2, "bytes 2-4/5", 3)
        assertTrue(decision is ResumeDecision.Append, "should append")
        decision as ResumeDecision.Append
        assertEquals(2L, decision.offset)
        assertEquals(5L, decision.total)
    }

    Suite.test("200 in reply to a range means start over") {
        val decision = Downloads.decide(200, 1024, null, 4096)
        assertTrue(decision is ResumeDecision.StartOver, "a peer that ignored Range must not be appended to")
        assertEquals(4096L, (decision as ResumeDecision.StartOver).total)
    }

    Suite.test("416 means the file changed underneath us") {
        assertTrue(Downloads.decide(416, 5000) is ResumeDecision.Restart, "restart from zero")
    }

    Suite.test("a 206 whose content range we cannot read is not appended to") {
        assertTrue(
            Downloads.decide(206, 10, "bytes bananas", 3) is ResumeDecision.StartOver,
            "an unreadable Content-Range must not be trusted",
        )
    }

    Suite.test("we honour the offset the peer reports, not the one we asked for") {
        val decision = Downloads.decide(206, 500, "bytes 0-99/100", 100) as ResumeDecision.Append
        assertEquals(0L, decision.offset, "the peer restarted the range")
    }

    Suite.test("content range parsing rejects nonsense") {
        assertNull(ContentRange.parse(null))
        assertNull(ContentRange.parse("bytes */5"))
        assertNull(ContentRange.parse("bytes 5-2/9"), )
        assertNull(ContentRange.parse("bytes 0-9/5"))
        val range = ContentRange.parse("bytes 2-4/5")
        assertNotNull(range)
        assertEquals(3L, range!!.length)
    }

    Suite.test("the total size comes from the right header") {
        assertEquals(5L, Downloads.totalSize(206, "bytes 2-4/5", 3))
        assertEquals(4096L, Downloads.totalSize(200, null, 4096))
    }

    Suite.group("upload")

    Suite.test("resume never runs past the end of our own file") {
        assertEquals(100L, Uploads.resumeOffset(100, 500))
        assertEquals(500L, Uploads.resumeOffset(900, 500), "a peer claiming too much is clamped")
        assertEquals(0L, Uploads.resumeOffset(-5, 500))
    }

    Suite.test("a 409 carrying the received count is an instruction to resume") {
        assertEquals(4L, Uploads.offsetFromConflict(409, "4"))
        assertNull(Uploads.offsetFromConflict(409, null), "a plain 409 means the name is taken")
        assertNull(Uploads.offsetFromConflict(409, "not a number"))
        assertNull(Uploads.offsetFromConflict(200, "4"))
    }

    Suite.test("remaining bytes never go negative") {
        assertEquals(400L, Uploads.remaining(500, 100))
        assertEquals(0L, Uploads.remaining(500, 900))
    }

    Suite.test("progress starts from the resume offset") {
        val progress = Progress.startingAt(2048, 4096)
        assertEquals(0.5, progress.fraction)
        assertEquals(0.75, progress.advanced(1024).fraction)
        assertNull(Progress(10, null).fraction, "unknown total means unknown progress")
    }

    // -------------------------------------------------------------- invites, §5

    Suite.group("invite")

    Suite.test("a full invite round trips") {
        val invite = Invite(
            host = "192.168.1.20", port = 8765, code = "12345678",
            deviceId = "dev-1", name = "Workshop PC", fingerprint = "ab".repeat(32),
        )
        val parsed = Invite.parse(invite.toUrl())
        assertEquals(invite.host, parsed.host)
        assertEquals(invite.port, parsed.port)
        assertEquals(invite.code, parsed.code)
        assertEquals(invite.fingerprint, parsed.fingerprint)
        assertEquals("Workshop PC", parsed.name)
        assertTrue(parsed.hasPin, "a 64 character fingerprint is a pin")
        assertTrue(parsed.isSecure, "https by default")
    }

    Suite.test("a name with spaces survives the QR round trip") {
        val parsed = Invite.parse(Invite("10.0.0.5", 8765, name = "Sha's Laptop #2").toUrl())
        assertEquals("Sha's Laptop #2", parsed.name)
    }

    Suite.test("a short fingerprint is a hint, not a pin") {
        assertFalse(Invite("h", 1, fingerprint = "ab".repeat(16)).hasPin, "32 characters is only a hint")
        assertFalse(Invite("h", 1).hasPin, "no fingerprint at all")
    }

    Suite.test("a bare address is accepted with the default port") {
        assertEquals(8765, Invite.parse("192.168.1.20").port)
        assertEquals(9000, Invite.parse("192.168.1.20:9000").port)
        assertEquals("192.168.1.20", Invite.parse("https://192.168.1.20:8765").host)
        assertFalse(Invite.parse("http://10.0.0.5:8765").isSecure, "http is a warning state")
    }

    Suite.test("rubbish is refused with something a person can read") {
        assertThrows<InvalidInvite>("empty") { Invite.parse("   ") }
        assertThrows<InvalidInvite>("wrong action") { Invite.parse("lanlink://open?host=a&port=1") }
        assertThrows<InvalidInvite>("no address") { Invite.parse("lanlink://pair?code=12345678") }
        assertThrows<InvalidInvite>("bad port") { Invite.parse("10.0.0.5:donkey") }
    }

    // -------------------------------------------------------------- pinning, §4

    Suite.group("pinning")

    Suite.test("the short fingerprint is grouped the way the desktop shows it") {
        val full = "a1b2c3d4e5f60789" + "0".repeat(48)
        assertEquals("A1B2 C3D4 E5F6 0789", Pinning.shortFingerprint(full))
    }

    Suite.test("a fingerprint with colons is still readable") {
        assertEquals("A1B2 C3D4", Pinning.shortFingerprint("a1:b2:c3:d4"))
    }
}
