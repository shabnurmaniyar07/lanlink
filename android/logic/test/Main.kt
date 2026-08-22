package link.lan.app.test

import link.lan.core.test.Suite
import kotlin.system.exitProcess

/**
 * Entry point for the app-logic suite.
 *
 * With no arguments it runs the offline checks. With `--interop host port code`
 * it also pairs with a real node, stores the result, reconnects with the stored
 * pin, and unpairs — the whole flow the phone will run, minus the phone.
 */
fun main(args: Array<String>) {
    registerLogicTests()
    registerBrowsingTests()
    registerInviteTests()
    registerConnectionTests()
    registerRelocationTests()

    val describe = args.indexOf("--invite")
    if (describe >= 0) {
        describeInvite(args.getOrNull(describe + 1) ?: error("--invite needs a URL"))
        exitProcess(0)
    }

    val fingerprint = args.indexOf("--fingerprint")
    if (fingerprint >= 0) {
        describeFingerprint(args.getOrNull(fingerprint + 1) ?: error("--fingerprint needs a value"))
        exitProcess(0)
    }

    val interop = args.indexOf("--interop")
    if (interop >= 0) {
        val host = args.getOrNull(interop + 1) ?: error("--interop needs a host")
        val port = args.getOrNull(interop + 2)?.toIntOrNull() ?: error("--interop needs a port")
        val code = args.getOrNull(interop + 3) ?: error("--interop needs a pairing code")
        registerLogicInteropTests(host, port, code)
    }

    exitProcess(Suite.report())
}
