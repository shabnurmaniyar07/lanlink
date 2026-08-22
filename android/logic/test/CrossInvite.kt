package link.lan.app.test

/**
 * Print what the Kotlin side makes of an invite, as one line of key=value, so
 * the Python suite can compare its own parse against it. Two implementations
 * that only ever agree with themselves are not interoperable.
 */
fun describeInvite(text: String) {
    when (val outcome = link.lan.app.examineInvite(text, allowInsecure = true)) {
        is link.lan.app.InviteCheck.Ready -> {
            val i = outcome.invite
            // Tab-separated: a device name may contain spaces, and this line is
            // parsed by the Python suite.
            println(
                listOf(
                    "host=${i.host}",
                    "port=${i.port}",
                    "code=${i.code}",
                    "id=${i.deviceId}",
                    "name=${i.name}",
                    "fp=${i.fingerprint}",
                    "scheme=${i.scheme}",
                    "pinned=${outcome.pinnedFingerprint.isNotEmpty()}",
                ).joinToString("\t", prefix = "ready\t")
            )
        }
        is link.lan.app.InviteCheck.AlreadyPaired -> println("paired")
        is link.lan.app.InviteCheck.Rejected -> println("rejected ${outcome.reason}")
    }
}

/**
 * Print the short fingerprint the phone would show, so the Python suite can
 * check it against the one the desktop puts on screen. This is the string a
 * person reads off one device and compares with the other; if the two formatted
 * it differently, pairing by eye would be impossible and nobody would know why
 * until they tried it.
 */
fun describeFingerprint(fingerprint: String) {
    println("short\t${link.lan.core.Pinning.shortFingerprint(fingerprint)}")
}
