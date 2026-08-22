package link.lan.app

import link.lan.core.CertificateMismatch
import link.lan.core.LanLinkClient
import link.lan.core.ProtocolError
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException

/**
 * What happened when the phone tried to talk to a device, and what to say about it.
 *
 * This lives here rather than in a ViewModel because getting it wrong is a
 * security problem, not a wording problem: a certificate mismatch must never be
 * shown as "could not connect", or the person shrugs and taps retry on an
 * impostor. [Standing.IMPOSTOR] is a refusal, and it reads like one.
 */
enum class Standing {
    /** Talking to it now. */
    CONNECTED,

    /** Nothing answered. Switched off, asleep, or on another network. */
    OFFLINE,

    /** Something answered, presenting a different certificate. Do not connect. */
    IMPOSTOR,

    /** It answered and rejected our token. The pairing is gone from its side. */
    UNAUTHORISED,

    /** It answered, and something else went wrong. */
    PROBLEM,
}

data class Connection(
    val standing: Standing,
    val message: String,
    val client: LanLinkClient? = null,
    val shares: List<link.lan.core.Share> = emptyList(),
) {
    val isUsable: Boolean get() = standing == Standing.CONNECTED && client != null

    /** A retry is worth offering for anything except an impostor. */
    val worthRetrying: Boolean get() = standing == Standing.OFFLINE || standing == Standing.PROBLEM
}

/**
 * Connect, verify the pin, authenticate, and only then report connected.
 *
 * The order is the point, and it is not negotiable:
 *
 *   1. [reconnect] fetches the certificate and compares it with the stored pin.
 *      A mismatch stops here — no token is sent to something we cannot identify.
 *   2. The client it returns is pinned to that certificate, so every later call
 *      fails the TLS handshake if the peer changes.
 *   3. `shares()` is the first authenticated call. Until it succeeds we do not
 *      claim to be connected: reaching a device is not the same as being
 *      allowed to use it.
 */
fun connectTo(
    device: KnownDevice,
    open: (KnownDevice) -> Reconnection = { reconnect(it) },
    listShares: (LanLinkClient) -> List<link.lan.core.Share> = { it.shares() },
): Connection = when (val outcome = open(device)) {
    is Reconnection.Changed -> Connection(Standing.IMPOSTOR, outcome.message)

    is Reconnection.Unreachable -> Connection(
        Standing.OFFLINE,
        "${device.name} did not answer. Check it is switched on and on the same Wi-Fi.",
    )

    is Reconnection.Ready -> try {
        val shares = listShares(outcome.client)
        Connection(
            standing = Standing.CONNECTED,
            message = if (shares.isEmpty()) {
                "${device.name} is connected, but it is not sharing any folders yet."
            } else {
                "Connected to ${device.name}."
            },
            client = outcome.client,
            shares = shares,
        )
    } catch (error: Throwable) {
        failureOf(device.name, error)
    }
}

/**
 * Turn whatever was thrown into something worth reading.
 *
 * Two rules: never show a stack trace, and never make a refusal sound like a
 * hiccup. The caller logs the exception; the person gets the sentence.
 */
fun failureOf(deviceName: String, error: Throwable): Connection = when (error) {
    is CertificateMismatch -> Connection(
        Standing.IMPOSTOR,
        "$deviceName is presenting a different certificate from the one you paired with. " +
            "LanLink will not connect. If the PC was reinstalled, pair again.",
    )

    is ProtocolError -> when (error.status) {
        401, 403 -> Connection(
            Standing.UNAUTHORISED,
            "$deviceName refused this phone's access. It was probably unpaired there — " +
                "pair again.",
        )

        404 -> Connection(Standing.PROBLEM, "$deviceName could not find what was asked for.")
        413 -> Connection(Standing.PROBLEM, "That file is larger than $deviceName accepts.")
        507 -> Connection(Standing.PROBLEM, "$deviceName has run out of disk space.")
        in 500..599 -> Connection(
            Standing.PROBLEM,
            "$deviceName ran into a problem (${error.status}). Try again in a moment.",
        )

        else -> Connection(
            Standing.PROBLEM,
            error.detail.ifBlank { "$deviceName answered ${error.status}." },
        )
    }

    // A pinned socket that fails the handshake is the pin doing its job.
    is SSLException -> Connection(
        Standing.IMPOSTOR,
        "The secure connection to $deviceName could not be established. Its certificate " +
            "does not match the one you paired with.",
    )

    is SocketTimeoutException -> Connection(
        Standing.OFFLINE,
        "$deviceName stopped responding. It may have gone to sleep or left the network.",
    )

    is ConnectException, is NoRouteToHostException, is UnknownHostException -> Connection(
        Standing.OFFLINE,
        "$deviceName is not reachable at that address. Both devices must be on the same " +
            "Wi-Fi, and LanLink must be running on the PC.",
    )

    is IOException -> Connection(
        Standing.OFFLINE,
        "The connection to $deviceName was interrupted.",
    )

    else -> Connection(
        Standing.PROBLEM,
        "Something went wrong talking to $deviceName (${error::class.java.simpleName}).",
    )
}

/** What to say when a transfer stops part way through. */
fun transferFailure(name: String, moved: Long, error: Throwable): String {
    val connection = failureOf(name, error)
    return if (moved > 0) {
        "${connection.message} ${bytes(moved)} had been transferred; you can retry."
    } else {
        connection.message
    }
}

// ------------------------------------------------------------------- progress

/**
 * Transfer speed, from bytes moved and time spent.
 *
 * Returns null rather than a wild number until there is enough of a sample to
 * mean anything — a progress bar that claims 900 MB/s for the first chunk is
 * worse than one that says nothing yet.
 */
fun speedOf(bytesMoved: Long, milliseconds: Long, minimumMillis: Long = 500): Long? {
    if (milliseconds < minimumMillis || bytesMoved <= 0) return null
    return bytesMoved * 1000 / milliseconds
}

fun describeSpeed(bytesPerSecond: Long?): String =
    if (bytesPerSecond == null || bytesPerSecond <= 0) "" else "${bytes(bytesPerSecond)}/s"

/** Seconds remaining, or null when the size or the speed is unknown. */
fun secondsRemaining(total: Long?, moved: Long, bytesPerSecond: Long?): Long? {
    if (total == null || bytesPerSecond == null || bytesPerSecond <= 0) return null
    val left = total - moved
    return if (left <= 0) 0 else left / bytesPerSecond
}

fun describeRemaining(seconds: Long?): String = when {
    seconds == null -> ""
    seconds < 60 -> "about ${seconds.coerceAtLeast(1)}s left"
    seconds < 3600 -> "about ${seconds / 60}m left"
    else -> "about ${seconds / 3600}h ${(seconds % 3600) / 60}m left"
}

// ------------------------------------------------------------------- moving

/**
 * A paired device that has turned up at a new address.
 *
 * Home networks renumber: a phone that was on a hotspot yesterday is on the
 * router today, and the address stored at pairing time is then wrong. mDNS
 * normally fixes this by itself, but plenty of networks block multicast, and
 * "offline" is a poor answer when the PC is sitting right there.
 *
 * The pin is what makes this safe. The certificate at the new address must be
 * the one already stored, so this can find a device that moved but can never be
 * used to point a stored token at a different machine.
 */
sealed class Relocation {
    data class Moved(val device: KnownDevice) : Relocation()
    data class WrongDevice(val message: String) : Relocation()
    data class Unreachable(val message: String) : Relocation()
}

fun relocate(
    device: KnownDevice,
    host: String,
    port: Int,
    probe: (String, Int) -> java.security.cert.X509Certificate = { h, p ->
        link.lan.core.CertificateProbe.fetch(h, p)
    },
): Relocation = try {
    val certificate = probe(host, port)
    val fingerprint = link.lan.core.Pinning.fingerprintOf(certificate)
    if (!fingerprint.equals(device.fingerprint, ignoreCase = true)) {
        Relocation.WrongDevice(
            "Something is answering at $host:$port, but it is not ${device.name} — its " +
                "certificate is different. Nothing was sent to it."
        )
    } else {
        Relocation.Moved(device.copy(host = host, port = port))
    }
} catch (error: Throwable) {
    Relocation.Unreachable("Nothing answered at $host:$port.")
}
