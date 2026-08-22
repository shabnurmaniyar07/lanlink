package link.lan.app.test

import link.lan.app.DeviceStanding
import link.lan.app.DeviceStore
import link.lan.app.KnownDevice
import link.lan.app.PairingFlow
import link.lan.app.PairingStep
import link.lan.app.Reconnection
import link.lan.app.SeenDevice
import link.lan.app.folderNameFor
import link.lan.app.mergeDevices
import link.lan.app.reconnect
import link.lan.core.CertificateProbe
import link.lan.core.LanLinkClient
import link.lan.core.PairOutcome
import link.lan.core.Pinning
import link.lan.core.test.Suite
import link.lan.core.test.assertEquals
import link.lan.core.test.assertFalse
import link.lan.core.test.assertNotNull
import link.lan.core.test.assertNull
import link.lan.core.test.assertTrue
import java.security.cert.X509Certificate
import java.util.UUID

private fun device(
    id: String = "pc-1",
    name: String = "Desk PC",
    host: String = "192.168.1.4",
    port: Int = 8765,
    fingerprint: String = "a".repeat(64),
    token: String = "token-$id",
) = KnownDevice(id, name, host, port, fingerprint, token)

fun registerLogicTests() {
    Suite.group("store")

    Suite.test("a stored device survives being written and read back") {
        val store = DeviceStore()
        store.remember(device(name = "Desk PC", token = "t-1"))
        store.remember(device(id = "pc-2", name = "Laptop", token = "t-2"))

        val reopened = DeviceStore.fromJson(store.asJson())
        assertEquals(2, reopened.all().size, "both devices came back")
        val first = reopened.find("pc-1")
        assertNotNull(first, "pc-1 came back")
        assertEquals("t-1", first!!.token, "the token survived")
        assertEquals("Desk PC", first.name)
        assertEquals(8765, first.port, "the port survived as a number")
    }

    Suite.test("a name with quotes and backslashes does not corrupt the file") {
        val store = DeviceStore()
        store.remember(device(name = """Sha"bnur\'s "PC"""", token = "t"))
        val reopened = DeviceStore.fromJson(store.asJson())
        assertEquals(1, reopened.all().size, "the store was still readable")
        assertEquals("""Sha"bnur\'s "PC"""", reopened.all().first().name)
    }

    Suite.test("damaged storage is an empty list, never a crash") {
        assertEquals(0, DeviceStore.fromJson(null).all().size)
        assertEquals(0, DeviceStore.fromJson("").all().size)
        assertEquals(0, DeviceStore.fromJson("not json at all").all().size)
        assertEquals(0, DeviceStore.fromJson("""{"devices":[{"id":"x"}]}""").all().size)
    }

    Suite.test("one damaged record costs one pairing, not all of them") {
        // Both are required together: a token used against an unpinned socket is
        // exactly the attack pinning exists to stop.
        val stored = """{"devices":[
            {"id":"a","token":"t","fingerprint":""},
            {"id":"b","token":"","fingerprint":"ff"},
            {"id":"c","token":"t","fingerprint":"ff","host":"h","port":"9000"}
        ]}"""
        val store = DeviceStore.fromJson(stored)
        assertEquals(1, store.all().size, "only the complete entry was kept")
        assertEquals("c", store.all().first().id)
        assertEquals(9000, store.all().first().port)
    }

    Suite.test("pairing again replaces the token and the pin together") {
        val store = DeviceStore()
        store.remember(device(fingerprint = "a".repeat(64), token = "old"))
        store.remember(device(fingerprint = "b".repeat(64), token = "new"))
        assertEquals(1, store.all().size, "the same device is not listed twice")
        assertEquals("new", store.find("pc-1")!!.token)
        assertEquals("b".repeat(64), store.find("pc-1")!!.fingerprint)
    }

    Suite.test("forgetting a device returns it, so the caller can also unpair remotely") {
        val store = DeviceStore(listOf(device()))
        val dropped = store.forget("pc-1")
        assertNotNull(dropped, "the device was handed back")
        assertEquals("token-pc-1", dropped!!.token, "with its token, to unpair with")
        assertFalse(store.isPaired("pc-1"), "and it is gone")
        assertNull(store.forget("pc-1"), "forgetting twice is not an error")
    }

    Suite.test("a device seen at a new address is followed") {
        val store = DeviceStore(listOf(device(host = "192.168.1.4")))
        store.seen("pc-1", "192.168.1.99", 8766, moment = 1234)
        val moved = store.find("pc-1")!!
        assertEquals("192.168.1.99:8766", moved.address, "DHCP moved it and the store kept up")
        assertEquals(1234L, moved.lastSeen)
        assertNull(store.seen("nobody", "h", 1, 1), "an unknown device is not invented")
    }

    // ----------------------------------------------------------------- merging

    Suite.group("device list")

    Suite.test("paired and discovered devices are both listed, once each") {
        val known = listOf(device(id = "pc-1", name = "Desk PC"), device(id = "pc-2", name = "Laptop"))
        val seen = listOf(
            SeenDevice("pc-1", "Desk PC", "192.168.1.4", 8765, "a".repeat(64)),
            SeenDevice("pc-3", "Someone else", "192.168.1.9", 8765),
        )
        val rows = mergeDevices(known, seen)

        assertEquals(3, rows.size, "two paired plus one stranger")
        assertEquals(DeviceStanding.READY, rows.first { it.id == "pc-1" }.standing)
        assertEquals(DeviceStanding.AWAY, rows.first { it.id == "pc-2" }.standing)
        assertEquals(DeviceStanding.NEW, rows.first { it.id == "pc-3" }.standing)
        assertTrue(rows.first { it.id == "pc-1" }.canOpen, "a device that is here can be opened")
        assertFalse(rows.first { it.id == "pc-2" }.canOpen, "one that is away cannot")
    }

    Suite.test("a paired device presenting a new certificate is called out, not quietly used") {
        val known = listOf(device(id = "pc-1", fingerprint = "a".repeat(64)))
        val seen = listOf(SeenDevice("pc-1", "Desk PC", "192.168.1.4", 8765, "b".repeat(64)))
        val row = mergeDevices(known, seen).first()

        assertEquals(DeviceStanding.IMPOSTOR, row.standing)
        assertFalse(row.canOpen, "nothing connects to it until the person pairs again")
    }

    Suite.test("mDNS advertises half a fingerprint, and half is enough to recognise") {
        // The desktop publishes fp[:32] in its TXT record — a hint, not a pin.
        // Comparing it whole would mark every paired device an impostor.
        val pin = "a".repeat(64)
        val known = listOf(device(id = "pc-1", fingerprint = pin))
        val advertised = listOf(SeenDevice("pc-1", "Desk PC", "h", 8765, pin.take(32)))
        assertEquals(DeviceStanding.READY, mergeDevices(known, advertised).first().standing)

        val wrong = listOf(SeenDevice("pc-1", "Desk PC", "h", 8765, "b".repeat(32)))
        assertEquals(
            DeviceStanding.IMPOSTOR,
            mergeDevices(known, wrong).first().standing,
            "a prefix that disagrees is still a disagreement",
        )
    }

    Suite.test("discovery without a fingerprint does not accuse anybody") {
        // mDNS records need not carry the fingerprint; absence is not a mismatch.
        val known = listOf(device(id = "pc-1"))
        val seen = listOf(SeenDevice("pc-1", "Desk PC", "192.168.1.4", 8765, ""))
        assertEquals(DeviceStanding.READY, mergeDevices(known, seen).first().standing)
    }

    Suite.test("a device that moved is shown at the address it is at now") {
        val known = listOf(device(id = "pc-1", host = "192.168.1.4"))
        val seen = listOf(SeenDevice("pc-1", "Desk PC", "10.0.0.7", 8765, "a".repeat(64)))
        assertEquals("10.0.0.7:8765", mergeDevices(known, seen).first().address)
    }

    Suite.test("the ones you can use come first") {
        val known = listOf(device(id = "away", name = "AAA"), device(id = "here", name = "ZZZ"))
        val seen = listOf(SeenDevice("here", "ZZZ", "h", 1, "a".repeat(64)))
        assertEquals("here", mergeDevices(known, seen).first().id, "READY sorts above AWAY")
    }

    Suite.test("a device name is not allowed to become a path") {
        // The phone names a download folder after the device. A name arriving
        // over the network is not allowed to decide where that folder lands.
        for (hostile in listOf("../../etc", "..", ".", "C:\\Windows", "a/b/c", "   ")) {
            val row = mergeDevices(emptyList(), listOf(SeenDevice("x", hostile, "h", 1))).first()
            val folder = folderNameFor(row)
            assertFalse(folder.contains('/'), "no separator survives <$hostile>")
            assertFalse(folder.contains('\\'), "no separator survives <$hostile>")
            assertFalse(folder == "." || folder == "..", "<$hostile> did not stay a traversal")
            assertTrue(folder.isNotBlank(), "<$hostile> left something usable")
        }
    }

    // ----------------------------------------------------------------- pairing

    Suite.group("pairing")

    val certificate = SelfSignedForTests.certificate()
    val goodPem = Pinning.toPem(certificate)

    Suite.test("the code cannot be sent before the fingerprint is confirmed") {
        var connected = false
        val flow = PairingFlow(
            "192.168.1.4", 8765, "phone", "Phone",
            probe = { _, _ -> certificate },
            connect = { connected = true; error("must not be reached") },
        )
        flow.probeCertificate()
        assertEquals(PairingStep.CONFIRMING, flow.step, "it waits for a person")

        assertNull(flow.submitCode("123456"), "the code was refused")
        assertFalse(connected, "nothing was sent to the device")
        assertEquals(PairingStep.FAILED, flow.step)
        assertTrue(flow.problem!!.contains("confirmed"), "and it says why")
    }

    Suite.test("saying the fingerprints differ sends nothing anywhere") {
        var connected = false
        val flow = PairingFlow(
            "192.168.1.4", 8765, "phone", "Phone",
            probe = { _, _ -> certificate },
            connect = { connected = true; error("must not be reached") },
        )
        flow.probeCertificate()
        flow.confirmFingerprint(false)

        assertEquals(PairingStep.FAILED, flow.step)
        assertFalse(connected, "the refusal is the mechanism working")
        assertNull(flow.result, "and nothing was stored")
    }

    Suite.test("the fingerprint is shown in groups a person can read aloud") {
        val flow = PairingFlow("h", 1, "phone", "Phone", probe = { _, _ -> certificate })
        val shown = flow.probeCertificate()
        assertNotNull(shown, "a certificate was presented")
        assertEquals(64, shown!!.fingerprint.length)
        assertEquals(19, shown.short.length, "four groups of four")
        assertTrue(shown.pem.startsWith("-----BEGIN CERTIFICATE-----"), "and the pin travels as PEM")
    }

    Suite.test("a wrong code can be tried again; the certificate stays confirmed") {
        val flow = PairingFlow(
            "192.168.1.4", 8765, "phone", "Phone",
            probe = { _, _ -> certificate },
            connect = { answering(PairOutcome.WrongCode) },
        )
        flow.probeCertificate()
        flow.confirmFingerprint(true)

        assertNull(flow.submitCode("000000"), "it was not accepted")
        assertEquals(PairingStep.AWAITING_CODE, flow.step, "so type it again")
        assertTrue(flow.problem!!.isNotBlank(), "with something to read")
        assertNull(flow.result, "and nothing stored in the meantime")
    }

    Suite.test("being locked out is not something to retry") {
        val flow = PairingFlow(
            "h", 1, "phone", "Phone",
            probe = { _, _ -> certificate },
            connect = { answering(PairOutcome.LockedOut) },
        )
        flow.probeCertificate()
        flow.confirmFingerprint(true)
        flow.submitCode("000000")

        assertEquals(PairingStep.FAILED, flow.step, "the owner has to switch pairing back on")
    }

    Suite.test("an empty code is not sent to the device") {
        var attempts = 0
        val flow = PairingFlow(
            "h", 1, "phone", "Phone",
            probe = { _, _ -> certificate },
            connect = { attempts++; answering(PairOutcome.WrongCode) },
        )
        flow.probeCertificate()
        flow.confirmFingerprint(true)
        flow.submitCode("   ")

        assertEquals(0, attempts, "nothing left the phone")
        assertEquals(PairingStep.AWAITING_CODE, flow.step)
    }

    Suite.test("a device that does not answer fails before anything is confirmed") {
        val flow = PairingFlow("h", 1, "phone", "Phone", probe = { _, _ -> error("connection refused") })
        assertNull(flow.probeCertificate(), "no certificate, no pairing")
        assertEquals(PairingStep.FAILED, flow.step)
    }

    // ------------------------------------------------------------ reconnecting

    Suite.group("reconnect")

    Suite.test("a device presenting its pinned certificate reconnects") {
        val known = device(fingerprint = Pinning.fingerprintOf(certificate))
        val outcome = reconnect(known, probe = { _, _ -> certificate }, build = { _, _ -> idleClient() })
        assertTrue(outcome is Reconnection.Ready, "it connected")
    }

    Suite.test("a changed certificate refuses, and says both fingerprints") {
        val known = device(fingerprint = "a".repeat(64))
        val outcome = reconnect(known, probe = { _, _ -> certificate }, build = { _, _ -> error("must not connect") })

        assertTrue(outcome is Reconnection.Changed, "the pin decided")
        val changed = outcome as Reconnection.Changed
        assertTrue(changed.message.contains("pair again"), "and it says what to do about it")
        assertFalse(changed.expected == changed.actual, "the two fingerprints differ")
    }

    Suite.test("a device that is off is unreachable, not an impostor") {
        val outcome = reconnect(device(), probe = { _, _ -> error("no route to host") })
        assertTrue(outcome is Reconnection.Unreachable, "being switched off is not an accusation")
    }
}

/** A device that never touches a socket: the flow's behaviour is what is under test. */
private fun answering(outcome: PairOutcome) = link.lan.app.Pairer { _, _, _ -> outcome }

/** A client object for the reconnect tests, which build one but never call it. */
private fun idleClient() = LanLinkClient("https://127.0.0.1:1", token = null)

/**
 * The real pairing flow, against a real node.
 *
 * The offline checks prove the order; this proves the order works against the
 * Python implementation, which is the only thing that proves anything.
 */
fun registerLogicInteropTests(host: String, port: Int, code: String) {
    Suite.group("logic interop")

    val clientId = "phone-${UUID.randomUUID()}"
    val flow = PairingFlow(host, port, clientId, "Kotlin phone")
    var paired: KnownDevice? = null

    Suite.test("pairing walks probe, confirm, code — and ends with a device to store") {
        val shown = flow.probeCertificate()
        assertNotNull(shown, "the node presented a certificate")
        assertEquals(Pinning.fingerprintOf(CertificateProbe.fetch(host, port)), shown!!.fingerprint)

        flow.confirmFingerprint(true)
        assertEquals(PairingStep.AWAITING_CODE, flow.step)

        val device = flow.submitCode(code)
        assertNotNull(device, "the node accepted the code: ${flow.problem}")
        assertEquals(PairingStep.PAIRED, flow.step)
        assertTrue(device!!.token.isNotBlank(), "a token came back")
        assertEquals(shown.fingerprint, device.fingerprint, "stored against the certificate we confirmed")
        paired = device
    }

    Suite.test("the stored device round-trips and then talks to the node") {
        val stored = paired ?: throw AssertionError("pairing did not produce a device")
        val store = DeviceStore()
        store.remember(stored)
        val reopened = DeviceStore.fromJson(store.asJson()).find(stored.id)
        assertNotNull(reopened, "the device came back from storage")

        when (val outcome = reconnect(reopened!!)) {
            is Reconnection.Ready -> {
                val shares = outcome.client.shares()
                assertTrue(shares.isNotEmpty(), "the reconnected client can list shares")
            }

            is Reconnection.Changed -> throw AssertionError("the pin should have matched: ${outcome.message}")
            is Reconnection.Unreachable -> throw AssertionError("unreachable: ${outcome.reason}")
        }
    }

    Suite.test("a stored pin that does not match the node refuses to connect") {
        val stored = paired ?: throw AssertionError("pairing did not produce a device")
        val tampered = stored.copy(fingerprint = "b".repeat(64))
        assertTrue(reconnect(tampered) is Reconnection.Changed, "the wrong pin was refused")
    }

    Suite.test("unpairing leaves the node as it was found") {
        val stored = paired ?: throw AssertionError("pairing did not produce a device")
        when (val outcome = reconnect(stored)) {
            is Reconnection.Ready -> assertTrue(outcome.client.unpair(clientId), "the node dropped this phone")
            else -> throw AssertionError("could not reconnect to unpair")
        }
    }
}

/**
 * A certificate to pin against in the offline checks.
 *
 * Any certificate that is not the node's will do, and the JDK ships a trust
 * store full of them — which beats generating one and beats a dependency.
 */
object SelfSignedForTests {
    private val cached: X509Certificate by lazy {
        val store = java.security.KeyStore.getInstance(
            java.io.File(System.getProperty("java.home"), "lib/security/cacerts"),
            null as CharArray?,
        )
        val alias = store.aliases().asSequence().first { store.isCertificateEntry(it) }
        store.getCertificate(alias) as X509Certificate
    }

    fun certificate(): X509Certificate = cached
}
