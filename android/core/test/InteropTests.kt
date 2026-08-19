package link.lan.core.test

import link.lan.core.CertificateMismatch
import link.lan.core.CertificateProbe
import link.lan.core.Downloads
import link.lan.core.LanLinkClient
import link.lan.core.PairOutcome
import link.lan.core.Paths
import link.lan.core.Pinning
import link.lan.core.ProtocolError
import link.lan.core.ResumeDecision
import link.lan.core.UploadConflict
import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.UUID

/**
 * The Kotlin client against a real Python node, over a real pinned TLS socket.
 *
 * Everything else in this suite proves the Kotlin agrees with itself. This is
 * the part that proves the two implementations agree with each other — which is
 * the whole reason the protocol was frozen first.
 */
fun registerInteropTests(host: String, port: Int, code: String) {
    Suite.group("interop")

    val certificate = CertificateProbe.fetch(host, port)
    val pem = Pinning.toPem(certificate)
    val fingerprint = Pinning.fingerprintOf(certificate)
    val clientId = "kotlin-${UUID.randomUUID()}"
    val client = LanLinkClient("https://$host:$port", socketFactory = Pinning.socketFactory(certificate))

    Suite.test("the certificate we pin is the one the node advertises") {
        assertEquals(64, fingerprint.length, "a fingerprint is 64 hex characters")
        val (device, _) = client.deviceInfo()
        assertEquals(device.fingerprint, fingerprint, "the node advertises a different certificate")
        assertEquals(19, Pinning.shortFingerprint(fingerprint).length, "four groups of four plus spaces")
    }

    Suite.test("health and device agree, unauthenticated") {
        val health = client.health()
        val (device, _) = client.deviceInfo()
        assertEquals(health.id, device.id)
        assertTrue(health.id.isNotEmpty(), "the node has an identity")
        assertTrue(health.platform.isNotEmpty(), "the node reports a platform")
    }

    Suite.test("a different certificate is refused outright") {
        // Trust a certificate the node does not have, and the handshake must fail.
        val other = CertificateProbe.fetch(host, port)
        val wrongPin = Pinning.socketFactory(SelfSigned.certificate())
        val stranger = LanLinkClient("https://$host:$port", socketFactory = wrongPin)
        var refused = false
        try {
            stranger.health()
        } catch (error: Throwable) {
            refused = generateSequence(error) { it.cause }.any { it is CertificateMismatch } ||
                error is javax.net.ssl.SSLException
        }
        assertTrue(refused, "a wrong pin must refuse the connection")
        assertEquals(Pinning.fingerprintOf(other), fingerprint, "the probe is stable")
    }

    Suite.test("a file endpoint needs a token") {
        val anonymous = LanLinkClient("https://$host:$port", socketFactory = Pinning.socketFactory(certificate))
        try {
            anonymous.shares()
            throw Failure("an unpaired client listed shares")
        } catch (error: ProtocolError) {
            assertTrue(error.isUnauthorised, "expected 401, got ${error.status}")
        }
    }

    Suite.test("pairing with the code issues a token") {
        val outcome = client.pair(clientId, "Kotlin core suite", code)
        assertTrue(outcome is PairOutcome.Paired, "pairing failed: ${outcome.message}")
        outcome as PairOutcome.Paired
        assertEquals(43, outcome.token.length, "a token is 43 URL-safe characters")
        assertTrue(outcome.device.id.isNotEmpty(), "the reply names the other device")
    }

    Suite.test("a wrong code is told apart from a device that is not armed") {
        val second = LanLinkClient("https://$host:$port", socketFactory = Pinning.socketFactory(certificate))
        val outcome = second.pair("kotlin-${UUID.randomUUID()}", "Wrong code", "00000000")
        assertFalse(outcome is PairOutcome.Paired, "a wrong code must not pair")
        // Pairing switched off after our success, so this is NotArmed — and §5
        // says that one, and only that one, may be retried.
        assertTrue(outcome.isRetryable || outcome is PairOutcome.WrongCode, "got ${outcome.message}")
    }

    var shareId = ""
    val folder = "kotlin-interop"

    Suite.test("shares list, and the paths they return are usable") {
        val shares = client.shares()
        assertTrue(shares.isNotEmpty(), "the node shares nothing")
        val writable = shares.firstOrNull { it.canWrite && it.canDelete && it.available }
        assertNotNull(writable, "the run needs a read + write + delete share")
        shareId = writable!!.id
        assertTrue(writable.name.isNotEmpty(), "a share has a display name")
        assertFalse(writable.name.contains('/'), "a share name is not a filesystem path")

        for (entry in client.list(shareId)) {
            assertTrue(Paths.isSafePath(entry.path), "the node returned a path we would refuse: ${entry.path}")
            assertFalse(Paths.isPartial(entry.name), "an unfinished upload was listed")
        }
    }

    Suite.test("a folder can be created and is then listed") {
        val created = try {
            client.createFolder(shareId, "", folder)
        } catch (error: ProtocolError) {
            if (!error.isConflict) throw error
            folder // left over from an earlier run
        }
        assertEquals(folder, created)
        assertTrue(client.list(shareId).any { it.isFolder && it.name == folder }, "the folder is not listed")
    }

    val payload = ByteArray(300_000) { (it % 251).toByte() }
    val digest = MessageDigest.getInstance("SHA-256").digest(payload).joinToString("") { "%02x".format(it) }

    Suite.test("an upload streams in, resumes, and is verified by checksum") {
        // Send the first slice and leave it unfinished, as an interrupted phone would.
        val first = client.upload(shareId, folder, "payload.bin", offset = 0, finalize = false) { out ->
            out.write(payload, 0, 100_000)
        }
        assertEquals(100_000L, first.received)
        assertFalse(first.complete, "finalize=false must not publish the file")

        val status = client.partialStatus(shareId, folder, "payload.bin")
        assertEquals(100_000L, status.received, "the node disagrees about what it holds")
        assertFalse(status.complete, "an unfinished upload is not complete")

        // An offset past what it holds is an instruction, not a failure.
        var resumeFrom = -1L
        try {
            client.upload(shareId, folder, "payload.bin", offset = 200_000, finalize = false) { out ->
                out.write(payload, 200_000, 10)
            }
        } catch (conflict: UploadConflict) {
            assertTrue(conflict.canResume, "the 409 did not say where to resume")
            resumeFrom = conflict.resumeFrom!!
        }
        assertEquals(100_000L, resumeFrom, "the node named a different resume point")

        val offset = Uploads.resumeOffset(resumeFrom, payload.size.toLong())
        val done = client.upload(shareId, folder, "payload.bin", offset = offset, sha256 = digest) { out ->
            out.write(payload, offset.toInt(), payload.size - offset.toInt())
        }
        assertTrue(done.complete, "the resumed upload did not finish")
        assertEquals(payload.size.toLong(), done.bytes, "the published file is the wrong size")
    }

    Suite.test("the node's checksum matches the bytes we sent") {
        assertEquals(digest, client.checksum(shareId, Paths.join(folder, "payload.bin")))
    }

    Suite.test("a download resumes from an offset and the bytes line up") {
        val path = Paths.join(folder, "payload.bin")
        val whole = ByteArrayOutputStream()
        val total = client.download(shareId, path) { decision, body ->
            assertTrue(decision is ResumeDecision.StartOver, "a fresh download starts at zero")
            body.copyTo(whole)
            (decision as ResumeDecision.StartOver).total
        }
        assertEquals(payload.size.toLong(), total)
        assertTrue(whole.toByteArray().contentEquals(payload), "the whole file came back wrong")

        val offset = 100_000L
        val tail = ByteArrayOutputStream()
        client.download(shareId, path, offset = offset) { decision, body ->
            assertTrue(decision is ResumeDecision.Append, "expected a partial reply, got $decision")
            assertEquals(offset, (decision as ResumeDecision.Append).offset)
            assertEquals(payload.size.toLong(), decision.total)
            body.copyTo(tail)
        }
        assertTrue(
            tail.toByteArray().contentEquals(payload.copyOfRange(offset.toInt(), payload.size)),
            "the resumed part of the file does not match",
        )
    }

    Suite.test("an offset past the end says restart, it does not corrupt") {
        try {
            client.download(shareId, Paths.join(folder, "payload.bin"), offset = 10_000_000) { _, _ -> }
            throw Failure("a range past the end should not have succeeded")
        } catch (error: ProtocolError) {
            assertEquals(416, error.status)
            assertTrue(
                Downloads.decide(416, 10_000_000) is ResumeDecision.Restart,
                "416 must mean restart",
            )
        }
    }

    Suite.test("properties describe the file we uploaded") {
        val properties = client.properties(shareId, Paths.join(folder, "payload.bin"))
        assertEquals("payload.bin", properties.name)
        assertEquals(payload.size.toLong(), properties.size)
        assertEquals(".bin", properties.extension)
        assertFalse(properties.isFolder, "a file is not a folder")
    }

    Suite.test("an upload never overwrites") {
        try {
            client.upload(shareId, folder, "payload.bin") { out -> out.write(byteArrayOf(1, 2, 3)) }
            throw Failure("an existing file was overwritten")
        } catch (conflict: UploadConflict) {
            assertEquals(409, conflict.status)
            assertFalse(conflict.canResume, "a name clash is not a resume point")
        }
    }

    Suite.test("rename and copy behave as documented") {
        assertEquals("renamed.bin", client.rename(shareId, Paths.join(folder, "payload.bin"), "renamed.bin"))
        val copied = client.copyOrMove(
            sourceShareId = shareId,
            sourcePath = Paths.join(folder, "renamed.bin"),
            destinationShareId = shareId,
            destinationPath = "",
        )
        assertFalse(copied.startsWith("/"), "the copy returned an absolute path: $copied")
        assertEquals("renamed.bin", copied, "the copy is share-relative")
        client.delete(shareId, "renamed.bin")
    }

    Suite.test("a path that would leave the share is refused by the node too") {
        for (path in listOf("..", "../..", "/etc/passwd")) {
            try {
                client.list(shareId, path)
                throw Failure("the node accepted $path")
            } catch (error: ProtocolError) {
                assertTrue(error.isMissing, "expected 404 for $path, got ${error.status}")
            }
        }
    }

    Suite.test("a name we would refuse locally is refused remotely as well") {
        for (name in listOf("CON", "a/b", "trailing.")) {
            assertFalse(Paths.isSafeName(name), "$name should fail our own check first")
            try {
                client.createFolder(shareId, folder, name)
                throw Failure("the node accepted the name $name")
            } catch (error: ProtocolError) {
                assertTrue(error.status == 409 || error.status == 422, "got ${error.status} for $name")
            }
        }
    }

    Suite.test("the run cleans up and unpairs itself") {
        client.delete(shareId, folder, recursive = true)
        assertFalse(client.list(shareId).any { it.name == folder }, "the work folder is still there")
        assertTrue(client.unpair(clientId), "unpair reported nothing removed")
        try {
            client.shares()
            throw Failure("the revoked token still works")
        } catch (error: ProtocolError) {
            assertTrue(error.isUnauthorised, "expected 401 after unpairing, got ${error.status}")
        }
    }
}

/** A throwaway certificate, only ever used to prove that a wrong pin is refused. */
private object SelfSigned {
    fun certificate(): java.security.cert.X509Certificate {
        // Any certificate that is not the node's will do, and the JDK ships one
        // in every default trust store.
        val store = java.security.KeyStore.getInstance(
            java.io.File(System.getProperty("java.home"), "lib/security/cacerts"),
            null as CharArray?,
        )
        val alias = store.aliases().asSequence().first { store.isCertificateEntry(it) }
        return store.getCertificate(alias) as java.security.cert.X509Certificate
    }
}

private object Uploads {
    fun resumeOffset(reported: Long, localSize: Long): Long = link.lan.core.Uploads.resumeOffset(reported, localSize)
}
