package link.lan.app

import link.lan.core.CertificateMismatch
import link.lan.core.LanLinkClient
import link.lan.core.PairOutcome
import link.lan.core.Pinning
import link.lan.core.ProtocolError
import java.security.cert.X509Certificate

/**
 * Pairing, in the only order that is safe.
 *
 *   1. fetch the certificate the device is presenting
 *   2. show its fingerprint and let the person confirm it
 *   3. only then send the pairing code, over a socket pinned to that certificate
 *   4. store the token and the pin together
 *
 * Sending the code first would hand it to whatever answered the address, and a
 * token stored without its pin is a token usable against an impostor. The
 * desktop client had this wrong once; here the state machine will not let a
 * caller do it, whatever the UI does.
 */
enum class PairingStep {
    /** Nothing has happened yet. */
    IDLE,

    /** Asking the device for its certificate. */
    PROBING,

    /** Waiting for the person to say the fingerprint matches what the PC shows. */
    CONFIRMING,

    /** Waiting for the pairing code to be typed. */
    AWAITING_CODE,

    /** The code is with the device. */
    SUBMITTING,

    /** Paired. [PairingFlow.result] holds the device to store. */
    PAIRED,

    /** Nothing was stored. [PairingFlow.problem] says why. */
    FAILED,
}

/**
 * Just the one call [PairingFlow] makes, so a test can stand in for a device
 * without a socket — and so the flow cannot reach for anything else.
 */
fun interface Pairer {
    fun pair(clientId: String, clientName: String, code: String): PairOutcome
}

/** What the phone must show before anyone types a code. */
data class Presented(
    val certificate: X509Certificate,
    val fingerprint: String,
    val short: String,
    val pem: String,
)

class PairingFlow(
    private val host: String,
    private val port: Int,
    private val clientId: String,
    private val clientName: String,
    private val probe: (String, Int) -> X509Certificate = { h, p -> link.lan.core.CertificateProbe.fetch(h, p) },
    private val connect: (String) -> Pairer = { pem ->
        val client = LanLinkClient(
            baseUrl = "https://$host:$port",
            socketFactory = Pinning.socketFactoryForPem(pem),
        )
        Pairer { id, name, code -> client.pair(id, name, code) }
    },
) {
    var step: PairingStep = PairingStep.IDLE
        private set

    var presented: Presented? = null
        private set

    var result: KnownDevice? = null
        private set

    var problem: String? = null
        private set

    /** Step 1. Fetch the certificate and describe it. Nothing secret has moved yet. */
    fun probeCertificate(): Presented? {
        step = PairingStep.PROBING
        return try {
            val certificate = probe(host, port)
            val pem = Pinning.toPem(certificate)
            val fingerprint = Pinning.fingerprintOf(certificate)
            val shown = Presented(certificate, fingerprint, Pinning.shortFingerprint(fingerprint), pem)
            presented = shown
            step = PairingStep.CONFIRMING
            shown
        } catch (error: Exception) {
            fail("This device did not answer with a certificate: ${error.message ?: error::class.java.simpleName}")
            null
        }
    }

    /**
     * Step 2, when a QR invite already said which certificate to expect.
     *
     * The invite came off the PC's own screen, so the comparison the person
     * would have made by eye can be made properly instead — and a mismatch is
     * refused outright rather than shrugged at, because someone who can answer
     * on that address cannot also forge the fingerprint printed on the PC.
     */
    fun confirmAgainst(expected: String): Boolean {
        if (step != PairingStep.CONFIRMING) {
            fail("There is no certificate to check yet.")
            return false
        }
        val shown = presented ?: run {
            fail("There is no certificate to check yet.")
            return false
        }
        if (!expected.equals(shown.fingerprint, ignoreCase = true)) {
            fail(
                "This device is presenting a different certificate from the one in the " +
                    "invite. Nothing was sent to it."
            )
            return false
        }
        step = PairingStep.AWAITING_CODE
        return true
    }

    /**
     * Step 2. The person compared the fingerprint with the one on the PC.
     *
     * Saying no is not an error; it is the mechanism working.
     */
    fun confirmFingerprint(matches: Boolean) {
        if (step != PairingStep.CONFIRMING) {
            fail("There is no fingerprint to confirm yet.")
            return
        }
        if (!matches) {
            fail("The fingerprints did not match, so nothing was sent to that device.")
            return
        }
        step = PairingStep.AWAITING_CODE
    }

    /**
     * Step 3 and 4. Send the code over the pinned socket and keep what comes back.
     *
     * Refuses outright unless the fingerprint was confirmed first.
     */
    fun submitCode(code: String): KnownDevice? {
        if (step != PairingStep.AWAITING_CODE) {
            fail("The certificate has to be confirmed before the code is sent.")
            return null
        }
        val shown = presented ?: run {
            fail("There is no pinned certificate to send the code over.")
            return null
        }
        val cleaned = code.trim().replace(" ", "")
        if (cleaned.isEmpty()) {
            step = PairingStep.AWAITING_CODE
            problem = "Type the code shown on the other device."
            return null
        }

        step = PairingStep.SUBMITTING
        return try {
            val client = connect(shown.pem)
            when (val outcome = client.pair(clientId, clientName, cleaned)) {
                is PairOutcome.Paired -> {
                    val device = KnownDevice(
                        id = outcome.device.id,
                        name = outcome.device.name.ifBlank { host },
                        host = host,
                        port = port,
                        fingerprint = shown.fingerprint,
                        token = outcome.token,
                        certificatePem = shown.pem,
                    )
                    result = device
                    problem = null
                    step = PairingStep.PAIRED
                    device
                }

                // Everything else keeps the confirmed certificate and lets the
                // person try again — except a lock-out, which needs the owner
                // to switch pairing back on before anything can work.
                PairOutcome.LockedOut -> {
                    fail(outcome.message)
                    null
                }

                else -> {
                    problem = outcome.message
                    step = PairingStep.AWAITING_CODE
                    null
                }
            }
        } catch (error: CertificateMismatch) {
            fail("The device changed its certificate while pairing. Nothing was stored.")
            null
        } catch (error: ProtocolError) {
            problem = error.detail.ifBlank { "The device refused the pairing (${error.status})." }
            step = PairingStep.AWAITING_CODE
            null
        } catch (error: Exception) {
            fail("The pairing could not be completed: ${error.message ?: error::class.java.simpleName}")
            null
        }
    }

    fun cancel() {
        step = PairingStep.IDLE
        presented = null
        result = null
        problem = null
    }

    private fun fail(message: String) {
        problem = message
        result = null
        step = PairingStep.FAILED
    }
}

/**
 * Reconnecting to a device already paired.
 *
 * The stored pin decides. A device presenting anything else is refused, and the
 * refusal names both fingerprints so the person can tell a reinstalled PC from
 * something worse.
 */
sealed class Reconnection {
    data class Ready(val client: LanLinkClient) : Reconnection()
    data class Changed(val expected: String, val actual: String) : Reconnection() {
        val message: String
            get() = "This device is presenting a different certificate. Expected " +
                "${Pinning.shortFingerprint(expected)}, got ${Pinning.shortFingerprint(actual)}. " +
                "If the PC was reinstalled, pair again; otherwise do not connect."
    }

    data class Unreachable(val reason: String) : Reconnection()
}

fun reconnect(
    device: KnownDevice,
    probe: (String, Int) -> X509Certificate = { h, p -> link.lan.core.CertificateProbe.fetch(h, p) },
    build: (KnownDevice, String) -> LanLinkClient = { known, pem ->
        LanLinkClient(
            baseUrl = "https://${known.host}:${known.port}",
            token = known.token,
            socketFactory = Pinning.socketFactoryForPem(pem),
        )
    },
): Reconnection = try {
    val certificate = probe(device.host, device.port)
    val fingerprint = Pinning.fingerprintOf(certificate)
    if (!fingerprint.equals(device.fingerprint, ignoreCase = true)) {
        Reconnection.Changed(device.fingerprint, fingerprint)
    } else {
        Reconnection.Ready(build(device, Pinning.toPem(certificate)))
    }
} catch (error: Exception) {
    Reconnection.Unreachable(error.message ?: error::class.java.simpleName)
}
