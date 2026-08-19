package link.lan.core.test

import kotlin.system.exitProcess

/**
 * Entry point for the Kotlin core suite.
 *
 * With no arguments it runs the offline checks. With `--interop host port code`
 * it also drives a real LanLink node over pinned TLS, which is what proves the
 * two implementations agree rather than each agreeing with itself.
 */
fun main(args: Array<String>) {
    registerCoreTests()

    val interop = args.indexOf("--interop")
    if (interop >= 0) {
        val host = args.getOrNull(interop + 1) ?: error("--interop needs a host")
        val port = args.getOrNull(interop + 2)?.toIntOrNull() ?: error("--interop needs a port")
        val code = args.getOrNull(interop + 3) ?: error("--interop needs a pairing code")
        registerInteropTests(host, port, code)
    }

    exitProcess(Suite.report())
}
