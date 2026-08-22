package link.lan.app.test

import link.lan.app.Connection
import link.lan.app.KnownDevice
import link.lan.app.Reconnection
import link.lan.app.Standing
import link.lan.app.connectTo
import link.lan.app.describeRemaining
import link.lan.app.describeSpeed
import link.lan.app.failureOf
import link.lan.app.secondsRemaining
import link.lan.app.speedOf
import link.lan.app.transferFailure
import link.lan.core.CertificateMismatch
import link.lan.core.LanLinkClient
import link.lan.core.ProtocolError
import link.lan.core.Share
import link.lan.core.test.Suite
import link.lan.core.test.assertEquals
import link.lan.core.test.assertFalse
import link.lan.core.test.assertNull
import link.lan.core.test.assertTrue
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import javax.net.ssl.SSLHandshakeException

private fun pc() = KnownDevice(
    id = "pc-1", name = "Desk PC", host = "192.168.1.4", port = 8765,
    fingerprint = "a".repeat(64), token = "t",
)

private fun idle() = LanLinkClient("https://127.0.0.1:1", token = null)

fun registerConnectionTests() {
    Suite.group("connecting")

    Suite.test("connected only after the token is accepted, not merely after it answers") {
        var listed = false
        val connection = connectTo(
            pc(),
            open = { Reconnection.Ready(idle()) },
            listShares = { listed = true; listOf(Share("s", "Demo", "rwd", true)) },
        )
        assertTrue(listed, "the first authenticated call was made")
        assertEquals(Standing.CONNECTED, connection.standing)
        assertTrue(connection.isUsable, "and the client is handed over")
        assertEquals(1, connection.shares.size)
    }

    Suite.test("reaching a device whose shares are refused is not being connected") {
        val connection = connectTo(
            pc(),
            open = { Reconnection.Ready(idle()) },
            listShares = { throw ProtocolError(401, "Pair with this device first.") },
        )
        assertEquals(Standing.UNAUTHORISED, connection.standing)
        assertFalse(connection.isUsable, "nothing is offered to browse")
        assertTrue(connection.message.contains("pair again"), "and it says what to do")
    }

    Suite.test("a changed certificate refuses and never becomes a retry") {
        val connection = connectTo(
            pc(),
            open = { Reconnection.Changed("a".repeat(64), "b".repeat(64)) },
            listShares = { throw AssertionError("must not be reached") },
        )
        assertEquals(Standing.IMPOSTOR, connection.standing)
        assertFalse(connection.worthRetrying, "an impostor is not something to try again")
        assertFalse(connection.isUsable, "and nothing is offered")
    }

    Suite.test("a device that is off is offline, and offering a retry is fair") {
        val connection = connectTo(
            pc(),
            open = { Reconnection.Unreachable("no route to host") },
            listShares = { throw AssertionError("must not be reached") },
        )
        assertEquals(Standing.OFFLINE, connection.standing)
        assertTrue(connection.worthRetrying, "being switched off is worth another try")
        assertFalse(connection.message.contains("no route to host"), "the raw error is for the log")
    }

    Suite.test("a device sharing nothing is still connected") {
        val connection = connectTo(pc(), open = { Reconnection.Ready(idle()) }, listShares = { emptyList() })
        assertEquals(Standing.CONNECTED, connection.standing)
        assertTrue(connection.message.contains("not sharing any folders"), "and it says why it looks empty")
    }

    // --------------------------------------------------------------- wording

    Suite.group("failures")

    Suite.test("a handshake failure is an identity problem, not a network hiccup") {
        for (error in listOf(SSLHandshakeException("bad cert"), CertificateMismatch("a".repeat(64), "b".repeat(64)))) {
            val connection: Connection = failureOf("Desk PC", error)
            assertEquals(Standing.IMPOSTOR, connection.standing, "${error::class.simpleName} is a refusal")
            assertFalse(connection.worthRetrying, "and never offered as a retry")
        }
    }

    Suite.test("the ordinary network failures read like something a person can act on") {
        assertEquals(Standing.OFFLINE, failureOf("Desk PC", SocketTimeoutException()).standing)
        assertEquals(Standing.OFFLINE, failureOf("Desk PC", ConnectException()).standing)
        assertEquals(Standing.OFFLINE, failureOf("Desk PC", IOException("reset")).standing)
        assertTrue(
            failureOf("Desk PC", ConnectException()).message.contains("same"),
            "it mentions the network they must share",
        )
    }

    Suite.test("server problems are told apart from refusals") {
        assertEquals(Standing.PROBLEM, failureOf("PC", ProtocolError(500, "")).standing)
        assertEquals(Standing.PROBLEM, failureOf("PC", ProtocolError(507, "")).standing)
        assertTrue(
            failureOf("PC", ProtocolError(507, "")).message.contains("disk space"),
            "and says which problem",
        )
        assertEquals(Standing.UNAUTHORISED, failureOf("PC", ProtocolError(403, "")).standing)
    }

    Suite.test("no message shows a class name where a sentence belongs") {
        for (error in listOf(SocketTimeoutException(), ConnectException(), ProtocolError(401, ""))) {
            val message = failureOf("Desk PC", error).message
            assertTrue(message.contains("Desk PC"), "it names the device")
            assertFalse(message.contains("Exception"), "and does not leak a class name: $message")
        }
    }

    Suite.test("a transfer that got part way says how far it got") {
        val message = transferFailure("Desk PC", 54_525_952, SocketTimeoutException())
        assertTrue(message.contains("52.0 MB"), "the person knows what was moved: $message")
        assertTrue(message.contains("retry"), "and that retrying is worth it")

        val nothing = transferFailure("Desk PC", 0, SocketTimeoutException())
        assertFalse(nothing.contains("had been transferred"), "nothing moved, nothing to mention")
    }

    // ---------------------------------------------------------------- speed

    Suite.group("progress")

    Suite.test("speed is not guessed from a sample too small to mean anything") {
        assertNull(speedOf(1_000_000, 30), "30ms is noise, not a measurement")
        assertNull(speedOf(0, 5_000), "nothing moved, no speed")
        assertEquals(2_000_000L, speedOf(2_000_000, 1_000), "2 MB in a second")
        assertEquals(1_000_000L, speedOf(2_000_000, 2_000))
    }

    Suite.test("speed and time left are shown only when they are known") {
        assertEquals("", describeSpeed(null))
        assertEquals("", describeSpeed(0))
        assertTrue(describeSpeed(1_048_576).endsWith("/s"), "a known speed is per second")

        assertNull(secondsRemaining(null, 0, 1000), "an unknown size has no estimate")
        assertNull(secondsRemaining(1000, 0, null), "an unknown speed has no estimate")
        assertEquals(10L, secondsRemaining(20_000, 10_000, 1_000))
        assertEquals(0L, secondsRemaining(1_000, 1_000, 1_000), "finished is not negative")
    }

    Suite.test("time left is phrased in units a person uses") {
        assertEquals("", describeRemaining(null))
        assertTrue(describeRemaining(45).contains("45s"), "seconds under a minute")
        assertTrue(describeRemaining(300).contains("5m"), "minutes under an hour")
        assertTrue(describeRemaining(7_200).contains("2h"), "hours beyond that")
    }
}

fun registerRelocationTests() {
    Suite.group("moving")

    val certificate = SelfSignedForTests.certificate()
    val real = link.lan.core.Pinning.fingerprintOf(certificate)

    Suite.test("a device found at a new address keeps its token and its pin") {
        val stored = pc().copy(fingerprint = real, host = "10.75.135.63")
        val outcome = link.lan.app.relocate(stored, "192.168.1.16", 8765) { _, _ -> certificate }

        assertTrue(outcome is link.lan.app.Relocation.Moved, "it was found")
        val moved = (outcome as link.lan.app.Relocation.Moved).device
        assertEquals("192.168.1.16:8765", moved.address, "the address followed")
        assertEquals(stored.token, moved.token, "the token did not change")
        assertEquals(stored.fingerprint, moved.fingerprint, "and neither did the pin")
    }

    Suite.test("a different machine at that address is refused, not adopted") {
        // This is the whole reason relocating is allowed to exist: without the
        // pin check it would be a way to aim a stored token at any host.
        val stored = pc().copy(fingerprint = "b".repeat(64))
        val outcome = link.lan.app.relocate(stored, "192.168.1.99", 8765) { _, _ -> certificate }

        assertTrue(outcome is link.lan.app.Relocation.WrongDevice, "the pin said no")
        assertTrue(
            (outcome as link.lan.app.Relocation.WrongDevice).message.contains("not Desk PC"),
            "and it says so plainly",
        )
    }

    Suite.test("nothing at that address is a plain miss") {
        val outcome = link.lan.app.relocate(pc(), "192.168.1.99", 8765) { _, _ -> error("refused") }
        assertTrue(outcome is link.lan.app.Relocation.Unreachable, "nothing answered")
    }
}
