package link.lan.app

import link.lan.core.DEFAULT_PORT
import link.lan.core.InvalidInvite
import link.lan.core.Invite

/**
 * What to do with a scanned QR code, a pasted `lanlink://` link, or an address
 * somebody typed by hand.
 *
 * Deciding this before any socket opens is the point. The interesting question
 * is not "can we reach it" but "do we already know what certificate it should
 * present" — an invite that carries the full fingerprint has already answered
 * it, and the person is spared comparing 64 hex characters on a phone screen.
 */
sealed class InviteCheck {
    /**
     * Worth pairing with.
     *
     * [pinnedFingerprint] is non-empty when the invite carried a full
     * fingerprint: the phone checks the certificate itself and nobody has to
     * read anything aloud. When it is empty the person confirms by eye, which
     * is the same guarantee with more work.
     */
    data class Ready(
        val invite: Invite,
        val pinnedFingerprint: String,
        val codeFromInvite: String,
    ) : InviteCheck() {
        val needsFingerprintByEye: Boolean get() = pinnedFingerprint.isEmpty()
        val needsCodeTyped: Boolean get() = codeFromInvite.isEmpty()
        val label: String get() = invite.name.ifBlank { "${invite.host}:${invite.port}" }
    }

    /** Already paired. Pairing again would mint a second token for no reason. */
    data class AlreadyPaired(val device: KnownDevice, val invite: Invite) : InviteCheck() {
        val message: String get() = "You are already paired with ${device.name}."
    }

    /** Nothing was sent anywhere. [reason] is written for the person, not the log. */
    data class Rejected(val reason: String) : InviteCheck()
}

/**
 * Read what was scanned or typed, and say what happens next.
 *
 * Refusals worth knowing about:
 *
 * - a plain-http invite, because the pairing code and the token that comes back
 *   would both cross the network in the clear. [allowInsecure] exists for
 *   somebody deliberately troubleshooting with TLS switched off, and defaults
 *   to off precisely because a QR code is not a considered decision.
 * - a fingerprint that is present but malformed, which is a broken or tampered
 *   invite either way and is not worth guessing about.
 */
fun examineInvite(
    text: String,
    store: DeviceStore = DeviceStore(),
    allowInsecure: Boolean = false,
): InviteCheck {
    val invite = try {
        Invite.parse(text)
    } catch (error: InvalidInvite) {
        return InviteCheck.Rejected(error.message ?: "That is not a LanLink invite.")
    }

    if (!looksLikeAHost(invite.host)) {
        return InviteCheck.Rejected("\"${invite.host}\" is not a device address.")
    }
    if (invite.port !in 1..65535) {
        return InviteCheck.Rejected("${invite.port} is not a port number.")
    }
    if (!invite.isSecure && !allowInsecure) {
        return InviteCheck.Rejected(
            "That invite asks for an unencrypted connection. The pairing code and the " +
                "token would both cross the network in the open, so LanLink will not use it."
        )
    }

    val declared = invite.fingerprint
    if (declared.isNotEmpty() && !invite.hasPin) {
        return InviteCheck.Rejected(
            "That invite carries a damaged certificate fingerprint. Scan it again."
        )
    }

    val existing = invite.deviceId.takeIf { it.isNotEmpty() }?.let { store.find(it) }
    if (existing != null) return InviteCheck.AlreadyPaired(existing, invite)

    return InviteCheck.Ready(
        invite = invite,
        pinnedFingerprint = if (invite.hasPin) declared.lowercase() else "",
        codeFromInvite = invite.code,
    )
}

/**
 * A hostname or an IP address, and nothing else.
 *
 * A bare name like `desk-pc` is perfectly good — Windows machines answer to
 * theirs — so the test is the shape of a host, not the presence of dots. What
 * this rules out is prose: anything with a space in it was never an address,
 * however hopefully it was typed or scanned.
 */
private fun looksLikeAHost(host: String): Boolean {
    if (host.isEmpty() || host.length > 253) return false
    if (host.startsWith('-') || host.endsWith('-') || host.endsWith('.')) return false
    // IPv6 arrives bracketed; everything else is letters, digits, dot and dash.
    if (host.startsWith("[") && host.endsWith("]")) {
        return host.drop(1).dropLast(1).all { it.isDigit() || it in "abcdefABCDEF:." }
    }
    return host.all { it.isLetterOrDigit() || it == '.' || it == '-' }
}

/**
 * The invite a PC would show for this device — the same string the desktop
 * writes into its QR code.
 *
 * Here so the phone can offer one too once it can receive, and so the parsing
 * above is tested against something that generates rather than only against
 * strings written by hand.
 */
fun inviteFor(
    host: String,
    port: Int = DEFAULT_PORT,
    code: String = "",
    deviceId: String = "",
    name: String = "",
    fingerprint: String = "",
): String = Invite(
    host = host,
    port = port,
    code = code,
    deviceId = deviceId,
    name = name,
    fingerprint = fingerprint.lowercase(),
).toUrl()
